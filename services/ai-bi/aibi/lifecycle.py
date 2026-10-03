"""A BI project's life after it is generated: versions, the .ssbi package, DEV → UAT → PROD, restore.

  DEV   the AI and the developer create and modify. A generation that passes validation ends here as
        READY_FOR_REVIEW.
  UAT   a reviewer approves. Refused unless the version's validation passed AND the dashboard in the BI
        engine is still, byte for byte, the one that was validated. The .ssbi package is built here.
  PROD  an explicit approval by a second person. What ships is the package UAT built — its checksum is
        verified again — not whatever the BI engine holds that day.

WHAT THIS KIT DOES NOT HAVE: three BI engines and three warehouses. There is one Superset and one PostgreSQL,
so a promotion here is the gate, the package, the record and a visible mark on the dashboard — not a copy onto
other machines. The package is what an installer on a real UAT or PROD would be given, which is the point of
building and checksumming it.

A project's folder in the `bi-projects` repository IS the package layout (manifest, dbt/, semantic/,
datasets/, metrics/, superset/, dashboards/, charts/, security/, tests/, documentation/): a version is a
commit of that folder, and a package is that commit, zipped, with the commit named in its manifest.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Optional

import yaml

from .models import Approval, DashboardSpec, ModelPlan, ProjectVersion, ValidationReport, utcnow
from .policy_engine import Identity, PolicyError
from .store import version_key
from .tools import ToolContext, Toolbox

# Set by a promotion, on purpose, after validation: they are governance marks, not content.
_GOVERNANCE_KEYS = ("certified_by", "certification_details", "published", "is_managed_externally", "external_url")


class LifecycleError(Exception):
    pass


def asset_version(bundle: bytes) -> str:
    """A fingerprint of a dashboard export that depends on its CONTENT only: not on the export's timestamped
    folder, its metadata file, or the marks a promotion sets."""
    digest = hashlib.sha256()
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        for name in sorted(archive.namelist(), key=lambda n: n.split("/", 1)[-1]):
            inner = name.split("/", 1)[-1]
            if name.endswith("/") or inner == "metadata.yaml":
                continue
            document = yaml.safe_load(archive.read(name))
            if isinstance(document, dict):
                for key in _GOVERNANCE_KEYS:
                    document.pop(key, None)
            digest.update(inner.encode())
            digest.update(json.dumps(document, sort_keys=True, default=str, ensure_ascii=False).encode())
    return "sha256:" + digest.hexdigest()


def _dump(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=110),
                    encoding="utf-8", newline="\n")


class Lifecycle:
    def __init__(self, tools: Toolbox, exports_dir: Path):
        self.tools = tools
        self.store = tools.store
        self.registry = tools.registry
        self.git = tools.git
        self.superset = tools.superset
        self.audit = tools.audit
        self.packages_dir = tools.state_dir / "packages"
        self.exports_dir = exports_dir

    # ── versions ────────────────────────────────────────────────────────────────────────────────────────────
    def next_version(self, project_id: str) -> str:
        latest = self.store.latest_version(project_id)
        if latest is None:
            return "1.0.0"
        major, minor, _ = version_key(latest.version)
        return f"{major}.{minor + 1}.0"

    def store_version(self, ctx: ToolContext, requirement: dict[str, Any], report: ValidationReport,
                      dbt_results: list[dict[str, Any]], notes: list[str]) -> ProjectVersion:
        """Write everything this version IS into the project's folder. Nothing is committed yet."""
        body = self.tools.project(ctx)
        project_id = body["project_id"]
        spec = DashboardSpec(**body["spec"])
        template = self.registry.template(spec.dashboard.template)
        version = self.next_version(project_id)
        folder = self.git.project_dir(project_id)
        for entry in folder.iterdir():                       # the folder is this version, not a pile of them
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()

        datasets = [self.registry.dataset(ds) for ds in spec.datasets()]
        metrics = [self.registry.metric(m) for m in spec.metrics()]
        plan = ModelPlan(**body["model_plan"]) if body.get("model_plan") else None
        if plan:
            datasets += [self.registry.dataset(j.dataset) for j in plan.joins
                         if j.dataset not in {d.id for d in datasets}]

        _dump(folder / "dashboards" / f"{spec.dashboard.id}.spec.yaml", spec.model_dump(exclude_none=True))
        _dump(folder / "dashboards" / "requirement.yaml", requirement)
        for dataset in datasets:
            document = dataset.model_dump(exclude_none=True)
            _dump(folder / "datasets" / f"{dataset.id}.yaml", {"dataset": {k: v for k, v in document.items() if k != "columns"},
                                                               "columns": document["columns"]})
        for metric in metrics:
            _dump(folder / "metrics" / f"{metric.id}.yaml", {"metric": metric.model_dump(exclude_none=True)})

        # dbt: what was generated, and exactly which governed project it was built against
        dbt_info = body.get("dbt") or {}
        dbt_document: dict[str, Any] = {"governed_project_commit": self.git.dbt.git("rev-parse", "main"),
                                        "generated": bool(plan)}
        if plan:
            workspace = self.git.workspaces / project_id / "dbt"
            for entry in dbt_info.get("files", []):
                target = folder / "dbt" / entry["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(workspace / entry["path"], target)
            dbt_document.update(branch=dbt_info.get("branch"), commit=dbt_info.get("commit"),
                                model=plan.name, plan=plan.model_dump())
        _dump(folder / "dbt" / "dbt.yaml", dbt_document)

        lineage = {"metrics": {}, "datasets": {}}
        for metric in metrics:
            lineage["metrics"][metric.id] = {"version": metric.version, "certified": metric.certified,
                                             "expression": metric.expression, **metric.lineage.model_dump()}
        for dataset in datasets:
            node = self.store.lineage_for(dataset.source_model) or {}
            lineage["datasets"][dataset.id] = {
                "version": dataset.version, "certified": dataset.certified, "relation": dataset.relation,
                "dbt_model": dataset.source_model, "derived_from": dataset.derived_from,
                "built_from": [p["name"] for p in node.get("depends_on", [])],
                "tests": node.get("tests", [])}
        _dump(folder / "semantic" / "lineage.yaml", lineage)

        identity = ctx.identity
        _dump(folder / "security" / "access.yaml", {
            "requested_by": identity.user, "tenant": identity.tenant, "roles": list(identity.roles),
            "datasets": {d.id: {"tenant": d.tenant,
                                "classifications": {name: col.classification for name, col in d.columns.items()},
                                "row_level_policy": self.tools.policy.row_policies(identity, d.id)}
                         for d in datasets},
            "bi_engine_connection": {"name": self.tools._bi_database[0], "read_only": True}})

        # the BI engine's own export: charts and dashboards where a reviewer expects them, the rest together
        bundle = self.superset.export_dashboard(body["engine"]["dashboard"]["id"])
        (folder / "superset").mkdir(parents=True, exist_ok=True)
        (folder / "superset" / "dashboard_export.zip").write_bytes(bundle)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            for name in archive.namelist():
                inner = name.split("/", 1)[-1]
                if name.endswith("/") or not inner:
                    continue
                top = inner.split("/", 1)[0]
                target = folder / inner if top in ("charts", "dashboards") else folder / "superset" / inner
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))

        (folder / "tests").mkdir(parents=True, exist_ok=True)
        (folder / "tests" / "validation_report.json").write_text(
            json.dumps({"summary": report.summary(), "checks": [c.model_dump() for c in report.checks]},
                       indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        (folder / "tests" / "dbt_results.json").write_text(json.dumps(dbt_results, indent=2), encoding="utf-8",
                                                           newline="\n")

        documentation = folder / "documentation"
        documentation.mkdir(parents=True, exist_ok=True)
        shots = []
        for render in self.tools.evidence.get(project_id, {}).get("renders", []):
            if render.screenshot and Path(render.screenshot).exists():
                shutil.copy2(render.screenshot, documentation / Path(render.screenshot).name)
                shots.append(Path(render.screenshot).name)
        (documentation / "README.md").write_text(
            self._readme(spec, template, metrics, datasets, plan, report, notes, version, shots),
            encoding="utf-8", newline="\n")

        record = ProjectVersion(
            project_id=project_id, version=version, created_by=identity.user, request_id=ctx.request_id,
            title=spec.dashboard.title, dbt_commit=dbt_info.get("commit") or dbt_document["governed_project_commit"],
            superset_asset_version=asset_version(bundle),
            dataset_versions={d.id: d.version for d in datasets},
            metric_versions={m.id: m.version for m in metrics},
            template_version=f"{template.id}@{template.version}",
            dashboard=body["engine"]["dashboard"], validation=report.summary())
        _dump(folder / "manifest.yaml", self._manifest(record))
        return record

    @staticmethod
    def _manifest(record: ProjectVersion, **extra: Any) -> dict[str, Any]:
        return {"format": "ssbi/1", "project_id": record.project_id, "version": record.version,
                "title": record.title, "state": record.state, "review_status": record.review_status,
                "created_by": record.created_by, "created_at": record.created_at, "request_id": record.request_id,
                "dbt_commit": record.dbt_commit, "superset_asset_version": record.superset_asset_version,
                "dataset_versions": record.dataset_versions, "metric_versions": record.metric_versions,
                "template_version": record.template_version, "validation": record.validation, **extra}

    def _readme(self, spec, template, metrics, datasets, plan, report, notes, version, shots) -> str:
        summary = report.summary()
        lines = [f"# {spec.dashboard.title}", "",
                 f"BI project `{spec.dashboard.id}` · version {version} · template `{template.id}@{template.version}`",
                 "", f"> {spec.dashboard.description}", "", "## What it shows", "",
                 "| Slot | Visualization | Metric | By | Dataset |", "|---|---|---|---|---|"]
        for c in spec.components:
            lines.append(f"| {c.slot} | {c.type} | {c.metric} | {c.dimension or '—'} | {c.dataset} |")
        lines += ["", "Filters: " + (", ".join(spec.filters) or "none"), "", "## Metrics", ""]
        for m in metrics:
            lines += [f"**{m.name}** (`{m.id}` v{m.version}, {'certified' if m.certified else 'NOT certified'}, "
                      f"owner {m.owner})", "", f"    {m.expression}", "", " ".join(m.documentation.split()), ""]
        lines += ["## Datasets", ""]
        for d in datasets:
            mark = "certified" if d.certified else "generated, NOT certified" if d.generated_by else "not certified"
            lines.append(f"- `{d.id}` v{d.version} — {mark} — `{d.relation}` — {d.description.strip()}")
        if plan:
            lines += ["", "## Generated transformation", "",
                      f"`{plan.name}` — {plan.reason}. It is built on the project's own branch into the "
                      "development schema, and reaches the governed dbt project only when a person merges it."]
        lines += ["", "## Validation", "",
                  f"{summary['checks']} checks, {summary['failures']} failed, {summary['warnings']} warnings.", "",
                  "| Category | Pass | Warn | Fail | Skipped |", "|---|---|---|---|---|"]
        for category, counts in summary["by_category"].items():
            lines.append(f"| {category} | {counts['pass']} | {counts['warn']} | {counts['fail']} | {counts['skipped']} |")
        if notes:
            lines += ["", "## Notes from the generation", ""] + [f"- {n}" for n in notes]
        for shot in shots:
            lines += ["", f"![{shot}]({shot})"]
        return "\n".join(lines) + "\n"

    def commit_version(self, ctx: ToolContext, record: ProjectVersion) -> ProjectVersion:
        message = (f"{record.project_id} {record.version}: {record.title}\n\n"
                   f"Request {record.request_id} by {record.created_by}\n"
                   f"Template {record.template_version} · dbt {record.dbt_commit[:12] if record.dbt_commit else '-'}\n"
                   f"Superset assets {record.superset_asset_version}")
        record.git_commit = self.tools.commit_project_version(ctx, message=message)["commit"]
        self.git.tag_project(record.project_id, record.version, "", f"{record.project_id} {record.version}")
        self.store.save_version(record)
        return record

    def mark_ready(self, ctx: ToolContext, record: ProjectVersion) -> ProjectVersion:
        if not record.validation.get("passed"):
            raise LifecycleError("a version whose validation failed cannot be offered for review")
        record.state, record.review_status = "DEV", "READY_FOR_REVIEW"
        self.store.save_version(record)
        self.audit.record(action="version:ready_for_review", user=ctx.identity.user, request_id=ctx.request_id,
                          actor="ai", ai_model=ctx.ai_model, project_id=record.project_id,
                          validation=record.validation,
                          detail={"version": record.version, "git_commit": record.git_commit,
                                  "dbt_commit": record.dbt_commit,
                                  "superset_asset_version": record.superset_asset_version})
        return record

    # ── the package ─────────────────────────────────────────────────────────────────────────────────────────
    def build_package(self, record: ProjectVersion) -> dict[str, Any]:
        """<project>-<version>.ssbi: the project's folder AS COMMITTED, with the commit named in its manifest."""
        # straight from the commit, as bytes: what is zipped is what was committed, not what lies in the folder
        done = subprocess.run(["git", "-c", "safe.directory=*", "archive", "--format=tar",
                               f"{record.git_commit}:{record.project_id}"],
                              cwd=str(self.git.projects.path), capture_output=True)
        if done.returncode != 0:
            raise LifecycleError("the version's commit could not be read: " + done.stderr.decode()[:200])
        files: dict[str, bytes] = {}
        with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
            for member in archive.getmembers():
                if member.isfile():
                    files[member.name] = archive.extractfile(member).read()
        checksums = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())
                     if name != "manifest.yaml"}
        manifest = self._manifest(record, git_commit=record.git_commit, packaged_at=utcnow(), files=checksums)
        files["manifest.yaml"] = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True).encode()

        self.packages_dir.mkdir(parents=True, exist_ok=True)
        path = self.packages_dir / f"{record.project_id}-{record.version}.ssbi"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
            for name in sorted(files):
                package.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), files[name])
        info = {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size, "files": len(files)}
        try:                                    # for whoever carries it to another environment
            self.exports_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, self.exports_dir / path.name)
            info["exported_to"] = f"exports/{path.name}"
        except OSError as exc:
            info["export_error"] = str(exc)[:200]
        return info

    def package_path(self, record: ProjectVersion) -> Optional[Path]:
        path = self.packages_dir / record.package.get("file", "")
        return path if record.package.get("file") and path.exists() else None

    # ── promotion ───────────────────────────────────────────────────────────────────────────────────────────
    def drift(self, record: ProjectVersion) -> Optional[str]:
        """None when the dashboard in the BI engine is still the one that was validated."""
        dashboard_id = record.dashboard.get("id")
        if dashboard_id is None or self.superset.get_dashboard(dashboard_id) is None:
            return "the dashboard no longer exists in the BI engine"
        current = asset_version(self.superset.export_dashboard(dashboard_id))
        if current != record.superset_asset_version:
            return (f"the dashboard in the BI engine ({current[:19]}…) is not the one that was validated "
                    f"({(record.superset_asset_version or '')[:19]}…): it was changed after validation")
        return None

    def promote(self, identity: Identity, project_id: str, target: str, comment: str, confirm: bool,
                request_id: str) -> ProjectVersion:
        record = self.store.latest_version(project_id)
        if record is None:
            raise LifecycleError(f"BI project {project_id} has no version to promote")
        try:
            self.tools.policy.require_approver(identity, target, record.created_by)
            expected = {"UAT": ("DEV", "READY_FOR_REVIEW"), "PROD": ("UAT", "READY_FOR_REVIEW")}[target]
            if (record.state, record.review_status) != expected:
                raise LifecycleError(f"{project_id} {record.version} is {record.state}/{record.review_status}; "
                                     f"a promotion to {target} needs {expected[0]}/{expected[1]}")
            if not record.validation.get("passed"):
                raise LifecycleError("unvalidated assets are not deployed: this version's validation did not pass")
            changed = self.drift(record)
            if changed:
                raise LifecycleError(f"unvalidated assets are not deployed: {changed}. Regenerate, or restore "
                                     f"version {record.version}.")
            if target == "PROD":
                if not confirm or not comment.strip():
                    raise LifecycleError("production needs an explicit approval: confirm=true and a comment "
                                         "saying what was checked in UAT")
                package = self.package_path(record)
                if package is None or hashlib.sha256(package.read_bytes()).hexdigest() != record.package.get("sha256"):
                    raise LifecycleError("the package approved in UAT is missing or no longer matches its checksum")
        except (LifecycleError, PolicyError) as exc:
            self.audit.record(action=f"promotion:{target}", user=identity.user, request_id=request_id,
                              actor="human", project_id=project_id, outcome="refused",
                              deployment={"target": target, "version": record.version},
                              detail={"reason": str(exc)})
            raise

        dashboard_id = record.dashboard["id"]
        marks: dict[str, Any] = {}
        if target == "UAT":
            # nothing is marked on the dashboard yet: it is under test, and it stays an unpublished draft
            record.package = self.build_package(record)
        else:
            marks = {"published": True, "is_managed_externally": True,
                     "certified_by": f"{identity.user} (SharpSight)",
                     "certification_details": f"SharpSight BI project {project_id} v{record.version} — PROD. "
                                              f"Package sha256 {record.package['sha256'][:16]}…"}
            for chart in self.store.get_project(project_id)["engine"]["charts"].values():
                self.superset.update_chart(chart["id"], {"is_managed_externally": True})
            self.superset.update_dashboard(dashboard_id, marks)

        record.approvals.append(Approval(target=target, decision="approved", by=identity.user, comment=comment))
        record.state = target
        record.review_status = "READY_FOR_REVIEW" if target == "UAT" else "APPROVED"
        self.store.save_version(record)

        folder = self.git.project_dir(project_id)
        _dump(folder / "deployments.yaml", {"deployments": [a.model_dump() for a in record.approvals],
                                            "package": record.package})
        self.git.commit_project(project_id, f"{project_id} {record.version}: approved for {target} by {identity.user}")
        tag = self.git.tag_project(project_id, record.version, target.lower(),
                                   f"{project_id} {record.version} → {target}, approved by {identity.user}")
        self.audit.record(action=f"promotion:{target}", user=identity.user, request_id=request_id, actor="human",
                          project_id=project_id, validation=record.validation,
                          superset_assets=[{"type": "dashboard", "id": dashboard_id, "action": "marked", **marks}],
                          deployment={"target": target, "version": record.version, "git_commit": record.git_commit,
                                      "tag": tag, "package": record.package, "comment": comment})
        return record

    def reject(self, identity: Identity, project_id: str, comment: str, request_id: str) -> ProjectVersion:
        record = self.store.latest_version(project_id)
        if record is None:
            raise LifecycleError(f"BI project {project_id} has no version to reject")
        self.tools.policy.require(identity, "bi.reject")
        if record.review_status != "READY_FOR_REVIEW":
            raise LifecycleError(f"{project_id} {record.version} is not awaiting review")
        target = "UAT" if record.state == "DEV" else "PROD"
        record.approvals.append(Approval(target=target, decision="rejected", by=identity.user, comment=comment))
        record.review_status = "REJECTED"
        self.store.save_version(record)
        self.audit.record(action=f"promotion:{target}", user=identity.user, request_id=request_id, actor="human",
                          project_id=project_id, outcome="rejected",
                          deployment={"target": target, "version": record.version, "comment": comment})
        return record

    # ── restore ─────────────────────────────────────────────────────────────────────────────────────────────
    def specification_at(self, project_id: str, version: str) -> DashboardSpec:
        """The specification as it was committed for that version — from git, not from memory."""
        tag = f"{project_id}/v{version}"
        listing = self.git.projects.git("ls-tree", "--name-only", f"{tag}:{project_id}/dashboards", check=False)
        name = next((n for n in listing.splitlines() if n.endswith(".spec.yaml")), None)
        if name is None:
            raise LifecycleError(f"version {version} of {project_id} is not in the repository")
        text = self.git.projects.git("show", f"{tag}:{project_id}/dashboards/{name}")
        return DashboardSpec(**yaml.safe_load(text))
