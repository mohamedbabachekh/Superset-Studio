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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

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
from .store import Store
from .superset_adapter import SupersetAdapter
from .validation_agent import ValidationAgent
from .visual_qa_agent import Render, VisualQaAgent


class ToolError(Exception):
    pass


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
                    datasets=note.get("datasets"), outcome="refused" if isinstance(exc, PolicyError) else "error",
                    detail={"error": str(exc)[:500]}, duration_ms=int((time.monotonic() - started) * 1000))
                raise
            self.audit.record(
                action=f"tool:{name}", user=ctx.identity.user, request_id=ctx.request_id, actor=ctx.actor,
                ai_model=ctx.ai_model, project_id=ctx.project_id, datasets=note.get("datasets"),
                sql=note.get("sql"), dbt_files=note.get("dbt_files"), superset_assets=note.get("superset_assets"),
                validation=note.get("validation"), detail=note.get("detail"),
                duration_ms=int((time.monotonic() - started) * 1000))
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

    def _run_sql(self, ctx: ToolContext, sql: str, purpose: str, limit: Optional[int] = None) -> QueryResult:
        """Every statement the engine runs, whichever tool asked for it, leaves its own audit record: the
        SQL, the datasets it read, how many rows came back — and never the rows."""
        common = dict(action="sql", user=ctx.identity.user, request_id=ctx.request_id, actor=ctx.actor,
                      ai_model=ctx.ai_model, project_id=ctx.project_id, sql=sql)
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
        return {"dashboard": dashboard, "charts": charts, "filters": placed["filters"],
                "datasets": self.project(ctx)["engine"]["datasets"]}

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
        metric = self.registry.metric(metric_id)
        if not any(self.policy.can_read(ctx.identity, self.registry.dataset(ds)) for ds in metric.lineage.datasets):
            raise PolicyError(f"{ctx.identity.user} has no access to the datasets behind {metric_id}", "dataset_denied")
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
            if any(self.policy.can_read(ctx.identity, self.registry.dataset(ds)) for ds in metric.lineage.datasets):
                found.append(self.semantic.describe(metric))
        return sorted(found, key=lambda m: (not m["certified"], m["id"]))

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
        note.update(dbt_files=files, detail={"model": plan.name, "branch": body["dbt"]["branch"], "commit": commit})
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
        report = self.dashboards.validate(spec, ctx.identity)
        body["spec"] = spec.model_dump()
        body["spec_valid"] = report.passed
        self.save_project(body)
        note.update(datasets=spec.datasets(), validation=report.summary(),
                    detail={"dashboard": spec.dashboard.id, "template": spec.dashboard.template,
                            "components": len(spec.components),
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
        payload = {"metrics": metrics, "description": dataset.description, "extra": json.dumps(certification),
                   "external_url": f"sharpsight://dataset/{dataset.id}"}
        if dataset.time_column:
            payload["main_dttm_col"] = dataset.time_column
        self.superset.update_dataset(engine_id, payload)

        body.setdefault("engine", {}).setdefault("datasets", {})[dataset.id] = engine_id
        body["engine"]["database_id"] = database_id
        self.save_project(body)
        note.update(datasets=[dataset.id],
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
            headline = self._run_sql(ctx, sql, f"headline value of {metric.id}").scalar()
        payload = self.charts.payload(
            component, dataset, metric, engine["datasets"][dataset.id], engine["dashboard"]["id"], template,
            spec.theme.color_scheme or template.theme.color_scheme,
            spec.theme.sequential_scheme or template.theme.sequential_scheme, body["project_id"], headline)

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
        note.update(datasets=[dataset.id],
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

        filters = self.dashboards.native_filters(spec, engine["datasets"], charts)
        self.superset.update_dashboard(engine["dashboard"]["id"], {
            "dashboard_title": spec.dashboard.title,
            "slug": spec.dashboard.id,
            "position_json": json.dumps(self.dashboards.layout(spec, template, charts)),
            "json_metadata": json.dumps(self.dashboards.json_metadata(spec, template, filters)),
            "css": self.dashboards.css(template),
        })
        self.save_project(body)
        note["superset_assets"] = [{"type": "dashboard", "id": engine["dashboard"]["id"], "action": "updated",
                                    "charts": len(charts), "filters": [f["name"] for f in filters],
                                    "template": f"{template.id}@{template.version}"}, *removed]
        return {"dashboard": engine["dashboard"], "charts": len(charts), "filters": len(filters)}

    @tool("get_dashboard_data", "Run every component's query through the BI engine and return what came back, "
          "with timings. Also probes each filter.")
    def get_dashboard_data(self, ctx: ToolContext, note: dict) -> dict[str, Any]:
        body, spec = self._valid_spec(ctx)
        engine = body.get("engine", {})
        data: dict[str, dict[str, Any]] = {}
        for component in spec.components:
            dataset = self.registry.dataset(component.dataset)
            metric = self.registry.metric(component.metric)
            context = self.charts.query_context(component, dataset, metric, engine["datasets"][dataset.id])
            data[component.slot] = self.superset.chart_data(context)

        probes = []
        for column in spec.filters:
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

        self.evidence.setdefault(body["project_id"], {}).update(data=data, probes=probes)
        note.update(datasets=spec.datasets(),
                    detail={"components": {slot: {"rows": o["rowcount"], "ms": o["duration_ms"],
                                                  "error": o["error"]} for slot, o in data.items()},
                            "filter_probes": len(probes)})
        return {"components": data, "filter_probes": probes}

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
            evidence["filters_applied"] = {column: values[column] for column in spec.filters if column in values}
        note["detail"] = {"renders": [{"viewport": r.viewport, "width": r.width, "load_ms": r.load_ms,
                                       "components": len(r.components), "requests": len(r.calls),
                                       "error": r.error} for r in renders],
                          "filtered_render": bool(mask), "filters_applied": evidence["filters_applied"]}
        return renders

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
        report.extend(self.validation.data(datasets, metrics_on,
                                           lambda sql, purpose: self._run_sql(ctx, sql, purpose)))
        engine_datasets = {ds: self.superset.get_dataset(engine_id) for ds, engine_id in engine.get("datasets", {}).items()}
        report.extend(self.validation.bi(spec, template, engine_datasets, data, probes, renders))
        if visual:
            report.extend(self.validation.dashboard_filters(
                spec, self.live_charts(engine), renders, evidence.get("filtered_render"),
                evidence.get("filters_applied")))
        report.extend(self.validation.security(spec, ctx.identity, ctx.query_log,
                                               self.superset.find_database(self._bi_database[0])))
        report.extend(self.validation.performance(data, renders))
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
            report.extend(self.visual.check(render, spec, template, self.live_charts(engine), data))

        evidence["report"] = report
        note.update(datasets=spec.datasets(), validation=report.summary(),
                    detail={"failures": [f"{c.category} · {c.name} · {c.subject}: {c.detail}" for c in report.failures][:20]})
        return report

    @tool("commit_project_version", "Commit the project's files to the BI projects repository.", CommitArgs)
    def commit_project_version(self, ctx: ToolContext, note: dict, message: str) -> dict[str, Any]:
        body = self.project(ctx)
        commit = self.git.commit_project(body["project_id"], message)
        note["detail"] = {"commit": commit, "repository": "bi-projects"}
        return {"commit": commit}
