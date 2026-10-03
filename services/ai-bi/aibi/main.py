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

import hmac
import subprocess
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .audit import AuditLog
from .config import Settings, get_settings
from .data_gateway import DataGateway
from .dbt_runner import DbtRunner, lineage_from_artifacts, semantic_layer
from .git_adapter import GitAdapter, GitError
from .lifecycle import Lifecycle, LifecycleError
from .llm import get_parser
from .people import People
from .models import RunRecord
from .pipeline import Pipeline
from .planner import new_steps
from .policy_engine import Identity, PolicyEngine, PolicyError
from .registry import Registry, RegistryError
from .store import Store
from .superset_adapter import SupersetAdapter, SupersetError
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
                                 self.registry.policies),
            state_dir=state, bi_database_name=settings.superset_database_name,
            bi_database_uri=settings.superset_database_uri, superset_public_url=settings.superset_public_url,
            dbt_dev_schema=settings.dbt_dev_schema)
        self.lifecycle = Lifecycle(self.tools, settings.exports_dir)
        self.people = People(self.registry, self.policy, self.superset, self.audit, settings.superset_user)
        self.parser = get_parser(settings.llm_provider, self.registry)
        self.pipeline = Pipeline(self.tools, self.lifecycle, self.parser)
        # one generation at a time: each drives dbt and a browser, and two would also race on one dashboard
        self.busy = threading.Lock()

    def bootstrap(self) -> None:
        commit = self.git.sync_governed_project(self.settings.dbt_project_dir)
        self.git.projects.ensure()
        nodes = lineage_from_artifacts(self.settings.dbt_prod_state_dir, "prod")
        self.store.save_lineage(nodes)
        self.dbt_semantic_layer = semantic_layer(self.settings.dbt_prod_state_dir,
                                                 [m.key for m in self.registry.metrics.values()])
        self.audit.record(action="service:start", user="system", request_id="-", actor="system",
                          ai_model=self.parser.name,
                          detail={"version": __version__, "dbt_project_commit": commit, "dbt_models": len(nodes),
                                  "dbt_semantic_layer": self.dbt_semantic_layer,
                                  "datasets": len(self.registry.datasets), "metrics": len(self.registry.metrics),
                                  "templates": len(self.registry.templates)})


service: Optional[Service] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global service
    service = Service(get_settings())
    service.bootstrap()
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


def caller(authorization: str = Header(default=""), x_sharpsight_user: str = Header(default="")) -> Identity:
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    expected = service.settings.api_token
    if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="this API is called by the SharpSight gateway, with its token")
    return service.policy.identify(x_sharpsight_user)


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
            "dbt_semantic_layer": service.dbt_semantic_layer}


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
    return [{"id": r.id, "project_id": r.project_id, "status": r.status, "user": r.user, "version": r.version,
             "started_at": r.started_at, "ended_at": r.ended_at, "error": r.error}
            for r in service.store.list_runs(project_id)]


# ── projects and versions ───────────────────────────────────────────────────────────────────────────────────
@app.get("/projects")
def projects(identity: Identity = Depends(reader)) -> Any:
    found = []
    for body in service.store.list_projects():
        if body.get("tenant") != identity.tenant:
            continue
        latest = service.store.latest_version(body["project_id"])
        found.append({"project_id": body["project_id"], "title": body.get("title"), "template": body.get("template"),
                      "created_by": body.get("created_by"),
                      "version": latest.version if latest else None, "state": latest.state if latest else None,
                      "review_status": latest.review_status if latest else None,
                      "dashboard": (body.get("engine") or {}).get("dashboard", {}).get("url")})
    return found


@app.get("/projects/{project_id}")
def project(project_id: str, identity: Identity = Depends(reader)) -> Any:
    body = _project(project_id, identity)
    return {"project": {k: body.get(k) for k in ("project_id", "title", "template", "tenant", "created_by",
                                                 "created_at", "spec", "spec_valid", "engine", "dbt")},
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
        raise HTTPException(status_code=404, detail="no package yet: it is built when the version is approved for UAT")
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
