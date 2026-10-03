"""The shapes everything else passes around.

Almost every model here FORBIDS unknown keys. That is the point of several of them: a dashboard specification
with a key nobody defined is not a richer specification, it is arbitrary state trying to get through.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    @property
    def id(self) -> str:
        return self.template.id

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


class DashboardHeader(Strict):
    id: str
    template: str
    title: str = Field(max_length=120)
    description: str = Field(default="", max_length=500)

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

    def component(self, slot: str) -> Optional[ComponentSpec]:
        return next((c for c in self.components if c.slot == slot), None)

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


class Approval(Strict):
    target: Literal["UAT", "PROD"]
    decision: Literal["approved", "rejected"]
    by: str
    at: str = Field(default_factory=utcnow)
    comment: str = ""


class ProjectVersion(Strict):
    project_id: str
    version: str
    state: Literal["DEV", "UAT", "PROD"] = "DEV"
    review_status: Literal["IN_PROGRESS", "READY_FOR_REVIEW", "APPROVED", "REJECTED"] = "IN_PROGRESS"
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
