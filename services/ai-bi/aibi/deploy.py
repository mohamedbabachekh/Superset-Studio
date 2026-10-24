"""Taking a version to another Superset — the « run » environment — and proving it arrived.

A `.ssbi` package already carries everything this needs: the specification, the governed datasets and
metrics it refers to, the dbt it was built with, the validation that passed, and — the part that matters
here — `superset/dashboard_export.zip`, Superset's OWN export of the dashboard, its charts, its datasets and
its connection, taken at the moment the version was committed (lifecycle.store_version).

So a deployment is not a re-creation. Nothing is generated again, nothing is asked of a language model, and
nothing is laid out a second time: the file that was validated is imported where it is wanted, repointed at
that environment's data, given the audience SharpSight decided, and then CHECKED — because an import that
returns 200 has not proved that a chart draws.

What this does NOT do, and will not: approve. A version reaches here already approved for its target by
someone other than whoever asked for it (lifecycle.promote). Executing a deployment is mechanical; deciding
one is not, and deciding stays where the second person is.

The five lessons this obeys, each paid for elsewhere in this repository:

  · an import KEEPS an existing connection's URI — so the connection is repointed afterwards, every time
    (docs/lessons.md); otherwise a run environment quietly reads the development warehouse;
  · a dashboard's audience is not in an export: roles and row-level rules are applied separately;
  · a catalog is recorded per dataset, and for PostgreSQL the catalog IS the database, so the target's
    database must be named what the package expects;
  · an import answers 200 for a dashboard whose charts cannot draw: every chart is asked for its data;
  · versions must match the matrix — a package built against one Superset is not assumed to fit another.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any, Callable, Literal, Optional

import yaml

from .compatibility import covers
from .models import ProjectVersion, Strict, utcnow
from .superset_adapter import SupersetAdapter, SupersetError
from .tools import AccessError, apply_access


class DeployError(RuntimeError):
    """A deployment that must not go on. Everything it says is meant to be read by a person."""


class Target(Strict):
    """Where a version goes, and what its data is called there."""
    name: str
    url: str
    user: str
    password: str
    # the governed connection in THAT environment: the same NAME as the package expects (the catalog is the
    # database), pointed at that environment's warehouse
    database_name: str
    database_uri: str
    # a target may be read-only to the Studio: then it is the command line's alone
    from_studio: bool = True
    # the approval a version needs to go THERE. It is the target's, not the caller's: a version signed off
    # for UAT by one reviewer must not reach a production Superset because a request said « UAT ». A target
    # nobody said is UAT is held to PROD — a missing setting asks for the second person, not for fewer.
    gate: Literal["UAT", "PROD"] = "PROD"
    # where a PERSON reaches it, when that is not where the engine does — a container name resolves inside
    # the network and nowhere else, and the link a deployment leaves behind is for somebody to open
    public_url: str = ""

    @property
    def where(self) -> str:
        return (self.public_url or self.url).rstrip("/")


class Step(Strict):
    key: str
    title: str
    status: str = "pending"                 # pending · done · failed · skipped
    detail: str = ""


class Deployment(Strict):
    id: str
    project_id: str
    version: str
    target: str
    by: str
    started_at: str = ""
    ended_at: Optional[str] = None
    status: str = "running"                 # running · succeeded · failed
    steps: list[Step] = []
    dashboard: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    error: Optional[str] = None

    def step(self, key: str) -> Step:
        return next(step for step in self.steps if step.key == key)


PLAN: list[tuple[str, str]] = [
    ("read_package", "Read the package and check its checksum"),
    ("check_compatibility", "Check it against the target's versions"),
    ("ensure_connection", "Point the governed connection at this environment"),
    ("import_assets", "Import the dashboard, its charts and its datasets"),
    ("repoint_connection", "Repoint the imported connection"),
    ("apply_access", "Grant the audience and the row-level rules"),
    ("verify_assets", "Read back what arrived"),
    ("verify_data", "Ask every chart for its data"),
    ("record", "Record the deployment"),
]


class Deployer:
    """One deployment, start to finish. Holds nothing between them."""

    def __init__(self, store: Any, audit: Any, lifecycle: Any, policy: Any, registry: Any,
                 adapter: Callable[[Target], SupersetAdapter] = None):
        self.store = store
        self.audit = audit
        self.lifecycle = lifecycle
        self.policy = policy
        self.registry = registry
        self._adapter = adapter or (lambda target: SupersetAdapter(target.url, target.user, target.password))

    # ── what may be deployed, and where ─────────────────────────────────────────────────────────────────────
    APPROVED_FOR = {"UAT": ("APPROVED_FOR_UAT", "UAT", "APPROVED_FOR_PROD", "PROD"),
                    "PROD": ("APPROVED_FOR_PROD", "PROD")}

    def refusal(self, record: Optional[ProjectVersion], target: Target, gate: str) -> Optional[str]:
        """Why this version may not go to that target — in the words a person needs. None: it may."""
        if record is None:
            return "there is no such version"
        if gate not in self.APPROVED_FOR:
            return f"{gate} is not a gate a deployment can be for"
        # the stricter of what was asked and what the target demands: asking for less changes nothing
        gate = "PROD" if "PROD" in (gate, target.gate) else "UAT"
        allowed = self.APPROVED_FOR[gate]
        if record.status not in allowed:
            return (f"{record.project_id} {record.version} is {record.status}: a deployment to {gate} carries "
                    f"a version already approved for it, by someone other than whoever asked for it. "
                    f"Approve it first — bash ai-bi.sh approve {record.project_id} {gate}.")
        if not record.validation.get("passed"):
            return "unvalidated assets are not deployed: this version's validation did not pass"
        problem = self.lifecycle.package_problem(record)
        if problem:
            return f"what would ship is not what was validated: {problem}"
        return None

    # ── the deployment itself ───────────────────────────────────────────────────────────────────────────────
    def deploy(self, record: ProjectVersion, target: Target, gate: str, by: str, request_id: str,
               deployment_id: str) -> Deployment:
        made = Deployment(id=deployment_id, project_id=record.project_id, version=record.version,
                          target=target.name, by=by, started_at=utcnow(),
                          steps=[Step(key=key, title=title) for key, title in PLAN])
        refused = self.refusal(record, target, gate)
        if refused:
            made.status, made.error = "failed", refused
            made.ended_at = utcnow()
            self._record(made, request_id, outcome="refused")
            return made
        try:
            self._run(made, record, target, gate)
            made.status = "succeeded"
        except (DeployError, SupersetError) as exc:
            made.status, made.error = "failed", f"{type(exc).__name__}: {str(exc)[:600]}"
            failed = next((step for step in made.steps if step.status == "pending"), None)
            if failed:
                failed.status, failed.detail = "failed", made.error
        made.ended_at = utcnow()
        self._record(made, request_id, outcome="ok" if made.status == "succeeded" else "error")
        return made

    def _run(self, made: Deployment, record: ProjectVersion, target: Target, gate: str) -> None:
        superset = self._adapter(target)

        # 1 · the package, and that it is the one that was validated
        step = made.step("read_package")
        path = self.lifecycle.package_path(record)
        if path is None or not Path(path).exists():
            raise DeployError("this version has no package to deploy")
        raw = Path(path).read_bytes()
        got = hashlib.sha256(raw).hexdigest()
        expected = (record.package or {}).get("sha256")
        if expected and got != expected:
            raise DeployError(f"{Path(path).name} is not the file that was validated "
                              f"(sha256 {got[:12]}…, expected {expected[:12]}…)")
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            names = package.namelist()
            if "superset/dashboard_export.zip" not in names:
                raise DeployError("this package carries no Superset export: it was built before exports were "
                                  "kept, and cannot be deployed")
            bundle = package.read("superset/dashboard_export.zip")
            schemas = _exported_schemas(bundle)
            manifest = yaml.safe_load(package.read("manifest.yaml").decode("utf-8")) or {}
        step.status, step.detail = "done", f"{Path(path).name}, {len(names)} files, sha256 {got[:12]}…"

        # 2 · the versions it was built against, against what is running there
        step = made.step("check_compatibility")
        built = (manifest.get("built_with") or {}).get("superset")
        running = superset.version()
        if built and running and not (covers(".".join(str(built).split(".")[:2]), running)):
            raise DeployError(f"this package was built against Superset {built} and {target.name} runs "
                              f"{running}: a package is not carried across a version the matrix has not "
                              f"tested (registry/compatibility.yaml)")
        step.status, step.detail = "done", f"built against {built or '—'}, {target.name} runs {running or '—'}"

        # 3 · the governed connection, by the name the package expects
        step = made.step("ensure_connection")
        database_id = superset.ensure_database(target.database_name, target.database_uri, sql_lab=False)
        step.status, step.detail = "done", f"{target.database_name} (#{database_id})"

        # 4 · the import itself — over a dashboard taken from its audience first. An overwrite KEEPS the existing
        # dashboard's roles (they are not in an export), so the previous version's audience would hold the new
        # charts before their row-level rules exist, and would keep them if any later step failed. Withdrawn
        # and read back before anything is imported: the access step grants it again once its rules are there.
        step = made.step("import_assets")
        spec = self.lifecycle.specification_at(record.project_id, record.version)
        existing = superset.get_dashboard(spec.dashboard.id)
        withdrawn = ""
        if existing is not None and existing.get("roles"):
            superset.update_dashboard(existing["id"], {"roles": []})
            still = superset.dashboard_roles(existing["id"])
            if still:
                raise DeployError(f"the dashboard {spec.dashboard.id} in {target.name} is still granted to "
                                  f"{', '.join(still)} after its audience was withdrawn: nothing was imported")
            withdrawn = f"; its previous audience ({len(existing['roles'])} roles) was withdrawn first"
        superset.import_dashboard(bundle, overwrite=True,
                                  passwords={f"databases/{target.database_name}.yaml": target.database_uri})
        step.status, step.detail = "done", f"the export was accepted{withdrawn}"

        # 5 · an import KEEPS an existing connection's URI (docs/lessons.md): repoint it, every time
        step = made.step("repoint_connection")
        superset.ensure_database(target.database_name, target.database_uri, sql_lab=False)
        step.status, step.detail = "done", f"{target.database_name} points at this environment"

        # 6 · who may open it, and which rows they see — never in an export
        step = made.step("apply_access")
        # Resolved strictly. A dataset or metric this registry no longer knows, quietly left out, is a
        # dashboard whose audience is decided over less than it shows — wider, and then read back as right.
        unknown = ([f"dataset {d}" for d in spec.datasets() if d not in self.registry.datasets]
                   + [f"metric {m}" for m in spec.metrics() if m not in self.registry.metrics])
        if unknown:
            raise DeployError(f"{record.project_id} {record.version} uses {', '.join(unknown)}, which the "
                              "registry no longer has: its audience cannot be decided, and nothing is granted")
        datasets = [self.registry.datasets[d] for d in spec.datasets()]
        metrics = [self.registry.metrics[m] for m in spec.metrics()]
        dashboard = superset.get_dashboard(spec.dashboard.id)
        if dashboard is None:
            raise DeployError(f"the dashboard {spec.dashboard.id} is not in {target.name} after the import")
        made.dashboard = {"id": dashboard["id"], "slug": spec.dashboard.id,
                          "url": f"{target.where}/superset/dashboard/{spec.dashboard.id}/"}
        access = self._apply_access(superset, target, dashboard["id"], datasets, metrics, database_id, schemas)
        audience, rules = access["audience"], access["rules"]
        step.status, step.detail = "done", (f"{len(audience)} roles ({', '.join(audience)}), {len(rules)} "
                                            f"row-level rules read back from {target.name}"
                                            if audience else "no role: nobody but an administrator")
        if access.get("kept"):
            step.detail += (f"; {len(access['kept'])} withdrawn rules kept while other dashboards are still "
                            f"granted to their role: {', '.join(access['kept'])}")

        # 7 · what arrived, read back
        step = made.step("verify_assets")
        charts = superset.dashboard_charts(dashboard["id"])
        wanted = len(spec.components)
        if len(charts) < wanted:
            raise DeployError(f"{len(charts)} of {wanted} charts arrived in {target.name}")
        # the grant was read back by the access step, which fails unless it is the one decided: what it read
        # is recorded here, rather than asked for a second time to the same answer
        granted = access["granted"]
        made.checks.append({"check": "charts arrived", "status": "pass", "detail": f"{len(charts)} of {wanted}"})
        made.checks.append({"check": "audience as SharpSight decided", "status": "pass",
                            "detail": ", ".join(granted) or "none"})
        step.status, step.detail = "done", f"{len(charts)} charts, {len(granted)} roles"

        # 8 · an import answers 200 for a dashboard whose charts cannot draw: ask each one
        step = made.step("verify_data")
        drew, failed = 0, []
        for chart in charts:
            try:
                answer = superset.chart_data(_query_of(chart))
                rows = sum(int(one.get("rowcount") or 0) for one in (answer.get("result") or []))
                drew += 1
                made.checks.append({"check": f"{chart.get('slice_name')} returns data", "status": "pass",
                                    "detail": f"{rows} rows"})
            except Exception as exc:                 # noqa: BLE001 — a chart that cannot draw is a finding
                failed.append(f"{chart.get('slice_name')}: {str(exc)[:160]}")
                made.checks.append({"check": f"{chart.get('slice_name')} returns data", "status": "fail",
                                    "detail": str(exc)[:200]})
        if failed:
            raise DeployError(f"{len(failed)} of {len(charts)} charts cannot draw in {target.name}: "
                              + "; ".join(failed[:3]))
        step.status, step.detail = "done", f"{drew} charts answered"

        # 9 · the record
        step = made.step("record")
        step.status, step.detail = "done", made.dashboard.get("url", "")

    def _apply_access(self, superset: SupersetAdapter, target: Target, dashboard_id: int, datasets: list,
                      metrics: list, database_id: int, schemas: dict[str, str]) -> dict[str, Any]:
        """The audience and the row-level rules, written to the target by the very helper that writes them to
        the kit's own Superset (tools.apply_access): the same role names, the same rule names, the same order —
        every rule read back before the dashboard is granted, the grant taken back if it fails. What is the
        target's own is only where each dataset is there."""
        try:
            # a rule hangs from the target's OWN dataset, which the import created there: found by the table
            # it reads, on the governed connection, in the schema the export recorded for it
            tables: dict[str, int] = {}
            for dataset in datasets:
                if not any(rule["dataset"] == dataset.id for rule in self.policy.row_rules(datasets, metrics)):
                    continue
                schema = schemas.get(dataset.source_model) or dataset.db_schema
                found = superset.find_dataset(database_id, schema, dataset.source_model)
                if found is None:
                    raise DeployError(f"the dataset {dataset.id} ({schema}.{dataset.source_model}) is not in "
                                      f"{target.name} after the import: its row-level rule has nothing to "
                                      "hang from, and a dashboard is not left without it")
                tables[dataset.id] = found["id"]
            return apply_access(superset, self.policy, dashboard_id, datasets, metrics, tables)
        except AccessError as exc:
            raise DeployError(f"{target.name} does not hold the access SharpSight decided: {exc}") from exc
        except SupersetError as exc:
            raise DeployError(f"{target.name} refused the audience or the row-level rules ({exc}): its service "
                              "account must be allowed to manage roles and row-level security, and the "
                              "dashboard is not left open without them") from exc

    def _record(self, made: Deployment, request_id: str, outcome: str) -> None:
        body = self.store.get_project(made.project_id) or {}
        history = body.setdefault("deployments", [])
        history.append(json.loads(made.model_dump_json()))
        body["deployments"] = history[-20:]
        self.store.save_project(made.project_id, body, utcnow())
        self.audit.record(action=f"deployment:{made.target}", user=made.by, request_id=request_id,
                          actor="human", project_id=made.project_id, outcome=outcome,
                          deployment={"target": made.target, "version": made.version, "id": made.id,
                                      "status": made.status},
                          detail={"error": made.error, "dashboard": made.dashboard,
                                  "steps": [f"{s.key}:{s.status}" for s in made.steps]})


def _exported_schemas(bundle: bytes) -> dict[str, str]:
    """The schema of each dataset in Superset's own export, by table name — where the import put it."""
    found: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(bundle)) as export:
        for name in export.namelist():
            if "/datasets/" in name and name.endswith((".yaml", ".yml")):
                body = yaml.safe_load(export.read(name).decode("utf-8")) or {}
                if body.get("table_name") and body.get("schema"):
                    found[body["table_name"]] = body["schema"]
    return found


def _query_of(chart: dict[str, Any]) -> dict[str, Any]:
    """The query context a chart was saved with — what the dashboard itself would send for it."""
    raw = chart.get("query_context")
    if not raw:
        raise DeployError(f"{chart.get('slice_name')} has no query context: it cannot be asked for its data")
    return json.loads(raw) if isinstance(raw, str) else raw
