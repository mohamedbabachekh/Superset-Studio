"""The Studio's copilot: a request in words, answered with a PROPOSAL — never with a change.

The brief (§47.6) asks for an assistant that returns « a structured proposal, not directly mutate production
state »: a summary, what it touches, the risks, and Preview / Apply / Reject. That is what this returns. The
Studio shows it; applying it saves the proposed specification as the draft, through the same route and the
same checks as a change made with the mouse; rejecting it does nothing at all.

WHAT READS THE REQUEST. No language model: none has been chosen for this kit, which calls nobody
(aibi/llm.py is the seam). What is here instead is the same deterministic reading the planner uses for a
whole dashboard — the governed vocabulary of governance/registry/lexicon.yaml, the registered metrics, the template's
own slots — narrowed to the handful of instructions a person gives an existing dashboard. It is narrower than
a model would be, and it never invents: what it does not recognise it says it did not recognise, and
proposes nothing. A model plugs in by producing the same `Proposal` from freer wording; everything after it
— the validation, the draft, the commit, the gates — is unchanged, which is the point of putting it here.

WHAT IT MAY PROPOSE is bounded by what a specification can say: a chart added to a free slot of the template
(a registered metric, a visualization the slot accepts, a dataset this person may read), a chart parked, a
title, a top N, a time grain, an order, a number format, and where things sit. It cannot write SQL, redefine
a metric, change a row-level rule or touch a dataset — those are not fields of a specification, which is why
the brief's own §51.3 rules about them are kept by construction rather than by a check.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from .dashboard_agent import DashboardAgent
from .layout import materialise as layout_as_drawn
from .layout import problems as layout_problems
from .layout import resolve as layout_in_force
from .layout import section_of_row
from .models import (ComponentOptions, ComponentSpec, DashboardSpec, LayoutRow, SectionSpec, Strict,
                     TemplateDef)
from .planner import match_vocabulary
from .policy_engine import Identity, PolicyEngine
from .registry import Registry

_ADD = re.compile(r"\b(add|put|include|insert|create)\b", re.I)
_REMOVE = re.compile(r"\b(remove|delete|drop|hide|take out|get rid of)\b", re.I)
_RENAME = re.compile(r"\b(?:rename|retitle|call|title)\b\s+(?P<what>.+?)\s+(?:to|as)\s+[«\"']?(?P<name>[^«»\"']+)",
                     re.I)
_TOP = re.compile(r"\b(?:top|first|only|limit(?:ed)?(?: to)?)\s+(\d{1,4})\b", re.I)
_COMPACT = re.compile(r"\b(compact|short|abbreviated)\b", re.I)
_FULL = re.compile(r"\b(full|exact|unabbreviated)\s+(?:numbers?|figures?|values?)\b", re.I)
_ASC = re.compile(r"\b(ascending|lowest first|smallest first|worst first)\b", re.I)
_DESC = re.compile(r"\b(descending|highest first|largest first|best first)\b", re.I)
_WIDER = re.compile(r"\b(wider|larger|bigger)\b", re.I)
_NARROWER = re.compile(r"\b(narrower|smaller)\b", re.I)
_TALLER = re.compile(r"\b(taller|higher)\b", re.I)
_SHORTER = re.compile(r"\b(shorter|flatter)\b", re.I)
_UP = re.compile(r"\b(up|higher up|earlier|first|above|to the top)\b", re.I)
_DOWN = re.compile(r"\b(down|lower|later|last|below|to the bottom)\b", re.I)
_MOVE = re.compile(r"\b(move|put|place|bring|send)\b", re.I)
_EXPLAIN = re.compile(r"\b(what is|what's|explain|describe|tell me about|why)\b", re.I)
_ALL_KPI = re.compile(r"\ball\b.{0,20}\b(kpis?|numbers?|cards?|tiles?)\b", re.I)


class Change(Strict):
    kind: Literal["add", "park", "title", "option", "layout"]
    slot: str
    says: str


class Proposal(Strict):
    request: str
    read_by: str
    understood: str
    summary: str
    changes: list[Change] = []
    # the specification as it WOULD be. Nothing is saved: the Studio sends this back as a draft if the
    # person applies it, and that save is checked like any other.
    spec: Optional[dict[str, Any]] = None
    touches: dict[str, list[str]] = {}
    validation: dict[str, Any] = {}
    problems: list[str] = []
    unresolved: list[str] = []
    answer: Optional[str] = None

    @property
    def proposes(self) -> bool:
        return bool(self.changes)


def diff(before: DashboardSpec, after: DashboardSpec, template: TemplateDef) -> list[str]:
    """What one specification changes about another, in the words of the brief's review panel (§21)."""
    said: list[str] = []
    was = {c.slot: c for c in before.components}
    now = {c.slot: c for c in after.components}
    for slot in [s for s in now if s not in was]:
        said.append(f"+ {now[slot].title or slot}: a {now[slot].type} of {now[slot].metric} on {slot}")
    for slot in [s for s in was if s not in now]:
        said.append(f"− {was[slot].title or slot} ({slot}) is no longer on the dashboard")
    for slot in [s for s in now if s in was]:
        old, new = was[slot], now[slot]
        for field in ("type", "metric", "dimension", "dataset", "title", "description"):
            if getattr(old, field) != getattr(new, field):
                said.append(f"~ {slot}: {field} {getattr(old, field) or '—'} → {getattr(new, field) or '—'}")
        for field in ComponentOptions.model_fields:
            if getattr(old.options, field) != getattr(new.options, field):
                said.append(f"~ {slot}: {field} {getattr(old.options, field) or '—'} → "
                            f"{getattr(new.options, field) or '—'}")
    if [r.model_dump() for r in layout_in_force(before, template)] != \
            [r.model_dump() for r in layout_in_force(after, template)]:
        said.append("~ the layout: " + ", ".join(
            f"{row.row} ({', '.join(f'{p.id} {p.width}/12' for p in row.slots)})"
            for row in layout_in_force(after, template)))
    if before.filters != after.filters:
        said.append(f"~ the filters: {', '.join(before.filters) or '—'} → {', '.join(after.filters) or '—'}")
    return said


class Copilot:
    """Reads a request against one project's specification. Holds nothing; every call is complete in itself."""

    name = "none (deterministic copilot)"

    def __init__(self, registry: Registry, policy: PolicyEngine, dashboards: DashboardAgent):
        self.registry = registry
        self.policy = policy
        self.dashboards = dashboards
        lexicon = registry.lexicon
        self._intents = [(phrase, intent) for intent, body in lexicon.get("intents", {}).items()
                         for phrase in body.get("phrases", [])]
        self._dimensions = [(phrase, column) for column, phrases in lexicon.get("dimensions", {}).items()
                            for phrase in phrases]
        self._grains = [(phrase, grain) for grain, phrases in lexicon.get("time_grains", {}).items()
                        for phrase in phrases]
        self._metrics = [(phrase, metric.id) for metric in registry.metrics.values()
                         for phrase in [metric.name, *metric.synonyms]]

    # ── what it can be asked ────────────────────────────────────────────────────────────────────────────────
    def examples(self, spec: DashboardSpec, template: TemplateDef) -> list[str]:
        """What this project can be asked for right now — the copilot's own suggestions (§47.6)."""
        free = self._free(spec, template)
        said = []
        if free:
            purpose = template.slots[free[0]].purpose
            metric = next(iter(self.registry.metrics.values()), None)
            said.append(f"add a {purpose} of {metric.name}" if metric else f"add a {purpose}")
        trend = next((c.slot for c in spec.components if template.slots[c.slot].purpose == "trend"), None)
        if trend:
            said.append("make the trend weekly")
        ranking = next((c.slot for c in spec.components if template.slots[c.slot].purpose == "ranking"), None)
        if ranking:
            said.append("show only the top 5 regions")
        said += ["make all the KPIs compact", "make the trend wider", "what is the QoE score?"]
        return said[:6]

    # ── the one entry point ─────────────────────────────────────────────────────────────────────────────────
    def propose(self, request: str, spec: DashboardSpec, template: TemplateDef, identity: Identity) -> Proposal:
        text = " ".join((request or "").split())
        after = spec.model_copy(deep=True)
        changes: list[Change] = []
        unresolved: list[str] = []
        understood: list[str] = []

        targets = self._targets(text, after, template)
        if _EXPLAIN.search(text) and not _ADD.search(text):
            return self._explain(text, after, template, targets)

        if _ADD.search(text):
            self._add(text, after, template, identity, changes, unresolved, understood)
        if _REMOVE.search(text) and targets:
            self._park(after, template, targets, changes, understood)
        renamed = _RENAME.search(text)
        if renamed:
            for slot in self._targets(renamed.group("what"), after, template) or targets:
                component = after.component(slot)
                if component:
                    component.title = renamed.group("name").strip()[:80]
                    changes.append(Change(kind="title", slot=slot, says=f"{slot} is called « {component.title} »"))
                    understood.append(f"rename {slot}")
                break
        self._options(text, after, template, targets, changes, understood)
        self._layout(text, after, template, targets, changes, understood, unresolved)

        if not changes and not unresolved:
            unresolved.append(
                f"« {text} » was not recognised. This copilot is deterministic — no language model has been "
                "chosen for this kit — so it understands a short list: add a view, remove one, rename one, "
                "a top N, a time grain, an order, compact or full numbers, wider, narrower, taller, shorter, "
                "up, down, and « what is … ».")
        return self._finish(text, after, spec, template, identity, changes, unresolved, understood)

    def compose(self, slot: str, metric_id: str, kind: Optional[str], dimension: Optional[str],
                time_grain: Optional[str], spec: DashboardSpec, template: TemplateDef,
                identity: Identity) -> Proposal:
        """The manual path (§48.3): the same proposal, from a form rather than from a sentence. « Do not
        force users to use AI »: every core operation has a predictable path that is not a prompt."""
        after = spec.model_copy(deep=True)
        changes: list[Change] = []
        unresolved: list[str] = []
        self._place(slot, metric_id, kind, dimension, time_grain, after, template, identity, changes,
                    unresolved)
        return self._finish(f"add a chart on {slot}", after, spec, template, identity, changes, unresolved,
                            [f"a {kind or 'chart'} of {metric_id} on {slot}"], read_by="the Studio's own form")

    def alter(self, slot: str, wanted: dict[str, Any], spec: DashboardSpec, template: TemplateDef,
              identity: Identity) -> Proposal:
        """A chart changed from the inspector's own controls — a dataset, a kind, a metric, a dimension.

        The same proposal the words and the gallery produce, for the same reason: changing what a chart
        measures is not a field edit. The metric must be one of the registry's and one this person is
        cleared for; the kind must be one the slot accepts and one the BI engine can actually draw; and the
        dataset is found again unless it was named, because a metric read by another breakdown may live
        somewhere else. What comes back is checked and never applied."""
        after = spec.model_copy(deep=True)
        changes: list[Change] = []
        unresolved: list[str] = []
        component = after.component(slot)
        if component is None:
            return Proposal(request=f"change {slot}", read_by="the Studio's own controls",
                            understood="nothing to change", summary="nothing is proposed",
                            unresolved=[f"{slot} holds no chart"])
        body = template.slots.get(slot)
        asked = {name: value for name, value in wanted.items() if value not in (None, "")}

        if "metric" in asked:
            metric = self.registry.metrics.get(asked["metric"])
            if metric is None:
                unresolved.append(f"{asked['metric']} is not a metric of the registry")
            elif not self.policy.may_use(identity, metric):
                unresolved.append(f"{identity.user} is not cleared for {metric.id} "
                                  f"(classified {metric.classification})")
            elif metric.id != component.metric:
                changes.append(Change(kind="option", slot=slot,
                                      says=f"{slot} measures {metric.name} instead of {component.metric}"))
                component.metric = metric.id
                component.dataset = None                    # found again below, for the new metric
        if "type" in asked and asked["type"] != component.type:
            kind = asked["type"]
            visualization = self.registry.visualizations.get(kind)
            if body is None or kind not in body.accepts:
                unresolved.append(f"slot {slot} accepts {', '.join(body.accepts) if body else 'nothing'}, "
                                  f"not a {kind}")
            elif visualization is None or visualization.status != "available":
                unresolved.append(f"{kind} is approved but not installed in this BI engine")
            else:
                changes.append(Change(kind="option", slot=slot, says=f"{slot} is drawn as a {kind}"))
                component.type = kind
                if kind in ("big_number", "histogram"):
                    component.dimension = None
        if "dimension" in asked and asked["dimension"] != component.dimension:
            changes.append(Change(kind="option", slot=slot,
                                  says=f"{slot} is broken down by {asked['dimension']}"))
            component.dimension = asked["dimension"]
            component.dataset = None
        elif wanted.get("dimension") == "" and component.dimension is not None:
            # the inspector's « — none — » is an empty string, and it means "by nothing", not "unchanged":
            # an absent field arrives as None from the Superset side (sharpsight/bi/api.py sends every key),
            # so only the empty string can say it
            changes.append(Change(kind="option", slot=slot,
                                  says=f"{slot} is no longer broken down by {component.dimension}"))
            component.dimension = None
            component.dataset = None
        if "dataset" in asked and asked["dataset"] != component.dataset:
            dataset = self.registry.datasets.get(asked["dataset"])
            if dataset is None or not self.policy.can_read(identity, dataset):
                unresolved.append(f"{identity.user} may not read {asked['dataset']}")
            else:
                changes.append(Change(kind="option", slot=slot, says=f"{slot} is read from {dataset.id}"))
                component.dataset = dataset.id
        if not changes and not unresolved:
            unresolved.append("nothing of that chart would change")
        return self._finish(f"change {slot}", after, spec, template, identity, changes, unresolved,
                            [f"{slot}: {', '.join(asked)}"], read_by="the Studio's own controls")

    # ── the instructions it knows ───────────────────────────────────────────────────────────────────────────
    def _free(self, spec: DashboardSpec, template: TemplateDef) -> list[str]:
        taken = {c.slot for c in spec.components}
        return [slot for row in template.layout for p in row.slots
                for slot in [p.id] if slot not in taken and slot in template.slots]

    def _targets(self, text: str, spec: DashboardSpec, template: TemplateDef) -> list[str]:
        """Which charts the request is about: a slot by name, a title, a purpose (« the trend »), a metric,
        or « all the KPIs »."""
        lowered = text.lower()
        if _ALL_KPI.search(text):
            return [c.slot for c in spec.components if template.slots[c.slot].purpose == "kpi"]
        found: list[str] = []
        for component in spec.components:
            slot = template.slots.get(component.slot)
            title = (component.title or "").lower()
            if (re.search(rf"\b{re.escape(component.slot.lower())}\b", lowered)
                    or (title and title in lowered)
                    or (slot and re.search(rf"\b{re.escape(slot.purpose)}\b", lowered))):
                found.append(component.slot)
        if found:
            return found
        named = [key for _, _, key in match_vocabulary(text, self._metrics)]
        return [c.slot for c in spec.components if c.metric in named]

    def _add(self, text: str, spec: DashboardSpec, template: TemplateDef, identity: Identity,
             changes: list[Change], unresolved: list[str], understood: list[str]) -> None:
        intents = match_vocabulary(text, self._intents)
        metrics = [key for _, _, key in match_vocabulary(text, self._metrics)]
        grains = [key for _, _, key in match_vocabulary(text, self._grains)]
        dimensions = [key for _, _, key in match_vocabulary(text, self._dimensions)]
        if not metrics:
            unresolved.append("no registered metric is named: a chart shows a metric of the registry, and the "
                              "request names none")
            return
        purpose = "kpi"
        prefer = None
        if intents:
            intent = intents[0][2]
            body = self.registry.lexicon.get("intents", {}).get(intent, {})
            purpose, prefer = body.get("purpose", intent), body.get("prefers")
        elif not re.search(r"\b(kpi|number|card|tile|headline)\b", text, re.I):
            unresolved.append("what kind of view was not said: « a trend », « a ranking », « a distribution », "
                              "« a map », « a table » — or « a KPI »")
            return
        free = [slot for slot in self._free(spec, template) if template.slots[slot].purpose == purpose]
        if not free:
            unresolved.append(f"template {template.id} has no free {purpose} slot: every one of them already "
                              "holds a chart. Remove one first, or ask for another kind of view")
            return
        dimension = next((d for d in dimensions if not (purpose == "trend" and d == "date")), None)
        self._place(free[0], metrics[0], None, dimension, grains[0] if grains else None, spec, template,
                    identity, changes, unresolved, prefer=prefer)
        understood.append(f"add a {purpose} of {metrics[0]}")

    def _place(self, slot: str, metric_id: str, kind: Optional[str], dimension: Optional[str],
               time_grain: Optional[str], spec: DashboardSpec, template: TemplateDef, identity: Identity,
               changes: list[Change], unresolved: list[str], prefer: Optional[str] = None) -> None:
        """Put one chart on one free slot — the registry choosing what it can be, not the caller."""
        if slot not in template.slots:
            unresolved.append(f"{slot} is not a slot of template {template.id}")
            return
        if spec.component(slot) is not None:
            unresolved.append(f"{slot} already holds a chart: park it first")
            return
        metric = self.registry.metrics.get(metric_id)
        if metric is None:
            unresolved.append(f"{metric_id} is not a metric of the registry")
            return
        if not self.policy.may_use(identity, metric):
            unresolved.append(f"{identity.user} is not cleared for {metric.id} "
                              f"(classified {metric.classification})")
            return
        body = template.slots[slot]
        draft = self.dashboards._draft_for(slot, body.purpose, template, metric, dimension, time_grain, "",
                                           prefer=kind or prefer)
        if draft is None:
            unresolved.append(f"no visualization {slot} accepts ({', '.join(body.accepts)}) can show "
                              f"{metric.name} that way")
            return
        if kind and draft.type != kind:
            unresolved.append(f"{slot} does not accept a {kind}, or {metric.name} cannot be drawn as one: "
                              f"it would be a {draft.type}")
            return
        spec.components.append(ComponentSpec(
            slot=slot, type=draft.type, metric=metric.id,
            dimension=None if draft.type in ("big_number", "histogram") else (draft.column or dimension),
            options=draft.options))
        # on the layout it is given the template's own place for that slot, so a canvas never has to guess
        if spec.layout:
            place = next((p for row in template.layout for p in row.slots if p.id == slot), None)
            name = _row_of(template, slot)
            row = next((r for r in spec.layout if r.row == name), None)
            if place and name and row is None:
                # a layout keeps only the rows that hold a chart (materialise, the Studio, a park), so the
                # slot's row may not be there at all: it is put back where the template has it
                row = _restore_row(spec, template, name)
            if place and row:
                row.slots.append(place.model_copy())
        changes.append(Change(kind="add", slot=slot,
                              says=f"a {draft.type} of {metric.name} on {slot}"
                                   + (f", by {draft.column}" if draft.column else "")))

    def _park(self, spec: DashboardSpec, template: TemplateDef, targets: list[str], changes: list[Change],
              understood: list[str]) -> None:
        for slot in targets:
            component = spec.component(slot)
            if component is None:
                continue
            if template.slots[slot].mandatory:
                changes.append(Change(kind="park", slot=slot,
                                      says=f"{slot} is mandatory in template {template.id} and stays"))
                continue
            section_id = template.section_of(slot)
            section = spec.section(section_id) if section_id else None
            if section is None and section_id:
                section = SectionSpec(id=section_id)
                spec.sections.append(section)
            spec.components = [c for c in spec.components if c.slot != slot]
            if section is not None:
                section.parked.append(component)
            for row in spec.layout:
                row.slots = [p for p in row.slots if p.id != slot]
            spec.layout = [row for row in spec.layout if row.slots]
            changes.append(Change(kind="park", slot=slot, says=f"{slot} is taken off the dashboard and kept "
                                                               "in its section, parked"))
            understood.append(f"remove {slot}")

    def _options(self, text: str, spec: DashboardSpec, template: TemplateDef, targets: list[str],
                 changes: list[Change], understood: list[str]) -> None:
        grains = [key for _, _, key in match_vocabulary(text, self._grains)]
        top = _TOP.search(text)
        for slot in targets:
            component = spec.component(slot)
            if component is None:
                continue
            options = component.options
            if grains and component.type in ("line", "area"):
                options.time_grain = grains[0]
                changes.append(Change(kind="option", slot=slot, says=f"{slot} is read by {grains[0]}"))
                understood.append(f"{slot}: time grain {grains[0]}")
            if top and component.type in ("bar", "table", "pie"):
                options.row_limit = int(top.group(1))
                changes.append(Change(kind="option", slot=slot, says=f"{slot} shows {top.group(1)} rows"))
                understood.append(f"{slot}: top {top.group(1)}")
            if _COMPACT.search(text):
                options.number_format = "compact"
                changes.append(Change(kind="option", slot=slot, says=f"{slot} shows numbers compact (1.2M)"))
            elif _FULL.search(text):
                options.number_format = "default"
                changes.append(Change(kind="option", slot=slot, says=f"{slot} shows numbers in full"))
            if _ASC.search(text) and component.type in ("bar", "table"):
                options.sort = "asc"
                changes.append(Change(kind="option", slot=slot, says=f"{slot} is ordered ascending"))
            elif _DESC.search(text) and component.type in ("bar", "table"):
                options.sort = "desc"
                changes.append(Change(kind="option", slot=slot, says=f"{slot} is ordered descending"))

    def _layout(self, text: str, spec: DashboardSpec, template: TemplateDef, targets: list[str],
                changes: list[Change], understood: list[str], unresolved: list[str]) -> None:
        if not targets:
            return
        rows = layout_as_drawn(spec, template)
        touched = False
        for slot in targets:
            row = next((r for r in rows if any(p.id == slot for p in r.slots)), None)
            if row is None:
                continue
            place = next(p for p in row.slots if p.id == slot)
            other = next((p for p in row.slots if p.id != slot), None)
            spare = template.grid.columns - sum(p.width for p in row.slots)
            least = template.responsive.min_slot_width
            floor = lambda s: least.get(template.slots[s].purpose, least.get("chart", 1))  # noqa: E731
            if _WIDER.search(text):
                if other is not None and other.width - 1 >= floor(other.id):
                    place.width, other.width, touched = place.width + 1, other.width - 1, True
                elif other is None and spare >= 1:
                    place.width, touched = place.width + 1, True
                else:
                    unresolved.append(
                        f"{slot} cannot be wider: the row is full and {other.id if other else 'nothing'} is "
                        f"already at the {template.id} template's smallest width for it"
                        if other is not None else f"{slot} cannot be wider: the row is full")
                    continue
                changes.append(Change(kind="layout", slot=slot, says=f"{slot} is {place.width}/12 wide"))
            elif _NARROWER.search(text):
                if place.width - 1 < floor(slot):
                    unresolved.append(f"{slot} cannot be narrower: {place.width}/12 is already the smallest "
                                      f"width {template.id} allows a {template.slots[slot].purpose}")
                    continue
                place.width, touched = place.width - 1, True
                if other is not None:
                    other.width += 1
                changes.append(Change(kind="layout", slot=slot, says=f"{slot} is {place.width}/12 wide"))
            if _TALLER.search(text) or _SHORTER.search(text):
                row.height = max(6, min(120, row.height + (4 if _TALLER.search(text) else -4)))
                touched = True
                changes.append(Change(kind="layout", slot=slot, says=f"row {row.row} is {row.height} units high"))
            if _MOVE.search(text) and (_UP.search(text) or _DOWN.search(text)):
                at = rows.index(row)
                to = max(0, at - 1) if _UP.search(text) else min(len(rows) - 1, at + 1)
                if to != at:
                    rows[at], rows[to] = rows[to], rows[at]
                    touched = True
                    changes.append(Change(kind="layout", slot=slot,
                                          says=f"row {row.row} moves {'up' if to < at else 'down'}"))
        if touched:
            spec.layout = rows
            understood.append("the layout")

    def _explain(self, text: str, spec: DashboardSpec, template: TemplateDef,
                 targets: list[str]) -> Proposal:
        """« What is … » — an answer from the registry, and no change. The brief's « explain changes » and
        « explain why this chart is empty »: the first is answerable from what is governed, the second needs
        the data, which this copilot does not read."""
        named = [key for _, _, key in match_vocabulary(text, self._metrics)]
        metric = self.registry.metrics.get(named[0]) if named else None
        if metric is None and targets:
            component = spec.component(targets[0])
            metric = self.registry.metrics.get(component.metric) if component else None
        if metric is None:
            return Proposal(request=text, read_by=self.name, understood="a question",
                            summary="nothing of the registry was named in that question",
                            unresolved=["name a metric, or select the chart the question is about"])
        dataset = next((self.registry.datasets.get(c.dataset or "") for c in spec.components
                        if c.metric == metric.id), None)
        answer = (f"{metric.name} ({metric.id}), version {metric.version}, owned by {metric.owner}, "
                  f"{'certified' if metric.certified else 'NOT certified'}. "
                  f"{' '.join(metric.documentation.split()) or 'No documentation in the registry.'} "
                  f"It is {metric.aggregation} of {metric.expression}, "
                  f"{'additive' if metric.additive else 'not additive — its parts do not add up to its whole, '
                     'so it is never drawn as a share'}. "
                  + (f"On this dashboard it is read from {dataset.id}, grain {', '.join(dataset.grain)}, "
                     f"owned by {dataset.owner}." if dataset else ""))
        return Proposal(request=text, read_by=self.name, understood=f"a question about {metric.id}",
                        summary="an answer, and no change", answer=answer)

    def _finish(self, text: str, after: DashboardSpec, before: DashboardSpec, template: TemplateDef,
                identity: Identity, changes: list[Change], unresolved: list[str], understood: list[str],
                read_by: Optional[str] = None) -> Proposal:
        if not changes:
            return Proposal(request=text, read_by=read_by or self.name,
                            understood=", ".join(understood) or "nothing this copilot knows",
                            summary="nothing is proposed", unresolved=unresolved)
        self.dashboards.resolve(after, identity)
        report = self.dashboards.validate(after, identity)
        return Proposal(
            request=text, read_by=read_by or self.name,
            understood=", ".join(understood) or "a change to this dashboard",
            summary="; ".join(line for line in diff(before, after, template)) or "no visible change",
            changes=changes, spec=after.model_dump(exclude_none=True),
            touches={"metrics": after.metrics(), "datasets": after.datasets(),
                     "slots": [c.slot for c in changes]},
            validation={**report.summary(),
                        "failures": [f"{c.name}: {c.detail}" for c in report.failures],
                        "warnings": [f"{c.name}: {c.detail}" for c in report.warnings]},
            problems=layout_problems(after, template), unresolved=unresolved)


def _row_of(template: TemplateDef, slot: str) -> Optional[str]:
    return next((row.row for row in template.layout for p in row.slots if p.id == slot), None)


def _restore_row(spec: DashboardSpec, template: TemplateDef, name: str) -> LayoutRow:
    """Put a template row the layout dropped back on it, empty, and return it.

    It goes next to the nearest row the template puts before it (or, failing that, after it), looked for
    first among the rows of its own section: a section is announced by one header above its first row, so
    its rows stay together. A row of a section with no row left goes beside another section's rows, never
    between two of them."""
    order = {row.row: at for at, row in enumerate(template.layout)}
    declared = next(row for row in template.layout if row.row == name)
    section = section_of_row(template, name)
    same = [at for at, row in enumerate(spec.layout) if section and section_of_row(template, row.row) == section]
    near = same or [at for at, row in enumerate(spec.layout) if row.row in order]
    before = [at for at in near if order[spec.layout[at].row] < order[name]]
    after = [at for at in near if order[spec.layout[at].row] > order[name]]
    if before:
        at = max(before, key=lambda i: order[spec.layout[i].row]) + 1
        if not same:
            # past the end of that row's section, so the new row does not cut it in two
            held = section_of_row(template, spec.layout[at - 1].row)
            while held and at < len(spec.layout) and section_of_row(template, spec.layout[at].row) == held:
                at += 1
    elif after:
        at = min(after, key=lambda i: order[spec.layout[i].row])
        if not same:
            held = section_of_row(template, spec.layout[at].row)
            while held and at > 0 and section_of_row(template, spec.layout[at - 1].row) == held:
                at -= 1
    else:
        at = len(spec.layout)
    row = LayoutRow(row=name, height=declared.height, slots=[])
    spec.layout.insert(at, row)
    return row
