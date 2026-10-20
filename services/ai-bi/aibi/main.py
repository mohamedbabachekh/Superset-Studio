"""The AI BI service's API — what the SharpSight portal calls.

WHO IS CALLING. In the target architecture the portal authenticates a person with Keycloak and calls this
service on their behalf. That portal is not part of this kit, so the same two facts arrive as two headers:

    Authorization: Bearer <AIBI_API_TOKEN>     the caller is the gateway (one shared secret, in .env)
    X-SharpSight-User: dev.user                the person it is acting for (registry/security/rbac.yaml)

The second header is trusted BECAUSE of the first, exactly as a gateway's identity header is — which is why
the port is published on loopback only and the token is never optional.

The interactive documentation page is off: it loads its script from a CDN, and this kit calls nobody.
The machine-readable description is at /openapi.json.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__, compatibility
from .audit import AuditLog
from .config import Settings, get_settings
from .config import targets as config_targets
from .copilot import Copilot, diff as spec_diff
from .data_gateway import DataGateway
from .deploy import Deployer
from .dbt_runner import DbtRunner, lineage_from_artifacts, semantic_layer
from .git_adapter import GitAdapter, GitError
from .layout import problems as layout_problems
from .layout import resolve as layout_in_force
from .layout import source as layout_source
from .lifecycle import Lifecycle, LifecycleError
from .llm import get_parser
from .people import People
from .models import HELD, ComponentSpec, DashboardSpec, RunRecord, utcnow
from .picture import PictureError, Pictures
from .pipeline import Pipeline
from .planner import new_steps
from .policy_engine import Identity, PolicyEngine, PolicyError
from .registry import Registry, RegistryError
from .store import Store
from .superset_adapter import SupersetAdapter, SupersetError
from .superset_view import SupersetView
from .tools import Toolbox, ToolContext, ToolError
from .visual_qa_agent import VisualQaAgent


class Service:
    def __init__(self, settings: Settings):
        self.settings = settings
        state = settings.state_dir
        self.registry = Registry(settings.registry_dir, generated_dir=state / "registry" / "datasets",
                                 prod_manifest=settings.dbt_prod_state_dir / "manifest.json")
        self.policy = PolicyEngine(self.registry)
        self.audit = AuditLog(state / "audit", settings.secrets())
        self.store = Store(state / "control-plane.sqlite")
        self.git = GitAdapter(state)
        self.superset = SupersetAdapter(settings.superset_url, settings.superset_user, settings.superset_password)
        self.tools = Toolbox(
            registry=self.registry, policy=self.policy, gateway=DataGateway(settings.reader_dsn, self.policy),
            dbt=DbtRunner(settings.dbt_env(), settings.dbt_prod_state_dir), git=self.git, superset=self.superset,
            store=self.store, audit=self.audit,
            visual=VisualQaAgent(settings.superset_url, settings.superset_user, settings.superset_password,
                                 self.registry.policies,
                                 {kind: v.engine_type for kind, v in self.registry.visualizations.items()}),
            state_dir=state, bi_database_name=settings.superset_database_name,
            bi_database_uri=settings.superset_database_uri, superset_public_url=settings.superset_public_url,
            dbt_dev_schema=settings.dbt_dev_schema)
        self.lifecycle = Lifecycle(self.tools, settings.exports_dir)
        # the Studio's tiles: one picture of the dashboard, cut per slot (aibi/picture.py)
        self.pictures = Pictures(self.tools.visual, state)
        # the Studio's copilot: a request in words, answered with a proposal and never with a change
        self.copilot = Copilot(self.registry, self.policy, self.tools.dashboards)
        # taking a version to another Superset — the « run » environment — and proving it arrived
        self.deployer = Deployer(self.store, self.audit, self.lifecycle, self.policy, self.registry)
        self.targets = config_targets()
        self.people = People(self.registry, self.policy, self.superset, self.audit, settings.superset_user)
        self.parser = get_parser(settings.llm_provider, self.registry)
        self.pipeline = Pipeline(self.tools, self.lifecycle, self.parser)
        # Superset is the interface: the engine's own records are shown there — tables, two dashboards, the
        # gate of each project as a tag (superset_view.py). A Superset session of its own: it runs beside a
        # generation, and a requests session is not shared between threads.
        self.view = SupersetView(
            self.registry, self.store, self.audit,
            SupersetAdapter(settings.superset_url, settings.superset_user, settings.superset_password),
            self.git.projects.path, settings.records_dsn,
            (settings.superset_database_name.replace("(read-only)", "records (read-only)"),
             settings.records_database_uri))
        # one generation at a time: each drives dbt and a browser, and two would also race on one dashboard
        self.busy = threading.Lock()
        self._compatibility: tuple[float, dict[str, Any]] = (0.0, {})

    def compatibility(self, fresh: bool = False) -> dict[str, Any]:
        """The matrix beside what is running. Asked of the BI engine and the warehouse at most every five
        minutes: /health is polled, and a version does not change between two polls."""
        asked_at, found = self._compatibility
        if fresh or not found or time.monotonic() - asked_at > 300:
            found = compatibility.report(self.registry.compatibility, __version__, compatibility.running(
                self.superset.version(), self.tools.gateway.server_version()))
            self._compatibility = (time.monotonic(), found)
        return found

    def bootstrap(self) -> None:
        commit = self.git.sync_governed_project(self.settings.dbt_project_dir)
        self.git.projects.ensure()
        nodes = lineage_from_artifacts(self.settings.dbt_prod_state_dir, "prod")
        self.store.save_lineage(nodes)
        self.dbt_semantic_layer = semantic_layer(self.settings.dbt_prod_state_dir,
                                                 [m.key for m in self.registry.metrics.values()])
        self.audit.record(action="service:start", user="system", request_id="-", actor="system",
                          ai_model=self.parser.name,
                          git_commit=commit,
                          detail={"version": __version__, "dbt_project_commit": commit, "dbt_models": len(nodes),
                                  "untested_components": self.compatibility(fresh=True)["untested"],
                                  "dbt_semantic_layer": self.dbt_semantic_layer,
                                  "datasets": len(self.registry.datasets), "metrics": len(self.registry.metrics),
                                  "templates": len(self.registry.templates)})


service: Optional[Service] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global service
    service = Service(get_settings())
    service.bootstrap()
    service.view.watch()
    yield


app = FastAPI(title="SharpSight AI BI", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)


@app.exception_handler(PolicyError)
async def _policy(_, exc: PolicyError):
    return JSONResponse(status_code=403, content={"error": str(exc), "code": exc.code})


@app.exception_handler(RegistryError)
async def _registry(_, exc: RegistryError):
    return JSONResponse(status_code=404, content={"error": str(exc)})


@app.exception_handler(LifecycleError)
async def _lifecycle(_, exc: LifecycleError):
    return JSONResponse(status_code=409, content={"error": str(exc)})


@app.exception_handler(ToolError)
async def _tool(_, exc: ToolError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(SupersetError)
async def _superset(_, exc: SupersetError):
    return JSONResponse(status_code=502, content={"error": f"the BI engine answered: {exc}"})


# What SUPERSET may ask. The extension's backend runs inside Superset and names the person signed in there: for
# that it holds a token — its own, not the gateway's. With it the engine answers these four calls and refuses
# every other, whoever is named. So « nothing in Superset approves » does not rest on the extension's source: a
# fault in it, another extension, Superset itself — none of them can approve, restore or read the audit trail
# with the only secret Superset has. The rule that PROD needs a second person is the engine's to hold.
# What the gateway's own token may ask for — the token the extension inside Superset carries, for the person
# signed in there. Everything a developer DOES in the Studio is here; nothing that approves, promotes,
# rejects or restores is, so the rule that PROD needs a second person cannot be gone round from Superset.
SUPERSET_MAY = frozenset({
    ("GET", "/projects"), ("GET", "/projects/{project_id}/changes"),
    ("POST", "/projects/{project_id}/commit"), ("GET", "/runs/{run_id}"),
    # the Studio: what it draws, the draft it keeps, and the tiles it shows
    ("GET", "/projects/{project_id}/studio"), ("PUT", "/projects/{project_id}/draft"),
    ("DELETE", "/projects/{project_id}/draft"), ("POST", "/projects/{project_id}/draft/validate"),
    ("POST", "/projects/{project_id}/draft/commit"), ("POST", "/projects/{project_id}/pictures"),
    ("GET", "/projects/{project_id}/picture/{slot}"),
    # the copilot and the gallery: both answer with a proposal and change nothing by themselves
    ("POST", "/projects/{project_id}/propose"), ("POST", "/projects/{project_id}/compose"),
    ("POST", "/projects/{project_id}/alter"),
    # what a run found, last time: the levels of validation only a run can produce
    ("GET", "/projects/{project_id}/report"), ("GET", "/datasets/{dataset_id}/lineage"),
    # the immutable package a version was cut as (§47.2 « Export Package »): reading it is not promoting it
    ("GET", "/projects/{project_id}/versions/{version}/package"),
    # where a version may go, where it has been, and taking it there — never approving it to go
    ("GET", "/targets"), ("GET", "/projects/{project_id}/deployments"),
    ("POST", "/projects/{project_id}/versions/{version}/deploy"),
    # the explorer's governed lists (the brief's section 7): read-only, and filtered to this person
    ("GET", "/datasets"), ("GET", "/metrics"), ("GET", "/templates"),
})


def superset_may(method: str, route: str) -> bool:
    return (method.upper(), route) in SUPERSET_MAY


def _same(presented: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(presented.encode(), expected.encode())


def caller(request: Request, authorization: str = Header(default=""),
           x_sharpsight_user: str = Header(default="")) -> Identity:
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if _same(token, service.settings.api_token):
        request.state.via = "command line"
        return service.policy.identify(x_sharpsight_user)
    if _same(token, service.settings.superset_gateway_token):
        request.state.via = "Superset"
        route = getattr(request.scope.get("route"), "path", "")
        if not superset_may(request.method, route):
            raise HTTPException(status_code=403, detail=(
                "this token is Superset's: with it the engine answers for the projects, the Studio, a validation, "
                "a commit and a run, and for nothing else — this is asked from the command line, with the "
                "gateway's own token"))
        return service.policy.identify(x_sharpsight_user)
    raise HTTPException(status_code=401, detail="this API is called by the SharpSight gateway, with its token")


def reader(identity: Identity = Depends(caller)) -> Identity:
    service.policy.require(identity, "bi.read")
    return identity


def _project(project_id: str, identity: Identity) -> dict[str, Any]:
    body = service.store.get_project(project_id)
    if body is None or body.get("tenant") != identity.tenant:
        raise HTTPException(status_code=404, detail=f"no BI project {project_id}")
    return body


# ── health ──────────────────────────────────────────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict[str, Any]:
    checks = {"bi_engine": service.superset.health()}
    try:
        checks["warehouse"] = service.tools.gateway.ping()
    except Exception:  # noqa: BLE001
        checks["warehouse"] = False
    return {"status": "ok" if all(checks.values()) else "degraded", "version": __version__, **checks,
            "ai_model": service.parser.name,
            "registry": {"datasets": len(service.registry.datasets), "metrics": len(service.registry.metrics),
                         "templates": len(service.registry.templates)},
            "dbt_semantic_layer": service.dbt_semantic_layer,
            # when the engine's records were last copied to where Superset draws them, and how many — and
            # why not, if the last attempt failed (it is tried again within a minute)
            "shown_in_superset": {**service.view.last, "error": service.view.error},
            # running against versions nobody verified is not a failure, and is not hidden either
            "untested_components": service.compatibility()["untested"]}


@app.get("/compatibility")
def compatibility_matrix(identity: Identity = Depends(reader)) -> Any:
    """The compatibility matrix, with the version of each component as it is running now."""
    return service.compatibility(fresh=True)


# ── registries ──────────────────────────────────────────────────────────────────────────────────────────────
def _context(identity: Identity, project_id: Optional[str] = None, actor: str = "human") -> ToolContext:
    return ToolContext(identity=identity, request_id=str(uuid.uuid4()), ai_model=service.parser.name,
                       project_id=project_id, actor=actor)


@app.get("/datasets")
def datasets(identity: Identity = Depends(reader)) -> Any:
    return service.tools.list_datasets(_context(identity))


@app.get("/datasets/{dataset_id}")
def dataset(dataset_id: str, identity: Identity = Depends(reader)) -> Any:
    ctx = _context(identity)
    return {**service.tools.get_dataset(ctx, dataset_id=dataset_id),
            **service.tools.get_dataset_schema(ctx, dataset_id=dataset_id)}


@app.get("/datasets/{dataset_id}/lineage")
def dataset_lineage(dataset_id: str, identity: Identity = Depends(reader)) -> Any:
    found = service.policy.require_dataset(identity, dataset_id)
    seen: dict[str, Any] = {}

    def walk(model_name: str, unique_id: Optional[str] = None) -> None:
        node = service.store.lineage_by_id(unique_id) if unique_id else service.store.lineage_for(model_name)
        if node is None or node["unique_id"] in seen:
            return
        seen[node["unique_id"]] = {k: node[k] for k in ("name", "resource_type", "environment", "relation",
                                                        "materialized", "path", "tests", "depends_on")}
        for parent in node["depends_on"]:
            walk(parent["name"], parent["unique_id"])

    walk(found.source_model)
    return {"dataset": found.id, "model": found.source_model, "derived_from": found.derived_from,
            "metrics": [m.id for m in service.registry.metrics.values() if service.tools.semantic.valid_on(m, found)[0]],
            "nodes": list(seen.values())}


@app.get("/metrics")
def metrics(certified_only: bool = False, identity: Identity = Depends(reader)) -> Any:
    return service.tools.list_metrics(_context(identity), certified_only=certified_only)


@app.get("/dimensions/{name}")
def dimension(name: str, dataset_id: Optional[str] = None, identity: Identity = Depends(reader)) -> Any:
    return service.tools.get_dimension(_context(identity), dimension=name, dataset_id=dataset_id)


@app.get("/templates")
def templates(identity: Identity = Depends(reader)) -> Any:
    return service.tools.list_dashboard_templates(_context(identity))


@app.get("/templates/{template_id}")
def template(template_id: str, identity: Identity = Depends(reader)) -> Any:
    return service.tools.get_dashboard_template(_context(identity), template_id=template_id)


# ── tools ───────────────────────────────────────────────────────────────────────────────────────────────────
class ToolCall(BaseModel):
    project_id: Optional[str] = None
    arguments: dict[str, Any] = Field(default_factory=dict)


@app.get("/tools")
def tools(identity: Identity = Depends(reader)) -> Any:
    return [{"name": spec.name, "description": spec.description, "needs": spec.capability,
             "arguments": spec.arguments.model_json_schema()} for spec in Toolbox.specs()]


@app.post("/tools/{name}")
def call_tool(name: str, call: ToolCall, identity: Identity = Depends(caller)) -> Any:
    return service.tools.call(name, _context(identity, call.project_id), call.arguments)


# ── generation ──────────────────────────────────────────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=4000)
    wait: bool = False


@app.post("/projects/generate")
def generate(request: GenerateRequest, response: Response, identity: Identity = Depends(caller)) -> Any:
    service.policy.require(identity, "bi.generate")
    if not service.busy.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a generation is already running; this kit runs one at a time")
    run = RunRecord(id=uuid.uuid4().hex[:12], request_id=str(uuid.uuid4()), user=identity.user,
                    prompt=request.prompt, ai_model=service.parser.name, steps=new_steps())
    service.store.save_run(run)

    def work() -> None:
        try:
            service.pipeline.execute(run, identity)
        finally:
            service.busy.release()

    if request.wait:
        work()
        return service.store.get_run(run.id)
    threading.Thread(target=work, name=f"run-{run.id}", daemon=True).start()
    response.status_code = 202
    return {"run_id": run.id, "request_id": run.request_id, "status": "running"}


@app.get("/runs/{run_id}")
def get_run(run_id: str, identity: Identity = Depends(reader)) -> Any:
    run = service.store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run {run_id}")
    return run


@app.get("/runs")
def list_runs(project_id: Optional[str] = None, identity: Identity = Depends(reader)) -> Any:
    return [{"id": r.id, "project_id": r.project_id, "status": r.status, "quality_gate": r.quality_gate,
             "user": r.user, "version": r.version, "started_at": r.started_at, "ended_at": r.ended_at,
             "error": r.error}
            for r in service.store.list_runs(project_id)]


# ── what a person edited in Superset: validated, and committed as a version ────────────────────────────────
# Superset is where a chart is edited — its own Explore, not a screen of ours. What a person changed there is
# read back in the specification's words (ChartAgent.read_back). `validate` says what would be taken, what
# cannot be, and what a specification has no place for; `commit` takes it and sends the project down the same
# road as a generated one — validation, the BI engine, visual QA, a version, a git commit, a package, review.
def _edited(project: dict[str, Any]) -> dict[str, Any]:
    project_id = project["project_id"]
    if not project.get("spec") or not (project.get("engine") or {}).get("dashboard"):
        raise LifecycleError(f"{project_id} has no dashboard in Superset to read")
    latest = service.store.latest_version(project_id)
    charts = service.tools.superset_changes(project)
    takeable = [c for c in charts if c["changed"] and not c["refused"]]
    refused = [c for c in charts if c["refused"]]
    set_back = [c for c in charts if c["not_carried"]]
    on_dashboard = service.tools.dashboard_differences(project)
    drift = service.lifecycle.drift(latest) if latest else None
    why_not = None
    if latest and latest.status in HELD:
        why_not = (f"{project_id} v{latest.version} is {latest.status}: this kit has one BI engine, so a commit would "
                   "rewrite the promoted dashboard in place")
    elif refused:
        why_not = "Superset holds what cannot be a governed chart — " + "; ".join(
            f"{c['title']} ({c['slot']}): {', '.join(c['refused'])}" for c in refused)
    elif not takeable:
        why_not = ("no chart was changed in Superset in a way a specification carries" + (
            "; what was changed is on the dashboard itself — " + "; ".join(on_dashboard) + " — and a specification "
            "has no place for it: restore the version to put it back" if on_dashboard else
            f"; the dashboard still differs from version {latest.version} ({drift}) by a setting a specification "
            "has no place for: restore the version to put it back" if drift else
            f": Superset shows version {latest.version} as validated" if latest else ""))
    return {"project_id": project_id, "version": latest.version if latest else None,
            "gate": latest.status if latest else (project.get("attempt") or {}).get("status"),
            "dashboard": project["engine"]["dashboard"].get("url"),
            "same_as_validated": drift is None if latest else None, "difference": drift,
            "charts": [{k: c[k] for k in ("slot", "chart_id", "title", "changed", "refused", "not_carried", "in_step")}
                       for c in charts],
            # the dashboard itself, as its editor left it: a commit applies the specification again, and undoes these
            "on_the_dashboard": on_dashboard,
            "would_take": [c["slot"] for c in takeable], "would_set_back": [c["slot"] for c in set_back],
            "can_commit": why_not is None, "why_not": why_not, "_adopted": {c["slot"]: c["adopted"] for c in takeable}}


@app.get("/projects/{project_id}/changes")
def project_changes(project_id: str, identity: Identity = Depends(reader)) -> Any:
    found = _edited(_project(project_id, identity))
    found.pop("_adopted")
    return found


class Commit(BaseModel):
    comment: str = Field(default="", max_length=500)


@app.post("/projects/{project_id}/commit")
def project_commit(project_id: str, body: Commit, response: Response, identity: Identity = Depends(caller)) -> Any:
    service.policy.require(identity, "bi.generate")
    project = _project(project_id, identity)
    found = _edited(project)
    if not found["can_commit"]:
        raise LifecycleError("nothing was committed: " + found["why_not"])
    spec = DashboardSpec(**project["spec"])
    spec.components = [ComponentSpec(**found["_adopted"][c.slot]) if c.slot in found["_adopted"] else c
                       for c in spec.components]
    if not service.busy.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a generation is already running; this kit runs one at a time")
    taken = {c["slot"]: c["changed"] for c in found["charts"] if c["slot"] in found["would_take"]}
    run = RunRecord(id=uuid.uuid4().hex[:12], request_id=str(uuid.uuid4()), user=identity.user, project_id=project_id,
                    prompt=(f"Commit of what was edited in Superset for {project_id} ({', '.join(sorted(taken))})"
                            + (f": {body.comment}" if body.comment else "")),
                    ai_model=service.parser.name, steps=new_steps())
    service.store.save_run(run)
    service.audit.record(action="commit:edited_in_superset", user=identity.user, request_id=run.request_id,
                         actor="human", project_id=project_id, metrics=spec.metrics(),
                         detail={"taken": taken, "comment": body.comment, "from_version": found["version"],
                                 "set_back": {c["slot"]: c["not_carried"] for c in found["charts"] if c["not_carried"]},
                                 "undone_on_the_dashboard": found["on_the_dashboard"]})

    def work() -> None:
        try:
            service.pipeline.execute(run, identity, edited=(project_id, spec))
        finally:
            service.busy.release()

    threading.Thread(target=work, name=f"run-{run.id}", daemon=True).start()
    response.status_code = 202
    return {"run_id": run.id, "request_id": run.request_id, "status": "running", "taken": sorted(taken),
            "set_back": found["would_set_back"], "undone_on_the_dashboard": found["on_the_dashboard"]}


# ── the Studio ──────────────────────────────────────────────────────────────────────────────────────────────
# The workspace of the brief's section 47, served to a page inside Superset (superset-addon/sharpsight_bi.py).
# It adds one state to this service: a DRAFT — the specification a person is editing, which is neither the
# committed version nor what Superset holds (§50.2). A draft is kept with its project, carries a token so two
# people cannot overwrite each other silently (§50.3), and becomes a version through the SAME road as every
# other commit: the pipeline, its ten gates, visual QA, git, a package.


def _token(draft: Optional[dict[str, Any]]) -> str:
    """What a save must present to be accepted: a fingerprint of the draft it is a change to."""
    if not draft:
        return "none"
    return hashlib.sha256(json.dumps(draft.get("spec"), sort_keys=True).encode()).hexdigest()[:16]


def _studio_spec(project: dict[str, Any]) -> tuple[DashboardSpec, bool]:
    """What the Studio shows: the draft when there is one, the project's own specification otherwise."""
    draft = project.get("draft")
    if draft:
        return DashboardSpec(**draft["spec"]), True
    return DashboardSpec(**project["spec"]), False


def _filter_options(spec: DashboardSpec, template: Any, identity: Identity) -> list[dict[str, Any]]:
    """The columns a filter may be put on. A filter reaches charts, so it is offered only on a dataset the
    dashboard already uses; the template says which ROLES of column may be filtered on and how many filters
    it allows; the registry says which columns this person may read at all."""
    offered: dict[str, dict[str, Any]] = {}
    for dataset_id in spec.datasets():
        dataset = service.registry.datasets.get(dataset_id)
        if dataset is None or not service.policy.can_read(identity, dataset):
            continue
        readable = service.policy.readable_columns(identity, dataset)
        for name, column in dataset.columns.items():
            if name not in readable or column.role not in template.filters.allowed_roles:
                continue
            entry = offered.setdefault(name, {"column": name, "role": column.role, "label": column.label or name,
                                              "datasets": [], "used": name in spec.filters})
            entry["datasets"].append(dataset.id)
    # a filter that does not reach every chart is a warning at validation, so say it here instead of there
    for entry in offered.values():
        entry["reaches_every_chart"] = len(entry["datasets"]) == len(spec.datasets())
    return sorted(offered.values(), key=lambda entry: (not entry["used"], entry["column"]))


def _security(spec: DashboardSpec, identity: Identity) -> dict[str, Any]:
    datasets = [service.registry.datasets[d] for d in spec.datasets() if d in service.registry.datasets]
    metrics = [service.registry.metrics[m] for m in spec.metrics() if m in service.registry.metrics]
    rules = service.policy.row_rules(datasets, metrics)
    return {"audience": service.policy.audience(datasets, metrics),
            "row_rules": rules,
            "classifications": sorted({m.classification for m in metrics}),
            "uncertified": sorted({m.id for m in metrics if not m.certified}
                                  | {d.id for d in datasets if not d.certified})}


def _studio_payload(project: dict[str, Any], identity: Identity) -> dict[str, Any]:
    spec, from_draft = _studio_spec(project)
    template = service.registry.template(spec.dashboard.template)
    latest = service.store.latest_version(project["project_id"])
    engine = project.get("engine") or {}
    charts = {slot: chart for slot, chart in (engine.get("charts") or {}).items() if chart.get("id")}
    rows = layout_in_force(spec, template)
    governed = []
    for component in spec.components:
        metric = service.registry.metrics.get(component.metric)
        dataset = service.registry.datasets.get(component.dataset or "")
        slot = template.slots.get(component.slot)
        governed.append({
            "slot": component.slot, "type": component.type, "metric": component.metric,
            "dimension": component.dimension, "dataset": component.dataset, "title": component.title,
            "description": component.description, "options": component.options.model_dump(exclude_none=True),
            "section": template.section_of(component.slot),
            "purpose": slot.purpose if slot else None, "accepts": slot.accepts if slot else [],
            "mandatory": bool(slot and slot.mandatory),
            "chart_id": (charts.get(component.slot) or {}).get("id"),
            # what the governance drawer of the brief's section 19 shows, for this chart
            "governance": {
                "metric_certified": bool(metric and metric.certified),
                "metric_version": metric.version if metric else None,
                "metric_owner": metric.owner if metric else None,
                "metric_definition": metric.documentation if metric else None,
                "dataset_certified": bool(dataset and dataset.certified),
                "dataset_owner": dataset.owner if dataset else None,
                "dataset_domain": dataset.domain if dataset else None,
                "dataset_grain": dataset.grain if dataset else None,
            }})
    return {
        "project": {"project_id": project["project_id"], "title": project.get("title"),
                    "template": spec.dashboard.template, "created_by": project.get("created_by"),
                    "dashboard_title": spec.dashboard.title, "description": spec.dashboard.description,
                    "owner": spec.dashboard.owner,
                    "version": latest.version if latest else None,
                    "gate": latest.status if latest else (project.get("attempt") or {}).get("status"),
                    "environment": latest.state if latest else "DEV",
                    "held": bool(latest and latest.status in HELD),
                    "dashboard": (engine.get("dashboard") or {}).get("url"),
                    "may_edit": "bi.generate" in identity.capabilities},
        # the template's constraints, which are what the canvas is allowed to rearrange
        "template": {"id": template.id, "version": template.version, "name": template.template.name,
                     "grid": template.grid.model_dump(), "rows": [r.model_dump() for r in template.layout],
                     "slots": {name: body.model_dump() for name, body in template.slots.items()},
                     "sections": [s.model_dump() for s in template.section_list()],
                     "min_slot_width": template.responsive.min_slot_width,
                     "viewports": [v.model_dump() for v in template.responsive.viewports],
                     "filters": template.filters.model_dump()},
        # the specification itself: what the workspace edits and sends back as a draft. Everything below is
        # read from it, and is here so the page does not have to derive what the engine already knows.
        "spec": spec.model_dump(exclude_none=True),
        "layout": [r.model_dump() for r in rows],
        "layout_of": layout_source(spec),
        "components": governed,
        "filters": spec.filters,
        "sections": [s.model_dump(exclude_none=True) for s in spec.sections],
        "theme": spec.theme.model_dump(exclude_none=True),
        "editing": "draft" if from_draft else "version",
        "draft": {k: project["draft"][k] for k in ("saved_at", "saved_by")} if project.get("draft") else None,
        "token": _token(project.get("draft")),
        "problems": layout_problems(spec, template),
        "pictures": service.pictures.taken(project["project_id"]),
        # what the draft changes about the committed specification — the brief's review panel (§21)
        "diff": spec_diff(DashboardSpec(**project["spec"]), spec, template) if from_draft else [],
        # the versions this project has, and where each stands: the release panel (§22). Promotion is not
        # here and is not meant to be: PROD needs a second person, on the command line.
        "versions": [{"version": v.version, "status": v.status, "state": v.state, "created_at": v.created_at,
                      "created_by": v.created_by, "title": v.title,
                      "git_commit": v.git_commit, "package": (v.package or {}).get("name"),
                      "validation": (v.validation or {}).get("summary") or {}}
                     for v in service.store.list_versions(project["project_id"])],
        "ask": service.copilot.examples(spec, template),
        # the component library (§47.3): what may be drawn, where each may go, and what is approved but not
        # installed in this BI engine — a library that offered a visualization the engine cannot draw would
        # be offering a refusal
        "visualizations": [
            {"id": name, "status": body.status, "needs": body.needs,
             "slots": [slot for slot, defined in template.slots.items() if name in defined.accepts],
             "purposes": sorted({template.slots[slot].purpose for slot in template.slots
                                 if name in template.slots[slot].accepts})}
            for name, body in service.registry.visualizations.items()],
        # the filters this dashboard may carry: a column of a dataset it already uses, in a role the
        # template allows, that this person may read (§47.5, « for a dashboard: filters »)
        "filter_options": _filter_options(spec, template, identity),
        # who may open the dashboard this becomes, and the row-level rules that follow it — SharpSight's
        # decision, enforced in Superset when a version is built (§19 « Security »)
        "security": _security(spec, identity),
        # the free slots a chart may be added to, with what each accepts: the gallery of §48.3
        "free_slots": [{"slot": slot, "purpose": template.slots[slot].purpose,
                        "accepts": template.slots[slot].accepts,
                        "mandatory": template.slots[slot].mandatory,
                        "section": template.section_of(slot)}
                       for slot in service.copilot._free(spec, template)],
    }


@app.get("/projects/{project_id}/studio")
def studio(project_id: str, identity: Identity = Depends(reader)) -> Any:
    """Everything the workspace draws, in one call: the project, its gate, the template's constraints, the
    layout in force, each chart with its governance, the draft if there is one, and when the tiles were cut."""
    project = _project(project_id, identity)
    if not project.get("spec"):
        raise LifecycleError(f"{project_id} has no specification yet: a project starts with a request")
    return _studio_payload(project, identity)


class Draft(BaseModel):
    spec: dict[str, Any]
    # the token the page was given: a save against an older draft is refused, not merged silently
    token: str = Field(default="none", max_length=32)


@app.put("/projects/{project_id}/draft")
def save_draft(project_id: str, body: Draft, identity: Identity = Depends(caller)) -> Any:
    """Keep what a person is editing. It changes nothing in Superset and commits nothing."""
    service.policy.require(identity, "bi.generate")
    project = _project(project_id, identity)
    held = _token(project.get("draft"))
    if body.token != held:
        raise HTTPException(status_code=409, detail=(
            "this draft was saved by someone else since the page was opened: reload it, or compare the two. "
            f"The page has {body.token}, the project has {held}."))
    try:
        spec = DashboardSpec(**body.spec)
    except Exception as exc:  # noqa: BLE001 — a malformed draft is an answer, not a crash
        raise HTTPException(status_code=422, detail=f"this is not a specification: {str(exc)[:500]}") from exc
    template = service.registry.template(spec.dashboard.template)
    if spec.dashboard.id != DashboardSpec(**project["spec"]).dashboard.id:
        raise HTTPException(status_code=422, detail="a draft is a change to this project's dashboard, not another")
    project["draft"] = {"spec": spec.model_dump(exclude_none=True), "saved_at": utcnow(), "saved_by": identity.user}
    service.store.save_project(project_id, project, utcnow())
    service.audit.record(action="draft:save", user=identity.user, request_id=str(uuid.uuid4()), actor="human",
                         project_id=project_id, metrics=spec.metrics(),
                         detail={"layout_of": layout_source(spec), "rows": len(spec.layout),
                                 "components": len(spec.components)})
    return {"token": _token(project["draft"]), "saved_at": project["draft"]["saved_at"],
            "problems": layout_problems(spec, template), "layout_of": layout_source(spec)}


@app.delete("/projects/{project_id}/draft")
def discard_draft(project_id: str, identity: Identity = Depends(caller)) -> Any:
    """Throw the draft away. The committed version and what Superset holds are untouched."""
    service.policy.require(identity, "bi.generate")
    project = _project(project_id, identity)
    had = project.pop("draft", None)
    if had:
        service.store.save_project(project_id, project, utcnow())
        service.audit.record(action="draft:discard", user=identity.user, request_id=str(uuid.uuid4()),
                             actor="human", project_id=project_id, detail={"saved_at": had.get("saved_at")})
    return {"discarded": bool(had), "token": "none"}


@app.post("/projects/{project_id}/draft/validate")
def validate_draft(project_id: str, identity: Identity = Depends(reader)) -> Any:
    """Level 1 of the brief's validation (§53.1), on the draft, without touching Superset: the layout, the
    slots, the visualizations, the metrics, the datasets, this person's clearance for each."""
    project = _project(project_id, identity)
    spec, from_draft = _studio_spec(project)
    report = service.tools.dashboards.validate(spec, identity)
    return {"of": "draft" if from_draft else "version", "summary": report.summary(),
            "checks": [c.model_dump(exclude_none=True) for c in report.checks],
            "problems": layout_problems(spec, service.registry.template(spec.dashboard.template))}


@app.post("/projects/{project_id}/draft/commit")
def commit_draft(project_id: str, body: Commit, response: Response, identity: Identity = Depends(caller)) -> Any:
    """Make the draft a version — through the same road as any other commit: the pipeline's ten gates, the
    BI engine, visual QA, git, a package. Nothing here is a shortcut past them."""
    service.policy.require(identity, "bi.generate")
    project = _project(project_id, identity)
    if not project.get("draft"):
        raise LifecycleError(f"{project_id} has no draft to commit")
    spec = DashboardSpec(**project["draft"]["spec"])
    template = service.registry.template(spec.dashboard.template)
    placing = layout_problems(spec, template)
    if placing:
        raise LifecycleError("the draft's layout is not one the template allows: " + "; ".join(placing))
    latest = service.store.latest_version(project_id)
    if latest and latest.status in HELD:
        raise LifecycleError(f"{project_id} v{latest.version} is {latest.status}: this kit has one BI engine, so a "
                             "commit would rewrite the promoted dashboard in place")
    if not service.busy.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a generation is already running; this kit runs one at a time")
    run = RunRecord(id=uuid.uuid4().hex[:12], request_id=str(uuid.uuid4()), user=identity.user, project_id=project_id,
                    prompt=(f"Commit of the Studio draft of {project_id}"
                            + (f": {body.comment}" if body.comment else "")),
                    ai_model=service.parser.name, steps=new_steps())
    service.store.save_run(run)
    service.audit.record(action="commit:studio_draft", user=identity.user, request_id=run.request_id, actor="human",
                         project_id=project_id, metrics=spec.metrics(),
                         detail={"comment": body.comment, "from_version": latest.version if latest else None,
                                 "layout_of": layout_source(spec), "saved_at": project["draft"]["saved_at"],
                                 "saved_by": project["draft"]["saved_by"]})

    def work() -> None:
        try:
            done = service.pipeline.execute(run, identity, edited=(project_id, spec))
            if done.status == "succeeded":
                # the draft became the version: what is left to edit is the version itself
                body_now = service.store.get_project(project_id) or {}
                if body_now.pop("draft", None) is not None:
                    service.store.save_project(project_id, body_now, utcnow())
        finally:
            service.busy.release()

    threading.Thread(target=work, name=f"run-{run.id}", daemon=True).start()
    response.status_code = 202
    return {"run_id": run.id, "request_id": run.request_id, "status": "running", "layout_of": layout_source(spec)}


class Request(BaseModel):
    request: str = Field(default="", max_length=500)


class Compose(BaseModel):
    slot: str = Field(max_length=40)
    metric: str = Field(max_length=120)
    type: Optional[str] = Field(default=None, max_length=40)
    dimension: Optional[str] = Field(default=None, max_length=120)
    time_grain: Optional[Literal["P1D", "P1W", "P1M"]] = None


def _proposal(project: dict[str, Any], identity: Identity, make) -> Any:  # noqa: ANN001
    spec, _ = _studio_spec(project)
    template = service.registry.template(spec.dashboard.template)
    proposal = make(spec, template)
    service.audit.record(action="copilot:propose", user=identity.user, request_id=str(uuid.uuid4()),
                         actor="ai", ai_model=proposal.read_by, project_id=project["project_id"],
                         metrics=proposal.touches.get("metrics", []),
                         outcome="refused" if proposal.unresolved and not proposal.changes else "ok",
                         detail={"request": proposal.request, "understood": proposal.understood,
                                 "changes": [c.says for c in proposal.changes],
                                 "unresolved": proposal.unresolved})
    return proposal


@app.post("/projects/{project_id}/propose")
def propose(project_id: str, body: Request, identity: Identity = Depends(caller)) -> Any:
    """A request in words, answered with a proposal: what it would change, what it touches, whether it would
    pass validation — and the specification as it would be. Nothing is saved. Applying it is the Studio
    sending that specification back as a draft, through the same route as any other change (§47.6)."""
    service.policy.require(identity, "bi.generate")
    project = _project(project_id, identity)
    return _proposal(project, identity,
                     lambda spec, template: service.copilot.propose(body.request, spec, template, identity))


@app.post("/projects/{project_id}/compose")
def compose(project_id: str, body: Compose, identity: Identity = Depends(caller)) -> Any:
    """The same proposal, from the Studio's own form rather than from a sentence: a chart on a free slot.
    « Do not force users to use AI » (§48.3) — the manual path is not a prompt, and is checked the same."""
    service.policy.require(identity, "bi.generate")
    project = _project(project_id, identity)
    return _proposal(project, identity, lambda spec, template: service.copilot.compose(
        body.slot, body.metric, body.type, body.dimension, body.time_grain, spec, template, identity))


class Alter(BaseModel):
    slot: str = Field(max_length=40)
    type: Optional[str] = Field(default=None, max_length=40)
    metric: Optional[str] = Field(default=None, max_length=120)
    dimension: Optional[str] = Field(default=None, max_length=120)
    dataset: Optional[str] = Field(default=None, max_length=120)


@app.post("/projects/{project_id}/alter")
def alter(project_id: str, body: Alter, identity: Identity = Depends(caller)) -> Any:
    """A chart changed from the Studio's own controls — a dataset, a kind, a metric, a dimension. The same
    proposal the words and the gallery produce: checked, and never applied by asking."""
    service.policy.require(identity, "bi.generate")
    project = _project(project_id, identity)
    return _proposal(project, identity, lambda spec, template: service.copilot.alter(
        body.slot, body.model_dump(exclude={"slot"}), spec, template, identity))


class Deploy(BaseModel):
    target: str = Field(max_length=40)
    gate: Literal["UAT", "PROD"] = "UAT"
    comment: str = Field(default="", max_length=500)


@app.get("/targets")
def deployment_targets(identity: Identity = Depends(reader)) -> Any:
    """Where a version may be deployed from here. A target is a Superset somewhere else, named in this
    service's environment and written down nowhere in the repository."""
    return [{"name": name, "url": target.where, "database": target.database_name,
             "from_studio": target.from_studio}
            for name, target in sorted(service.targets.items())]


@app.get("/projects/{project_id}/deployments")
def deployments(project_id: str, identity: Identity = Depends(reader)) -> Any:
    """Every deployment of this project, most recent last — what went where, when, by whom, and what was
    read back."""
    project = _project(project_id, identity)
    return project.get("deployments") or []


@app.post("/projects/{project_id}/versions/{version}/deploy")
def deploy(project_id: str, version: str, body: Deploy, request: Request,
           identity: Identity = Depends(caller)) -> Any:
    """Take a version to another Superset, and prove it arrived.

    What it deploys is the package that was validated — not a new generation — and it deploys only a version
    ALREADY APPROVED for that gate, by someone other than whoever asked for it. Approving is elsewhere, on
    purpose; this executes an approval that exists, and records what it did."""
    service.policy.require(identity, "bi.generate")
    _project(project_id, identity)
    target = service.targets.get(body.target)
    if target is None:
        raise LifecycleError(f"no deployment target is called « {body.target} ». "
                             f"This service knows: {', '.join(sorted(service.targets)) or 'none'}")
    if not target.from_studio and getattr(request.state, "via", "") != "command line":
        raise LifecycleError(f"{body.target} is deployed to from the command line only")
    record = service.store.get_version(project_id, version)
    refused = service.deployer.refusal(record, target, body.gate)
    if refused:
        raise LifecycleError(refused)
    if not service.busy.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a generation is already running; this kit runs one at a time")
    try:
        return service.deployer.deploy(record, target, body.gate, identity.user, str(uuid.uuid4()),
                                       uuid.uuid4().hex[:12])
    finally:
        service.busy.release()


@app.get("/projects/{project_id}/report")
def last_report(project_id: str, identity: Identity = Depends(reader)) -> Any:
    """The whole of the last version's validation — the brief's levels 2 to 6, which only a run can produce:
    the SQL and the data, the security probes, the render, the picture, the timings (§53)."""
    project = _project(project_id, identity)
    latest = service.store.latest_version(project_id)
    if latest is None:
        raise HTTPException(status_code=404, detail=f"{project_id} has no version yet")
    return {"version": latest.version, "status": latest.status, "created_at": latest.created_at,
            "created_by": latest.created_by, "validation": latest.validation,
            "dashboard": latest.dashboard, "package": latest.package, "built_with": latest.built_with,
            "history": [h.model_dump() for h in latest.history], "approvals": [a.model_dump() for a in latest.approvals]}


@app.post("/projects/{project_id}/pictures")
def take_pictures(project_id: str, identity: Identity = Depends(reader)) -> Any:
    """Cut this project's tiles out of one picture of its dashboard (aibi/picture.py). Expensive on purpose:
    it is a browser, a page load and every chart's own query."""
    project = _project(project_id, identity)
    engine = project.get("engine") or {}
    dashboard = (engine.get("dashboard") or {}).get("id")
    charts = {slot: chart for slot, chart in (engine.get("charts") or {}).items() if chart.get("id")}
    if not dashboard or not charts:
        raise LifecycleError(f"{project_id} has no dashboard in Superset to photograph")
    try:
        return service.pictures.cut(project_id, int(dashboard), charts)
    except PictureError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/projects/{project_id}/picture/{slot}")
def picture(project_id: str, slot: str, identity: Identity = Depends(reader)) -> Any:
    """One tile: the chart as the dashboard drew it when the pictures were last taken."""
    _project(project_id, identity)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", slot):
        raise HTTPException(status_code=404, detail="no such slot")
    path = service.pictures.tile(project_id, slot)
    if path is None:
        raise HTTPException(status_code=404, detail=f"no picture of {slot} has been taken")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


# ── projects and versions ───────────────────────────────────────────────────────────────────────────────────
@app.get("/projects")
def projects(identity: Identity = Depends(reader)) -> Any:
    found = []
    for body in service.store.list_projects():
        if body.get("tenant") != identity.tenant:
            continue
        latest = service.store.latest_version(body["project_id"])
        attempt = body.get("attempt") or {}
        found.append({"project_id": body["project_id"], "title": body.get("title"), "template": body.get("template"),
                      "created_by": body.get("created_by"),
                      # where its latest version stands among the quality gates — and, for a project that
                      # has no version, where its only attempt stopped
                      "version": latest.version if latest else None,
                      "status": latest.status if latest else attempt.get("status"),
                      "environment": latest.state if latest else "DEV",
                      # the last generation asked of it, whether or not it produced a version
                      "last_attempt": {k: attempt.get(k) for k in ("run_id", "status", "version", "by", "error")}
                      if attempt else None,
                      "dashboard": (body.get("engine") or {}).get("dashboard", {}).get("url")})
    return found


@app.get("/projects/{project_id}")
def project(project_id: str, identity: Identity = Depends(reader)) -> Any:
    body = _project(project_id, identity)
    return {"project": {k: body.get(k) for k in ("project_id", "title", "template", "tenant", "created_by",
                                                 "created_at", "spec", "spec_valid", "engine", "dbt", "attempt")},
            "versions": service.store.list_versions(project_id),
            "git": service.git.projects.log(limit=20, path=project_id)}


@app.get("/projects/{project_id}/versions/{version}")
def version(project_id: str, version: str, identity: Identity = Depends(reader)) -> Any:
    _project(project_id, identity)
    record = service.store.get_version(project_id, version)
    if record is None:
        raise HTTPException(status_code=404, detail=f"{project_id} has no version {version}")
    return record


def _file_at(project_id: str, version: str, path: str) -> bytes:
    done = subprocess.run(["git", "-c", "safe.directory=*", "show", f"{project_id}/v{version}:{project_id}/{path}"],
                          cwd=str(service.git.projects.path), capture_output=True)
    if done.returncode != 0:
        raise HTTPException(status_code=404, detail=f"{path} is not in version {version} of {project_id}")
    return done.stdout


@app.get("/projects/{project_id}/versions/{version}/spec")
def version_spec(project_id: str, version: str, identity: Identity = Depends(reader)) -> Any:
    _project(project_id, identity)
    return service.lifecycle.specification_at(project_id, version)


@app.get("/projects/{project_id}/versions/{version}/validation")
def version_validation(project_id: str, version: str, identity: Identity = Depends(reader)) -> Response:
    _project(project_id, identity)
    return Response(_file_at(project_id, version, "tests/validation_report.json"), media_type="application/json")


@app.get("/projects/{project_id}/versions/{version}/screenshot")
def version_screenshot(project_id: str, version: str, viewport: str = "desktop",
                       identity: Identity = Depends(reader)) -> Response:
    _project(project_id, identity)
    if not viewport.isalnum():
        raise HTTPException(status_code=400, detail="a viewport is one word")
    return Response(_file_at(project_id, version, f"documentation/dashboard-{viewport}.png"), media_type="image/png")


@app.get("/projects/{project_id}/versions/{version}/package")
def version_package(project_id: str, version: str, identity: Identity = Depends(reader)) -> FileResponse:
    _project(project_id, identity)
    record = service.store.get_version(project_id, version)
    path = service.lifecycle.package_path(record) if record else None
    if path is None:
        raise HTTPException(status_code=404, detail="this version has no package")
    return FileResponse(path, media_type="application/zip", filename=path.name)


# ── review ──────────────────────────────────────────────────────────────────────────────────────────────────
class Decision(BaseModel):
    target: Literal["UAT", "PROD"]
    comment: str = ""
    confirm: bool = False


class Rejection(BaseModel):
    comment: str = Field(min_length=3)


class Restore(BaseModel):
    version: str


@app.post("/projects/{project_id}/approve")
def approve(project_id: str, decision: Decision, identity: Identity = Depends(caller)) -> Any:
    _project(project_id, identity)
    return service.lifecycle.promote(identity, project_id, decision.target, decision.comment, decision.confirm,
                                     str(uuid.uuid4()))


@app.post("/projects/{project_id}/reject")
def reject(project_id: str, rejection: Rejection, identity: Identity = Depends(caller)) -> Any:
    _project(project_id, identity)
    return service.lifecycle.reject(identity, project_id, rejection.comment, str(uuid.uuid4()))


@app.post("/projects/{project_id}/discard")
def discard(project_id: str, identity: Identity = Depends(caller)) -> Any:
    _project(project_id, identity)
    if not service.busy.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a generation is running on this BI engine; try again after it")
    try:
        return service.lifecycle.discard(identity, project_id, str(uuid.uuid4()))
    finally:
        service.busy.release()


@app.post("/projects/{project_id}/restore")
def restore(project_id: str, request: Restore, identity: Identity = Depends(caller)) -> Any:
    """Put the dashboard back as a version specified it. Superset keeps no history; SharpSight does."""
    _project(project_id, identity)
    service.policy.require(identity, "bi.restore")
    latest = service.store.latest_version(project_id)
    if latest and latest.state == "PROD":
        raise LifecycleError("a dashboard in PROD is not restored in place: generate a new version and promote it")
    spec = service.lifecycle.specification_at(project_id, request.version)
    ctx = _context(identity, project_id)
    if not service.busy.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a generation is running on this BI engine; try again after it")
    try:
        verdict = service.tools.save_dashboard_spec(ctx, spec=spec)
        if not verdict.passed:
            raise LifecycleError("that version's specification is no longer valid: "
                                 + "; ".join(f"{c.name} {c.detail}" for c in verdict.failures))
        applied = service.tools.apply_specification(ctx)
    finally:
        service.busy.release()
    service.audit.record(action="restore", user=identity.user, request_id=ctx.request_id, actor="human",
                         project_id=project_id, detail={"restored_version": request.version},
                         superset_assets=[{"type": "dashboard", "id": applied["dashboard"]["id"], "action": "restored"}])
    return {"project_id": project_id, "restored_version": request.version, "dashboard": applied["dashboard"],
            "drift": service.lifecycle.drift(service.store.get_version(project_id, request.version))}


# ── people ──────────────────────────────────────────────────────────────────────────────────────────────────
@app.post("/people/sync")
def people_sync(identity: Identity = Depends(caller)) -> Any:
    """Give every BI-engine account the SharpSight roles rbac.yaml gives its user — and take back the rest."""
    return service.people.sync(identity, str(uuid.uuid4()))


@app.post("/people/{username}")
def people_create(username: str, identity: Identity = Depends(caller)) -> Any:
    """An account in the BI engine for a person of rbac.yaml who has none. The password is in this answer
    and nowhere else."""
    return service.people.create(identity, username, str(uuid.uuid4()))


# ── audit ───────────────────────────────────────────────────────────────────────────────────────────────────
@app.get("/audit")
def audit(request_id: Optional[str] = None, project_id: Optional[str] = None, action: Optional[str] = None,
          limit: int = 200, identity: Identity = Depends(caller)) -> Any:
    service.policy.require(identity, "audit.read")
    return service.audit.read(request_id=request_id, project_id=project_id, action=action, limit=min(limit, 2000))


@app.get("/audit/verify")
def audit_verify(identity: Identity = Depends(caller)) -> Any:
    service.policy.require(identity, "audit.read")
    return service.audit.verify()


@app.exception_handler(GitError)
async def _git(_, exc: GitError):
    return JSONResponse(status_code=500, content={"error": str(exc)})
