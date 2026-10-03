"""The shared assets of the asset library that are SharpSight's own: charts and sections defined once.

A dataset or a metric is shared because it is in a governed registry. A chart or a section becomes shared
when a developer puts it here: from then on dashboards REFERENCE it instead of carrying a copy, and it can
be asked where it is used before anybody changes it.

What is kept is a definition in SharpSight's vocabulary — an approved visualization of a governed metric —
never BI-engine state. It lives in the control plane's store and, as a file, in the `bi-projects` repository
under `_library/`, so that a shared asset has a history like everything else.

Three rules are enforced here and nowhere else:
  · a shared asset that is used is not changed without its impact having been looked at;
  · a shared asset that is used is not deleted;
  · a change un-certifies: what was certified was the asset as it stood.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import yaml
from pydantic import ValidationError

from .audit import AuditLog
from .models import ChartAsset, SectionAsset, utcnow
from .policy_engine import Identity, PolicyEngine
from .registry import Registry
from .semantic_agent import SemanticAgent
from .store import Store

KINDS = {"chart": ChartAsset, "section": SectionAsset}


class LibraryError(Exception):
    def __init__(self, message: str, impact: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.impact = impact


class Library:
    def __init__(self, store: Store, registry: Registry, policy: PolicyEngine, audit: AuditLog, git: Any = None):
        self.store = store
        self.registry = registry
        self.policy = policy
        self.audit = audit
        self.git = git
        self.semantic = SemanticAgent(registry)

    # ── reading ─────────────────────────────────────────────────────────────────────────────────────────────
    def chart(self, asset_id: str) -> Optional[ChartAsset]:
        body = self.store.get_asset("chart", asset_id)
        return ChartAsset(**body) if body else None

    def charts(self) -> list[ChartAsset]:
        return [ChartAsset(**body) for body in self.store.list_assets("chart")]

    def section(self, asset_id: str) -> Optional[SectionAsset]:
        body = self.store.get_asset("section", asset_id)
        return SectionAsset(**body) if body else None

    def sections(self) -> list[SectionAsset]:
        return [SectionAsset(**body) for body in self.store.list_assets("section")]

    def get(self, kind: str, asset_id: str) -> Any:
        if kind not in KINDS:
            raise LibraryError(f"the library keeps charts and sections, not a {kind}")
        found = self.chart(asset_id) if kind == "chart" else self.section(asset_id)
        if found is None:
            raise LibraryError(f"there is no shared {kind} {asset_id}")
        return found

    # ── what makes a chart a valid definition ───────────────────────────────────────────────────────────────
    def chart_problems(self, chart: ChartAsset, identity: Identity) -> list[str]:
        """The same rules a specification's component is held to, for a chart that has no slot yet."""
        registry, problems = self.registry, []
        visualization = registry.visualizations.get(chart.type)
        if visualization is None:
            return [f"{chart.type} is not an approved visualization"]
        if visualization.status != "available":
            problems.append(f"{chart.type} is approved but its plugin is not installed in this BI engine")
        metric = registry.metrics.get(chart.metric)
        if metric is None:
            return [*problems, f"{chart.metric} is not in the metric registry"]
        if not self.policy.may_use(identity, metric):
            return [*problems, f"{identity.user} has no access to metric {chart.metric}"]
        needs = visualization.needs
        if needs.get("additive_metric") and not metric.additive:
            problems.append(f"{metric.id} is {metric.aggregation}: a {chart.type} of it would show shares of nothing")
        if chart.type == "histogram" and not metric.distribution_column:
            problems.append(f"{metric.id} has no column to draw a distribution of")
        if needs.get("dimension") and not chart.dimension:
            problems.append(f"a {chart.type} needs a dimension")
        candidates = [d for d in self.policy.permitted_datasets(identity) if self.semantic.valid_on(metric, d)[0]]
        if chart.dataset:
            candidates = [d for d in candidates if d.id == chart.dataset]
            if not candidates:
                problems.append(f"{metric.id} is not valid on {chart.dataset}, or {identity.user} may not read it")
        if chart.dimension and needs.get("dimension"):
            holders = [d for d in candidates if chart.dimension in d.columns]
            if not holders:
                problems.append(f"no dataset {identity.user} may read gives {metric.id} by {chart.dimension}")
            elif not any(set(d.columns[chart.dimension].determined_by or [chart.dimension]) <= set(metric.dimensions)
                         for d in holders):
                problems.append(f"{metric.id} may not be broken down by {chart.dimension}: its dimensions are "
                                f"{', '.join(metric.dimensions)}")
        return problems

    def section_problems(self, section: SectionAsset) -> list[str]:
        problems = [f"shared chart {c} does not exist" for c in section.charts if self.chart(c) is None]
        if not section.charts:
            problems.append("a section is made of at least one shared chart")
        if len(set(section.charts)) != len(section.charts):
            problems.append("a section names each shared chart once")
        return problems

    # ── writing ─────────────────────────────────────────────────────────────────────────────────────────────
    def save(self, identity: Identity, kind: str, body: dict[str, Any], request_id: str,
             impact: Optional[Callable[[str, str], dict[str, Any]]] = None, acknowledged: bool = False) -> Any:
        """Create a shared asset, or change one. `impact(kind, id)` says what uses it; a change to an asset
        in use goes through only when the caller has seen that and says so."""
        self.policy.require(identity, "assets.write")
        if kind not in KINDS:
            raise LibraryError(f"the library keeps charts and sections, not a {kind}")
        existing = self.store.get_asset(kind, body.get("id", ""))
        # what the caller may set; the rest is the library's to keep
        own = {k: v for k, v in body.items() if k not in ("certified", "certified_by", "revision", "created_by",
                                                         "created_at", "updated_at")}
        try:
            if existing:
                asset = KINDS[kind](**{**existing, **own, "revision": existing["revision"] + 1,
                                       "updated_at": utcnow(), "certified": False, "certified_by": None})
            else:
                asset = KINDS[kind](**{**own, "created_by": identity.user, "owner": own.get("owner") or identity.user})
        except ValidationError as exc:
            # what is wrong, field by field — not the validator's own footnotes
            raise LibraryError(f"not a valid shared {kind}: " + "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg'].removeprefix('Value error, ')}" for e in exc.errors())) from exc
        problems = self.chart_problems(asset, identity) if kind == "chart" else self.section_problems(asset)
        if problems:
            raise LibraryError(f"not a valid shared {kind}: " + "; ".join(problems))
        unchanged = existing is not None and all(existing.get(k) == v for k, v in asset.model_dump().items()
                                                 if k not in ("revision", "updated_at", "certified", "certified_by"))
        if unchanged:
            return KINDS[kind](**existing)
        used = impact(kind, asset.id) if (existing and impact) else None
        if used and used["total"] and not acknowledged:
            raise LibraryError(f"shared {kind} {asset.id} is in use — {used['message']} Look at the impact, then "
                               "send the change again with acknowledge_impact.", impact=used)
        self.store.save_asset(kind, asset.id, asset.model_dump(), asset.updated_at)
        commit = self._mirror(kind, asset.id, asset.model_dump(exclude_none=True),
                              f"library: {kind} {asset.id} r{asset.revision} by {identity.user}")
        self.audit.record(action=f"library:{'change' if existing else 'create'}", user=identity.user,
                          request_id=request_id, actor="human", git_commit=commit,
                          metrics=[asset.metric] if kind == "chart" else [],
                          detail={"kind": kind, "id": asset.id, "revision": asset.revision,
                                  "was_certified": bool(existing and existing.get("certified")),
                                  "used_by": used["counts"] if used else None})
        return asset

    def certify(self, identity: Identity, kind: str, asset_id: str, request_id: str) -> Any:
        self.policy.require(identity, "assets.certify")
        asset = self.get(kind, asset_id)
        if asset.created_by == identity.user:
            raise LibraryError("an asset is certified by someone other than the person who made it")
        asset.certified, asset.certified_by, asset.updated_at = True, identity.user, utcnow()
        self.store.save_asset(kind, asset.id, asset.model_dump(), asset.updated_at)
        commit = self._mirror(kind, asset.id, asset.model_dump(exclude_none=True),
                              f"library: {kind} {asset.id} r{asset.revision} certified by {identity.user}")
        self.audit.record(action="library:certify", user=identity.user, request_id=request_id, actor="human",
                          git_commit=commit, detail={"kind": kind, "id": asset.id, "revision": asset.revision})
        return asset

    def delete(self, identity: Identity, kind: str, asset_id: str, request_id: str,
               impact: Callable[[str, str], dict[str, Any]]) -> None:
        self.policy.require(identity, "assets.write")
        asset = self.get(kind, asset_id)
        used = impact(kind, asset_id)
        if used["total"]:
            raise LibraryError(f"shared {kind} {asset_id} is in use and is not deleted — {used['message']}", impact=used)
        self.store.delete_asset(kind, asset_id)
        commit = self._mirror(kind, asset_id, None, f"library: {kind} {asset_id} removed by {identity.user}")
        self.audit.record(action="library:delete", user=identity.user, request_id=request_id, actor="human",
                          git_commit=commit, detail={"kind": kind, "id": asset_id, "revision": asset.revision})

    def _mirror(self, kind: str, asset_id: str, document: Optional[dict[str, Any]], message: str) -> Optional[str]:
        """The asset as a file in the `bi-projects` repository, committed: a shared asset has a history."""
        if self.git is None:
            return None
        path = self.git.project_dir("_library") / f"{kind}s" / f"{asset_id}.yaml"
        if document is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=110),
                            encoding="utf-8", newline="\n")
        return self.git.commit_project("_library", message)
