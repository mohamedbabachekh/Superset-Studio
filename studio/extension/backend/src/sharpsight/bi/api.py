"""What the SharpSight panel asks, answered inside Superset — by asking the SharpSight engine.

This is a gateway and nothing else. It holds no state and decides nothing: each call goes to the engine with
the engine's gateway token and the name of the person signed in to Superset, and the engine applies
SharpSight's own rules (governance/registry/security/rbac.yaml) to that person.

WHO IS ASKING is read from Superset's session, on the server — never from the request. A Superset account
whose name is not a user of rbac.yaml is refused by the engine (403); so is an action its role does not hold.
That makes Superset's account management part of what SharpSight trusts: someone who can create a Superset
account named like a developer can validate and commit as them. It was accepted for these two actions, and for
no other: nothing here approves, promotes, rejects or restores — those stay on the command line, so the rule
that PROD needs a second person cannot be gone round from Superset.
"""
from __future__ import annotations

import os
import re
from typing import Any

import requests
from flask import Response, g, request
from flask_appbuilder.api import expose, protect, safe
from superset_core.rest_api.api import RestApi
from superset_core.rest_api.decorators import api

VIEW = "SharpSightBI"
PERMISSIONS = ("can_read", "can_write")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")        # a project id, a run id: nothing that is a path


@api(id="sharpsight_bi", name="SharpSight BI",
     description="Project context, validate and commit — asked of the SharpSight engine for the person signed in")
class SharpSightBiApi(RestApi):
    openapi_spec_tag = "SharpSight BI"
    class_permission_name = VIEW
    method_permission_name = {"projects": "read", "changes": "read", "run": "read", "commit": "write",
                              # the Studio: what it draws is a read; the draft it keeps is a write
                              "studio": "read", "datasets": "read", "metrics": "read",
                              "templates": "read", "check_draft": "read", "picture": "read",
                              "save_draft": "write", "drop_draft": "write", "commit_draft": "write",
                              "pictures": "write", "propose": "write", "compose": "write",
                              "report": "read", "lineage": "read", "package": "read", "alter": "write",
                              "dataset": "read",
                              "targets": "read", "deployments": "read", "deploy": "write"}
    allow_browser_login = True
    # Flask-AppBuilder exempts an API's blueprint from CSRF protection unless told otherwise, and Superset's
    # own @api for extensions does not tell it (its own APIs do, through a mixin an extension does not get).
    # Left as it was, a POST carrying a developer's session cookie and no token started a commit — measured.
    csrf_exempt = False

    def _engine(self, method: str, path: str, **sent: Any) -> Response:
        base = os.environ.get("SHARPSIGHT_ENGINE_URL", "").rstrip("/")
        token = os.environ.get("SHARPSIGHT_ENGINE_TOKEN", "")
        if not base or not token:
            return self.response(503, message="Superset was not told where the SharpSight engine is")
        try:
            answer = requests.request(method, base + path, timeout=60, headers={
                "Authorization": f"Bearer {token}", "X-SharpSight-User": g.user.username}, **sent)
        except requests.RequestException as error:
            return self.response(502, message=f"the SharpSight engine did not answer ({type(error).__name__})")
        try:
            body = answer.json()
        except ValueError:
            body = {"detail": answer.text[:300]}
        if answer.status_code >= 400:
            said = body.get("error") or body.get("detail") or body if isinstance(body, dict) else body
            return self.response(answer.status_code, message=str(said)[:800])
        return self.response(answer.status_code, result=body)

    def _engine_file(self, method: str, path: str, kind: str, name: str = "") -> Response:
        """Bytes, not JSON — a tile of the canvas, or the package a version was cut as. Everything else about
        the call is the same: the engine's token, the name of the person signed in to Superset, and the
        engine's own refusals."""
        base = os.environ.get("SHARPSIGHT_ENGINE_URL", "").rstrip("/")
        token = os.environ.get("SHARPSIGHT_ENGINE_TOKEN", "")
        if not base or not token:
            return self.response(503, message="Superset was not told where the SharpSight engine is")
        try:
            answer = requests.request(method, base + path, timeout=120, headers={
                "Authorization": f"Bearer {token}", "X-SharpSight-User": g.user.username})
        except requests.RequestException as error:
            return self.response(502, message=f"the SharpSight engine did not answer ({type(error).__name__})")
        if answer.status_code != 200 or not (answer.headers.get("Content-Type") or "").startswith(kind):
            return self.response(answer.status_code if answer.status_code >= 400 else 502,
                                 message="the engine gave no file for that")
        headers = {"Cache-Control": "no-cache"}
        if name:
            headers["Content-Disposition"] = f'attachment; filename="{name}"'
        return Response(answer.content, mimetype=answer.headers.get("Content-Type"), headers=headers)

    @expose("/projects", methods=("GET",))
    @protect()
    @safe
    def projects(self) -> Response:
        """The BI projects the person signed in may see, and the gate each stands at.
        ---
        get:
          responses:
            200:
              description: The projects
        """
        return self._engine("GET", "/projects")

    @expose("/projects/<project_id>/changes", methods=("GET",))
    @protect()
    @safe
    def changes(self, project_id: str) -> Response:
        """Validate: what Superset holds now against the project's specification, chart by chart.
        ---
        get:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          responses:
            200:
              description: What changed, what cannot be taken, what a commit would undo
        """
        if not _NAME.match(project_id):
            return self.response(400, message="that is not a project's name")
        return self._engine("GET", f"/projects/{project_id}/changes")

    @expose("/projects/<project_id>/commit", methods=("POST",))
    @protect()
    @safe
    def commit(self, project_id: str) -> Response:
        """Commit: take what was edited in Superset into a new version, validated, awaiting review.
        ---
        post:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          responses:
            202:
              description: The run that was started
        """
        if not _NAME.match(project_id):
            return self.response(400, message="that is not a project's name")
        comment = str((request.get_json(silent=True) or {}).get("comment") or "")[:500]
        return self._engine("POST", f"/projects/{project_id}/commit", json={"comment": comment})

    @expose("/runs/<run_id>", methods=("GET",))
    @protect()
    @safe
    def run(self, run_id: str) -> Response:
        """A run started by a commit: its steps, and how it ended.
        ---
        get:
          parameters:
          - in: path
            name: run_id
            schema: {type: string}
          responses:
            200:
              description: The run
        """
        if not _NAME.match(run_id):
            return self.response(400, message="that is not a run's name")
        return self._engine("GET", f"/runs/{run_id}")

    # ── the Studio ──────────────────────────────────────────────────────────────────────────────────────────
    # The workspace of the brief's sections 46–60, served as a page of Superset's own (sharpsight_bi.py). The
    # gateway's part is unchanged: it carries the question to the engine with the name of the person signed in,
    # and the engine answers for that person. A draft is kept there, not here — an extension holds no state
    # that could not be recovered outside Superset.

    @expose("/projects/<project_id>/studio", methods=("GET",))
    @protect()
    @safe
    def studio(self, project_id: str) -> Response:
        """Everything the workspace draws: the project, its gate, the template's constraints, the layout in
        force, each chart with its governance, the draft, and when the tiles were cut.
        ---
        get:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          responses:
            200:
              description: The workspace
        """
        if not _NAME.match(project_id):
            return self.response(400, message="that is not a project's name")
        return self._engine("GET", f"/projects/{project_id}/studio")

    @expose("/projects/<project_id>/draft", methods=("PUT",))
    @protect()
    @safe
    def save_draft(self, project_id: str) -> Response:
        """Keep what a person is editing. It changes nothing in Superset and commits nothing.
        ---
        put:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          responses:
            200:
              description: The draft's new token
        """
        if not _NAME.match(project_id):
            return self.response(400, message="that is not a project's name")
        body = request.get_json(silent=True) or {}
        if len(request.get_data() or b"") > 256 * 1024:
            return self.response(413, message="a specification is not that large")
        return self._engine("PUT", f"/projects/{project_id}/draft",
                            json={"spec": body.get("spec") or {}, "token": str(body.get("token") or "none")[:32]})

    @expose("/projects/<project_id>/draft", methods=("DELETE",))
    @protect()
    @safe
    def drop_draft(self, project_id: str) -> Response:
        """Throw the draft away. The committed version, and what Superset holds, are untouched.
        ---
        delete:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          responses:
            200:
              description: Whether there was one
        """
        if not _NAME.match(project_id):
            return self.response(400, message="that is not a project's name")
        return self._engine("DELETE", f"/projects/{project_id}/draft")

    @expose("/projects/<project_id>/draft/validate", methods=("POST",))
    @protect()
    @safe
    def check_draft(self, project_id: str) -> Response:
        """Validate the draft: its layout, its slots, its visualizations, its metrics and datasets, and this
        person's clearance for each — without touching Superset.
        ---
        post:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          responses:
            200:
              description: The report
        """
        if not _NAME.match(project_id):
            return self.response(400, message="that is not a project's name")
        return self._engine("POST", f"/projects/{project_id}/draft/validate")

    @expose("/projects/<project_id>/draft/commit", methods=("POST",))
    @protect()
    @safe
    def commit_draft(self, project_id: str) -> Response:
        """Make the draft a version — through the same road as any other commit: the engine's ten gates,
        visual QA, git, a package.
        ---
        post:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          responses:
            202:
              description: The run that was started
        """
        if not _NAME.match(project_id):
            return self.response(400, message="that is not a project's name")
        comment = str((request.get_json(silent=True) or {}).get("comment") or "")[:500]
        return self._engine("POST", f"/projects/{project_id}/draft/commit", json={"comment": comment})

    @expose("/projects/<project_id>/pictures", methods=("POST",))
    @protect()
    @safe
    def pictures(self, project_id: str) -> Response:
        """Take the canvas's tiles again: one picture of the dashboard, cut per slot.
        ---
        post:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          responses:
            200:
              description: When they were taken, and of what
        """
        if not _NAME.match(project_id):
            return self.response(400, message="that is not a project's name")
        return self._engine("POST", f"/projects/{project_id}/pictures")

    @expose("/projects/<project_id>/picture/<slot>", methods=("GET",))
    @protect()
    @safe
    def picture(self, project_id: str, slot: str) -> Response:
        """One tile: the chart as the dashboard drew it when the pictures were last taken.
        ---
        get:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          - in: path
            name: slot
            schema: {type: string}
          responses:
            200:
              description: A PNG
        """
        if not _NAME.match(project_id) or not _NAME.match(slot):
            return self.response(400, message="that is not a project's name and a slot")
        return self._engine_file("GET", f"/projects/{project_id}/picture/{slot}", "image/png")

    @expose("/datasets", methods=("GET",))
    @protect()
    @safe
    def datasets(self) -> Response:
        """The governed datasets this person may read — the explorer's own list.
        ---
        get:
          responses:
            200:
              description: The datasets
        """
        return self._engine("GET", "/datasets")

    @expose("/metrics", methods=("GET",))
    @protect()
    @safe
    def metrics(self) -> Response:
        """The registered metrics, certified or not, this person may use.
        ---
        get:
          responses:
            200:
              description: The metrics
        """
        return self._engine("GET", "/metrics")

    @expose("/templates", methods=("GET",))
    @protect()
    @safe
    def templates(self) -> Response:
        """The dashboard templates: where things may go, and what may go there.
        ---
        get:
          responses:
            200:
              description: The templates
        """
        return self._engine("GET", "/templates")

    @expose("/projects/<project_id>/propose", methods=("POST",))
    @protect()
    @safe
    def propose(self, project_id: str) -> Response:
        """A request in words, answered with a proposal: what it would change, what it touches, whether it
        would pass validation, and the specification as it would be. Nothing is saved by asking.
        ---
        post:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          responses:
            200:
              description: The proposal
        """
        if not _NAME.match(project_id):
            return self.response(400, message="that is not a project's name")
        asked = str((request.get_json(silent=True) or {}).get("request") or "")[:500]
        return self._engine("POST", f"/projects/{project_id}/propose", json={"request": asked})

    @expose("/projects/<project_id>/compose", methods=("POST",))
    @protect()
    @safe
    def compose(self, project_id: str) -> Response:
        """The same proposal, from the Studio's own form: a chart on a free slot of the template.
        ---
        post:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          responses:
            200:
              description: The proposal
        """
        if not _NAME.match(project_id):
            return self.response(400, message="that is not a project's name")
        body = request.get_json(silent=True) or {}
        wanted = {key: body.get(key) for key in ("slot", "metric", "type", "dimension", "time_grain")}
        return self._engine("POST", f"/projects/{project_id}/compose", json=wanted)

    @expose("/projects/<project_id>/report", methods=("GET",))
    @protect()
    @safe
    def report(self, project_id: str) -> Response:
        """What the last run found: the levels of validation only a run can produce — the data, the security
        probes, the render, the picture, the timings.
        ---
        get:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          responses:
            200:
              description: The report
        """
        if not _NAME.match(project_id):
            return self.response(400, message="that is not a project's name")
        return self._engine("GET", f"/projects/{project_id}/report")

    @expose("/datasets/<dataset_id>", methods=("GET",))
    @protect()
    @safe
    def dataset(self, dataset_id: str) -> Response:
        """One governed dataset: its registry metadata and its columns — their names, types, roles and
        classifications. No row of data is read or returned.
        ---
        get:
          parameters:
          - in: path
            name: dataset_id
            schema: {type: string}
          responses:
            200:
              description: The dataset
        """
        if not _NAME.match(dataset_id):
            return self.response(400, message="that is not a dataset's name")
        return self._engine("GET", f"/datasets/{dataset_id}")

    @expose("/datasets/<dataset_id>/lineage", methods=("GET",))
    @protect()
    @safe
    def lineage(self, dataset_id: str) -> Response:
        """Where a governed dataset comes from: its dbt model, what that model is built from, its tests.
        ---
        get:
          parameters:
          - in: path
            name: dataset_id
            schema: {type: string}
          responses:
            200:
              description: The lineage
        """
        if not _NAME.match(dataset_id):
            return self.response(400, message="that is not a dataset's name")
        return self._engine("GET", f"/datasets/{dataset_id}/lineage")

    @expose("/projects/<project_id>/versions/<version>/package", methods=("GET",))
    @protect()
    @safe
    def package(self, project_id: str, version: str) -> Response:
        """The immutable package a version was cut as — what a deployment service imports elsewhere. Reading
        it is not promoting it: who may promote, and where, is the engine's and the command line's.
        ---
        get:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          - in: path
            name: version
            schema: {type: string}
          responses:
            200:
              description: The .ssbi package
        """
        if not _NAME.match(project_id) or not re.fullmatch(r"\d+(\.\d+){0,2}", version or ""):
            return self.response(400, message="that is not a project and a version")
        return self._engine_file("GET", f"/projects/{project_id}/versions/{version}/package",
                                 "application/zip", f"{project_id}-{version}.ssbi")

    @expose("/projects/<project_id>/alter", methods=("POST",))
    @protect()
    @safe
    def alter(self, project_id: str) -> Response:
        """A chart changed from the Studio's own controls — a dataset, a kind, a metric, a dimension —
        answered with a proposal, like the words and the gallery. Nothing is saved by asking.
        ---
        post:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          responses:
            200:
              description: The proposal
        """
        if not _NAME.match(project_id):
            return self.response(400, message="that is not a project's name")
        body = request.get_json(silent=True) or {}
        wanted = {key: body.get(key) for key in ("slot", "type", "metric", "dimension", "dataset")}
        return self._engine("POST", f"/projects/{project_id}/alter", json=wanted)

    @expose("/targets", methods=("GET",))
    @protect()
    @safe
    def targets(self) -> Response:
        """Where a version may be deployed from here: a Superset somewhere else, named in the engine's
        environment. Nothing about a target is written in this extension.
        ---
        get:
          responses:
            200:
              description: The targets
        """
        return self._engine("GET", "/targets")

    @expose("/projects/<project_id>/deployments", methods=("GET",))
    @protect()
    @safe
    def deployments(self, project_id: str) -> Response:
        """What went where, when, by whom, and what was read back afterwards.
        ---
        get:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          responses:
            200:
              description: The deployments
        """
        if not _NAME.match(project_id):
            return self.response(400, message="that is not a project's name")
        return self._engine("GET", f"/projects/{project_id}/deployments")

    @expose("/projects/<project_id>/versions/<version>/deploy", methods=("POST",))
    @protect()
    @safe
    def deploy(self, project_id: str, version: str) -> Response:
        """Take a version to another Superset — one a second person has already signed off for that gate,
        and not the person who asked for it. This carries out a decision made elsewhere; it makes none.
        ---
        post:
          parameters:
          - in: path
            name: project_id
            schema: {type: string}
          - in: path
            name: version
            schema: {type: string}
          responses:
            200:
              description: What the deployment did, step by step
        """
        if not _NAME.match(project_id) or not re.fullmatch(r"\d+(\.\d+){0,2}", version or ""):
            return self.response(400, message="that is not a project and a version")
        body = request.get_json(silent=True) or {}
        wanted = {"target": str(body.get("target") or "")[:40], "gate": str(body.get("gate") or "UAT")[:8],
                  "comment": str(body.get("comment") or "")[:500]}
        return self._engine("POST", f"/projects/{project_id}/versions/{version}/deploy", json=wanted)
