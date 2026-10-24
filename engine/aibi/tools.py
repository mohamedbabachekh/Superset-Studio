"""The tool layer: the only hands the AI has.

Every action the engine can take is one of the tools below — a name, typed arguments, a policy check, an audit
record. There is no tool that takes a shell command, a file path outside a workspace, a connection string or
a blob of BI-engine state, so there is nothing for a model to talk its way into.

The tools that write to the BI engine take a SLOT or nothing at all: what they create is derived from the
project's validated specification, and they refuse to run until one has been saved and found valid. That is
section 6's rule — « validate this specification before calling Superset » — as a locked door rather than a
habit.

The same registry of tools is what `GET /tools` lists (with JSON schemas) and what an MCP server would expose:
when a language model is wired in, these descriptions are its entire interface.
"""
from __future__ import annotations

import functools
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from pydantic import BaseModel, Field

from .audit import AuditLog
from .chart_agent import ChartAgent
from .dashboard_agent import DashboardAgent
from .data_gateway import DataGateway, QueryLog, QueryResult
from .dbt_agent import DbtAgent
from .dbt_runner import DbtResult, DbtRunner, lineage_from_artifacts
from .git_adapter import GitAdapter
from .models import DashboardSpec, ModelPlan, ValidationReport, utcnow
from .policy_engine import Identity, PolicyEngine, PolicyError
from .registry import Registry
from .semantic_agent import SemanticAgent
from .sql_agent import SqlAgent
from .sql_agent import quote as sql_quote
from .store import Store
from .superset_adapter import SupersetAdapter, sign_in
from .validation_agent import ValidationAgent
from .visual_qa_agent import Render, VisualQaAgent


class ToolError(Exception):
    pass


class AccessError(RuntimeError):
    """The BI engine does not hold the access SharpSight decided, and the dashboard was not left granted
    without it. Everything it says is meant to be read by a person."""


RULE_PREFIX = "sharpsight:"


def engine_role(role: str) -> str:
    """The BI engine's name for a SharpSight role."""
    return f"sharpsight_{role}"


def rule_name(role: str, dataset_id: str) -> str:
    return f"{RULE_PREFIX}{role}:{dataset_id}"


def apply_access(superset: SupersetAdapter, policy: PolicyEngine, dashboard_id: int, datasets: list,
                 metrics: list, tables: dict[str, int]) -> dict[str, Any]:
    """Who may open one dashboard, and which rows they see there, written to a Superset and read back.

    The ONE place this is done: the kit's own Superset (Toolbox.apply_superset_access) and a run environment
    (deploy.Deployer) both come here, so a rule is named, written, kept and withdrawn the same way in both,
    and is found and updated in place the next time instead of being written twice. `tables` is where each
    dataset is in THAT Superset: SharpSight dataset id → the engine's dataset id.

    The order is the point. Every rule is written and read back BEFORE the dashboard is granted, so the
    audience never holds a dashboard whose rows are not yet filtered; if granting it fails, or the grant read
    back is not the one decided, the previous grant is put back. Rules the policy has withdrawn are deleted
    only AFTER the grant: a role leaving the audience loses the dashboard before it loses its rule, never the
    other way round. That holds for every dashboard, not this one only: a withdrawn rule is KEPT while another
    dashboard over its dataset is still granted to its role (that dashboard was not re-applied, and its
    audience would see every row), and is withdrawn by the run that finds no such dashboard left. A failed
    deletion after that leaves a rule that restricts more than the policy does, and the grant stands — nothing
    about it is wider than decided."""
    audience = policy.audience(datasets, metrics)
    wanted = {rule_name(r["role"], r["dataset"]): r for r in policy.row_rules(datasets, metrics)}
    unplaced = sorted({r["dataset"] for r in wanted.values() if r["dataset"] not in tables})
    if unplaced:
        raise AccessError(f"{', '.join(unplaced)} is not in the BI engine: its row-level rule has nothing to "
                          "hang from, and the dashboard is not granted without it")
    mine = {d.id: d for d in datasets}

    def ours(rule: dict[str, Any]) -> Optional[tuple[str, str]]:
        """(role, dataset) of a SharpSight rule on one of this dashboard's datasets; None for any other."""
        parts = rule["name"].split(":", 2)
        return (parts[1], parts[2]) if len(parts) == 3 and parts[2] in mine else None

    def withdrawn(rule: dict[str, Any]) -> bool:
        # a rule another dashboard over the same dataset still needs is NOT withdrawn because this
        # dashboard's audience does not include its role: only the policy can withdraw it
        found = ours(rule)
        return found is not None and rule["name"] not in wanted and not policy.declares_rule(found[0], mine[found[1]])

    # 1 · the roles, then every rule, then what the engine holds of them
    role_ids = {role: superset.ensure_role(engine_role(role)) for role in audience}
    assets: list[dict[str, Any]] = []
    for name, rule in wanted.items():
        rule_id, action = superset.upsert_row_rule(
            name, tables[rule["dataset"]], role_ids[rule["role"]], rule["clause"], group_key=rule["dataset"],
            description=f"SharpSight row-level policy of role {rule['role']} on {rule['dataset']}. "
                        "Written by the AI BI service from governance/registry/security/rbac.yaml; edit it there.")
        assets.append({"type": "row_level_rule", "id": rule_id, "name": name, "action": action})
    held = {r["name"]: r for r in superset.row_rules(RULE_PREFIX) if ours(r)}
    problems = []
    for name, rule in wanted.items():
        have = held.get(name)
        if have is None:
            problems.append(f"row-level rule {name} is missing")
        elif (have.get("clause") != rule["clause"]
              or [t["id"] for t in have.get("tables") or []] != [tables[rule["dataset"]]]
              or [r["id"] for r in have.get("roles") or []] != [role_ids[rule["role"]]]):
            problems.append(f"row-level rule {name} is not what the policy says")
    if problems:
        raise AccessError("the BI engine does not hold the row-level rules SharpSight decided, and the dashboard "
                          "was not granted: " + "; ".join(problems))

    # 2 · the grant, last — and taken back if it is not what was decided
    previous = superset.dashboard_role_ids(dashboard_id)
    decided = sorted(engine_role(r) for r in audience)
    try:
        superset.update_dashboard(dashboard_id, {"roles": sorted(role_ids.values())})
        granted = sorted(superset.dashboard_roles(dashboard_id))
        if granted != decided:
            raise AccessError(f"the dashboard is granted to {', '.join(granted) or 'no role'}; SharpSight grants "
                              f"it to {', '.join(decided) or 'no role'}")
    except Exception as exc:
        try:
            superset.update_dashboard(dashboard_id, {"roles": previous})
            undone = "the previous grant was put back"
        except Exception as again:                     # noqa: BLE001 — said, not hidden
            undone = f"AND PUTTING THE PREVIOUS GRANT BACK FAILED ({again}): check the dashboard's roles by hand"
        raise AccessError(f"granting the dashboard failed ({exc}); {undone}") from exc
    assets.insert(0, {"type": "dashboard", "id": dashboard_id, "action": "granted", "roles": granted})

    # 3 · what the policy has withdrawn, withdrawn — after the grant that no longer needs it, and only once no
    # other dashboard over its dataset still holds its role: that grant was decided under the old policy and
    # stands until its own dashboard is applied again, and until then the rule is what filters its rows
    def still_granted_by(rule: dict[str, Any]) -> list[str]:
        role = engine_role(rule["name"].split(":", 2)[1])
        try:
            others = sorted({d for t in rule.get("tables") or [] for d in superset.dataset_dashboards(t["id"])}
                            - {dashboard_id})
            return [f"dashboard #{d}" for d in others if role in superset.dashboard_roles(d)]
        except Exception as exc:                       # noqa: BLE001 — not known to be free: kept, and said
            return [f"a dashboard that could not be checked ({str(exc)[:120]})"]

    kept: dict[str, list[str]] = {}
    for rule in [r for r in superset.row_rules(RULE_PREFIX) if withdrawn(r)]:
        holders = still_granted_by(rule)
        if holders:
            kept[rule["name"]] = holders
            assets.append({"type": "row_level_rule", "id": rule["id"], "name": rule["name"], "action": "kept",
                           "still_granted_by": holders})
            continue
        superset.delete_row_rule(rule["id"])
        assets.append({"type": "row_level_rule", "id": rule["id"], "name": rule["name"], "action": "deleted"})
    left = sorted(r["name"] for r in superset.row_rules(RULE_PREFIX) if withdrawn(r) and r["name"] not in kept)
    if left:
        raise AccessError("the grant and the rules are as decided, but rules the policy withdrew are still "
                          "there (they restrict, they do not open): " + ", ".join(left))
    return {"audience": audience, "granted": granted, "rules": sorted(wanted), "assets": assets,
            "kept": {name: holders for name, holders in sorted(kept.items())}}


@dataclass
class ToolContext:
    identity: Identity
    request_id: str
    ai_model: str
    project_id: Optional[str] = None
    actor: str = "ai"
    query_log: QueryLog = field(default_factory=QueryLog)


@dataclass
class ToolSpec:
    name: str
    description: str
    arguments: type[BaseModel]
    capability: str = "tools.use"


class NoArgs(BaseModel):
    pass


class DatasetArgs(BaseModel):
    dataset_id: str


class SampleArgs(BaseModel):
    dataset_id: str
    limit: int = Field(default=20, ge=1, le=100)


class MetricArgs(BaseModel):
    metric_id: str


class DimensionArgs(BaseModel):
    dimension: str
    dataset_id: Optional[str] = None


class ListMetricsArgs(BaseModel):
    domain: Optional[str] = None
    certified_only: bool = False


class TemplateArgs(BaseModel):
    template_id: str


class SqlArgs(BaseModel):
    sql: str
    limit: Optional[int] = Field(default=None, ge=1)
    purpose: str = ""


class ValidateSqlArgs(BaseModel):
    sql: str


class PlanArgs(BaseModel):
    plan: ModelPlan


class DbtSelectArgs(BaseModel):
    select: str
    exclude: Optional[str] = None


class SlotArgs(BaseModel):
    slot: str


class SpecArgs(BaseModel):
    spec: DashboardSpec


class CommitArgs(BaseModel):
    message: str


class ValidateArgs(BaseModel):
    visual: bool = True


def tool(name: str, description: str, arguments: type[BaseModel] = NoArgs,
         capability: str = "tools.use") -> Callable:
    """Register a tool. Whatever happens inside — success, refusal, crash — one audit record is written.

    `capability` is what the caller must hold. Looking something up in the registries needs only `bi.read` —
    a reviewer must be able to see what a dashboard is made of. Anything that runs a query, builds a model
    or touches the BI engine needs `tools.use`. Which datasets either may see is a separate question, asked
    inside each tool."""

    def wrap(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def inner(self: "Toolbox", ctx: ToolContext, **kwargs: Any) -> Any:
            args = arguments(**kwargs)
            note: dict[str, Any] = {}
            started = time.monotonic()
            try:
                self.policy.require(ctx.identity, capability)
                result = fn(self, ctx, note, **{k: getattr(args, k) for k in type(args).model_fields})
            except Exception as exc:
                self.audit.record(
                    action=f"tool:{name}", user=ctx.identity.user, request_id=ctx.request_id, actor=ctx.actor,
                    ai_model=ctx.ai_model, project_id=ctx.project_id, sql=note.get("sql"),
                    datasets=note.get("datasets"), metrics=note.get("metrics"),
                    outcome="refused" if isinstance(exc, PolicyError) else "error",
                    detail={"error": str(exc)[:500]}, duration_ms=int((time.monotonic() - started) * 1000))
                raise
            self.audit.record(
                action=f"tool:{name}", user=ctx.identity.user, request_id=ctx.request_id, actor=ctx.actor,
                ai_model=ctx.ai_model, project_id=ctx.project_id, datasets=note.get("datasets"),
                sql=note.get("sql"), dbt_files=note.get("dbt_files"), superset_assets=note.get("superset_assets"),
                validation=note.get("validation"), detail=note.get("detail"), metrics=note.get("metrics"),
                visual_qa=note.get("visual_qa"), git_commit=note.get("git_commit"),
                deployment=note.get("deployment"), duration_ms=int((time.monotonic() - started) * 1000))
            return result

        inner.spec = ToolSpec(name, description, arguments, capability)  # type: ignore[attr-defined]
        return inner

    return wrap


class Toolbox:
    def __init__(self, *, registry: Registry, policy: PolicyEngine, gateway: DataGateway, dbt: DbtRunner,
                 git: GitAdapter, superset: SupersetAdapter, store: Store, audit: AuditLog, visual: VisualQaAgent,
                 state_dir: Path, bi_database_name: str, bi_database_uri: str, superset_public_url: str,
                 dbt_dev_schema: str):
        self.registry = registry
        self.policy = policy
        self.gateway = gateway
        self.dbt = dbt
        self.git = git
        self.superset = superset
        self.store = store
        self.audit = audit
        self.visual = visual
        self.state_dir = state_dir
        self.superset_public_url = superset_public_url
        self.dbt_dev_schema = dbt_dev_schema
        self._bi_database = (bi_database_name, bi_database_uri)
        self._bi_database_id: Optional[int] = None
        self.semantic = SemanticAgent(registry)
        self.sql = SqlAgent()
        self.charts = ChartAgent(registry)
        self.dashboards = DashboardAgent(registry, policy, self.semantic)
        self.dbt_agent = DbtAgent(registry)
        self.validation = ValidationAgent(registry, policy, self.sql, store)
        # what the last retrieval and render of each project found — evidence for validate_dashboard
        self.evidence: dict[str, dict[str, Any]] = {}
        # Who builds a package. The lifecycle sets it: a package is cut from a committed version, and what
        # a version is, is the lifecycle's to know. The tool below only asks.
        self.packager: Optional[Callable[[str], dict[str, Any]]] = None

    # ── the catalogue ───────────────────────────────────────────────────────────────────────────────────────
    @classmethod
    def specs(cls) -> list[ToolSpec]:
        return [getattr(cls, name).spec for name in dir(cls) if hasattr(getattr(cls, name), "spec")]

    def call(self, name: str, ctx: ToolContext, arguments: dict[str, Any]) -> Any:
        for attr in dir(type(self)):
            method = getattr(type(self), attr)
            if getattr(method, "spec", None) and method.spec.name == name:
                return getattr(self, attr)(ctx, **arguments)
        raise ToolError(f"there is no tool named {name}")

    # ── project state ───────────────────────────────────────────────────────────────────────────────────────
    def project(self, ctx: ToolContext) -> dict[str, Any]:
        if not ctx.project_id:
            raise ToolError("this tool works on a BI project, and none is open")
        body = self.store.get_project(ctx.project_id)
        if body is None:
            raise ToolError(f"BI project {ctx.project_id} does not exist")
        if body.get("tenant") != ctx.identity.tenant:
            raise PolicyError(f"{ctx.identity.user} has no access to BI project {ctx.project_id}", "tenant")
        return body

    def save_project(self, body: dict[str, Any]) -> None:
        self.store.save_project(body["project_id"], body, utcnow())

    def _valid_spec(self, ctx: ToolContext) -> tuple[dict[str, Any], DashboardSpec]:
        body = self.project(ctx)
        if not body.get("spec") or not body.get("spec_valid"):
            raise PolicyError("the BI engine is not called before a dashboard specification has been saved and "
                              "found valid (save_dashboard_spec)", "spec_not_validated")
        return body, DashboardSpec(**body["spec"])

    @staticmethod
    def live_charts(engine: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """The charts that are in the BI engine now. A slot dropped from the specification keeps its record
        (so the chart can come back as itself) but has no id until it does."""
        return {slot: chart for slot, chart in engine.get("charts", {}).items() if chart.get("id") is not None}

    def bi_database_id(self) -> int:
        if self._bi_database_id is None:
            self._bi_database_id = self.superset.ensure_database(*self._bi_database)
        return self._bi_database_id

    def _run_sql(self, ctx: ToolContext, sql: str, purpose: str, limit: Optional[int] = None,
                 metrics: Optional[list[str]] = None) -> QueryResult:
        """Every statement the engine runs, whichever tool asked for it, leaves its own audit record: the
        SQL, the datasets it read, the metric it computed, how many rows came back — and never the rows."""
        common = dict(action="sql", user=ctx.identity.user, request_id=ctx.request_id, actor=ctx.actor,
                      ai_model=ctx.ai_model, project_id=ctx.project_id, sql=sql, metrics=metrics)
        try:
            result = self.gateway.run(sql, ctx.identity, limit=limit, purpose=purpose, log=ctx.query_log)
        except Exception as exc:
            self.audit.record(**common, outcome="refused" if isinstance(exc, PolicyError) else "error",
                              detail={"purpose": purpose, "error": str(exc).strip().splitlines()[0][:300]})
            raise
        self.audit.record(**common, datasets=result.verdict.datasets, duration_ms=result.duration_ms,
                          detail={"purpose": purpose, "row_count": result.row_count, "truncated": result.truncated})
        return result

    def apply_specification(self, ctx: ToolContext) -> dict[str, Any]:
        """The saved specification, made real in the BI engine: datasets, dashboard, one chart per slot, then
        the layout. Every step is one of the tools below, so each is checked and audited on its own; running
        it again changes only what differs."""
        _, spec = self._valid_spec(ctx)
        for dataset_id in spec.datasets():
            self.create_superset_dataset(ctx, dataset_id=dataset_id)
        dashboard = self.create_superset_dashboard(ctx)
        charts = {c.slot: self.create_superset_chart(ctx, slot=c.slot) for c in spec.components}
        placed = self.update_superset_dashboard(ctx)
        access = self.apply_superset_access(ctx)
        return {"dashboard": dashboard, "charts": charts, "filters": placed["filters"],
                "datasets": self.project(ctx)["engine"]["datasets"], "access": access}

    # ── datasets and metrics ────────────────────────────────────────────────────────────────────────────────
    @tool("list_datasets", "List the datasets the requesting user may read, with owner and certification.",
          capability="bi.read")
    def list_datasets(self, ctx: ToolContext, note: dict) -> list[dict[str, Any]]:
        permitted = self.policy.permitted_datasets(ctx.identity)
        note["datasets"] = [d.id for d in permitted]
        return [{"id": d.id, "name": d.name, "owner": d.owner, "certified": d.certified, "kind": d.kind,
                 "domain": d.domain, "grain": d.grain, "description": d.description} for d in permitted]

    @tool("get_dataset", "Registry metadata of one dataset: owner, certification, source model, grain, SLA.",
          DatasetArgs, capability="bi.read")
    def get_dataset(self, ctx: ToolContext, note: dict, dataset_id: str) -> dict[str, Any]:
        dataset = self.policy.require_dataset(ctx.identity, dataset_id)
        note["datasets"] = [dataset.id]
        body = dataset.model_dump(exclude={"columns"}, exclude_none=True)
        body["relation"] = dataset.relation
        return body

    @tool("get_dataset_schema", "Columns of one dataset: type, role, classification, declared range.", DatasetArgs,
          capability="bi.read")
    def get_dataset_schema(self, ctx: ToolContext, note: dict, dataset_id: str) -> dict[str, Any]:
        dataset = self.policy.require_dataset(ctx.identity, dataset_id)
        note["datasets"] = [dataset.id]
        lineage = self.store.lineage_for(dataset.source_model) or {}
        readable = set(self.policy.readable_columns(ctx.identity, dataset))
        return {"dataset": dataset.id, "columns": {
            name: {**column.model_dump(exclude_none=True),
                   "physical_type": lineage.get("columns", {}).get(name, {}).get("type"),
                   "readable": name in readable}
            for name, column in dataset.columns.items()}}

    @tool("get_dataset_sample", "A few rows of a dataset. Columns classified above the user's clearance are "
          "left out, and nothing sampled is written to the audit log.", SampleArgs)
    def get_dataset_sample(self, ctx: ToolContext, note: dict, dataset_id: str, limit: int) -> dict[str, Any]:
        dataset = self.policy.require_dataset(ctx.identity, dataset_id)
        columns = self.policy.readable_columns(ctx.identity, dataset)
        if not columns:
            raise PolicyError(f"{ctx.identity.user} may read no column of {dataset_id}", "classification")
        sql = self.sql.sample(dataset, columns, limit)
        result = self._run_sql(ctx, sql, f"sample of {dataset_id}", limit)
        note.update(datasets=[dataset.id], sql=sql,
                    detail={"row_count": result.row_count, "columns": result.columns,
                            "withheld_columns": self.policy.withheld_columns(ctx.identity, dataset)})
        return {"dataset": dataset.id, "columns": result.columns, "rows": result.rows}

    @tool("get_metric", "A governed metric: expression, format, dimensions, owner, certification, lineage.",
          MetricArgs, capability="bi.read")
    def get_metric(self, ctx: ToolContext, note: dict, metric_id: str) -> dict[str, Any]:
        note["metrics"] = [metric_id]
        metric = self.policy.require_metric(ctx.identity, metric_id)
        note["datasets"] = metric.lineage.datasets
        return self.semantic.describe(metric)

    @tool("list_metrics", "The governed metrics the user may use, certified ones first.", ListMetricsArgs,
          capability="bi.read")
    def list_metrics(self, ctx: ToolContext, note: dict, domain: Optional[str], certified_only: bool) -> list[dict]:
        found = []
        for metric in self.registry.metrics.values():
            if certified_only and not metric.certified:
                continue
            if domain and not metric.id.startswith(domain + "."):
                continue
            if self.policy.may_use(ctx.identity, metric):
                found.append(self.semantic.describe(metric))
        note["metrics"] = [m["id"] for m in found]
        return sorted(found, key=lambda m: (not m["certified"], m["id"]))

    @tool("get_dimension", "A governed dimension: the column behind a word of the business (« regional » is "
          "region), the datasets it is found in, its type, role, classification and expected values there, and "
          "the metrics that may be broken down by it. Reads the registry; returns no data.", DimensionArgs,
          capability="bi.read")
    def get_dimension(self, ctx: ToolContext, note: dict, dimension: str, dataset_id: Optional[str]) -> dict[str, Any]:
        wanted = " ".join(dimension.lower().replace("_", " ").split())
        vocabulary = self.registry.lexicon.get("dimensions", {})
        column = next((name for name, phrases in vocabulary.items()
                       if wanted in (name.replace("_", " "), *(p.lower() for p in phrases))), dimension.strip())
        scope = ([self.policy.require_dataset(ctx.identity, dataset_id)] if dataset_id
                 else self.policy.permitted_datasets(ctx.identity))
        holders = [d for d in scope if column in d.columns and d.columns[column].role != "measure"]
        if not holders:
            where = dataset_id or f"any dataset {ctx.identity.user} may read"
            raise ToolError(f"« {dimension} » is not a dimension of {where}")
        holders.sort(key=lambda d: (not d.certified, d.id))
        usable = [m for m in self.registry.metrics.values() if self.policy.may_use(ctx.identity, m)
                  and any(set(d.columns[column].determined_by or [column]) <= set(m.dimensions) for d in holders)]
        note.update(datasets=[d.id for d in holders], metrics=[m.id for m in usable])
        return {"dimension": column, "label": holders[0].label(column),
                "synonyms": vocabulary.get(column, []),
                "datasets": {d.id: {**d.columns[column].model_dump(exclude_none=True), "certified": d.certified,
                                    "readable": column in self.policy.readable_columns(ctx.identity, d),
                                    "part_of_grain": column in d.grain} for d in holders},
                "metrics": [m.id for m in usable]}

    @tool("list_dashboard_templates", "The predefined dashboard templates.", capability="bi.read")
    def list_dashboard_templates(self, ctx: ToolContext, note: dict) -> list[dict[str, Any]]:
        return [{"id": t.id, "name": t.template.name, "version": t.version, "description": t.template.description,
                 "slots": {s: {"purpose": b.purpose, "accepts": b.accepts, "mandatory": b.mandatory}
                           for s, b in t.slots.items()}} for t in self.registry.templates.values()]

    @tool("get_dashboard_template", "One template: layout, slots and what each accepts, theme, filters.",
          TemplateArgs, capability="bi.read")
    def get_dashboard_template(self, ctx: ToolContext, note: dict, template_id: str) -> dict[str, Any]:
        return self.registry.template(template_id).model_dump()

    # ── SQL ─────────────────────────────────────────────────────────────────────────────────────────────────
    @tool("validate_sql", "Say whether a statement would be allowed to run, and why not if not. Runs nothing.",
          ValidateSqlArgs, capability="bi.read")
    def validate_sql(self, ctx: ToolContext, note: dict, sql: str) -> dict[str, Any]:
        verdict = self.policy.validate_sql(sql, ctx.identity)
        note.update(sql=sql, datasets=verdict.datasets, detail={"ok": verdict.ok, "problems": verdict.problems})
        return {"ok": verdict.ok, "problems": verdict.problems, "datasets": verdict.datasets,
                "checks": verdict.checks}

    @tool("execute_readonly_sql", "Run one SELECT over registered datasets through the read-only data gateway. "
          "Row-capped and timed out; anything else is refused.", SqlArgs)
    def execute_readonly_sql(self, ctx: ToolContext, note: dict, sql: str, limit: Optional[int],
                             purpose: str) -> QueryResult:
        result = self._run_sql(ctx, sql, purpose, limit)
        note.update(datasets=result.verdict.datasets,
                    detail={"purpose": purpose, "row_count": result.row_count, "truncated": result.truncated,
                            "columns": result.columns})
        return result

    # ── dbt ─────────────────────────────────────────────────────────────────────────────────────────────────
    def _workspace(self, ctx: ToolContext) -> Path:
        return self.git.workspace(self.project(ctx)["project_id"])

    @tool("generate_dbt_model", "Write a dbt model and its tests from a transformation plan, on the project's "
          "own branch. The rendered SQL must pass the policy engine before a file is written.", PlanArgs)
    def generate_dbt_model(self, ctx: ToolContext, note: dict, plan: ModelPlan) -> dict[str, Any]:
        body = self.project(ctx)
        for dataset_id in [plan.base_dataset, *[j.dataset for j in plan.joins]]:
            self.policy.require_dataset(ctx.identity, dataset_id)
        text = self.dbt_agent.render_sql(plan, body["project_id"], ctx.request_id)
        verdict = self.policy.validate_dbt_model(text, ctx.identity)
        note.update(sql=text, datasets=verdict.datasets)
        if not verdict.ok:
            raise PolicyError("the generated model was refused: " + "; ".join(verdict.problems), "dbt_refused")
        workspace = self._workspace(ctx)
        files = self.dbt_agent.write(plan, workspace, body["project_id"], ctx.request_id)
        commit = self.git.commit_workspace(body["project_id"],
                                           f"{body['project_id']}: generate {plan.name}\n\nRequest {ctx.request_id}\n{plan.reason}")
        body["model_plan"] = plan.model_dump()
        body["dbt"] = {"model": plan.name, "files": files, "commit": commit, "branch": f"ai/{body['project_id']}"}
        self.save_project(body)
        note.update(dbt_files=files, git_commit=commit,
                    detail={"model": plan.name, "branch": body["dbt"]["branch"], "commit": commit,
                            "repository": "dbt"})
        return {"model": plan.name, "files": files, "commit": commit, "verdict": verdict}

    def _dbt_note(self, note: dict, result: DbtResult) -> dict[str, Any]:
        note["detail"] = {**result.summary(), "failures": [f["unique_id"] for f in result.failures()]}
        if not result.ok:
            note["detail"]["output"] = result.output_tail
        return {"ok": result.ok, **result.summary(), "results": result.results, "failures": result.failures(),
                "output": result.output_tail}

    @tool("compile_dbt", "Compile the selected models in the project's workspace (target dev).", DbtSelectArgs)
    def compile_dbt(self, ctx: ToolContext, note: dict, select: str, exclude: Optional[str]) -> dict[str, Any]:
        return self._dbt_note(note, self.dbt.compile(self._workspace(ctx), select))

    @tool("run_dbt_model", "Build the selected models into the development schema, deferring everything else "
          "to the governed production build.", DbtSelectArgs)
    def run_dbt_model(self, ctx: ToolContext, note: dict, select: str, exclude: Optional[str]) -> dict[str, Any]:
        workspace = self._workspace(ctx)
        outcome = self._dbt_note(note, self.dbt.run(workspace, select))
        if outcome["ok"]:
            built = {r["unique_id"]: r["status"] for r in outcome["results"]}
            self.dbt.docs(workspace, select)
            self._record_generated(workspace, built)
        return outcome

    def _record_generated(self, workspace: Path, outcomes: dict[str, str]) -> None:
        """Keep what dbt knows about the GENERATED models. Only those: the workspace's manifest also lists
        the governed models — as the dev target would name them, in a schema they were never built in — and
        those are known, correctly, from the production build.

        dbt rewrites run_results.json on every command, so each call sees one command's outcomes. They are
        therefore passed in and MERGED: a model's build status and its columns survive the test run that
        follows, and a test already seen passing is not turned back into « not run » by a later compile."""
        for node in lineage_from_artifacts(workspace / "target", "dev"):
            if "ai_generated" not in node["tags"]:
                continue
            previous = self.store.lineage_by_id(node["unique_id"]) or {}
            known = {t["name"]: t["status"] for t in previous.get("tests", [])}
            node["status"] = outcomes.get(node["unique_id"]) or previous.get("status")
            node["columns"] = node["columns"] or previous.get("columns", {})
            for test in node["tests"]:
                test["status"] = outcomes.get(test["unique_id"]) or known.get(test["name"], "not run")
            self.store.save_lineage([node])

    @tool("run_dbt_test", "Run the dbt tests of the selected models.", DbtSelectArgs)
    def run_dbt_test(self, ctx: ToolContext, note: dict, select: str, exclude: Optional[str]) -> dict[str, Any]:
        workspace = self._workspace(ctx)
        outcome = self._dbt_note(note, self.dbt.test(workspace, select, exclude))
        self._record_generated(workspace, {r["unique_id"]: r["status"] for r in outcome["results"]})
        return outcome

    # ── the specification ───────────────────────────────────────────────────────────────────────────────────
    @tool("save_dashboard_spec", "Validate a dashboard specification and save it as the project's own. The BI "
          "engine tools work from the saved specification, and only when it is valid.", SpecArgs)
    def save_dashboard_spec(self, ctx: ToolContext, note: dict, spec: DashboardSpec) -> ValidationReport:
        body = self.project(ctx)
        resolved = self.dashboards.resolve(spec, ctx.identity)
        report = self.dashboards.validate(spec, ctx.identity)
        body["spec"] = spec.model_dump()
        body["spec_valid"] = report.passed
        self.save_project(body)
        note.update(datasets=spec.datasets(), metrics=spec.metrics(), validation=report.summary(),
                    detail={"dashboard": spec.dashboard.id, "template": spec.dashboard.template,
                            "version": spec.dashboard.version, "components": len(spec.components),
                            "datasets_resolved_from_the_registry": resolved,
                            "failures": [f"{c.name}: {c.subject} {c.detail}".strip() for c in report.failures]})
        return report

    # ── the BI engine ───────────────────────────────────────────────────────────────────────────────────────
    @tool("create_superset_dataset", "Register a governed dataset in the BI engine on the read-only connection, "
          "with its certified metrics saved on it.", DatasetArgs)
    def create_superset_dataset(self, ctx: ToolContext, note: dict, dataset_id: str) -> dict[str, Any]:
        body, _ = self._valid_spec(ctx)
        dataset = self.policy.require_dataset(ctx.identity, dataset_id)
        database_id = self.bi_database_id()
        found = self.superset.find_dataset(database_id, dataset.db_schema, dataset.source_model)
        if found is None:
            engine_id = self.superset.create_dataset(database_id, dataset.db_schema, dataset.source_model)
        else:
            engine_id = found["id"]

        current = self.superset.get_dataset(engine_id)
        # Re-read the table's columns ONLY when the engine is missing one the registry has — a generated model
        # rebuilt with another shape. A refresh that changes nothing still rewrites the dataset (and, in 6.1,
        # logs a failed connection to a database named after the schema for every date column).
        if not set(dataset.columns) <= {column["column_name"] for column in current.get("columns", [])}:
            self.superset.refresh_dataset(engine_id)
            current = self.superset.get_dataset(engine_id)
        ours = {m["metric_name"]: m for m in (self.semantic.engine_metric(metric)
                                              for metric in self.registry.metrics.values()
                                              if self.semantic.valid_on(metric, dataset)[0])}
        metrics = []
        for existing in current.get("metrics", []):
            name = existing["metric_name"]
            metrics.append({"id": existing["id"], **ours.pop(name)} if name in ours else
                           {"id": existing["id"], "metric_name": name, "expression": existing["expression"]})
        metrics.extend(ours.values())
        certification = ({"certification": {"certified_by": dataset.owner,
                                            "details": f"SharpSight dataset {dataset.id} v{dataset.version}"}}
                         if dataset.certified else
                         {"warning_markdown": f"Generated by SharpSight AI BI for project {dataset.project} — not certified"})
        # Each column is given the name people read. Every column the engine has is sent back with its id:
        # a column left out of this list is one the engine deletes.
        columns = [{"id": column["id"], "column_name": column["column_name"],
                    "verbose_name": dataset.label(column["column_name"])}
                   for column in current.get("columns", [])]
        payload = {"metrics": metrics, "columns": columns, "description": dataset.description,
                   "extra": json.dumps(certification), "external_url": f"sharpsight://dataset/{dataset.id}"}
        if dataset.time_column:
            payload["main_dttm_col"] = dataset.time_column
        self.superset.update_dataset(engine_id, payload)

        body.setdefault("engine", {}).setdefault("datasets", {})[dataset.id] = engine_id
        body["engine"]["database_id"] = database_id
        self.save_project(body)
        note.update(datasets=[dataset.id],
                    metrics=[m.id for m in self.registry.metrics.values() if self.semantic.valid_on(m, dataset)[0]],
                    superset_assets=[{"type": "dataset", "id": engine_id, "action": "created" if found is None else "updated",
                                      "table": dataset.relation, "metrics": [m["metric_name"] for m in metrics]}])
        return {"id": engine_id, "dataset": dataset.id, "created": found is None}

    @tool("create_superset_dashboard", "Create the project's dashboard in the BI engine (empty, unpublished). "
          "Its title and address come from the saved specification.")
    def create_superset_dashboard(self, ctx: ToolContext, note: dict) -> dict[str, Any]:
        body, spec = self._valid_spec(ctx)
        slug = spec.dashboard.id
        known = body.get("engine", {}).get("dashboard", {}).get("id")
        existing = self.superset.get_dashboard(known) if known else None
        existing = existing or self.superset.get_dashboard(slug)
        if existing is None:
            dashboard_id = self.superset.create_dashboard({
                "dashboard_title": spec.dashboard.title, "slug": slug, "published": False,
                "external_url": f"sharpsight://bi-project/{body['project_id']}"})
            existing = self.superset.get_dashboard(dashboard_id)
            action = "created"
        else:
            action = "reused"
        dashboard = {"id": existing["id"], "uuid": existing.get("uuid"), "slug": slug,
                     "url": f"{self.superset_public_url}/superset/dashboard/{slug}/"}
        body.setdefault("engine", {})["dashboard"] = dashboard
        self.save_project(body)
        note["superset_assets"] = [{"type": "dashboard", "id": dashboard["id"], "slug": slug, "action": action}]
        return dashboard

    @tool("create_superset_chart", "Create or update the chart of one slot of the saved specification.", SlotArgs)
    def create_superset_chart(self, ctx: ToolContext, note: dict, slot: str) -> dict[str, Any]:
        body, spec = self._valid_spec(ctx)
        component = spec.component(slot)
        if component is None:
            raise ToolError(f"the saved specification has no component in slot {slot}")
        engine = body.get("engine", {})
        if "dashboard" not in engine or component.dataset not in engine.get("datasets", {}):
            raise ToolError("the dashboard and the component's dataset must exist in the BI engine first")
        template = self.registry.template(spec.dashboard.template)
        dataset = self.policy.require_dataset(ctx.identity, component.dataset)
        metric = self.registry.metric(component.metric)

        headline = None
        if component.type == "big_number":
            sql = self.sql.metric_query(dataset, [metric])
            headline = self._run_sql(ctx, sql, f"headline value of {metric.id}", metrics=[metric.id]).scalar()
        parts = None
        if self.registry.visualizations[component.type].needs.get("additive_metric"):
            # a share-of-total chart labels every slice when there are few: how many are there?
            parts = len(self._run_sql(ctx, self.sql.distinct_values(dataset, component.dimension, 50),
                                      f"how many parts {component.dimension} has, for the labels of {slot}").rows)
        payload = self.charts.payload(
            component, dataset, metric, engine["datasets"][dataset.id], engine["dashboard"]["id"], template,
            self.dashboards.scheme(spec, template),
            spec.theme.sequential_scheme or template.theme.sequential_scheme, body["project_id"], headline, parts)

        previous = engine.get("charts", {}).get(slot, {})
        known = previous.get("id")
        if known and self.superset.get_chart(known) is not None:
            self.superset.update_chart(known, payload)
            chart_id, action = known, "updated"
        else:
            # A chart that was deleted and comes back for the same slot keeps its uuid: it is the same chart
            # to anything outside the engine that refers to it — an export, an embed, a link.
            if previous.get("uuid"):
                payload["uuid"] = previous["uuid"]
            chart_id, action = self.superset.create_chart(payload), "created"
        chart = self.superset.get_chart(chart_id) or {}
        engine.setdefault("charts", {})[slot] = {"id": chart_id, "uuid": chart.get("uuid"),
                                                 "name": payload["slice_name"], "type": component.type,
                                                 "engine_type": payload["viz_type"]}
        self.save_project(body)
        note.update(datasets=[dataset.id], metrics=[metric.id],
                    superset_assets=[{"type": "chart", "id": chart_id, "slot": slot, "action": action,
                                      "viz_type": payload["viz_type"], "name": payload["slice_name"]}])
        return engine["charts"][slot]

    @tool("update_superset_dashboard", "Lay the project's charts out on its dashboard from the template, with "
          "the specification's filters and the template's theme. Charts of slots no longer specified are removed.")
    def update_superset_dashboard(self, ctx: ToolContext, note: dict) -> dict[str, Any]:
        body, spec = self._valid_spec(ctx)
        engine = body.get("engine", {})
        if "dashboard" not in engine:
            raise ToolError("the dashboard must exist in the BI engine first")
        template = self.registry.template(spec.dashboard.template)
        specified = {c.slot for c in spec.components}
        removed = []
        for slot in [s for s in engine.get("charts", {}) if s not in specified]:
            chart = engine["charts"][slot]
            if chart.get("id") is None:
                continue
            self.superset.delete_chart(chart["id"])
            removed.append({"type": "chart", "id": chart["id"], "slot": slot, "action": "deleted"})
            # the record stays, without its id: should the slot return, the chart returns with the same uuid
            engine["charts"][slot] = {**chart, "id": None}
        charts = {slot: chart for slot, chart in self.live_charts(engine).items() if slot in specified}

        filters = self.dashboards.native_filters(spec, engine["datasets"], charts, template)
        self.superset.update_dashboard(engine["dashboard"]["id"], {
            "dashboard_title": spec.dashboard.title,
            "slug": spec.dashboard.id,
            "position_json": json.dumps(self.dashboards.layout(spec, template, charts)),
            "json_metadata": json.dumps(self.dashboards.json_metadata(spec, template, filters)),
            "css": self.dashboards.css(template, spec.theme.brand),
        })
        self.save_project(body)
        note["superset_assets"] = [{"type": "dashboard", "id": engine["dashboard"]["id"], "action": "updated",
                                    "charts": len(charts), "filters": [f["name"] for f in filters],
                                    "template": f"{template.id}@{template.version}"}, *removed]
        return {"dashboard": engine["dashboard"], "charts": len(charts), "filters": len(filters)}

    # ── who may open it, and which rows they see ────────────────────────────────────────────────────────────
    @staticmethod
    def engine_role(role: str) -> str:
        """The BI engine's name for a SharpSight role."""
        return engine_role(role)

    @staticmethod
    def rule_name(role: str, dataset_id: str) -> str:
        return rule_name(role, dataset_id)

    @tool("apply_superset_access", "Grant the dashboard, in the BI engine, to the SharpSight roles that may read "
          "every one of its datasets, and give the BI engine each role's row-level policy as a rule. Nothing "
          "here is chosen by the caller: it is all read from SharpSight's RBAC.")
    def apply_superset_access(self, ctx: ToolContext, note: dict) -> dict[str, Any]:
        body, spec = self._valid_spec(ctx)
        engine = body.get("engine", {})
        if "dashboard" not in engine:
            raise ToolError("the dashboard must exist in the BI engine first")
        datasets = [self.registry.dataset(ds) for ds in spec.datasets()]
        metrics = [self.registry.metric(m) for m in spec.metrics()]
        # the same helper a deployment to a run environment uses: one way to write, grant and withdraw
        try:
            done = apply_access(self.superset, self.policy, engine["dashboard"]["id"], datasets, metrics,
                                engine.get("datasets", {}))
        except AccessError as exc:
            raise ToolError(str(exc)) from exc
        engine["access"] = {"roles": done["audience"], "row_rules": done["rules"]}
        self.save_project(body)
        note.update(datasets=[d.id for d in datasets], metrics=[m.id for m in metrics], superset_assets=done["assets"])
        return engine["access"]

    _DATA_DEPENDENT = ("show_labels_threshold", "y_axis_format", "datasource")

    def superset_changes(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        """Each chart of the project as Superset holds it NOW, against the specification — for the day a
        person edits one in Superset's own Explore. Per slot: what changed in governed terms (and can be
        taken into the specification), what cannot be a component at all (and why), and what Superset holds
        that a specification has no place for (and a build would set back)."""
        spec, engine = DashboardSpec(**body["spec"]), body["engine"]
        template = self.registry.template(spec.dashboard.template)
        by_engine_id = {engine_id: dataset_id for dataset_id, engine_id in engine.get("datasets", {}).items()}
        found = []
        for slot, chart in self.live_charts(engine).items():
            component = spec.component(slot)
            if component is None:
                continue
            live = self.superset.get_chart(chart["id"])
            entry: dict[str, Any] = {"slot": slot, "chart_id": chart["id"], "changed": {}, "refused": [],
                                     "not_carried": [], "adopted": None}
            if live is None:
                entry.update(title=chart.get("name"), refused=["the chart no longer exists in Superset"])
            else:
                read = self.charts.read_back(component, live, template.slots[slot].purpose, by_engine_id)
                adopted = read["component"]
                entry.update(title=live.get("slice_name"), changed=read["changed"], refused=read["refused"])
                if adopted is not None:
                    dataset, metric = self.registry.dataset(adopted.dataset), self.registry.metric(adopted.metric)
                    expected = self.charts.form_data(
                        adopted, dataset, metric, engine["datasets"][dataset.id], template,
                        self.dashboards.scheme(spec, template),
                        spec.theme.sequential_scheme or template.theme.sequential_scheme)
                    actual = json.loads(live.get("params") or "{}")
                    entry["not_carried"] = sorted(k for k, v in expected.items() if not k.startswith("_")
                                                  and k not in self._DATA_DEPENDENT and k in actual and actual[k] != v)
                    entry["adopted"] = adopted.model_dump()
            entry["in_step"] = not (entry["changed"] or entry["refused"] or entry["not_carried"])
            found.append(entry)
        return found

    def dashboard_differences(self, body: dict[str, Any]) -> list[str]:
        """What a person changed on the dashboard ITSELF, in Superset's dashboard editor, that a specification
        does not carry: a chart added by hand, a layout that is no longer the template's, filters that are not
        the specification's. A commit applies the specification again, so each of these is undone by it —
        which is why they are named, to the person and in the audit record, before anything is committed:
        nobody loses work without having been told what."""
        spec, engine = DashboardSpec(**body["spec"]), body["engine"]
        template = self.registry.template(spec.dashboard.template)
        live = self.superset.get_dashboard(engine["dashboard"]["id"])
        if live is None:
            return ["the dashboard no longer exists in Superset"]
        charts = self.live_charts(engine)
        found: list[str] = []
        ours = {chart["id"] for chart in charts.values()}
        added = sorted(str(c.get("slice_name") or c.get("id")) for c in self.superset.dashboard_charts(live["id"])
                       if c.get("id") not in ours)
        if added:
            found.append("added by hand, and in no slot of the template: " + ", ".join(f"« {name} »" for name in added))

        def shape(position: dict[str, Any]) -> list[Any]:
            rows = []
            for key in (position.get("GRID_ID") or {}).get("children", []):
                node = position.get(key) or {}
                if node.get("type") == "HEADER":
                    rows.append(("header", (node.get("meta") or {}).get("text")))
                else:
                    rows.append((node.get("type"), [((position.get(child) or {}).get("meta") or {}).get(field)
                                                    for child in node.get("children", [])
                                                    for field in ("chartId", "width", "height")]))
            return rows

        if shape(json.loads(live.get("position_json") or "{}")) != shape(self.dashboards.layout(spec, template, charts)):
            found.append("the layout is no longer the template's: rows, sizes or section headers were changed")
        named = lambda filters: sorted(str(f.get("name")) for f in filters)  # noqa: E731
        have = named(json.loads(live.get("json_metadata") or "{}").get("native_filter_configuration") or [])
        want = named(self.dashboards.native_filters(spec, engine.get("datasets", {}), charts, template))
        if have != want:
            found.append(f"its filters are {', '.join(have) or 'none'}; the specification's are {', '.join(want) or 'none'}")
        return found

    def access_differences(self, body: dict[str, Any]) -> list[str]:
        """How the access set in the BI engine differs from what SharpSight grants. Empty when it does not."""
        spec, engine = DashboardSpec(**body["spec"]), body["engine"]
        datasets = [self.registry.dataset(ds) for ds in spec.datasets()]
        metrics = [self.registry.metric(m) for m in spec.metrics()]
        want_roles = sorted(self.engine_role(r) for r in self.policy.audience(datasets, metrics))
        have_roles = self.superset.dashboard_roles(engine["dashboard"]["id"])
        differences = []
        if have_roles != want_roles:
            differences.append(f"the dashboard is granted to {', '.join(have_roles) or 'no role'}; SharpSight "
                               f"grants it to {', '.join(want_roles) or 'no role'}")
        wanted = {self.rule_name(r["role"], r["dataset"]): r for r in self.policy.row_rules(datasets, metrics)}
        mine = {d.id for d in datasets}
        found = {r["name"]: r for r in self.superset.row_rules("sharpsight:") if r["name"].split(":", 2)[-1] in mine}
        for name, rule in wanted.items():
            have = found.get(name)
            if have is None:
                differences.append(f"row-level rule {name} is missing")
                continue
            if have["clause"] != rule["clause"]:
                differences.append(f"row-level rule {name} reads « {have['clause']} »; the policy is « {rule['clause']} »")
            if [t["id"] for t in have["tables"]] != [engine["datasets"][rule["dataset"]]]:
                differences.append(f"row-level rule {name} is not on the dataset it is for")
            if [r["name"] for r in have["roles"]] != [self.engine_role(rule["role"])]:
                differences.append(f"row-level rule {name} is not for the role it is for")
        # A rule for a role outside THIS audience may be one another dashboard over the same dataset needs:
        # it is a difference only when the policy no longer declares it at all (apply_access withdraws those).
        by_id = {d.id: d for d in datasets}
        for name in sorted(set(found) - set(wanted)):
            parts = name.split(":", 2)
            if len(parts) != 3 or not self.policy.declares_rule(parts[1], by_id[parts[2]]):
                differences.append(f"row-level rule {name} is not one SharpSight declares")
        return differences

    def probe_access(self, body: dict[str, Any], role: Optional[str]) -> dict[str, Any]:
        """ASK the BI engine, as an account that holds its base role and exactly one SharpSight role — or
        none — what it may open and what rows it gets.

        The account is the engine's own (`sharpsight-probe-<role>`), given a password nobody keeps for as
        long as the question takes, and switched off again. For a role, the rows the BI engine returns are
        compared with the rows the data gateway returns to that same role: two layers, one policy."""
        spec, engine = DashboardSpec(**body["spec"]), body["engine"]
        dashboard_id = engine["dashboard"]["id"]
        roles = [self.superset.find_role("Gamma")] + ([self.superset.ensure_role(self.engine_role(role))] if role else [])
        username, password = f"sharpsight-probe-{role or 'outsider'}", secrets.token_urlsafe(24)
        account = self.superset.ensure_account(username, roles, password, active=True)
        base = self.superset.base_url
        result: dict[str, Any] = {"account": username, "role": role, "dashboard": None, "data": {}, "rows": {},
                                  "direct": {}, "export": {"assets": {}, "data": {}}}
        try:
            session = sign_in(base, username, password)
            ask = lambda path: session.get(f"{base}{path}", timeout=60, allow_redirects=False).status_code  # noqa: E731
            result["dashboard"] = ask(f"/api/v1/dashboard/{dashboard_id}")
            charts = self.live_charts(engine)
            for dataset_id in spec.datasets():
                dataset = self.registry.dataset(dataset_id)
                component = next(c for c in spec.components if c.dataset == dataset_id)
                # the column the question is asked BY: one the role's policy speaks of, else a plain dimension
                policy = self.policy.role_row_policy(role, dataset) if role else None
                column = next((c for c in dataset.columns if policy and c in policy), None) or next(
                    (c for c in dataset.grain if dataset.columns[c].role == "dimension"), dataset.grain[0])
                question = lambda kind: session.post(f"{base}/api/v1/chart/data", timeout=60, json={  # noqa: E731
                    "datasource": {"id": engine["datasets"][dataset_id], "type": "table"}, "force": True,
                    # asked in the dashboard's name: a role granted a dashboard is granted its charts' data
                    "form_data": {"dashboardId": dashboard_id, "slice_id": charts[component.slot]["id"]},
                    "queries": [{"columns": [column], "metrics": ["count"], "filters": [], "orderby": [],
                                 "extras": {"where": "", "having": ""}, "row_limit": 1000}],
                    "result_format": kind, "result_type": "full"})
                answer = question("json")
                result["data"][dataset_id] = answer.status_code
                # the same question, asked for as a FILE: what « download as CSV » would hand over
                exported = question("csv")
                result["export"]["data"][dataset_id] = exported.status_code
                if role and answer.status_code == 200:
                    seen = sorted(str(row[column]) for row in answer.json()["result"][0]["data"])
                    expected = sorted(str(row[0]) for row in self.gateway.run(
                        f"select {sql_quote(column)}, count(*) from {dataset.relation} group by {sql_quote(column)}",
                        self.policy.identity_for_role(role, body["tenant"]),
                        purpose=f"what {role} may see of {dataset_id}").rows)
                    result["rows"][dataset_id] = {"by": column, "bi_engine": len(seen), "data_gateway": len(expected),
                                                  "same": seen == expected, "restricted": policy is not None,
                                                  "exported": (max(0, len(exported.text.strip().splitlines()) - 1)
                                                               if exported.status_code == 200 else None)}
            # Each thing the dashboard is made of, asked for by ITSELF — its own address, not the dashboard's.
            chart_id = next(iter(charts.values()))["id"]
            dataset_engine_id = next(iter(engine["datasets"].values()))
            result["direct"] = {
                "dashboard page": ask(f"/superset/dashboard/{engine['dashboard']['slug']}/"),
                "chart": ask(f"/api/v1/chart/{chart_id}"),
                "chart data": ask(f"/api/v1/chart/{chart_id}/data/"),
                "chart in the editor": ask(f"/api/v1/explore/?slice_id={chart_id}"),
                "dataset": ask(f"/api/v1/dataset/{dataset_engine_id}"),
                "SQL Lab": session.post(f"{base}/api/v1/sqllab/execute/", timeout=60, json={
                    "database_id": engine["database_id"], "sql": "select 1", "schema": "bi"}).status_code,
            }
            result["export"]["assets"] = {
                "dashboard": ask(f"/api/v1/dashboard/export/?q=!({dashboard_id})"),
                "chart": ask(f"/api/v1/chart/export/?q=!({chart_id})"),
                "dataset": ask(f"/api/v1/dataset/export/?q=!({dataset_engine_id})"),
            }
        finally:
            self.superset.set_account_active(account, False)
        return result

    def anonymous_access(self, body: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """The dashboard's addresses, asked for by nobody: no session at all. Each must end at the sign-in
        page or at a refusal."""
        engine, base = body["engine"], self.superset.base_url
        dashboard_id = engine["dashboard"]["id"]
        chart_id = next(iter(self.live_charts(engine).values()))["id"]
        asked: dict[str, dict[str, Any]] = {}
        for what, path in (("dashboard page", f"/superset/dashboard/{engine['dashboard']['slug']}/"),
                           ("dashboard", f"/api/v1/dashboard/{dashboard_id}"),
                           ("chart", f"/api/v1/chart/{chart_id}"),
                           ("chart data", f"/api/v1/chart/{chart_id}/data/"),
                           ("export", f"/api/v1/dashboard/export/?q=!({dashboard_id})")):
            answer = requests.get(base + path, timeout=30, allow_redirects=False)
            to_sign_in = answer.status_code in (301, 302, 303, 307, 308) and "/login" in answer.headers.get("Location", "")
            asked[what] = {"status": answer.status_code, "to_sign_in": to_sign_in,
                           "refused": to_sign_in or answer.status_code in (401, 403, 404)}
        return asked

    def export_problems(self, probe: dict[str, Any]) -> list[str]:
        """What the account took out of the BI engine that the export policy does not give it."""
        policy = self.registry.policies.get("security", {}).get("export", {})
        role, problems = probe["role"], []
        # the definition leaves through the .ssbi package: served to an account, whatever the policy says
        # of data, it is a way around the package — unless the policy names the audience for it too
        if policy.get("assets", "nobody") == "nobody" or role is None:
            problems += [f"the definition of the {what} was exported" for what, status in
                         probe["export"]["assets"].items() if status == 200]
        for dataset_id, status in probe["export"]["data"].items():
            rows = probe["rows"].get(dataset_id, {})
            if role is None or policy.get("data", "audience") != "audience":
                if status == 200:
                    problems.append(f"the data of {dataset_id} was exported as a file")
            elif status != 200:
                problems.append(f"the data of {dataset_id} could not be exported ({status}), which the policy allows")
            elif rows and rows.get("exported") != rows["data_gateway"]:
                problems.append(f"the file exported from {dataset_id} has {rows.get('exported')} values of "
                                f"{rows['by']}; the role's policy gives {rows['data_gateway']}")
        return problems

    @staticmethod
    def direct_problems(probe: dict[str, Any]) -> list[str]:
        """What the account reached by asking for it outright. Someone the dashboard is granted to reaches
        the dashboard's page — and nothing it is made of on its own: not the chart, not the dataset, not SQL."""
        return [f"{what} was served ({status})" for what, status in probe["direct"].items()
                if status == 200 and not (what == "dashboard page" and probe["role"] is not None)]

    @staticmethod
    def refused(probe: dict[str, Any]) -> bool:
        """The BI engine gave the account neither the dashboard nor any of its data."""
        return probe["dashboard"] in (401, 403, 404) and all(s in (401, 403, 404) for s in probe["data"].values())

    @staticmethod
    def served(probe: dict[str, Any]) -> bool:
        """The BI engine gave the account the dashboard, its data, and exactly the rows its role may see."""
        return (probe["dashboard"] == 200 and all(s == 200 for s in probe["data"].values())
                and all(r["same"] for r in probe["rows"].values()))

    @tool("get_dashboard_data", "Run every component's query through the BI engine and return what came back, "
          "with timings. Also probes each filter.")
    def get_dashboard_data(self, ctx: ToolContext, note: dict) -> dict[str, Any]:
        body, spec = self._valid_spec(ctx)
        engine = body.get("engine", {})
        data: dict[str, dict[str, Any]] = {}
        contexts = []
        for component in spec.components:
            dataset = self.registry.dataset(component.dataset)
            metric = self.registry.metric(component.metric)
            context = self.charts.query_context(component, dataset, metric, engine["datasets"][dataset.id])
            contexts.append(context)
            data[component.slot] = self.superset.chart_data(context)

        # Concurrency: that many people open the dashboard in the same instant. Every query is sent uncached
        # (the context forces it), so this is the warehouse under load, not the cache.
        users = int(self.registry.policies.get("performance", {}).get("concurrent_users", 0))
        concurrency = None
        if users > 1 and contexts:
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=users) as pool:
                answers = [answer for batch in pool.map(
                    lambda _: [self.superset.chart_data(c) for c in contexts], range(users)) for answer in batch]
            concurrency = {"users": users, "queries": len(answers),
                           "failed": sum(1 for a in answers if a["http_status"] != 200 or a["error"]),
                           "slowest_ms": max(a["duration_ms"] for a in answers),
                           "wall_ms": int((time.monotonic() - started) * 1000)}

        probes = []
        for column in dict.fromkeys(column for _, column, _ in self.dashboards.filter_uses(spec)):
            for dataset_id in spec.datasets():
                dataset = self.registry.dataset(dataset_id)
                if column not in dataset.columns:
                    continue
                component = next(c for c in spec.components if c.dataset == dataset_id)
                metric = self.registry.metric(component.metric)
                kpi = component.model_copy(update={"type": "big_number", "dimension": None})
                engine_id = engine["datasets"][dataset_id]
                baseline = self.superset.chart_data(self.charts.query_context(kpi, dataset, metric, engine_id))
                if dataset.columns[column].role == "time":
                    value: Any = "Last week"
                    context = self.charts.query_context(kpi, dataset, metric, engine_id, time_range=value)
                    distinct = 2
                else:
                    values = self._run_sql(ctx, self.sql.distinct_values(dataset, column, 50),
                                           f"values of {dataset_id}.{column} for the filter probe").records()
                    if not values:
                        continue
                    value, distinct = values[0]["value"], len(values)
                    context = self.charts.query_context(kpi, dataset, metric, engine_id,
                                                        [{"col": column, "op": "IN", "val": [value]}])
                probes.append({"filter": column, "dataset": dataset_id, "value": value, "baseline": baseline,
                               "filtered": self.superset.chart_data(context), "expect_change": distinct > 1})

        self.evidence.setdefault(body["project_id"], {}).update(data=data, probes=probes, concurrency=concurrency)
        note.update(datasets=spec.datasets(), metrics=spec.metrics(),
                    detail={"components": {slot: {"rows": o["rowcount"], "ms": o["duration_ms"],
                                                  "error": o["error"]} for slot, o in data.items()},
                            "filter_probes": len(probes), "concurrency": concurrency})
        return {"components": data, "filter_probes": probes, "concurrency": concurrency}

    @tool("render_dashboard_screenshot", "Open the dashboard in a headless browser at each of the template's "
          "viewports: a screenshot, each chart's geometry, and the requests the dashboard itself made.")
    def render_dashboard_screenshot(self, ctx: ToolContext, note: dict) -> list[Render]:
        body, spec = self._valid_spec(ctx)
        engine = body.get("engine", {})
        template = self.registry.template(spec.dashboard.template)
        out_dir = self.state_dir / "work" / body["project_id"] / "renders"
        charts = len(self.live_charts(engine))
        renders = [self.visual.render(engine["dashboard"]["id"], charts, out_dir, viewport.name, viewport.width)
                   for viewport in template.responsive.viewports]
        evidence = self.evidence.setdefault(body["project_id"], {})
        evidence["renders"] = renders

        # Once more, with every filter of the specification SET — to the values the probes found. What each
        # chart then asks for is compared with what it asked unfiltered: the only proof that the filters, as
        # configured on the dashboard, reach the charts they are scoped to.
        values = {p["filter"]: p["value"] for p in evidence.get("probes", [])}
        mask = self.dashboards.filter_state(spec, values)
        evidence["filtered_render"] = evidence["filters_applied"] = None
        if mask:
            evidence["filtered_render"] = self.visual.render(
                engine["dashboard"]["id"], charts, out_dir, "filtered", template.responsive.viewports[0].width,
                native_filters=mask)
            evidence["filters_applied"] = {column: values[column] for _, column, _ in self.dashboards.filter_uses(spec)
                                           if column in values}
        note["detail"] = {"renders": [{"viewport": r.viewport, "width": r.width, "load_ms": r.load_ms,
                                       "components": len(r.components), "requests": len(r.calls),
                                       "error": r.error} for r in renders],
                          "filtered_render": bool(mask), "filters_applied": evidence["filters_applied"]}
        note["visual_qa"] = {"renders": self._renders_for_audit(renders)}
        return renders

    @staticmethod
    def _renders_for_audit(renders: list[Render]) -> list[dict[str, Any]]:
        return [{"viewport": r.viewport, "width": r.width, "load_ms": r.load_ms, "components": len(r.components),
                 "screenshot": Path(r.screenshot).name if r.screenshot else None, "error": r.error} for r in renders]

    @tool("validate_dashboard", "The whole validation — data, SQL, BI, security, performance, visual — over the "
          "data last retrieved and the screenshots last rendered. With visual=false, everything that does not "
          "need a render.", ValidateArgs)
    def validate_dashboard(self, ctx: ToolContext, note: dict, visual: bool) -> ValidationReport:
        body, spec = self._valid_spec(ctx)
        engine = body.get("engine", {})
        template = self.registry.template(spec.dashboard.template)
        evidence = self.evidence.get(body["project_id"], {})
        data, probes = evidence.get("data", {}), evidence.get("probes", [])
        renders = evidence.get("renders", []) if visual else []
        datasets = [self.registry.dataset(ds) for ds in spec.datasets()]
        metrics_on: dict[str, list] = {}
        for component in spec.components:
            metric = self.registry.metric(component.metric)
            if metric not in metrics_on.setdefault(component.dataset, []):
                metrics_on[component.dataset].append(metric)

        report = ValidationReport()
        report.extend(self.dashboards.validate(spec, ctx.identity))
        report.extend(self.validation.data(
            datasets, metrics_on, lambda sql, purpose: self._run_sql(ctx, sql, purpose),
            readable={d.id: self.policy.readable_columns(ctx.identity, d) for d in datasets}))
        engine_datasets = {ds: self.superset.get_dataset(engine_id) for ds, engine_id in engine.get("datasets", {}).items()}
        report.extend(self.validation.bi(spec, template, engine_datasets, data, probes, renders))
        dashboard_id = engine["dashboard"]["id"]
        charts = self.live_charts(engine)
        report.extend(self.validation.dependencies(spec, engine_datasets, {
            "on_dashboard": [c["id"] for c in self.superset.dashboard_charts(dashboard_id)],
            "charts": charts, "connection": self._bi_database[0],
            "filters": [{"name": f["name"], "column": f["targets"][0]["column"]["name"],
                         "dataset_id": f["targets"][0]["datasetId"]}
                        for f in self.dashboards.native_filters(spec, engine["datasets"], charts, template)
                        if f["targets"] and f["targets"][0]]}))
        if visual:
            report.extend(self.validation.dashboard_filters(
                spec, self.live_charts(engine), renders, evidence.get("filtered_render"),
                evidence.get("filters_applied"), self.dashboards.reach(spec, template)))
        outsider = self.probe_access(body, None)
        self.audit.record(action="access_probe", user=ctx.identity.user, request_id=ctx.request_id, actor=ctx.actor,
                          ai_model=ctx.ai_model, project_id=ctx.project_id,
                          outcome="ok" if self.refused(outsider) else "error", detail=outsider)
        report.extend(self.validation.security(
            spec, ctx.identity, ctx.query_log, self.superset.find_database(self._bi_database[0]),
            access={"differences": self.access_differences(body), "granted": engine.get("access", {}),
                    "outsider": outsider, "outsider_refused": self.refused(outsider),
                    "export_policy": self.registry.policies.get("security", {}).get("export", {}),
                    "export_problems": self.export_problems(outsider),
                    "direct_problems": self.direct_problems(outsider),
                    "anonymous": self.anonymous_access(body)}))
        report.extend(self.validation.performance(data, renders, evidence.get("concurrency")))
        model_verdicts = {}
        if body.get("model_plan"):
            plan = ModelPlan(**body["model_plan"])
            text = self.dbt_agent.render_sql(plan, body["project_id"], ctx.request_id)
            model_verdicts[plan.name] = self.policy.validate_dbt_model(text, ctx.identity)
        report.extend(self.validation.sql_checks(ctx.query_log, ctx.identity, model_verdicts,
                                                 self.gateway.timeout_ms, self.gateway.max_rows))
        if visual and not renders:
            report.add("visual", "screenshot generated", "fail", "", "the dashboard has not been rendered",
                       code="render_failed")
        for render in renders:
            report.extend(self.visual.check(render, spec, template, charts, data,
                                            self.dashboards.section_titles(spec, template, charts)))
        if visual and renders:
            # the page is styled by the template's stylesheet and no other: a rule added by hand in the BI
            # engine is a template violation no screenshot measurement would name
            live = (self.superset.get_dashboard(dashboard_id) or {}).get("css") or ""
            same = live.strip() == self.dashboards.css(template, spec.theme.brand).strip()
            report.add("visual", "no template violation", "pass" if same else "fail", "stylesheet",
                       "the dashboard's stylesheet is the template's own, generated from design tokens" if same
                       else "the dashboard's stylesheet is not the one the template generates",
                       code=None if same else "template_violation")

        evidence["report"] = report
        note.update(datasets=spec.datasets(), metrics=spec.metrics(), validation=report.summary(),
                    detail={"failures": [f"{c.category} · {c.name} · {c.subject}: {c.detail}" for c in report.failures][:20]})
        if visual:
            seen = [c for c in report.checks if c.category == "visual"]
            line = lambda c: f"{c.name} · {c.subject}: {c.detail}"  # noqa: E731
            note["visual_qa"] = {"renders": self._renders_for_audit(renders), "checks": len(seen),
                                 "failed": [line(c) for c in seen if c.status == "fail"][:20],
                                 "warned": [line(c) for c in seen if c.status == "warn"][:20]}
        return report

    @tool("commit_project_version", "Commit the project's files to the BI projects repository.", CommitArgs)
    def commit_project_version(self, ctx: ToolContext, note: dict, message: str) -> dict[str, Any]:
        body = self.project(ctx)
        commit = self.git.commit_project(body["project_id"], message)
        note["detail"] = {"commit": commit, "repository": "bi-projects"}
        note["git_commit"] = commit
        return {"commit": commit}

    @tool("create_deployment_package", "Build the .ssbi package of the project's latest version: its folder "
          "exactly as committed, with a manifest giving each file's checksum. Only a version that passed "
          "validation and is awaiting review has one; it is built once, and every later gate checks that very "
          "file.")
    def create_deployment_package(self, ctx: ToolContext, note: dict) -> dict[str, Any]:
        body = self.project(ctx)
        if self.packager is None:
            raise ToolError("this service has no packager")
        package = self.packager(body["project_id"])
        note.update(git_commit=package.get("git_commit"),
                    deployment={"package": {k: package.get(k) for k in ("file", "sha256", "bytes", "files", "version")}},
                    detail={"exported_to": package.get("exported_to"), "export_error": package.get("export_error")})
        return package
