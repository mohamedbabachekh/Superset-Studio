"""The asset library, as one catalogue: what there is, where it is used, and what a change would touch.

« Assets are reusable objects. Dashboards are compositions of those objects. » This file is where that is
made visible. It owns nothing: datasets, metrics, templates and chart templates are the registries';
shared charts and sections are the library's; dashboards and their own charts are the projects'. It reads
them all and answers four questions —

  · what assets are there, filed under which domain, shared or local to one dashboard;
  · where is this one used (dashboards, charts, metrics, sections, clients);
  · what would changing it affect, and is any of that in production;
  · is this dashboard in order: certified, owned, described, its dependencies whole, its security set.

Everything a caller sees is what the caller may read: a dataset they have no grant on is not in their library.
"""
from __future__ import annotations

from typing import Any, Optional

from .chart_agent import ChartAgent
from .library import Library
from .models import HELD, DashboardSpec, TemplateDef
from .policy_engine import Identity, PolicyEngine
from .registry import Registry
from .semantic_agent import SemanticAgent
from .store import Store

KINDS = ("dataset", "metric", "dimension", "chart", "section", "dashboard", "template", "chart_template")
_DIMENSION_ROLES = ("dimension", "time", "geo_region", "key")


class AssetError(Exception):
    pass


class Catalog:
    def __init__(self, registry: Registry, policy: PolicyEngine, store: Store, library: Library):
        self.registry = registry
        self.policy = policy
        self.store = store
        self.library = library
        self.semantic = SemanticAgent(registry)
        self.charts = ChartAgent(registry)

    # ── the projects, read once ─────────────────────────────────────────────────────────────────────────────
    def _projects(self, tenant: str) -> list[dict[str, Any]]:
        found = []
        for body in self.store.list_projects():
            if body.get("tenant") != tenant:
                continue
            latest = self.store.latest_version(body["project_id"])
            attempt = body.get("attempt") or {}

            def read(document: Any) -> Optional[DashboardSpec]:
                try:
                    return DashboardSpec(**document) if document else None
                except ValueError:
                    return None

            found.append({"id": body["project_id"], "title": body.get("title") or body["project_id"],
                          "spec": read(body.get("spec")), "draft": read((body.get("draft") or {}).get("spec")),
                          "status": latest.status if latest else attempt.get("status", "DRAFT"),
                          "environment": latest.state if latest else "DEV", "version": latest.version if latest else None,
                          "created_by": body.get("created_by"), "engine": body.get("engine") or {},
                          "tenant": body.get("tenant")})
        return found

    def _components(self, tenant: str) -> list[dict[str, Any]]:
        """Every chart of every dashboard: the built specification's, and a draft's where there is one."""
        uses = []
        for project in self._projects(tenant):
            built = project["spec"]
            for spec, draft in ((built, False), (project["draft"], True)):
                if spec is None:
                    continue
                template = self._template(spec)
                for component in spec.components:
                    if draft and built is not None and built.component(component.slot) == component:
                        continue        # the draft says of this slot what the built dashboard already says
                    uses.append({"dashboard": project["id"], "dashboard_title": spec.dashboard.title,
                                 "slot": component.slot, "chart": self._title(component, template),
                                 "component": component, "status": project["status"],
                                 "environment": project["environment"], "draft": draft,
                                 "filters": [*spec.filters, *(f for s in spec.sections for f in s.filters)],
                                 "template": template.id if template else spec.dashboard.template,
                                 "sections": [s.asset for s in spec.sections if s.asset]})
        return uses

    def _template(self, spec: DashboardSpec) -> Optional[TemplateDef]:
        try:
            return self.registry.template(spec.dashboard.template)
        except Exception:  # noqa: BLE001
            return None

    def _title(self, component: Any, template: Optional[TemplateDef]) -> str:
        metric = self.registry.metrics.get(component.metric)
        slot = template.slots.get(component.slot) if template else None
        if metric is None:
            return component.title or component.slot
        return self.charts.title(component, metric, slot.purpose if slot else "")

    # ── where is this used ──────────────────────────────────────────────────────────────────────────────────
    def usage(self, kind: str, asset_id: str, tenant: str) -> dict[str, Any]:
        registry = self.registry
        components = self._components(tenant)
        charts: list[dict[str, Any]] = []
        extra: dict[str, Any] = {}

        def on(test: Any) -> list[dict[str, Any]]:
            return [u for u in components if test(u)]

        if kind == "dataset":
            charts = on(lambda u: u["component"].dataset == asset_id)
            extra["metrics"] = sorted(m.id for m in registry.metrics.values() if asset_id in registry.datasets
                                      and self.semantic.valid_on(m, registry.datasets[asset_id])[0])
            extra["datasets"] = sorted(d.id for d in registry.datasets.values() if asset_id in d.derived_from)
            extra["shared_charts"] = sorted(c.id for c in self.library.charts() if c.dataset == asset_id)
        elif kind == "metric":
            charts = on(lambda u: u["component"].metric == asset_id)
            extra["shared_charts"] = sorted(c.id for c in self.library.charts() if c.metric == asset_id)
        elif kind == "dimension":
            charts = on(lambda u: u["component"].dimension == asset_id or asset_id in u["filters"])
            extra["shared_charts"] = sorted(c.id for c in self.library.charts() if c.dimension == asset_id)
        elif kind == "template":
            charts = on(lambda u: u["template"] == asset_id)
        elif kind == "chart":
            charts = on(lambda u: u["component"].asset == asset_id)
            extra["shared_sections"] = sorted(s.id for s in self.library.sections() if asset_id in s.charts)
        elif kind == "section":
            charts = on(lambda u: asset_id in u["sections"])
        elif kind == "chart_template":
            extra["shared_charts"] = sorted(c.id for c in self.library.charts() if c.template == asset_id)
        elif kind == "dashboard":
            charts = on(lambda u: u["dashboard"] == asset_id)
        else:
            raise AssetError(f"there is no kind of asset called {kind}")

        dashboards: dict[str, dict[str, Any]] = {}
        for use in charts:
            entry = dashboards.setdefault(use["dashboard"], {
                "id": use["dashboard"], "title": use["dashboard_title"], "status": use["status"],
                "environment": use["environment"], "draft_only": True})
            entry["draft_only"] = entry["draft_only"] and use["draft"]
        return {"dashboards": sorted(dashboards.values(), key=lambda d: d["id"]),
                "charts": [{"dashboard": u["dashboard"], "slot": u["slot"], "title": u["chart"], "draft": u["draft"]}
                           for u in charts] if kind != "template" else [],
                "clients": sorted({tenant}) if charts else [], **extra}

    def impact(self, kind: str, asset_id: str, tenant: str) -> dict[str, Any]:
        """What a change to this asset would touch — and whether any of it has been handed on to UAT or PROD."""
        used = self.usage(kind, asset_id, tenant)
        held = [d for d in used["dashboards"] if d["status"] in HELD]
        production = [d for d in held if d["environment"] == "PROD"]
        counts = {"dashboards": len(used["dashboards"]), "charts": len(used["charts"]), "clients": len(used["clients"]),
                  **{k: len(v) for k, v in used.items() if k not in ("dashboards", "charts", "clients")}}
        total = sum(counts[k] for k in counts if k != "clients")
        severity = "high" if production else "medium" if held else "low" if total else "none"
        parts = [f"{n} {name.replace('_', ' ')[:-1] if n == 1 else name.replace('_', ' ')}"
                 for name, n in counts.items() if n and name != "clients"]
        message = ("It is not used by anything." if not total else
                   "It is used by " + ", ".join(parts) + "."
                   + (" Changing it may affect PRODUCTION assets: " + ", ".join(d["id"] for d in production) + "."
                      if production else
                      " Changing it may affect assets handed on to UAT: " + ", ".join(d["id"] for d in held) + "."
                      if held else ""))
        return {"kind": kind, "id": asset_id, "counts": counts, "total": total, "severity": severity,
                "production": [d["id"] for d in production], "held": [d["id"] for d in held],
                "message": message, "used_by": used}

    # ── the assets, as cards ────────────────────────────────────────────────────────────────────────────────
    def _domain(self, kind: str, asset_id: str) -> dict[str, Any]:
        key = self.registry.domain_of(kind, asset_id)
        return {"domain": key, "domain_name": self.registry.domains[key].name if key else "Unfiled"}

    def cards(self, identity: Identity, kind: Optional[str] = None, project_id: Optional[str] = None) -> list[dict[str, Any]]:
        """Every asset the caller may see, one card each. `project_id`: also what is LOCAL to that dashboard."""
        registry, policy, tenant = self.registry, self.policy, identity.tenant
        components = self._components(tenant)
        favorites = {(k, i) for k, i in self.store.list_favorites(identity.user)}
        cards: list[dict[str, Any]] = []

        def count(test: Any) -> dict[str, int]:
            hits = [u for u in components if test(u)]
            return {"dashboards": len({u["dashboard"] for u in hits}), "charts": len(hits)}

        def card(kind_: str, asset_id: str, name: str, **fields: Any) -> None:
            if kind in (None, kind_):
                cards.append({"kind": kind_, "id": asset_id, "name": name, "scope": "shared", "project": None,
                              "favorite": (kind_, asset_id) in favorites, **fields})

        for dataset in policy.permitted_datasets(identity):
            local = bool(dataset.generated_by)
            if local and project_id and dataset.project != project_id:
                continue
            card("dataset", dataset.id, dataset.name, **self._domain("dataset", dataset.id),
                 status="generated" if local else "certified" if dataset.certified else "not_certified",
                 owner=dataset.owner, description=" ".join(dataset.description.split()),
                 scope="local" if local else "shared", project=dataset.project if local else None,
                 used_by=count(lambda u, d=dataset.id: u["component"].dataset == d),
                 facts={"type": dataset.kind, "grain": dataset.grain,
                        "refresh": _refresh(dataset.freshness_sla_minutes), "columns": len(dataset.columns)})
        for metric in registry.metrics.values():
            if policy.may_use(identity, metric):
                card("metric", metric.id, metric.name, **self._domain("metric", metric.id),
                     status="certified" if metric.certified else "not_certified", owner=metric.owner,
                     description=" ".join(metric.documentation.split()),
                     used_by=count(lambda u, m=metric.id: u["component"].metric == m),
                     facts={"aggregation": metric.aggregation, "format": metric.format, "unit": metric.unit,
                            "classification": metric.classification, "dimensions": metric.dimensions})
        seen_dimensions: dict[str, dict[str, Any]] = {}
        for dataset in policy.permitted_datasets(identity):
            for name, column in dataset.columns.items():
                if column.role in _DIMENSION_ROLES and name not in seen_dimensions and not dataset.generated_by:
                    seen_dimensions[name] = {"dataset": dataset, "column": column}
        for name, found in seen_dimensions.items():
            card("dimension", name, found["dataset"].label(name), **self._domain("dataset", found["dataset"].id),
                 status="certified" if found["dataset"].certified else "not_certified", owner=found["dataset"].owner,
                 description=found["column"].description,
                 used_by=count(lambda u, n=name: u["component"].dimension == n or n in u["filters"]),
                 facts={"type": found["column"].type, "role": found["column"].role,
                        "classification": found["column"].classification,
                        "datasets": sorted(d.id for d in policy.permitted_datasets(identity) if name in d.columns)})
        for chart in self.library.charts():
            metric = registry.metrics.get(chart.metric)
            if metric is None or not policy.may_use(identity, metric):
                continue
            card("chart", chart.id, chart.name, **self._domain("metric", chart.metric),
                 status="certified" if chart.certified else "not_certified", owner=chart.owner,
                 description=chart.description, used_by=count(lambda u, c=chart.id: u["component"].asset == c),
                 facts={"type": chart.type, "metric": chart.metric, "dimension": chart.dimension,
                        "dataset": chart.dataset, "revision": chart.revision, "template": chart.template})
        # LOCAL charts: a dashboard's own — its working copy's, when it has a draft. With a project named,
        # that dashboard's only (the builder's library); without, every dashboard's (the catalogue, a search).
        for project in self._projects(tenant):
            working = project["draft"] or project["spec"]
            if working is None or (project_id and project["id"] != project_id):
                continue
            for component in working.components:
                metric = registry.metrics.get(component.metric)
                if not component.asset and metric is not None and policy.may_use(identity, metric):
                    card("chart", f"{project['id']}:{component.slot}", self._title(component, self._template(working)),
                         **self._domain("metric", component.metric), status="local", owner="",
                         description=component.description or "", scope="local", project=project["id"],
                         used_by={"dashboards": 1, "charts": 1},
                         facts={"type": component.type, "metric": component.metric, "dimension": component.dimension,
                                "dataset": component.dataset, "slot": component.slot,
                                "dashboard": working.dashboard.title})
        for section in self.library.sections():
            card("section", section.id, section.name, domain=None, domain_name="Sections",
                 status="certified" if section.certified else "not_certified", owner=section.owner,
                 description=section.description,
                 used_by={"dashboards": len({u["dashboard"] for u in components if section.id in u["sections"]}),
                          "charts": len(section.charts)},
                 facts={"charts": section.charts, "filters": section.filters, "revision": section.revision})
        for project in self._projects(tenant):
            spec = project["spec"] or project["draft"]
            card("dashboard", project["id"], spec.dashboard.title if spec else project["title"], domain=None,
                 domain_name="Dashboards", status=project["status"], owner=(spec.dashboard.owner if spec else None)
                 or project["created_by"] or "", description=spec.dashboard.description if spec else "",
                 used_by={"dashboards": 0, "charts": len(spec.components) if spec else 0},
                 facts={"version": project["version"], "environment": project["environment"],
                        "template": spec.dashboard.template if spec else None,
                        "draft": project["draft"] is not None})
        for template in registry.templates.values():
            card("template", template.id, template.template.name, domain=None, domain_name="Dashboard templates",
                 status="certified", owner="", description=template.template.description,
                 used_by={"dashboards": len({u["dashboard"] for u in components if u["template"] == template.id}),
                          "charts": 0},
                 facts={"version": template.version, "slots": len(template.slots),
                        "sections": [s.title or s.id for s in template.section_list()]})
        for key, chart_template in registry.chart_templates.items():
            card("chart_template", key, chart_template.name, domain=None, domain_name="Chart templates",
                 status="certified" if chart_template.status == "available" else "not_available", owner="",
                 description=chart_template.description,
                 used_by={"dashboards": 0, "charts": sum(1 for c in self.library.charts() if c.template == key)},
                 facts={"visualization": chart_template.visualization, "alternatives": chart_template.alternatives,
                        "needs": chart_template.needs, "reason": chart_template.reason})
        return cards

    def tree(self, identity: Identity, project_id: Optional[str] = None) -> dict[str, Any]:
        """The library as the Studio's left panel shows it: favourites first, then one folder per domain."""
        cards = self.cards(identity, project_id=project_id)
        folders: dict[str, dict[str, Any]] = {key: {"id": key, "name": domain.name, "description": domain.description,
                                                    "owner": domain.owner, "assets": []}
                                              for key, domain in self.registry.domains.items()}
        loose: list[dict[str, Any]] = []
        for card in cards:
            (folders[card["domain"]]["assets"] if card.get("domain") in folders else loose).append(card)
        return {"favorites": [c for c in cards if c["favorite"]],
                "domains": [f for f in folders.values() if f["assets"]], "unfiled": loose,
                "counts": {kind: sum(1 for c in cards if c["kind"] == kind) for kind in KINDS}}

    def search(self, identity: Identity, q: str = "", kind: Optional[str] = None, domain: Optional[str] = None,
               status: Optional[str] = None, owner: Optional[str] = None, used_by: Optional[str] = None,
               scope: Optional[str] = None, project_id: Optional[str] = None) -> dict[str, Any]:
        """Search before navigation: every kind of asset at once, grouped by kind, filtered five ways."""
        words = [w for w in q.lower().split() if w]
        vocabulary = self.registry.lexicon.get("dimensions", {})
        uses = self._components(identity.tenant) if used_by else []

        def haystack(card: dict[str, Any]) -> str:
            extra: list[str] = []
            if card["kind"] == "metric":
                extra = self.registry.metrics[card["id"]].synonyms
            if card["kind"] == "dimension":
                extra = vocabulary.get(card["id"], [])
            return " ".join([card["id"], card["name"], card.get("description") or "", *extra,
                             *(str(v) for v in card["facts"].values() if isinstance(v, str))]).lower()

        def rank(card: dict[str, Any]) -> tuple[int, str]:
            name = card["name"].lower()
            first = words[0] if words else ""
            return (0 if name.startswith(first) else 1 if first in name or first in card["id"].lower() else 2, name)

        def used(card: dict[str, Any]) -> bool:
            field = {"dataset": "dataset", "metric": "metric", "dimension": "dimension", "chart": "asset"}.get(card["kind"])
            return any(u["dashboard"] == used_by and (
                (field and getattr(u["component"], field) == card["id"]) or
                (card["kind"] == "template" and u["template"] == card["id"]) or
                (card["kind"] == "section" and card["id"] in u["sections"]) or
                (card["kind"] == "chart" and card["id"] == f"{used_by}:{u['slot']}")) for u in uses)

        groups: dict[str, list[dict[str, Any]]] = {}
        for card in self.cards(identity, kind, project_id=project_id or used_by):
            text = haystack(card)
            if (all(w in text for w in words) and (not domain or card.get("domain") == domain)
                    and (not status or card["status"] == status) and (not scope or card["scope"] == scope)
                    and (not owner or owner.lower() in (card.get("owner") or "").lower())
                    and (not used_by or used(card))):
                groups.setdefault(card["kind"], []).append(card)
        return {"query": q, "total": sum(len(v) for v in groups.values()),
                "groups": {k: sorted(groups[k], key=rank)[:50] for k in KINDS if k in groups}}

    def detail(self, identity: Identity, kind: str, asset_id: str) -> dict[str, Any]:
        """One asset: its card, its whole definition, where it is used, what changing it would touch."""
        if kind not in KINDS:
            raise AssetError(f"there is no kind of asset called {kind}")
        project_id = asset_id.split(":", 1)[0] if kind == "chart" and ":" in asset_id else None
        card = next((c for c in self.cards(identity, kind, project_id=project_id) if c["id"] == asset_id), None)
        if card is None:
            raise AssetError(f"{identity.user} has no {kind} called {asset_id} in the asset library")
        registry = self.registry
        definition: dict[str, Any] = {}
        if kind == "dataset":
            dataset = registry.datasets[asset_id]
            readable = set(self.policy.readable_columns(identity, dataset))
            definition = {**dataset.model_dump(exclude={"columns"}, exclude_none=True), "relation": dataset.relation,
                          "columns": {name: {**col.model_dump(exclude_none=True), "label": dataset.label(name),
                                             "readable": name in readable} for name, col in dataset.columns.items()}}
        elif kind == "metric":
            definition = self.semantic.describe(registry.metrics[asset_id])
        elif kind == "dimension":
            definition = {"synonyms": registry.lexicon.get("dimensions", {}).get(asset_id, []), **card["facts"]}
        elif kind in ("chart", "section") and project_id is None:
            definition = self.library.get(kind, asset_id).model_dump()
        elif kind == "template":
            definition = registry.templates[asset_id].model_dump()
        elif kind == "chart_template":
            definition = registry.chart_templates[asset_id].model_dump()
        impact = self.impact(kind, asset_id, identity.tenant) if project_id is None else None
        return {**card, "definition": definition, "impact": impact}

    # ── a dashboard, looked at as a composition ─────────────────────────────────────────────────────────────
    def outline(self, spec: DashboardSpec, template: TemplateDef, sections: list[dict[str, Any]],
                problems: Optional[dict[str, int]] = None) -> dict[str, Any]:
        """The dashboard as a tree — sections, and in each its charts — the way a layers panel shows a page."""
        problems = problems or {}
        tree = []
        for section in sections:
            own = spec.section(section["id"])
            nodes = []
            for slot in section["slots"]:
                component = spec.component(slot) or next((p for p in (own.parked if own else []) if p.slot == slot), None)
                if component is None:
                    continue
                metric = self.registry.metrics.get(component.metric)
                nodes.append({"slot": slot, "title": self._title(component, template), "type": component.type,
                              "metric": component.metric, "metric_name": metric.name if metric else component.metric,
                              "dimension": component.dimension, "dataset": component.dataset,
                              "scope": "shared" if component.asset else "local", "asset": component.asset,
                              "kpi": component.type == "big_number", "parked": spec.component(slot) is None,
                              "mandatory": template.slots[slot].mandatory, "problems": problems.get(slot, 0)})
            tree.append({"id": section["id"], "title": section["title"] or section["id"],
                         "description": section["description"], "owner": section["owner"],
                         "visibility": section["visibility"], "asset": section["asset"], "filters": section["filters"],
                         "charts": nodes,
                         "empty_slots": [s for s in section["slots"] if not any(n["slot"] == s for n in nodes)]})
        return {"dashboard": {"id": spec.dashboard.id, "title": spec.dashboard.title, "owner": spec.dashboard.owner,
                              "template": template.id, "version": spec.dashboard.version},
                "filters": spec.filters, "sections": tree,
                "counts": {"sections": len(tree), "charts": sum(len(s["charts"]) for s in tree)}}

    def governance(self, spec: DashboardSpec, template: TemplateDef, identity: Identity,
                   last_validation: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """The best-practice checklist, visible while developing: certified, owned, described, whole, secured."""
        registry, items = self.registry, []

        def item(check: str, problems: list[str], ok: str, level: str = "warn") -> None:
            items.append({"check": check, "status": level if problems else "pass",
                          "detail": "; ".join(problems) if problems else ok, "count": len(problems)})

        datasets = [registry.datasets[d] for d in spec.datasets() if d in registry.datasets]
        metrics = [registry.metrics[m] for m in spec.metrics() if m in registry.metrics]
        item("Datasets certified", [f"{d.id} is {'generated for this project' if d.generated_by else 'registered'}, "
                                    "not certified" for d in datasets if not d.certified],
             f"{len(datasets)} of {len(datasets)}")
        item("Metrics certified", [f"{m.id} is not certified" for m in metrics if not m.certified],
             f"{len(metrics)} of {len(metrics)}")
        owners = ([] if spec.dashboard.owner else ["the dashboard has no owner"]) + [
            f"dataset {d.id} has no owner" for d in datasets if not d.owner] + [
            f"metric {m.id} has no owner" for m in metrics if not m.owner]
        item("Owner defined", owners, f"the dashboard is {spec.dashboard.owner}'s; every dataset and metric has an owner")
        undescribed = [c.slot for c in spec.components if not c.description]
        described = ([] if spec.dashboard.description.strip() else ["the dashboard has no description"]) + (
            [f"{len(undescribed)} chart(s) have no description of their own ({', '.join(undescribed)}): each "
             "shows its metric's"] if undescribed else []) + [
            f"section « {s.title or s.id} » has no description" for s in spec.sections
            if s.visibility == "visible" and not (s.description or next(
                (t.description for t in template.section_list() if t.id == s.id), ""))]
        item("Description complete", described, "the dashboard, its sections and its charts are described")
        broken = [f"dataset {d} is not registered" for d in spec.datasets() if d not in registry.datasets] + [
            f"metric {m} is not registered" for m in spec.metrics() if m not in registry.metrics] + [
            f"{c.slot} names shared chart {c.asset}, which does not exist" for c in spec.components
            if c.asset and self.library.chart(c.asset) is None] + [
            f"section {s.id} names shared section {s.asset}, which does not exist" for s in spec.sections
            if s.asset and self.library.section(s.asset) is None] + [
            f"{c.slot} is not a slot of template {template.id}" for c in spec.components if c.slot not in template.slots]
        item("No broken dependencies", broken, "every dataset, metric and shared asset it names exists", "fail")
        audience = self.policy.audience(datasets, metrics)
        rules = self.policy.row_rules(datasets, metrics)
        unreadable = [d.id for d in datasets if not self.policy.can_read(identity, d)]
        item("Security configured",
             ([f"no role may open it: none is granted every dataset and cleared for every metric"] if datasets and not audience
              else []) + [f"{identity.user} may not read {d}" for d in unreadable],
             f"for {', '.join(audience) or 'nobody yet'}; {len(rules)} row-level rule(s); data may be exported by "
             f"{registry.policies.get('security', {}).get('export', {}).get('data', 'audience')}", "fail")
        item("Dataset refresh SLA", [f"{d.id} has no refresh SLA" for d in datasets if not d.freshness_sla_minutes],
             "every dataset states how fresh it must be")
        shared = [self.library.chart(c.asset) for c in spec.components if c.asset]
        item("Shared assets certified", [f"shared chart {c.id} (r{c.revision}) is not certified" for c in shared
                                         if c is not None and not c.certified],
             f"{len(shared)} shared chart(s) referenced" if shared else "no shared chart is referenced")
        copies = []
        for component in spec.components:
            twin = next((c for c in self.library.charts() if not component.asset and (c.type, c.metric, c.dimension)
                         == (component.type, component.metric, component.dimension)), None)
            if twin is not None:
                copies.append(f"{component.slot} is the same chart as shared « {twin.name} » ({twin.id}): reference it")
        item("No duplicated asset", copies, "no local chart repeats a shared one")
        if last_validation is not None:
            item("Validation", [f"{last_validation.get('failures')} check(s) failed"]
                 if not last_validation.get("passed") else [],
                 f"{last_validation.get('checks')} checks passed on the last build, "
                 f"{last_validation.get('warnings')} warning(s)", "fail")
        return items

    def definition(self, project_id: str, identity: Identity, sections: list[dict[str, Any]],
                   spec: DashboardSpec, template: TemplateDef) -> dict[str, Any]:
        """The dashboard as the brief's section 15 lays it out: metadata, sections, shared assets, visual
        assets, security, deployment."""
        registry = self.registry
        latest = self.store.latest_version(project_id)
        datasets = [registry.datasets[d] for d in spec.datasets() if d in registry.datasets]
        metrics = [registry.metrics[m] for m in spec.metrics() if m in registry.metrics]
        outline = self.outline(spec, template, sections)
        charts = [c for s in outline["sections"] for c in s["charts"] if not c["parked"]]
        return {
            "metadata": {"name": spec.dashboard.title, "id": spec.dashboard.id, "project": project_id,
                         "description": spec.dashboard.description, "owner": spec.dashboard.owner,
                         "version": latest.version if latest else spec.dashboard.version,
                         "status": latest.status if latest else "DRAFT", "template": f"{template.id}@{template.version}"},
            "sections": outline["sections"],
            "shared_assets": {
                "datasets": [{"id": d.id, "name": d.name, "certified": d.certified,
                              "scope": "local" if d.generated_by else "shared"} for d in datasets],
                "metrics": [{"id": m.id, "name": m.name, "certified": m.certified} for m in metrics],
                "filters": [{"column": f, "scope": "dashboard"} for f in spec.filters] + [
                    {"column": f, "scope": f"section {s.id}"} for s in spec.sections for f in s.filters],
                "charts": sorted({c["asset"] for c in charts if c["asset"]}),
                "sections": sorted({s.asset for s in spec.sections if s.asset})},
            "visual_assets": {"kpi_cards": [c for c in charts if c["kpi"]], "charts": [c for c in charts if not c["kpi"]]},
            "security": {"audience": self.policy.audience(datasets, metrics),
                         "row_level_rules": self.policy.row_rules(datasets, metrics),
                         "classifications": {m.id: m.classification for m in metrics},
                         "export": registry.policies.get("security", {}).get("export", {})},
            "deployment": {"environment": latest.state if latest else "DEV",
                           "history": [h.model_dump() for h in latest.history] if latest else [],
                           "package": latest.package if latest else {}, "git_commit": latest.git_commit if latest else None},
        }


def _refresh(minutes: Optional[int]) -> str:
    if not minutes:
        return "no SLA"
    if minutes % 1440 == 0:
        return "Daily" if minutes == 1440 else f"Every {minutes // 1440} days"
    if minutes % 60 == 0:
        return "Hourly" if minutes == 60 else f"Every {minutes // 60} hours"
    return f"Every {minutes} minutes"
