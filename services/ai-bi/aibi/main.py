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
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import __version__, compatibility
from .assets import KINDS, AssetError, Catalog
from .audit import AuditLog
from .config import Settings, get_settings
from .data_gateway import DataGateway
from .dbt_runner import DbtRunner, lineage_from_artifacts, semantic_layer
from .git_adapter import GitAdapter, GitError
from .library import Library, LibraryError
from .lifecycle import Lifecycle, LifecycleError
from .llm import get_parser
from .people import People
from .models import HELD, Attempt, DashboardHeader, DashboardSpec, RunRecord, SectionSpec, utcnow
from .pipeline import Pipeline
from .planner import new_steps, slugify
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
                                 self.registry.policies,
                                 {kind: v.engine_type for kind, v in self.registry.visualizations.items()}),
            state_dir=state, bi_database_name=settings.superset_database_name,
            bi_database_uri=settings.superset_database_uri, superset_public_url=settings.superset_public_url,
            dbt_dev_schema=settings.dbt_dev_schema)
        # the asset library: shared charts and sections, and the catalogue over everything
        self.library = Library(self.store, self.registry, self.policy, self.audit, self.git)
        self.tools.dashboards.library = self.library
        self.catalog = Catalog(self.registry, self.policy, self.store, self.library)
        self.tools.catalog = self.catalog
        self.lifecycle = Lifecycle(self.tools, settings.exports_dir)
        self.people = People(self.registry, self.policy, self.superset, self.audit, settings.superset_user)
        self.parser = get_parser(settings.llm_provider, self.registry)
        self.pipeline = Pipeline(self.tools, self.lifecycle, self.parser)
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


@app.exception_handler(LibraryError)
async def _library(_, exc: LibraryError):
    # a change stopped because the asset is in use carries what uses it: the caller looks, then decides
    return JSONResponse(status_code=409, content={"error": str(exc), "impact": exc.impact})


@app.exception_handler(AssetError)
async def _asset(_, exc: AssetError):
    return JSONResponse(status_code=404, content={"error": str(exc)})


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
            "dbt_semantic_layer": service.dbt_semantic_layer,
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


# ── the asset library ───────────────────────────────────────────────────────────────────────────────────────
@app.get("/me")
def me(identity: Identity = Depends(caller)) -> Any:
    return {"user": identity.user, "type": identity.type, "tenant": identity.tenant, "roles": list(identity.roles),
            "capabilities": sorted(identity.capabilities)}


@app.get("/assets")
def assets(kind: Optional[str] = None, project_id: Optional[str] = None, identity: Identity = Depends(reader)) -> Any:
    if kind is not None and kind not in KINDS:
        raise AssetError(f"there is no kind of asset called {kind}")
    return service.catalog.cards(identity, kind, project_id)


@app.get("/assets/tree")
def assets_tree(project_id: Optional[str] = None, identity: Identity = Depends(reader)) -> Any:
    return service.catalog.tree(identity, project_id)


@app.get("/assets/search")
def assets_search(q: str = "", kind: Optional[str] = None, domain: Optional[str] = None, status: Optional[str] = None,
                  owner: Optional[str] = None, used_by: Optional[str] = None, scope: Optional[str] = None,
                  project_id: Optional[str] = None, identity: Identity = Depends(reader)) -> Any:
    return service.catalog.search(identity, q, kind, domain, status, owner, used_by, scope, project_id)


@app.get("/assets/{kind}/{asset_id}")
def asset(kind: str, asset_id: str, identity: Identity = Depends(reader)) -> Any:
    found = service.catalog.detail(identity, kind, asset_id)
    if kind == "dataset":
        # « Rows: 48M » — counted through the data gateway, as the caller, and audited like any statement
        dataset = service.registry.dataset(asset_id)
        try:
            found["facts"]["rows"] = service.tools._run_sql(_context(identity), service.tools.sql.row_count(dataset),
                                                           f"row count of {asset_id}, for the asset library").scalar()
        except Exception:  # noqa: BLE001 — the card is still worth showing without its count
            found["facts"]["rows"] = None
    return found


@app.get("/assets/{kind}/{asset_id}/impact")
def asset_impact(kind: str, asset_id: str, identity: Identity = Depends(reader)) -> Any:
    service.catalog.detail(identity, kind, asset_id)            # the caller may see it, or this raises
    return service.catalog.impact(kind, asset_id, identity.tenant)


@app.put("/assets/{kind}/{asset_id}/favorite")
def asset_favorite(kind: str, asset_id: str, identity: Identity = Depends(reader)) -> Any:
    service.catalog.detail(identity, kind, asset_id)
    service.store.set_favorite(identity.user, kind, asset_id, True)
    return {"kind": kind, "id": asset_id, "favorite": True}


@app.delete("/assets/{kind}/{asset_id}/favorite")
def asset_unfavorite(kind: str, asset_id: str, identity: Identity = Depends(reader)) -> Any:
    service.store.set_favorite(identity.user, kind, asset_id, False)
    return {"kind": kind, "id": asset_id, "favorite": False}


class SharedAsset(BaseModel):
    asset: dict[str, Any]
    acknowledge_impact: bool = False


def _impact(tenant: str):
    return lambda kind, asset_id: service.catalog.impact(kind, asset_id, tenant)


@app.post("/assets/{kind}")
def asset_save(kind: str, body: SharedAsset, identity: Identity = Depends(caller)) -> Any:
    """Create a shared chart or section — or change one. A change to one that is in use is refused with its
    impact, until the caller has seen it and sends `acknowledge_impact`."""
    document = dict(body.asset)
    document.setdefault("id", slugify(document.get("name", "")))
    return service.library.save(identity, kind, document, str(uuid.uuid4()), _impact(identity.tenant),
                                body.acknowledge_impact)


@app.delete("/assets/{kind}/{asset_id}")
def asset_delete(kind: str, asset_id: str, identity: Identity = Depends(caller)) -> Any:
    service.library.delete(identity, kind, asset_id, str(uuid.uuid4()), _impact(identity.tenant))
    return {"kind": kind, "id": asset_id, "deleted": True}


@app.post("/assets/{kind}/{asset_id}/certify")
def asset_certify(kind: str, asset_id: str, identity: Identity = Depends(caller)) -> Any:
    return service.library.certify(identity, kind, asset_id, str(uuid.uuid4()))


class FromTemplate(BaseModel):
    template: str
    metric: str
    dimension: Optional[str] = None
    dataset: Optional[str] = None
    type: Optional[str] = None
    name: Optional[str] = None
    description: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    share: bool = False


@app.post("/chart-templates/create")
def chart_from_template(body: FromTemplate, identity: Identity = Depends(reader)) -> Any:
    """« Create from template »: a chart template, a metric, a dimension → a chart definition, checked.
    With `share`, it is put in the library; without, it is returned for one dashboard to use."""
    from .models import ChartAsset

    template = service.registry.chart_templates.get(body.template)
    if template is None:
        raise AssetError(f"there is no chart template {body.template}")
    if template.status != "available":
        raise LibraryError(f"« {template.name} » cannot be drawn yet: {template.reason}")
    kind = body.type or template.visualization
    if kind not in (template.visualization, *template.alternatives):
        raise LibraryError(f"« {template.name} » is drawn as {', '.join([template.visualization, *template.alternatives])}")
    metric = service.policy.require_metric(identity, body.metric)
    words = (body.dimension or "").replace("_", " ")
    name = body.name or (metric.name if not words else f"{metric.name} by {words.upper() if len(words) <= 3 else words}")
    document = {"id": slugify(name), "name": name[:80], "description": body.description, "type": kind,
                "metric": metric.id, "dimension": body.dimension, "dataset": body.dataset,
                "options": {**template.options, **body.options}, "template": body.template}
    if body.share:
        return {"chart": service.library.save(identity, "chart", document, str(uuid.uuid4()),
                                              _impact(identity.tenant)), "shared": True, "problems": []}
    try:
        chart = ChartAsset(**document)
    except ValidationError as exc:
        raise LibraryError(f"not a valid chart: {exc.errors()[0]['msg']}") from exc
    return {"chart": chart, "shared": False, "problems": service.library.chart_problems(chart, identity)}


@app.get("/chart-templates")
def chart_templates(identity: Identity = Depends(reader)) -> Any:
    return {key: body.model_dump() for key, body in service.registry.chart_templates.items()}


@app.get("/security/overview")
def security_overview(identity: Identity = Depends(reader)) -> Any:
    """Organizations, users and permissions, for those who administer or audit them; one's own for the rest."""
    rbac = service.registry.rbac
    mine = {"user": identity.user, "tenant": identity.tenant, "roles": list(identity.roles)}
    if not {"access.manage", "audit.read"} & identity.capabilities:
        return {"restricted": True, "me": mine, "tenants": [identity.tenant]}
    return {"restricted": False, "me": mine, "tenants": rbac.tenants,
            "users": {name: body.model_dump() for name, body in rbac.users.items()},
            "roles": {name: body.model_dump() for name, body in rbac.roles.items()},
            "row_filters": rbac.row_filters}


# ── BI Studio: composing a dashboard ────────────────────────────────────────────────────────────────────────
STUDIO_DIR = Path(__file__).parent / "studio"


@app.get("/builder/config")
def builder_config() -> Any:
    """What the Studio's page needs before anybody has signed in. Nothing here is a secret."""
    return {"version": __version__, "bi_engine_url": service.settings.superset_public_url,
            "tokens": service.registry.tokens.model_dump(), "ai_model": service.parser.name,
            # which approved visualizations this BI engine can draw, and what each needs
            "visualizations": {kind: v.model_dump() for kind, v in service.registry.visualizations.items()}}


class Working(BaseModel):
    spec: dict[str, Any]
    project_id: Optional[str] = None


def _read_spec(document: dict[str, Any]) -> DashboardSpec:
    try:
        return DashboardSpec(**document)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:6])
        raise HTTPException(status_code=422, detail=f"not a dashboard specification — {problems}") from exc


def _inspect(spec: DashboardSpec, identity: Identity, project_id: Optional[str] = None) -> dict[str, Any]:
    """Everything BI Studio shows about a working specification, computed here — where the rules are —
    and nowhere in the page: validation, the canvas, the outline, governance, who it is for."""
    dashboards, registry = service.tools.dashboards, service.registry
    resolved = dashboards.resolve(spec, identity)
    report = dashboards.validate(spec, identity)
    try:
        template = registry.template(spec.dashboard.template)
    except RegistryError:
        return {"spec": spec.model_dump(), "validation": report.summary(), "checks": report.checks,
                "template": None, "canvas": [], "outline": None, "governance": [], "audience": [], "titles": {}}
    problems: dict[str, int] = {}
    for check in report.failures:
        slot = check.slot or check.subject.split(" ")[0]
        if slot in template.slots:
            problems[slot] = problems.get(slot, 0) + 1
    datasets = [registry.datasets[d] for d in spec.datasets() if d in registry.datasets]
    metrics = [registry.metrics[m] for m in spec.metrics() if m in registry.metrics]
    latest = service.store.latest_version(project_id) if project_id else None
    sections = dashboards.sections(spec, template)
    return {"spec": spec.model_dump(), "validation": report.summary(), "checks": report.checks,
            "template": template.model_dump(), "canvas": dashboards.canvas(spec, template),
            "outline": service.catalog.outline(spec, template, sections, problems),
            "governance": service.catalog.governance(spec, template, identity, latest.validation if latest else None),
            "audience": service.policy.audience(datasets, metrics),
            "row_rules": service.policy.row_rules(datasets, metrics),
            "titles": {c.slot: service.catalog._title(c, template) for c in spec.components},
            "problems": problems, "datasets_resolved": resolved}


@app.post("/builder/inspect")
def builder_inspect(body: Working, identity: Identity = Depends(reader)) -> Any:
    return _inspect(_read_spec(body.spec), identity, body.project_id)


class FillSection(BaseModel):
    spec: dict[str, Any]
    section: str
    asset: str


@app.post("/builder/fill-section")
def builder_fill_section(body: FillSection, identity: Identity = Depends(reader)) -> Any:
    """Fill one section of a working specification from a shared section of the library."""
    spec = _read_spec(body.spec)
    template = service.registry.template(spec.dashboard.template)
    try:
        notes = service.tools.dashboards.fill_section(spec, template, body.section, body.asset)
    except ValueError as exc:
        raise AssetError(str(exc)) from exc
    return {**_inspect(spec, identity), "notes": notes}


class NewProject(BaseModel):
    title: str = Field(min_length=3, max_length=80)
    template: str


def _editable(project_id: str) -> Optional[str]:
    """Why this project cannot be changed, or None when it can."""
    latest = service.store.latest_version(project_id)
    if latest is not None and latest.status in HELD:
        return (f"version {latest.version} is {latest.status}: this kit has one BI engine, so changing the "
                "project would rewrite the promoted dashboard in place")
    return None


@app.post("/builder/projects")
def builder_new_project(body: NewProject, identity: Identity = Depends(caller)) -> Any:
    """A new BI project, started by hand: an empty specification on a template, every section named."""
    service.policy.require(identity, "bi.generate")
    project_id = slugify(body.title)
    if service.store.get_project(project_id) is not None:
        raise LifecycleError(f"BI project {project_id} already exists")
    template = service.registry.template(body.template)
    title = template.template.title_pattern.format(subject=body.title)
    suffix = slugify(title.replace(body.title, "", 1))
    spec = DashboardSpec(
        dashboard=DashboardHeader(id=project_id if slugify(title) == project_id else f"{project_id}-{suffix}",
                                  template=template.id, title=title, owner=identity.user),
        components=[], sections=[SectionSpec(id=s.id) for s in template.section_list()])
    request_id = str(uuid.uuid4())
    attempt = Attempt(run_id="-", request_id=request_id, by=identity.user)
    attempt.move("DRAFT", by=identity.user, note="started in BI Studio")
    service.tools.save_project({
        "project_id": project_id, "tenant": identity.tenant, "created_by": identity.user, "created_at": utcnow(),
        "title": body.title, "template": template.id, "spec_valid": False, "attempt": attempt.model_dump(),
        "draft": {"spec": spec.model_dump(), "saved_by": identity.user, "saved_at": utcnow()}})
    service.audit.record(action="studio:project", user=identity.user, request_id=request_id, actor="human",
                         project_id=project_id, detail={"template": template.id, "title": body.title})
    return builder_project(project_id, identity)


@app.get("/builder/projects/{project_id}")
def builder_project(project_id: str, identity: Identity = Depends(reader)) -> Any:
    """A project as the Studio opens it: its working specification — the draft when there is one, what was
    built otherwise — and whether it may be changed."""
    body = _project(project_id, identity)
    latest = service.store.latest_version(project_id)
    draft = body.get("draft")
    document = (draft or {}).get("spec") or body.get("spec")
    if not document:
        raise HTTPException(status_code=404, detail=f"BI project {project_id} has no specification yet")
    attempt = body.get("attempt") or {}
    locked = _editable(project_id)
    return {"project_id": project_id, "title": body.get("title"), "created_by": body.get("created_by"),
            "status": latest.status if latest else attempt.get("status", "DRAFT"),
            "environment": latest.state if latest else "DEV", "version": latest.version if latest else None,
            "history": latest.history if latest else attempt.get("history", []),
            "editable": locked is None and "bi.generate" in identity.capabilities,
            "locked_because": locked or (None if "bi.generate" in identity.capabilities
                                         else f"{identity.user} may read this project, not change it"),
            "draft": {"saved_by": draft.get("saved_by"), "saved_at": draft.get("saved_at")} if draft else None,
            "dashboard_url": (body.get("engine") or {}).get("dashboard", {}).get("url"),
            # where it is in Superset — what the Studio frames: the dashboard, and each slot's chart
            "superset": ({"dashboard_id": body["engine"]["dashboard"]["id"],
                          "charts": {slot: {"id": c["id"], "name": c.get("name")}
                                     for slot, c in service.tools.live_charts(body["engine"]).items()}}
                         if (body.get("engine") or {}).get("dashboard") else None),
            # False while the working specification is not valid yet, and so not in Superset
            "applied": draft is None and bool(body.get("spec")) and bool((body.get("engine") or {}).get("dashboard")),
            "last_attempt": {k: attempt.get(k) for k in ("run_id", "status", "version", "error")} if attempt else None,
            **_inspect(_read_spec(document), identity, project_id)}


def _changeable(project_id: str, identity: Identity) -> dict[str, Any]:
    service.policy.require(identity, "bi.generate")
    project = _project(project_id, identity)
    locked = _editable(project_id)
    if locked:
        raise LifecycleError(f"{project_id} cannot be changed: {locked}")
    return project


def _engine_free() -> None:
    if not service.busy.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a generation is running on this BI engine; try again after it")


@app.post("/builder/projects/{project_id}/apply")
def builder_apply(project_id: str, body: Working, identity: Identity = Depends(caller)) -> Any:
    """The working specification, made real in Superset's DEV dashboard — so that what the Studio shows IS
    Superset. A specification that is not valid yet (a mandatory slot still empty) is kept as a draft, and
    nothing is sent to Superset until it is. No version is written: that is what a build is for."""
    project = _changeable(project_id, identity)
    spec = _read_spec(body.spec)
    found = _inspect(spec, identity, project_id)
    ctx = _context(identity, project_id)
    applied = found["validation"]["passed"]
    if not applied:
        project["draft"] = {"spec": found["spec"], "saved_by": identity.user, "saved_at": utcnow()}
        service.tools.save_project(project)
    else:
        _engine_free()
        try:
            service.tools.save_dashboard_spec(ctx, spec=_read_spec(found["spec"]))
            service.tools.apply_specification(ctx)
        finally:
            service.busy.release()
        project = service.store.get_project(project_id)
        if project.pop("draft", None) is not None:
            service.tools.save_project(project)
    service.audit.record(action="studio:apply", user=identity.user, request_id=ctx.request_id, actor="human",
                         project_id=project_id, datasets=spec.datasets(), metrics=spec.metrics(),
                         validation=found["validation"],
                         detail={"applied_to_the_bi_engine": applied, "components": len(spec.components),
                                 "sections": [s.id for s in spec.sections if s.visibility == "visible"],
                                 "shared_charts": sorted({c.asset for c in spec.components if c.asset})})
    return builder_project(project_id, identity)


@app.get("/builder/projects/{project_id}/superset")
def builder_superset(project_id: str, identity: Identity = Depends(reader)) -> Any:
    """What Superset holds, against what SharpSight specified: each chart a person edited in Superset's own
    editor, and whether the dashboard is still the version that was validated."""
    body = _project(project_id, identity)
    engine = body.get("engine") or {}
    latest = service.store.latest_version(project_id)
    if not engine.get("dashboard") or not body.get("spec") or not body.get("spec_valid"):
        return {"charts": [], "in_step": True, "version": None}
    held = latest is not None and latest.status in HELD
    charts = [] if held else service.tools.superset_changes(body)
    return {"charts": [c for c in charts if not c["in_step"]], "in_step": all(c["in_step"] for c in charts),
            "version": ({"version": latest.version, "status": latest.status, "differs": service.lifecycle.drift(latest)}
                        if latest else None)}


@app.post("/builder/projects/{project_id}/adopt")
def builder_adopt(project_id: str, identity: Identity = Depends(caller)) -> Any:
    """Take into the specification what a person changed in Superset's Explore — the part that can be said
    in governed terms. A chart that cannot be (an ad-hoc metric, an unapproved visualization) is left as
    specified and named in the answer. The specification is then applied again, so that Superset holds the
    governed form of what was taken."""
    project = _changeable(project_id, identity)
    if not project.get("spec_valid"):
        raise LifecycleError(f"{project_id} has nothing in Superset to take changes from yet")
    ctx = _context(identity, project_id)
    changes = service.tools.superset_changes(project)
    taken = [c for c in changes if c["changed"] and not c["refused"]]
    if not taken:
        raise LifecycleError("there is nothing to take: no chart was changed in Superset in a way a specification "
                             "can carry" + ("; " + "; ".join(f"{c['slot']}: {', '.join(c['refused'])}" for c in changes
                                                             if c["refused"]) if any(c["refused"] for c in changes) else ""))
    spec = _read_spec(project["spec"])
    for change in taken:
        spec.components = [c if c.slot != change["slot"] else type(c)(**change["adopted"]) for c in spec.components]
    found = _inspect(spec, identity, project_id)
    if not found["validation"]["passed"]:
        raise LifecycleError("what was made in Superset cannot be this dashboard's specification: " + "; ".join(
            f"{c.name} ({c.subject}) {c.detail}".strip() for c in found["checks"] if c.status == "fail"))
    _engine_free()
    try:
        service.tools.save_dashboard_spec(ctx, spec=_read_spec(found["spec"]))
        service.tools.apply_specification(ctx)
    finally:
        service.busy.release()
    service.audit.record(action="studio:adopt", user=identity.user, request_id=ctx.request_id, actor="human",
                         project_id=project_id, metrics=spec.metrics(),
                         detail={"taken": {c["slot"]: c["changed"] for c in taken},
                                 "refused": {c["slot"]: c["refused"] for c in changes if c["refused"]},
                                 "set_back": {c["slot"]: c["not_carried"] for c in changes if c["not_carried"]}})
    return {**builder_project(project_id, identity), "taken": [c["slot"] for c in taken],
            "refused": {c["slot"]: c["refused"] for c in changes if c["refused"]}}


class PutBack(BaseModel):
    to: Literal["specification", "version"] = "specification"


@app.post("/builder/projects/{project_id}/put-back")
def builder_put_back(project_id: str, body: PutBack, identity: Identity = Depends(caller)) -> Any:
    """Make Superset hold again what SharpSight says: the working specification (undoing what was edited by
    hand in Superset), or the latest version (discarding what was composed since it was built)."""
    project = _changeable(project_id, identity)
    ctx = _context(identity, project_id)
    latest = service.store.latest_version(project_id)
    if body.to == "version" and latest is None:
        raise LifecycleError(f"{project_id} has no version to go back to")
    if body.to == "specification" and not project.get("spec_valid"):
        raise LifecycleError(f"{project_id} has no valid specification in Superset yet")
    _engine_free()
    try:
        if body.to == "version":
            verdict = service.tools.save_dashboard_spec(ctx, spec=service.lifecycle.specification_at(project_id, latest.version))
            if not verdict.passed:
                raise LifecycleError("that version's specification is no longer valid: "
                                     + "; ".join(f"{c.name} {c.detail}" for c in verdict.failures))
        service.tools.apply_specification(ctx)
    finally:
        service.busy.release()
    project = service.store.get_project(project_id)
    if body.to == "version" and project.pop("draft", None) is not None:
        service.tools.save_project(project)
    service.audit.record(action="restore", user=identity.user, request_id=ctx.request_id, actor="human",
                         project_id=project_id,
                         detail={"put_back_to": body.to, "version": latest.version if latest and body.to == "version" else None,
                                 "reason": "asked for in BI Studio"})
    return builder_project(project_id, identity)


@app.post("/builder/projects/{project_id}/build")
def builder_build(project_id: str, response: Response, identity: Identity = Depends(caller)) -> Any:
    """Build what was composed: the working specification goes through validation, the BI engine, visual QA, a
    version, a commit and a package — the same steps and gates as one the engine wrote."""
    project = _changeable(project_id, identity)
    document = (project.get("draft") or {}).get("spec") or project.get("spec")
    if not document:
        raise LifecycleError(f"{project_id} has nothing to build yet")
    spec = _read_spec(document)
    if not service.busy.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a generation is already running; this kit runs one at a time")
    run = RunRecord(id=uuid.uuid4().hex[:12], request_id=str(uuid.uuid4()), user=identity.user,
                    prompt=f"Build of what was composed in BI Studio for {project_id}: {spec.dashboard.title}",
                    ai_model=service.parser.name, steps=new_steps(), project_id=project_id)
    service.store.save_run(run)

    def work() -> None:
        try:
            service.pipeline.execute(run, identity, composed=(project_id, spec))
        finally:
            service.busy.release()

    threading.Thread(target=work, name=f"run-{run.id}", daemon=True).start()
    response.status_code = 202
    return {"run_id": run.id, "request_id": run.request_id, "status": "running"}


@app.get("/projects/{project_id}/outline")
def project_outline(project_id: str, identity: Identity = Depends(reader)) -> Any:
    return builder_project(project_id, identity)["outline"]


@app.get("/projects/{project_id}/governance")
def project_governance(project_id: str, identity: Identity = Depends(reader)) -> Any:
    return builder_project(project_id, identity)["governance"]


@app.get("/projects/{project_id}/definition")
def project_definition(project_id: str, identity: Identity = Depends(reader)) -> Any:
    """The dashboard as a composition: metadata, sections, shared assets, visual assets, security, deployment."""
    body = _project(project_id, identity)
    document = body.get("spec") or (body.get("draft") or {}).get("spec")
    if not document:
        raise HTTPException(status_code=404, detail=f"BI project {project_id} has no specification yet")
    spec = _read_spec(document)
    template = service.registry.template(spec.dashboard.template)
    return service.catalog.definition(project_id, identity, service.tools.dashboards.sections(spec, template),
                                      spec, template)


@app.get("/dbt/models")
def dbt_models(identity: Identity = Depends(reader)) -> Any:
    """What dbt knows, as SharpSight keeps it: every model of the governed project (the production build) and
    every model the engine generated on a branch of its own — its layer, where it is built, its tests and how
    each ended, what it is built from, and which governed dataset it is the source of."""
    feeds = {d.source_model: d for d in service.registry.datasets.values()}
    models = []
    for node in service.store.list_lineage():
        if node.get("resource_type") not in ("model", "seed"):
            continue
        dataset = feeds.get(node["name"])
        if dataset is not None and not service.policy.can_read(identity, dataset):
            dataset = None
        path = node.get("path") or ""
        tests = node.get("tests", [])
        layer = ("generated" if "ai_generated" in node.get("tags", []) else
                 "seed" if node["resource_type"] == "seed" else
                 next((part for part in ("staging", "intermediate", "marts") if part in path), "model"))
        models.append({
            "name": node["name"], "kind": node["resource_type"], "environment": node["environment"], "layer": layer,
            "path": path, "materialized": node.get("materialized"), "relation": node.get("relation"),
            "status": node.get("status"), "columns": len(node.get("columns") or {}),
            "built_from": [p["name"] for p in node.get("depends_on", [])],
            "tests": {"total": len(tests), "passed": sum(1 for t in tests if t.get("status") == "pass"),
                      "failed": [t["name"] for t in tests if t.get("status") in ("fail", "error")],
                      "not_run": sum(1 for t in tests if t.get("status") in (None, "not run"))},
            "dataset": ({"id": dataset.id, "name": dataset.name, "certified": dataset.certified,
                         "project": dataset.project} if dataset else None)})
    return {"models": models,
            "docs": "/dbt-docs/" if (service.settings.dbt_prod_state_dir / "index.html").exists() else None,
            "semantic_layer": service.dbt_semantic_layer,
            "governed_commit": service.git.dbt.git("rev-parse", "main", check=False)[:12]}


# dbt's own documentation site, as the production build published it: served as it is, framed by BI Studio.
if get_settings().dbt_prod_state_dir.is_dir():
    app.mount("/dbt-docs", StaticFiles(directory=get_settings().dbt_prod_state_dir, html=True), name="dbt-docs")

# The Studio's page: three static files, served by this service — no build step, no script from anywhere else.
# Mounted LAST, so that no route above can be shadowed by a file.
if STUDIO_DIR.is_dir():
    app.mount("/studio", StaticFiles(directory=STUDIO_DIR, html=True), name="studio")
