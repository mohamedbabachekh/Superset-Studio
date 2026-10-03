"""The dashboard, as a specification first.

Three stages, in this order and never another:

  1. a DRAFT — the request's views laid into the template's slots, each with what it needs of a dataset;
  2. a SPECIFICATION — the normalized document of section 6: template, filters, one component per slot. It is
     what is reviewed, versioned and restored, and it says nothing in the BI engine's vocabulary;
  3. the engine's layout, filters and stylesheet, DERIVED from the specification and the template.

The AI chooses content for predefined slots. It does not choose positions, sizes or CSS: those come from the
template and the design tokens, and stage 3 has no input the AI controls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .dataset_agent import Need
from .models import (RESTRICTED_CLASSIFICATIONS, ComponentOptions, ComponentSpec, DashboardHeader, DashboardSpec,
                     MetricDef, Requirement, SectionSpec, TemplateDef, ValidationReport)
from .planner import slugify
from .policy_engine import Identity, PolicyEngine
from .registry import Registry
from .semantic_agent import SemanticAgent


@dataclass
class Draft:
    slot: str
    purpose: str
    type: str
    metric: MetricDef
    column: Optional[str] = None       # a column asked for by name
    role: Optional[str] = None         # or by what it must be
    options: ComponentOptions = field(default_factory=ComponentOptions)
    phrase: str = ""
    # filled when a dataset has been found for it
    dataset: Optional[str] = None
    dimension: Optional[str] = None

    def need(self) -> Need:
        return Need(purpose=self.purpose, metric=self.metric, column=self.column, role=self.role)


class DashboardAgent:
    def __init__(self, registry: Registry, policy: PolicyEngine, semantic: SemanticAgent):
        self.registry = registry
        self.policy = policy
        self.semantic = semantic

    # ── 1 · the draft ───────────────────────────────────────────────────────────────────────────────────────
    def draft(self, requirement: Requirement, template: TemplateDef,
              metrics: list[MetricDef]) -> tuple[list[Draft], list[str]]:
        notes: list[str] = []
        drafts: list[Draft] = []
        if not metrics:
            return drafts, ["no metric to show"]
        primary = metrics[0]
        by_id = {m.id: m for m in metrics}
        free = {purpose: template.slots_for(purpose) for purpose in {s.purpose for s in template.slots.values()}}

        for metric, slot in zip(metrics, list(free.get("kpi", []))):
            drafts.append(Draft(slot=slot, purpose="kpi", type="big_number", metric=metric))
            free["kpi"].remove(slot)
        for metric in metrics[len(drafts):]:
            notes.append(f"template {template.id} has no KPI slot left for {metric.name}")

        intents = self.registry.lexicon.get("intents", {})
        for view in requirement.views:
            purpose = intents.get(view.intent, {}).get("purpose", view.intent)
            slots = free.get(purpose) or []
            if not slots:
                notes.append(f"template {template.id} has no free slot for « {view.phrase} »")
                continue
            metric = by_id.get(view.metric or "", primary)
            made = self._draft_for(slots[0], purpose, template, metric, view.dimension, view.time_grain, view.phrase,
                                   prefer=view.prefer)
            if made is not None and view.prefer and made.type != view.prefer:
                wanted = self.registry.visualizations.get(view.prefer)
                why = (f"slot {slots[0]} of template {template.id} does not accept a {view.prefer}"
                       if view.prefer not in template.slots[slots[0]].accepts else
                       f"{metric.name}'s parts do not add up to a whole"
                       if wanted is not None and wanted.needs.get("additive_metric") and not metric.additive else
                       f"a {view.prefer} is not available")
                notes.append(f"« {view.phrase} » is drawn as a {made.type}, not a {view.prefer}: {why}")
            if made is None:
                # the slot stays free: a mandatory one is then filled with something that CAN be drawn
                notes.append(f"« {view.phrase} » left out: no visualization approved for slot {slots[0]} can show "
                             f"{metric.name} that way")
                continue
            slots.pop(0)
            drafts.append(made)

        # a template's mandatory slots are filled whether or not the request thought of them
        for slot_id, slot in template.slots.items():
            if slot.mandatory and not any(d.slot == slot_id for d in drafts):
                # the first requested metric the slot can actually show
                made = next((m for m in (self._draft_for(slot_id, slot.purpose, template, candidate, None, None, "")
                                         for candidate in metrics) if m is not None), None)
                if made is not None:
                    drafts.append(made)
                    notes.append(f"slot {slot_id} is mandatory in template {template.id}: filled with "
                                 f"{made.metric.name} by default")
        return drafts, notes

    def _draft_for(self, slot: str, purpose: str, template: TemplateDef, metric: MetricDef,
                   dimension: Optional[str], time_grain: Optional[str], phrase: str,
                   skip_types: tuple[str, ...] = (), prefer: Optional[str] = None) -> Optional[Draft]:
        defaults = [d for d in self.registry.lexicon.get("default_ranking_dimensions", [])
                    if d in metric.dimensions]
        accepts = template.slots[slot].accepts
        # the slot's own order, with the preferred type tried first WHEN the slot accepts it
        for kind in sorted(accepts, key=lambda k: k != prefer):
            visualization = self.registry.visualizations.get(kind)
            if kind in skip_types or visualization is None or visualization.status != "available":
                continue
            if visualization.needs.get("additive_metric") and not metric.additive:
                continue
            draft = Draft(slot=slot, purpose=purpose, type=kind, metric=metric, phrase=phrase)
            role = visualization.needs.get("dimension_role")
            if kind == "big_number":
                return draft
            if kind in ("line", "area"):
                draft.role = "time"
                draft.options = ComponentOptions(time_grain=time_grain or "P1D")
                return draft
            if kind == "histogram":
                if metric.distribution_column is None:
                    continue
                draft.column = metric.distribution_column
                return draft
            if role:
                draft.role = role
                return draft
            draft.column = dimension or (defaults[0] if defaults else None)
            if draft.column is None:
                continue
            return draft
        return None

    def fallback(self, draft: Draft, template: TemplateDef) -> Optional[Draft]:
        """The next approved type the slot accepts, when the first cannot be served by any dataset."""
        return self._draft_for(draft.slot, draft.purpose, template, draft.metric, None, None, draft.phrase,
                               skip_types=(draft.type,))

    # ── 2 · the specification ───────────────────────────────────────────────────────────────────────────────
    def specify(self, project_id: str, requirement: Requirement, template: TemplateDef,
                drafts: list[Draft], version: Optional[str] = None) -> tuple[DashboardSpec, list[str]]:
        notes: list[str] = []
        order = [p.id for row in template.layout for p in row.slots]
        components = [
            ComponentSpec(slot=d.slot, type=d.type, metric=d.metric.id, dataset=d.dataset,
                          dimension=None if d.type in ("big_number", "histogram") else d.dimension,
                          options=d.options)
            for d in sorted(drafts, key=lambda d: order.index(d.slot)) if d.dataset
        ]
        used = [self.registry.dataset(ds) for ds in dict.fromkeys(c.dataset for c in components)]
        filters: list[str] = []
        for column in requirement.filters:
            holders = [d for d in used if column in d.columns]
            if not holders:
                notes.append(f"filter « {column} » dropped: no dataset of this dashboard has that column")
                continue
            if holders[0].columns[column].role not in template.filters.allowed_roles:
                notes.append(f"filter « {column} » dropped: template {template.id} does not allow filtering on it")
                continue
            if len(filters) >= template.filters.max:
                notes.append(f"filter « {column} » dropped: template {template.id} allows {template.filters.max}")
                continue
            filters.append(column)
        title = template.template.title_pattern.format(subject=requirement.subject)
        # « CEM QoE » on the executive template is the project cem-qoe and the dashboard cem-qoe-overview
        suffix = slugify(title.replace(requirement.subject, "", 1))
        dashboard_id = project_id if slugify(title) == project_id else f"{project_id}-{suffix}"
        header = DashboardHeader(id=dashboard_id, template=template.id, title=title, version=version,
                                 description=" ".join(requirement.prompt.split())[:500])
        sections = [SectionSpec(id=section.id) for section in template.section_list()]
        return DashboardSpec(dashboard=header, filters=filters, components=components, sections=sections), notes

    # ── sections ────────────────────────────────────────────────────────────────────────────────────────────
    def sections(self, spec: DashboardSpec, template: TemplateDef) -> list[dict[str, Any]]:
        """Each of the template's sections as THIS dashboard has it: the template's rows and title, and what
        the dashboard says of it — another title, a description, an owner, filters of its own, hidden."""
        found = []
        for section in template.section_list():
            own = spec.section(section.id)
            found.append({"id": section.id, "rows": section.rows, "slots": template.slots_in(section.id),
                          "title": own.title if own and own.title is not None else section.title,
                          "description": own.description if own and own.description is not None else section.description,
                          "owner": own.owner if own else None, "visibility": own.visibility if own else "visible",
                          "filters": list(own.filters) if own else [],
                          "declared": own is not None})
        return found

    def resolve(self, spec: DashboardSpec, identity: Identity) -> list[str]:
        """Give each component that names no dataset the one its metric comes from. A specification may leave
        the dataset out — section 23's own example does — and it is then the registry that says where the
        metric, broken down that way, is found: a governed dataset before a generated one, the metric's own
        before one derived from it. Nothing is found for a breakdown no dataset offers; validation says so."""
        resolved: list[str] = []
        for component in spec.components:
            metric = self.registry.metrics.get(component.metric)
            if component.dataset or metric is None:
                continue
            visualization = self.registry.visualizations.get(component.type)
            role = visualization.needs.get("dimension_role") if visualization else None
            by_role = role not in (None, "time") and not component.dimension
            found = [d for d in self.policy.permitted_datasets(identity)
                     if self.semantic.valid_on(metric, d)[0]
                     and (component.dimension is None or component.dimension in d.columns)
                     and (not by_role or d.columns_with_role(role))]
            found.sort(key=lambda d: (not d.certified, d.id not in metric.lineage.datasets, d.id))
            if found:
                component.dataset = found[0].id
                if by_role:
                    component.dimension = found[0].columns_with_role(role)[0]
                resolved.append(f"{component.slot}: {found[0].id}")
        return resolved

    def validate(self, spec: DashboardSpec, identity: Identity) -> ValidationReport:
        """Everything that can be known to be wrong before the BI engine is called."""
        report = ValidationReport()
        add = lambda name, ok, subject="", detail="", **kw: report.add(  # noqa: E731
            "specification", name, "pass" if ok else "fail", subject, detail, **kw)
        try:
            template = self.registry.template(spec.dashboard.template)
        except Exception as exc:  # noqa: BLE001
            add("template exists", False, spec.dashboard.template, str(exc))
            return report
        add("template exists", True, f"{template.id}@{template.version}")

        slots = [c.slot for c in spec.components]
        duplicated = sorted({s for s in slots if slots.count(s) > 1})
        add("one component per slot", not duplicated, detail=", ".join(duplicated))
        missing = [s for s, body in template.slots.items() if body.mandatory and s not in slots]
        add("mandatory slots filled", not missing, detail=", ".join(missing))

        for component in spec.components:
            subject = f"{component.slot} ({component.type})"
            slot = template.slots.get(component.slot)
            if slot is None:
                add("slot exists in template", False, subject, f"{template.id} has no slot {component.slot}")
                continue
            visualization = self.registry.visualizations.get(component.type)
            if visualization is None or component.type not in slot.accepts:
                add("visualization allowed in slot", False, subject,
                    f"slot {component.slot} accepts {', '.join(slot.accepts)}", slot=component.slot)
                continue
            if visualization.status != "available":
                add("visualization installed", False, subject,
                    f"{component.type} is approved but its plugin is not installed in this BI engine",
                    slot=component.slot)
                continue
            add("visualization allowed in slot", True, subject)

            metric = self.registry.metrics.get(component.metric)
            if metric is None:
                add("metric is registered", False, subject, f"{component.metric} is not in the metric registry")
                continue
            report.add("specification", "metric is certified", "pass" if metric.certified else "warn", subject,
                       f"{metric.id} v{metric.version}" + ("" if metric.certified else " is not certified"))
            cleared = self.policy.cleared_for(identity, metric)
            add("requester is cleared for the metric", cleared, subject,
                f"{metric.id} is classified {metric.classification}", code=None if cleared else "unauthorized_metric")

            dataset = self.registry.datasets.get(component.dataset or "")
            if dataset is None:
                add("dataset is registered", False, subject,
                    f"{component.dataset} is not in the dataset registry" if component.dataset else
                    f"no dataset {identity.user} may read gives {metric.id}"
                    + (f" by {component.dimension}" if component.dimension else ""))
                continue
            if not self.policy.can_read(identity, dataset):
                add("requester may read the dataset", False, subject,
                    f"{identity.user} has no access to {dataset.id}", code="unauthorized_dataset")
                continue
            add("requester may read the dataset", True, subject, dataset.id)
            valid, why = self.semantic.valid_on(metric, dataset)
            add("metric is valid on the dataset", valid, subject, f"{metric.id} on {dataset.id}: {why}")

            needs = visualization.needs
            if needs.get("additive_metric"):
                add("metric's parts add up to its whole", metric.additive, subject,
                    f"{metric.id} is {metric.aggregation}" + ("" if metric.additive else
                                                              f": a {component.type} of it would show shares of nothing"))
            if component.type == "histogram":
                ok = bool(metric.distribution_column) and metric.distribution_column in dataset.columns
                add("distribution column exists", ok, subject, str(metric.distribution_column))
            elif needs.get("dimension") or needs.get("dimension_role"):
                column = component.dimension or (dataset.time_column if needs.get("dimension_role") == "time" else None)
                body = dataset.columns.get(column or "")
                if body is None:
                    add("dimension is a column of the dataset", False, subject,
                        f"{component.dimension} is not a column of {dataset.id}", slot=component.slot)
                    continue
                wanted = needs.get("dimension_role")
                role_ok = wanted is None or body.role == wanted
                add("dimension has the role the visualization needs", role_ok, subject,
                    f"{column} is {body.role}" + ("" if role_ok else f", {component.type} needs {wanted}"))
                if component.type == "map":
                    add("map dimension names its geography", body.geo is not None, subject,
                        f"{column}: " + (f"{body.geo.standard}, {body.geo.country}" if body.geo else "no geography"))
                add("dimension is not a restricted column", body.classification not in RESTRICTED_CLASSIFICATIONS,
                    subject, f"{column} is classified {body.classification}")
                # the registry says what a metric may be broken down by; a column a join added counts as the
                # key it is a function of
                through = body.determined_by or [column]
                allowed = set(through) <= set(metric.dimensions)
                add("metric may be broken down by the dimension", allowed, subject,
                    f"{metric.id} by {column}" + ("" if allowed else
                                                   f": its dimensions are {', '.join(metric.dimensions)}"))

        # ── sections ──────────────────────────────────────────────────────────────────────────────────────────
        known = {s.id for s in template.section_list()}
        declared = [s.id for s in spec.sections]
        add("one entry per section", len(set(declared)) == len(declared), detail=", ".join(declared))
        section_filters = 0
        for section in spec.sections:
            subject = f"section {section.id}"
            if section.id not in known:
                add("section exists in template", False, subject, f"{template.id} has {', '.join(sorted(known))}")
                continue
            inside = set(template.slots_in(section.id))
            if any(p.slot not in inside for p in section.parked):
                add("a parked component belongs to its section", False, subject)
            if section.visibility == "hidden":
                shown = [c.slot for c in spec.components if c.slot in inside]
                add("a hidden section is not on the dashboard", not shown, subject,
                    f"{', '.join(shown)} would still be drawn" if shown else f"{len(section.parked)} component(s) kept aside")
                must = [s for s in inside if template.slots[s].mandatory]
                add("a section holding a mandatory slot is not hidden", not must, subject, ", ".join(must))
                continue
            if section.parked:
                add("only a hidden section keeps components aside", False, subject)
            holders = [self.registry.datasets[c.dataset] for c in spec.components
                       if c.slot in inside and c.dataset in self.registry.datasets]
            for column in section.filters:
                section_filters += 1
                body = next((d.columns[column] for d in holders if column in d.columns), None)
                add("section filter is a column of the section's datasets", body is not None, f"{subject} · {column}")
                if body is not None:
                    add("filter role allowed by the template", body.role in template.filters.allowed_roles,
                        f"{subject} · {column}", f"{column} is {body.role}")
                    add("filter is not a restricted column", body.classification not in RESTRICTED_CLASSIFICATIONS,
                        f"{subject} · {column}", f"classified {body.classification}")
                add("a filter is set once", column not in spec.filters, f"{subject} · {column}",
                    "" if column not in spec.filters else "it is already a filter of the whole dashboard")

        used = [self.registry.datasets[ds] for ds in spec.datasets() if ds in self.registry.datasets]
        total_filters = len(spec.filters) + section_filters
        add("filter count within the template's limit", total_filters <= template.filters.max,
            detail=f"{total_filters} of {template.filters.max}")
        for column in spec.filters:
            holders = [d for d in used if column in d.columns]
            if not holders:
                add("filter is a column of the dashboard's datasets", False, column)
                continue
            body = holders[0].columns[column]
            add("filter is a column of the dashboard's datasets", True, column)
            add("filter role allowed by the template", body.role in template.filters.allowed_roles, column,
                f"{column} is {body.role}")
            add("filter is not a restricted column", body.classification not in RESTRICTED_CLASSIFICATIONS, column,
                f"classified {body.classification}")
            partial = [d.id for d in used if column not in d.columns]
            if partial:
                report.add("specification", "filter reaches every chart", "warn", column,
                           f"does not apply to charts on {', '.join(partial)}")

        tokens = self.registry.tokens
        theme = spec.theme
        add("theme uses approved tokens",
            (theme.color_scheme is None or theme.color_scheme in tokens.color_schemes)
            and (theme.sequential_scheme is None or theme.sequential_scheme in tokens.sequential_schemes)
            and (theme.variant is None or theme.variant in tokens.theme_variants),
            detail=f"{theme.color_scheme or template.theme.color_scheme} / "
                   f"{theme.sequential_scheme or template.theme.sequential_scheme}")
        return report

    # ── 3 · what the engine is given ────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _widths(row_slots: list[Any], placed: list[Any], collapse: bool) -> list[int]:
        widths = [p.width for p in placed]
        if not collapse or len(placed) == len(row_slots):
            return widths
        spare = sum(p.width for p in row_slots) - sum(widths)
        share, extra = divmod(spare, len(placed))
        return [w + share + (1 if i < extra else 0) for i, w in enumerate(widths)]

    def layout(self, spec: DashboardSpec, template: TemplateDef, charts: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """The engine's grid for this specification: rows and widths from the template, nothing else."""
        position: dict[str, Any] = {
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
            "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
            "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": spec.dashboard.title}},
        }
        # A section the specification names, and that has a title, is announced by a header above its first
        # row. A specification that names no section is rows only — as every dashboard was before sections.
        section_of_row = {name: section for section in self.sections(spec, template) for name in section["rows"]}
        announced: set[str] = set()
        for row in template.layout:
            placed = [p for p in row.slots if p.id in charts]
            if not placed:
                continue
            section = section_of_row.get(row.row)
            if section and section["declared"] and section["title"] and section["id"] not in announced:
                announced.add(section["id"])
                header_id = f"HEADER-{section['id']}"
                position["GRID_ID"]["children"].append(header_id)
                position[header_id] = {"type": "HEADER", "id": header_id, "children": [],
                                       "parents": ["ROOT_ID", "GRID_ID"],
                                       "meta": {"text": section["title"], "headerSize": "SMALL_HEADER",
                                                "background": "BACKGROUND_TRANSPARENT"}}
            row_id = f"ROW-{row.row}"
            widths = self._widths(row.slots, placed, template.responsive.collapse_empty_slots)
            position["GRID_ID"]["children"].append(row_id)
            position[row_id] = {"type": "ROW", "id": row_id, "children": [f"CHART-{p.id}" for p in placed],
                                "parents": ["ROOT_ID", "GRID_ID"], "meta": {"background": "BACKGROUND_TRANSPARENT"}}
            for placement, width in zip(placed, widths):
                chart = charts[placement.id]
                position[f"CHART-{placement.id}"] = {
                    "type": "CHART", "id": f"CHART-{placement.id}", "children": [],
                    "parents": ["ROOT_ID", "GRID_ID", row_id],
                    "meta": {"chartId": chart["id"], "width": width, "height": row.height,
                             "sliceName": chart["name"], "uuid": chart.get("uuid")},
                }
        return position

    @staticmethod
    def filter_id(spec: DashboardSpec, column: str, section: Optional[str] = None) -> str:
        return f"NATIVE_FILTER-{spec.dashboard.id}-" + (f"{section}-" if section else "") + column

    def section_titles(self, spec: DashboardSpec, template: TemplateDef, slots: Any) -> list[str]:
        """The headers the dashboard must show: the titled sections it names that have a chart on the page."""
        return [s["title"] for s in self.sections(spec, template)
                if s["declared"] and s["title"] and any(slot in slots for slot in s["slots"])]

    def filter_uses(self, spec: DashboardSpec) -> list[tuple[str, str, Optional[str]]]:
        """(filter id, column, section) for every filter: the dashboard's own, then each visible section's."""
        uses: list[tuple[str, str, Optional[str]]] = [(self.filter_id(spec, c), c, None) for c in spec.filters]
        for section in spec.sections:
            if section.visibility == "visible":
                uses += [(self.filter_id(spec, c, section.id), c, section.id) for c in section.filters]
        return uses

    def reach(self, spec: DashboardSpec, template: TemplateDef) -> dict[str, list[str]]:
        """For each slot, the filter columns that reach its chart: the dashboard's, and its own section's."""
        reached = {}
        for component in spec.components:
            own = spec.section(template.section_of(component.slot) or "")
            reached[component.slot] = list(dict.fromkeys([*spec.filters, *(
                own.filters if own is not None and own.visibility == "visible" else [])]))
        return reached

    def filter_state(self, spec: DashboardSpec, values: dict[str, Any]) -> dict[str, Any]:
        """The dashboard's filters, set to the given values, as the engine reads them from an address."""
        used = [self.registry.dataset(ds) for ds in spec.datasets()]
        mask: dict[str, Any] = {}
        for fid, column, _section in self.filter_uses(spec):
            holder = next((d for d in used if column in d.columns), None)
            if holder is None or column not in values:
                continue
            value = values[column]
            if holder.columns[column].role == "time":
                mask[fid] = {"id": fid, "extraFormData": {"time_range": value},
                             "filterState": {"value": value}, "ownState": {}}
            else:
                mask[fid] = {"id": fid, "extraFormData": {"filters": [{"col": column, "op": "IN", "val": [value]}]},
                             "filterState": {"value": [value]}, "ownState": {}}
        return mask

    def native_filters(self, spec: DashboardSpec, engine_dataset_ids: dict[str, int],
                       charts: dict[str, dict[str, Any]], template: Optional[TemplateDef] = None) -> list[dict[str, Any]]:
        """The engine's filters. One that belongs to a section reaches that section's charts and no others."""
        datasets = {c.slot: self.registry.dataset(c.dataset) for c in spec.components if c.slot in charts}
        titles = {s["id"]: s["title"] for s in self.sections(spec, template)} if template is not None else {}
        filters = []
        for fid, column, section in self.filter_uses(spec):
            inside = {slot: d for slot, d in datasets.items()
                      if section is None or (template is not None and slot in template.slots_in(section))}
            ordered = sorted({d.id: d for d in inside.values()}.values(), key=lambda d: (not d.certified, d.id))
            holder = next((d for d in ordered if column in d.columns), None)
            if holder is None:
                continue
            in_scope = [charts[slot]["id"] for slot, d in inside.items() if column in d.columns]
            excluded = [chart["id"] for chart in charts.values() if chart["id"] not in in_scope]
            is_time = holder.columns[column].role == "time"
            filters.append({
                "id": fid,
                "name": holder.label(column) + (f" · {titles.get(section) or section}" if section else ""),
                "type": "NATIVE_FILTER",
                "filterType": "filter_time" if is_time else "filter_select",
                "targets": [{}] if is_time else [{"datasetId": engine_dataset_ids[holder.id],
                                                  "column": {"name": column}}],
                "controlValues": ({} if is_time else
                                  {"enableEmptyFilter": False, "defaultToFirstItem": False, "multiSelect": True,
                                   "searchAllOptions": False, "inverseSelection": False}),
                "defaultDataMask": {"extraFormData": {}, "filterState": {}, "ownState": {}},
                "cascadeParentIds": [],
                "scope": {"rootPath": ["ROOT_ID"], "excluded": excluded},
                "chartsInScope": in_scope,
                "tabsInScope": [],
                "description": holder.columns[column].description,
            })
        return filters

    def css(self, template: TemplateDef) -> str:
        """The dashboard's stylesheet, from tokens. It applies to this dashboard only — the engine stores it
        on the dashboard — and every value in it is looked up, none is written by the requester or a model."""
        tokens = self.registry.tokens
        color = lambda use, default: tokens.colors[template.colors.get(use, default)]  # noqa: E731
        font = lambda use, default: tokens.typography[template.typography.get(use, default)]  # noqa: E731
        space = lambda use, default: tokens.spacing[template.spacing.get(use, default)]  # noqa: E731
        radius = tokens.radius.get("card", 8)
        title = font("chart_title", "heading_s")
        border = f"1px solid {tokens.colors['border']}" if template.components.get("chart_card") == "bordered" else "none"
        rules = [
            f"/* SharpSight template {template.id}@{template.version} — generated from design tokens */",
            f".dashboard-content {{ background: {color('background', 'surface')}; }}",
            f".dashboard-component-chart-holder {{ background: {color('card', 'surface')}; border: {border}; "
            f"border-radius: {radius}px; padding: {space('card_padding', 'sm')}px; }}",
            f".dashboard-component-chart-holder .header-title {{ font-size: {title['size_px']}px; "
            f"font-weight: {title['weight']}; color: {tokens.colors['text']}; }}",
            # a section's header: the template's own heading token, and nothing behind it
            f".dashboard-component-header {{ font-size: {font('section_title', 'heading_m')['size_px']}px; "
            f"font-weight: {font('section_title', 'heading_m')['weight']}; color: {tokens.colors['text']}; "
            "background: transparent; }",
        ]
        # Not a design choice — a correction. The engine sizes the title field from a hidden element in the
        # theme's « strong » weight and draws the title itself in the normal one; with this kit's theme the
        # two differ by a pixel and the last letters turn into an ellipsis (« Customer Experien… », 352 px
        # needed, 351 given). Inheriting the weight makes the drawn text the measured text.
        rules.append(".dashboard-header-container .dynamic-title-input { font-weight: inherit; }")
        if template.components.get("kpi_card") == "accent_top":
            rules.append(".dashboard-component-chart-holder:has([data-test-viz-type='big_number_total']) "
                         f"{{ border-top: 3px solid {color('accent', 'primary')}; }}")
        return "\n".join(rules) + "\n"

    def json_metadata(self, spec: DashboardSpec, template: TemplateDef, filters: list[dict[str, Any]]) -> dict[str, Any]:
        # NO dashboard-level colour scheme. Superset pushes one into every chart, and the country map reads
        # its presence as « colour by category »: the regions came out in six unrelated colours that said
        # nothing about the metric (first render, 2026-10-03). Each chart already carries the template's
        # scheme itself, so series colours stay the same across the dashboard.
        return {
            "native_filter_configuration": filters,
            "cross_filters_enabled": True,
            "filter_bar_orientation": template.filters.orientation.upper(),
            "refresh_frequency": 0,
            "timed_refresh_immune_slices": [],
            "expanded_slices": {},
            "label_colors": {},
        }
