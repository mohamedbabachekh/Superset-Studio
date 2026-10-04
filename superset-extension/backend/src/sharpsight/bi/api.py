"""What the SharpSight panel asks, answered inside Superset — by asking the SharpSight engine.

This is a gateway and nothing else. It holds no state and decides nothing: each call goes to the engine with
the engine's gateway token and the name of the person signed in to Superset, and the engine applies
SharpSight's own rules (registry/security/rbac.yaml) to that person.

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
    method_permission_name = {"projects": "read", "changes": "read", "run": "read", "commit": "write"}
    allow_browser_login = True

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
