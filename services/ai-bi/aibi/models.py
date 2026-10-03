"""The shapes everything else passes around.

Almost every model here FORBIDS unknown keys. That is the point of several of them: a dashboard specification
with a key nobody defined is not a richer specification, it is arbitrary state trying to get through.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

Classification = Literal["public", "internal", "confidential", "sensitive", "pii"]
# Never sampled, never shown to a model, and refused in SQL unless a role is granted the classification.
RESTRICTED_CLASSIFICATIONS = ("sensitive", "pii")


def tidy(name: str) -> str:
    """A column's name as a person reads it: `rat` is RAT, `site_id` is Site id."""
    words = name.replace("_", " ")
    return words.upper() if len(words) <= 3 else words.capitalize()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── dataset registry ────────────────────────────────────────────────────────────────────────────────────────
class GeoRef(Strict):
    standard: str
    country: str


class ColumnDef(Strict):
    type: str
    # No default, on purpose: an unclassified column fails the load.
    classification: Classification
    role: Optional[str] = None
    description: str = ""
    range: Optional[list[Optional[float]]] = None
    accepted_values: Optional[list[str]] = None
    geo: Optional[GeoRef] = None
    # what the column is called where people read it (a table header, a filter); default: its name, tidied
    label: Optional[str] = None
    # for a column a join added: the key(s) it is a function of. `region_iso` is determined by `region`, so
    # whatever may be broken down by region may be broken down by its code.
    determined_by: Optional[list[str]] = None


class DatasetDef(Strict):
    id: str
    name: str
    owner: str
    certified: bool = False
    kind: Literal["fact", "dimension"] = "fact"
    source_type: str
    source_model: str
    freshness_sla_minutes: Optional[int] = None
    domain: str = ""
    tenant: str = "default"
    version: str = "0.1"
    description: str = ""
    grain: list[str] = Field(default_factory=list)
    time_column: Optional[str] = None
    freshness_column: Optional[str] = None
    join_keys: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    columns: dict[str, ColumnDef]
    # set on what the engine generated; a certified dataset has none of these
    generated_by: Optional[str] = None
    project: Optional[str] = None
    derived_from: list[str] = Field(default_factory=list)
    environment: Literal["prod", "dev"] = "prod"
    # where it physically is — read from the dbt manifest, never typed by hand
    db_schema: Optional[str] = None

    def label(self, column: str) -> str:
        body = self.columns.get(column)
        return (body.label if body is not None and body.label else None) or tidy(column)

    def columns_with_role(self, role: str) -> list[str]:
        return [name for name, col in self.columns.items() if col.role == role]

    def restricted_columns(self) -> list[str]:
        return [name for name, col in self.columns.items() if col.classification in RESTRICTED_CLASSIFICATIONS]

    @property
    def relation(self) -> str:
        return f"{self.db_schema}.{self.source_model}" if self.db_schema else self.source_model


# ── metric registry ─────────────────────────────────────────────────────────────────────────────────────────
class MetricLineage(Strict):
    datasets: list[str]
    columns: list[str]
    dbt_models: list[str] = Field(default_factory=list)


class MetricDef(Strict):
    id: str
    name: str
    short_name: Optional[str] = None
    expression: str
    aggregation: str
    dimensions: list[str] = Field(default_factory=list)
    format: str = "#,##0"
    unit: str = ""
    certified: bool = False
    owner: str
    version: str
    documentation: str = ""
    synonyms: list[str] = Field(default_factory=list)
    valid_range: Optional[list[Optional[float]]] = None
    distribution_column: Optional[str] = None
    # Do its values for the parts add up to its value for the whole? Users per region do; a score or a rate
    # per region does not, and a pie of sixteen rates shows each as a « share » of a sum that means nothing.
    # False unless said otherwise: the safe side is to refuse the pie.
    additive: bool = False
    # No default, on purpose, as for a column: a metric nobody classified fails the load. A metric may be
    # LESS sensitive than the columns it is computed from (a count of customers is not a customer), which
    # is why it is stated here and not derived.
    classification: Classification
    lineage: MetricLineage

    @property
    def key(self) -> str:
        """The name the metric is saved under in the BI engine: one word, stable, traceable to the registry."""
        return self.id.replace(".", "__")


# ── templates ───────────────────────────────────────────────────────────────────────────────────────────────
class TemplateHeader(Strict):
    id: str
    name: str
    version: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    title_pattern: str = "{subject}"


class SlotPlacement(Strict):
    id: str
    width: int = Field(ge=1, le=12)


class LayoutRow(Strict):
    row: str
    height: int = Field(ge=6, le=120)
    slots: list[SlotPlacement]


class SlotDef(Strict):
    purpose: str
    accepts: list[str]
    mandatory: bool = False


class TemplateSection(Strict):
    """What some of a template's rows are FOR. A section owns whole rows; a dashboard may rename it, describe
    it, give it an owner, scope a filter to it or hide it — it cannot move a row from one to another."""
    id: str
    title: str = ""
    description: str = ""
    rows: list[str]


class TemplateFilters(Strict):
    max: int = 5
    allowed_roles: list[str] = Field(default_factory=lambda: ["time", "dimension"])
    orientation: Literal["horizontal", "vertical"] = "horizontal"


class TemplateTheme(Strict):
    color_scheme: str
    sequential_scheme: str
    variant: str = "light"


class Viewport(Strict):
    name: str
    width: int


class Responsive(Strict):
    viewports: list[Viewport]
    collapse_empty_slots: bool = True
    min_slot_width: dict[str, int] = Field(default_factory=dict)


class Grid(Strict):
    columns: int = 12
    row_unit_px: int = 8


class TemplateDef(Strict):
    template: TemplateHeader
    grid: Grid = Field(default_factory=Grid)
    layout: list[LayoutRow]
    slots: dict[str, SlotDef]
    filters: TemplateFilters = Field(default_factory=TemplateFilters)
    theme: TemplateTheme
    typography: dict[str, str] = Field(default_factory=dict)
    spacing: dict[str, str] = Field(default_factory=dict)
    colors: dict[str, str] = Field(default_factory=dict)
    components: dict[str, str] = Field(default_factory=dict)
    kpi_formatting: dict[str, Any] = Field(default_factory=dict)
    responsive: Responsive
    sections: list[TemplateSection] = Field(default_factory=list)

    @property
    def id(self) -> str:
        return self.template.id

    def section_list(self) -> list[TemplateSection]:
        """The template's sections, in reading order. One that names none has a section per row, untitled."""
        return self.sections or [TemplateSection(id=row.row, rows=[row.row]) for row in self.layout]

    def slots_in(self, section_id: str) -> list[str]:
        rows = next((s.rows for s in self.section_list() if s.id == section_id), [])
        return [p.id for row in self.layout if row.row in rows for p in row.slots]

    def section_of(self, slot: str) -> Optional[str]:
        return next((s.id for s in self.section_list() if slot in self.slots_in(s.id)), None)

    @property
    def version(self) -> str:
        return self.template.version

    def slots_for(self, purpose: str) -> list[str]:
        """Slot ids of one purpose, in reading order (the layout's, not the dictionary's)."""
        ordered = [p.id for row in self.layout for p in row.slots]
        return [s for s in ordered if self.slots[s].purpose == purpose]


class DesignTokens(Strict):
    colors: dict[str, str]
    color_schemes: list[str]
    sequential_schemes: list[str]
    typography: dict[str, dict[str, int]]
    spacing: dict[str, int]
    radius: dict[str, int] = Field(default_factory=dict)
    component_variants: dict[str, list[str]]
    theme_variants: list[str]


class Visualization(Strict):
    engine_type: str
    status: Literal["available", "not_installed"] = "available"
    kind: str = "builtin"
    needs: dict[str, Any] = Field(default_factory=dict)


class ChartTemplate(Strict):
    """A way to start a chart that is not « from scratch »: Time series, Top N, KPI… Each names an approved
    visualization and what the developer still has to choose. One nobody can draw yet says so, and why."""
    name: str
    description: str = ""
    visualization: Optional[str] = None
    alternatives: list[str] = Field(default_factory=list)
    purpose: Optional[str] = None
    needs: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    status: Literal["available", "not_available"] = "available"
    reason: str = ""


class Domain(Strict):
    """A logical folder of the asset library — what the business calls a subject, not a database schema."""
    name: str
    description: str = ""
    owner: str = ""
    datasets: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)


# ── security ────────────────────────────────────────────────────────────────────────────────────────────────
class RoleDef(Strict):
    capabilities: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    classifications: list[str] = Field(default_factory=list)


class UserDef(Strict):
    type: Literal["human", "ai", "service"] = "human"
    tenant: str = "default"
    roles: list[str] = Field(default_factory=list)


class Rbac(Strict):
    tenants: list[str]
    roles: dict[str, RoleDef]
    users: dict[str, UserDef]
    # role -> dataset id -> SQL predicate over that dataset's columns
    row_filters: dict[str, dict[str, str]] = Field(default_factory=dict)


# ── the request, understood ─────────────────────────────────────────────────────────────────────────────────
class ViewIntent(Strict):
    intent: str
    phrase: str
    metric: Optional[str] = None
    dimension: Optional[str] = None
    time_grain: Optional[str] = None
    # an approved visualization the view would rather be — a wish, granted only if the slot accepts it and
    # the metric suits it
    prefer: Optional[str] = None


class Requirement(Strict):
    """What a request asks for, in the registry's own terms. A model fills the same structure; nothing
    downstream knows which of the two wrote it."""
    prompt: str
    subject: str
    template: Optional[str] = None
    metrics: list[str] = Field(default_factory=list)
    views: list[ViewIntent] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    # what was understood, but not word for word: « the CEM Executive template » read as `executive`
    assumptions: list[str] = Field(default_factory=list)
    parser: str = "deterministic"


# ── the dashboard specification ─────────────────────────────────────────────────────────────────────────────
class ComponentOptions(Strict):
    """The only knobs a component has. Each is bounded; none is free text that reaches the BI engine."""
    row_limit: Optional[int] = Field(default=None, ge=1, le=1000)
    number_format: Optional[Literal["default", "compact"]] = None
    time_grain: Optional[Literal["P1D", "P1W", "P1M"]] = None
    bins: Optional[int] = Field(default=None, ge=4, le=40)
    sort: Optional[Literal["desc", "asc"]] = None


class ComponentSpec(Strict):
    slot: str
    type: str
    metric: str
    dimension: Optional[str] = None
    dataset: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=80)
    options: ComponentOptions = Field(default_factory=ComponentOptions)
    # what the chart is for, in a sentence; without one it is described by its metric's documentation
    description: Optional[str] = Field(default=None, max_length=300)
    # A SHARED chart of the asset library, by its id. The component is then that chart: its type, metric,
    # dimension and options are the library's, written here when the specification is saved so that a
    # version records exactly what was built.
    asset: Optional[str] = None


class SectionSpec(Strict):
    """What a dashboard says of one of its template's sections."""
    id: str
    title: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = Field(default=None, max_length=300)
    owner: Optional[str] = Field(default=None, max_length=80)
    # hidden: kept in the specification, absent from the dashboard. Its components wait in `parked`.
    visibility: Literal["visible", "hidden"] = "visible"
    # filters that reach this section's charts and no others
    filters: list[str] = Field(default_factory=list)
    # a SHARED section of the asset library this one was filled from
    asset: Optional[str] = None
    parked: list[ComponentSpec] = Field(default_factory=list)


class DashboardHeader(Strict):
    id: str
    template: str
    title: str = Field(max_length=120)
    description: str = Field(default="", max_length=500)
    owner: Optional[str] = Field(default=None, max_length=80)
    # The version of the BI project this specification is. The engine writes the one it is about to
    # produce; a specification that arrives with its own (« 1.0 ») is accepted, and renumbered when stored.
    version: Optional[str] = Field(default=None, pattern=r"^\d+(\.\d+){0,2}$")

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", value):
            raise ValueError("a dashboard id is lower-case words joined by hyphens")
        return value


class ThemeChoice(Strict):
    color_scheme: Optional[str] = None
    sequential_scheme: Optional[str] = None
    variant: Optional[str] = None


class DashboardSpec(Strict):
    dashboard: DashboardHeader
    filters: list[str] = Field(default_factory=list)
    components: list[ComponentSpec]
    theme: ThemeChoice = Field(default_factory=ThemeChoice)
    # A dashboard is a composition of sections. A specification that names none is laid out as rows only,
    # as every specification was before sections existed — so an older version restores as it was.
    sections: list[SectionSpec] = Field(default_factory=list)

    def component(self, slot: str) -> Optional[ComponentSpec]:
        return next((c for c in self.components if c.slot == slot), None)

    def section(self, section_id: str) -> Optional[SectionSpec]:
        return next((s for s in self.sections if s.id == section_id), None)

    def datasets(self) -> list[str]:
        seen: list[str] = []
        for c in self.components:
            if c.dataset and c.dataset not in seen:
                seen.append(c.dataset)
        return seen

    def metrics(self) -> list[str]:
        seen: list[str] = []
        for c in self.components:
            if c.metric not in seen:
                seen.append(c.metric)
        return seen


# ── the asset library's own assets ──────────────────────────────────────────────────────────────────────────
def _slug(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", value):
        raise ValueError("an asset id is lower-case words joined by hyphens")
    return value


class ChartAsset(Strict):
    """A chart defined ONCE and referenced by the dashboards that use it. It is a definition in SharpSight's
    vocabulary — an approved visualization of a governed metric — never engine state."""
    id: str
    name: str = Field(max_length=80)
    description: str = Field(default="", max_length=300)
    owner: str = Field(default="", max_length=80)
    type: str
    metric: str
    dimension: Optional[str] = None
    dataset: Optional[str] = None
    options: ComponentOptions = Field(default_factory=ComponentOptions)
    template: Optional[str] = None           # the chart template it was started from
    certified: bool = False
    certified_by: Optional[str] = None
    revision: int = 1
    created_by: str = ""
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)

    _check_id = field_validator("id")(_slug)


class SectionAsset(Strict):
    """A section defined once — a title, what it is for, and the shared charts it is made of, in order."""
    id: str
    name: str = Field(max_length=80)
    description: str = Field(default="", max_length=300)
    owner: str = Field(default="", max_length=80)
    charts: list[str]
    filters: list[str] = Field(default_factory=list)
    certified: bool = False
    certified_by: Optional[str] = None
    revision: int = 1
    created_by: str = ""
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)

    _check_id = field_validator("id")(_slug)


# ── a transformation the AI proposes ────────────────────────────────────────────────────────────────────────
class JoinPlan(Strict):
    dataset: str
    model: str
    on: list[str]
    add_columns: list[str]


class ModelPlan(Strict):
    """A generated dbt model, as a plan. The SQL is rendered from this by a fixed template; a language model
    proposing a transformation proposes one of these, never a file."""
    name: str
    dataset_id: str
    description: str
    base_dataset: str
    base_model: str
    columns: list[str]
    joins: list[JoinPlan]
    grain: list[str]
    reason: str


# ── validation ──────────────────────────────────────────────────────────────────────────────────────────────
Category = Literal["data", "sql", "bi", "security", "performance", "visual", "specification"]
Status = Literal["pass", "warn", "fail", "skipped"]


class Check(Strict):
    category: Category
    name: str
    status: Status
    subject: str = ""
    detail: str = ""
    # machine-readable, for the fix loop: what kind of problem, and on which slot
    code: Optional[str] = None
    slot: Optional[str] = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(Strict):
    checks: list[Check] = Field(default_factory=list)
    generated_at: str = Field(default_factory=utcnow)

    def add(self, category: Category, name: str, status: Status, subject: str = "", detail: str = "",
            **extra: Any) -> Check:
        check = Check(category=category, name=name, status=status, subject=subject, detail=detail,
                      code=extra.pop("code", None), slot=extra.pop("slot", None), evidence=extra)
        self.checks.append(check)
        return check

    def extend(self, other: "ValidationReport") -> None:
        self.checks.extend(other.checks)

    def replace_category(self, category: str, other: "ValidationReport") -> None:
        self.checks = [c for c in self.checks if c.category != category] + \
                      [c for c in other.checks if c.category == category]

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.status == "warn"]

    @property
    def passed(self) -> bool:
        return not self.failures

    def summary(self) -> dict[str, Any]:
        by_category: dict[str, dict[str, int]] = {}
        for c in self.checks:
            by_category.setdefault(c.category, {"pass": 0, "warn": 0, "fail": 0, "skipped": 0})[c.status] += 1
        return {"passed": self.passed, "checks": len(self.checks), "failures": len(self.failures),
                "warnings": len(self.warnings), "by_category": by_category}


# ── runs, versions, approvals ───────────────────────────────────────────────────────────────────────────────
class StepRecord(Strict):
    number: int
    key: str
    title: str
    status: Literal["pending", "running", "done", "skipped", "failed"] = "pending"
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    summary: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class RunRecord(Strict):
    id: str
    request_id: str
    project_id: Optional[str] = None
    user: str
    prompt: str
    ai_model: str
    status: Literal["running", "succeeded", "failed"] = "running"
    steps: list[StepRecord] = Field(default_factory=list)
    started_at: str = Field(default_factory=utcnow)
    ended_at: Optional[str] = None
    version: Optional[str] = None
    error: Optional[str] = None
    # the quality gate the run left its project at (section 31): READY_FOR_REVIEW, VALIDATION_FAILED, …
    quality_gate: Optional[str] = None


class Approval(Strict):
    target: Literal["UAT", "PROD"]
    decision: Literal["approved", "rejected"]
    by: str
    at: str = Field(default_factory=utcnow)
    comment: str = ""


# The quality gates, in the brief's own words (section 31). An AI-generated project does not become deployable
# because the AI says it is complete: it is in exactly one of these, and only a gate moves it to the next.
Gate = Literal["DRAFT", "AI_GENERATED", "VALIDATING", "VALIDATION_FAILED", "READY_FOR_REVIEW",
               "APPROVED_FOR_UAT", "UAT", "UAT_FAILED", "APPROVED_FOR_PROD", "PROD"]

# where a version stands when it is in each
ENVIRONMENT = {"DRAFT": "DEV", "AI_GENERATED": "DEV", "VALIDATING": "DEV", "VALIDATION_FAILED": "DEV",
               "READY_FOR_REVIEW": "DEV", "APPROVED_FOR_UAT": "DEV", "UAT": "UAT", "UAT_FAILED": "UAT",
               "APPROVED_FOR_PROD": "UAT", "PROD": "PROD"}

# Handed on: a version in one of these is neither regenerated in place nor discarded.
HELD = ("APPROVED_FOR_UAT", "UAT", "APPROVED_FOR_PROD", "PROD")


class StatusChange(Strict):
    status: Gate
    at: str = Field(default_factory=utcnow)
    by: str
    note: str = ""


class Attempt(Strict):
    """One generation's way through the gates, kept on the PROJECT. A version is only written by a run whose
    validation passed — so this is where a run that did not get that far leaves its record: how far it
    went, and what stopped it."""
    run_id: str
    request_id: str
    by: str
    status: Gate = "DRAFT"
    history: list[StatusChange] = Field(default_factory=list)
    version: Optional[str] = None
    error: Optional[str] = None

    def move(self, status: str, by: str, note: str = "") -> None:
        self.status = status  # type: ignore[assignment]
        self.history.append(StatusChange(status=status, by=by, note=note))  # type: ignore[arg-type]


class ProjectVersion(Strict):
    project_id: str
    version: str
    status: Gate = "DRAFT"
    # every gate it went through, in order: who moved it, when, and why
    history: list[StatusChange] = Field(default_factory=list)
    git_commit: Optional[str] = None
    dbt_commit: Optional[str] = None
    superset_asset_version: Optional[str] = None
    dataset_versions: dict[str, str] = Field(default_factory=dict)
    metric_versions: dict[str, str] = Field(default_factory=dict)
    template_version: Optional[str] = None
    created_by: str
    created_at: str = Field(default_factory=utcnow)
    request_id: str
    title: str = ""
    dashboard: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    package: dict[str, Any] = Field(default_factory=dict)
    approvals: list[Approval] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _read_older_records(cls, data: Any) -> Any:
        """A record written before the ten states had two fields, `state` and `review_status`. It is read
        as what it meant; and `state`, which is now derived, is never taken from a stored record."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        state, review = data.pop("state", None), data.pop("review_status", None)
        if "status" not in data and state is not None:
            data["status"] = {("DEV", "READY_FOR_REVIEW"): "READY_FOR_REVIEW", ("UAT", "READY_FOR_REVIEW"): "UAT",
                              ("PROD", "APPROVED"): "PROD", ("UAT", "REJECTED"): "UAT_FAILED"}.get((state, review), "DRAFT")
        return data

    @computed_field
    @property
    def state(self) -> str:
        """The environment the version stands in: DEV, UAT or PROD."""
        return ENVIRONMENT[self.status]

    def move(self, status: str, by: str, note: str = "") -> None:
        self.status = status  # type: ignore[assignment]
        self.history.append(StatusChange(status=status, by=by, note=note))  # type: ignore[arg-type]
