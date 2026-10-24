"""Everything the engine asks of the BI engine goes through this file, and only through its REST API.

Superset's metadata database is never read or written directly — not for speed, not « just this once ». That
is what keeps Superset upgradeable on its own, and it is also why this file is the only one to rewrite the
day the BI engine is something else: nothing above it says « slice », « form_data » or « position_json ».

Verified against the kit's own image (Superset 6.1.0) on 2026-10-03: the routes and the schema fields used
here were read out of superset/{charts,dashboards,datasets}/, not recalled.

What Superset 6.1 does NOT offer, and what the engine does instead:
  · no dashboard version history or restore → SharpSight keeps every version (specification + export) in
    git, and restores by re-applying a specification (aibi/lifecycle.py);
  · no headless browser in the image, so no screenshot endpoint that works → the engine renders with its own
    browser (aibi/visual_qa_agent.py).
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional
from urllib.parse import quote

import prison
import requests


class SupersetError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


CSRF_FIELD = re.compile(r'name="csrf_token"[^>]*?value="([^"]+)"')


def sign_in(base_url: str, username: str, password: str, timeout: int = 30) -> requests.Session:
    """A signed-in BROWSER session, obtained without a browser — for the engine's own browser to carry, and
    for the probe accounts that ask the BI engine what it lets them see.

    The first version typed the password into the sign-in page. That page fires requests of its own before
    anyone is signed in (its event log, the language pack); when one of them is answered AFTER the sign-in,
    its `Set-Cookie` replaces the fresh session with an anonymous one, and the dashboard request that follows
    is sent back to the sign-in page. It lost that race once in some twenty-five renders (2026-10-03).

    Here nothing is loaded that was not asked for: the form's own token, one POST, one check."""
    session = requests.Session()
    form = session.get(f"{base_url}/login/", timeout=timeout)
    token = CSRF_FIELD.search(form.text)
    if token is None:
        raise SupersetError("the BI engine's sign-in form carries no csrf_token: its sign-in has changed")
    session.post(f"{base_url}/login/", timeout=timeout, allow_redirects=False,
                 data={"username": username, "password": password, "csrf_token": token.group(1)})
    if session.get(f"{base_url}/api/v1/me/", timeout=timeout).status_code != 200:
        raise SupersetError(f"the BI engine did not accept the sign-in of {username}")
    csrf = session.get(f"{base_url}/api/v1/security/csrf_token/", timeout=timeout)
    if csrf.status_code == 200:
        session.headers["X-CSRFToken"] = csrf.json()["result"]
    session.headers["Referer"] = base_url
    return session


class SupersetAdapter:
    def __init__(self, base_url: str, username: str, password: str, timeout: int = 90):
        self.base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self.timeout = timeout
        self._session: Optional[requests.Session] = None

    # ── authentication ──────────────────────────────────────────────────────────────────────────────────────
    def login(self) -> None:
        session = requests.Session()
        answer = session.post(f"{self.base_url}/api/v1/security/login", timeout=self.timeout,
                              json={"username": self._username, "password": self._password,
                                    "provider": "db", "refresh": True})
        if answer.status_code != 200:
            raise SupersetError(f"Superset refused the service account ({answer.status_code})", answer.status_code)
        session.headers["Authorization"] = f"Bearer {answer.json()['access_token']}"
        csrf = session.get(f"{self.base_url}/api/v1/security/csrf_token/", timeout=self.timeout)
        if csrf.status_code == 200:
            session.headers["X-CSRFToken"] = csrf.json()["result"]
        session.headers["Referer"] = self.base_url
        self._session = session

    def _request(self, method: str, path: str, *, expect: tuple[int, ...] = (200, 201), raw: bool = False,
                 **kwargs: Any) -> Any:
        if self._session is None:
            self.login()
        url = f"{self.base_url}{path}"
        answer = self._session.request(method, url, timeout=self.timeout, **kwargs)
        if answer.status_code == 401:            # the token expired: sign in again, once
            self.login()
            answer = self._session.request(method, url, timeout=self.timeout, **kwargs)
        if answer.status_code not in expect:
            raise SupersetError(f"{method} {path} → {answer.status_code}: {self._reason(answer)}", answer.status_code)
        if raw:
            return answer
        return answer.json() if answer.content else {}

    @staticmethod
    def _reason(answer: requests.Response) -> str:
        try:
            body = answer.json()
        except ValueError:
            return answer.text[:300]
        if isinstance(body, dict):
            if body.get("errors"):
                return "; ".join(str(e.get("message", e)) for e in body["errors"])[:600]
            if "message" in body:
                return json.dumps(body["message"], ensure_ascii=False)[:600]
        return json.dumps(body, ensure_ascii=False)[:600]

    # Superset answers a list at most 100 entries at a time (FAB_API_MAX_PAGE_SIZE), whatever is asked for.
    PAGE_SIZE = 100
    # A bound on the pages read, so a server that ignores `page` is an error and not an endless loop.
    MAX_PAGES = 200
    # The column each list is read in order of. Without one, Superset pages a query that has no ORDER BY, and
    # PostgreSQL may give page 1 rows page 0 already gave and skip others: a read-back would call a rule
    # missing that exists, and a clean-up would never see a stale one. Each of these is one the resource's API
    # accepts (`id` is refused for row-level rules), and Superset adds the primary key as the tie-breaker.
    ORDER_BY = {"rowlevelsecurity": "name", "dataset": "table_name", "database": "database_name",
                "dashboard": "dashboard_title", "security/roles": "name", "security/users": "username"}

    def _list(self, resource: str, filters: list[dict[str, Any]], columns: Optional[list[str]] = None) -> list[dict]:
        """EVERY entry that matches, page after page. A first page only is a silent truncation: a read-back
        over 150 row-level rules would call fifty of them missing, and a clean-up would never see them."""
        found: list[dict] = []
        for page in range(self.MAX_PAGES):
            query: dict[str, Any] = {"filters": filters, "page_size": self.PAGE_SIZE, "page": page,
                                     "order_column": self.ORDER_BY[resource], "order_direction": "asc"}
            if columns:
                query["columns"] = columns
            answer = self._request("GET", f"/api/v1/{resource}/", params={"q": prison.dumps(query)})
            batch = answer.get("result", [])
            found.extend(batch)
            count = answer.get("count")
            if len(batch) < self.PAGE_SIZE or (count is not None and len(found) >= int(count)):
                return found
        raise SupersetError(f"GET /api/v1/{resource}/ → more than {self.MAX_PAGES} pages: the list was not read "
                            "to its end, and a partial list is not taken for the whole")

    def health(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/health", timeout=10).status_code == 200
        except requests.RequestException:
            return False

    def version(self) -> Optional[str]:
        """The BI engine's version, as it states it itself."""
        try:
            answer = requests.get(f"{self.base_url}/version", timeout=10)
            return str(answer.json()["version_string"]) if answer.status_code == 200 else None
        except (requests.RequestException, ValueError, KeyError):
            return None

    # ── database connections ────────────────────────────────────────────────────────────────────────────────
    def find_database(self, name: str) -> Optional[dict[str, Any]]:
        found = self._list("database", [{"col": "database_name", "opr": "eq", "value": name}])
        return found[0] if found else None

    def ensure_database(self, name: str, sqlalchemy_uri: str, sql_lab: bool = True) -> int:
        """The read-only connection the engine's datasets hang from. Created once, and its URI kept current —
        a password rotated in .env reaches Superset on the next start. `sql_lab=False` for a connection that
        exists to draw dashboards and must not be a place to write queries (the engine's own records)."""
        payload = {"database_name": name, "sqlalchemy_uri": sqlalchemy_uri, "expose_in_sqllab": sql_lab,
                   "allow_dml": False, "allow_ctas": False, "allow_cvas": False, "allow_file_upload": False}
        existing = self.find_database(name)
        if existing is None:
            return self._request("POST", "/api/v1/database/", json=payload)["id"]
        self._request("PUT", f"/api/v1/database/{existing['id']}", json=payload)
        return existing["id"]

    # ── datasets ────────────────────────────────────────────────────────────────────────────────────────────
    def find_dataset(self, database_id: int, schema: str, table: str) -> Optional[dict[str, Any]]:
        found = self._list("dataset", [{"col": "table_name", "opr": "eq", "value": table},
                                       {"col": "schema", "opr": "eq", "value": schema},
                                       {"col": "database", "opr": "rel_o_m", "value": database_id}])
        return found[0] if found else None

    def create_dataset(self, database_id: int, schema: str, table: str) -> int:
        return self._request("POST", "/api/v1/dataset/",
                             json={"database": database_id, "schema": schema, "table_name": table})["id"]

    def get_dataset(self, dataset_id: int) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/dataset/{dataset_id}")["result"]

    def update_dataset(self, dataset_id: int, payload: dict[str, Any]) -> None:
        self._request("PUT", f"/api/v1/dataset/{dataset_id}", json=payload)

    def delete_dataset(self, dataset_id: int) -> None:
        self._request("DELETE", f"/api/v1/dataset/{dataset_id}", expect=(200, 404))

    def refresh_dataset(self, dataset_id: int) -> None:
        """Re-read the table's columns — needed when a generated model is rebuilt with another shape."""
        self._request("PUT", f"/api/v1/dataset/{dataset_id}/refresh")

    # ── charts ──────────────────────────────────────────────────────────────────────────────────────────────
    def get_chart(self, chart_id: int) -> Optional[dict[str, Any]]:
        try:
            return self._request("GET", f"/api/v1/chart/{chart_id}")["result"]
        except SupersetError as exc:
            if exc.status == 404:
                return None
            raise

    def create_chart(self, payload: dict[str, Any]) -> int:
        return self._request("POST", "/api/v1/chart/", json=payload)["id"]

    def update_chart(self, chart_id: int, payload: dict[str, Any]) -> None:
        self._request("PUT", f"/api/v1/chart/{chart_id}", json=payload)

    def delete_chart(self, chart_id: int) -> None:
        self._request("DELETE", f"/api/v1/chart/{chart_id}", expect=(200, 404))

    # ── dashboards ──────────────────────────────────────────────────────────────────────────────────────────
    def get_dashboard(self, id_or_slug: Any) -> Optional[dict[str, Any]]:
        try:
            return self._request("GET", f"/api/v1/dashboard/{id_or_slug}")["result"]
        except SupersetError as exc:
            if exc.status == 404:
                return None
            raise

    def create_dashboard(self, payload: dict[str, Any]) -> int:
        return self._request("POST", "/api/v1/dashboard/", json=payload)["id"]

    def update_dashboard(self, dashboard_id: int, payload: dict[str, Any]) -> None:
        self._request("PUT", f"/api/v1/dashboard/{dashboard_id}", json=payload)

    def delete_dashboard(self, dashboard_id: int) -> None:
        self._request("DELETE", f"/api/v1/dashboard/{dashboard_id}", expect=(200, 404))

    def copy_dashboard(self, dashboard_id: int, title: str, duplicate_charts: bool = True) -> int:
        """A copy with charts of its own. The engine takes the copy's LAYOUT from `positions` inside the
        metadata it is sent, not from the original: sent the original's metadata as stored, the copy came out
        with its charts and no layout at all (found by adapter_check, the first time this was run)."""
        source = self.get_dashboard(dashboard_id) or {}
        metadata = json.loads(source.get("json_metadata") or "{}")
        metadata["positions"] = json.loads(source.get("position_json") or "{}")
        answer = self._request("POST", f"/api/v1/dashboard/{dashboard_id}/copy/",
                               json={"dashboard_title": title, "duplicate_slices": duplicate_charts,
                                     "json_metadata": json.dumps(metadata), "css": source.get("css") or ""})
        return answer["result"]["id"]

    def find_dashboards(self, title: str) -> list[dict[str, Any]]:
        return self._list("dashboard", [{"col": "dashboard_title", "opr": "eq", "value": title}])

    def dashboard_charts(self, dashboard_id: int) -> list[dict[str, Any]]:
        return self._request("GET", f"/api/v1/dashboard/{dashboard_id}/charts").get("result", [])

    def dashboard_datasets(self, dashboard_id: int) -> list[dict[str, Any]]:
        return self._request("GET", f"/api/v1/dashboard/{dashboard_id}/datasets").get("result", [])

    def dataset_dashboards(self, dataset_id: int) -> list[int]:
        """The dashboards a dataset is drawn on, by id — those a row-level rule on it still filters."""
        answer = self._request("GET", f"/api/v1/dataset/{dataset_id}/related_objects", expect=(200, 404), raw=True)
        if answer.status_code == 404:
            return []
        return sorted(one["id"] for one in (answer.json().get("dashboards") or {}).get("result", []))

    def export_dashboard(self, dashboard_id: int) -> bytes:
        """The dashboard as Superset itself exports it: a zip of YAML — dashboard, charts, datasets, connection
        (its password masked by Superset)."""
        answer = self._request("GET", "/api/v1/dashboard/export/", params={"q": prison.dumps([dashboard_id])},
                               raw=True)
        return answer.content

    def import_dashboard(self, bundle: bytes, overwrite: bool = True,
                         passwords: Optional[dict[str, str]] = None) -> None:
        data = {"overwrite": "true" if overwrite else "false"}
        if passwords:
            data["passwords"] = json.dumps(passwords)
        self._request("POST", "/api/v1/dashboard/import/", data=data,
                      files={"formData": ("dashboard.zip", bundle, "application/zip")})

    # ── Superset's own extension system ─────────────────────────────────────────────────────────────────────
    def extensions(self) -> list[dict[str, Any]]:
        """The extensions Superset loaded. An empty list when its extension system is off: the API is then
        not there at all (404), which is an answer, not a failure."""
        try:
            return self._request("GET", "/api/v1/extensions/").get("result") or []
        except SupersetError as exc:
            if exc.status == 404:
                return []
            raise

    # ── tags: what Superset's own dashboard list shows beside a name, and filters on ────────────────────────
    _DASHBOARD = 3      # superset.tags.models.ObjectType

    def dashboard_tags(self, dashboard_id: int) -> list[str]:
        """The tags a person or the engine put on a dashboard — not the ones Superset derives (owner:…, type:…)."""
        found = self.get_dashboard(dashboard_id) or {}
        return [tag["name"] for tag in found.get("tags") or [] if tag.get("type") == 1]

    def tag_dashboard(self, dashboard_id: int, names: list[str]) -> None:
        self.tag("dashboard", dashboard_id, names)

    def untag_dashboard(self, dashboard_id: int, name: str) -> None:
        self._request("DELETE", f"/api/v1/tag/{self._DASHBOARD}/{dashboard_id}/{quote(name, safe='')}/",
                      expect=(200, 204, 404))

    _TAGGED = {"chart": 2, "dashboard": 3, "dataset": 4}      # superset.tags.models.ObjectType

    def tag(self, kind: str, object_id: int, names: list[str]) -> None:
        """Add tags to a chart, a dashboard or a dataset. One already there is left as it is."""
        self._request("POST", f"/api/v1/tag/{self._TAGGED[kind]}/{object_id}/", json={"properties": {"tags": names}})

    # ── access: roles, row-level rules, accounts ────────────────────────────────────────────────────────────
    def find_role(self, name: str) -> Optional[int]:
        found = self._list("security/roles", [{"col": "name", "opr": "eq", "value": name}])
        return found[0]["id"] if found else None

    def ensure_role(self, name: str) -> int:
        """A role with no permission of its own: it is a NAME a dashboard can be granted to. What its holders
        may do in the BI engine at all comes from the base role they also hold."""
        return self.find_role(name) or self._request("POST", "/api/v1/security/roles/", json={"name": name})["id"]

    def dashboard_roles(self, dashboard_id: int) -> list[str]:
        return sorted(role["name"] for role in (self.get_dashboard(dashboard_id) or {}).get("roles", []))

    def dashboard_role_ids(self, dashboard_id: int) -> list[int]:
        """The ids a dashboard is granted to — what an update takes, and what is put back when one fails."""
        return sorted(role["id"] for role in (self.get_dashboard(dashboard_id) or {}).get("roles", []))

    def row_rules(self, prefix: str) -> list[dict[str, Any]]:
        return self._list("rowlevelsecurity", [{"col": "name", "opr": "sw", "value": prefix}])

    def upsert_row_rule(self, name: str, engine_dataset_id: int, role_id: int, clause: str, group_key: str,
                        description: str) -> tuple[int, str]:
        payload = {"name": name, "description": description, "filter_type": "Regular", "clause": clause,
                   "tables": [engine_dataset_id], "roles": [role_id], "group_key": group_key}
        existing = next((rule for rule in self.row_rules(name) if rule["name"] == name), None)
        if existing is None:
            return self._request("POST", "/api/v1/rowlevelsecurity/", json=payload)["id"], "created"
        current = (existing["clause"], [t["id"] for t in existing["tables"]], [r["id"] for r in existing["roles"]],
                   existing["group_key"], existing["filter_type"])
        if current == (clause, [engine_dataset_id], [role_id], group_key, "Regular"):
            return existing["id"], "unchanged"
        self._request("PUT", f"/api/v1/rowlevelsecurity/{existing['id']}", json=payload)
        return existing["id"], "updated"

    def delete_row_rule(self, rule_id: int) -> None:
        self._request("DELETE", f"/api/v1/rowlevelsecurity/{rule_id}", expect=(200, 404))

    def ensure_account(self, username: str, role_ids: list[int], password: str, active: bool = True) -> int:
        found = self._list("security/users", [{"col": "username", "opr": "eq", "value": username}])
        if found:
            self._request("PUT", f"/api/v1/security/users/{found[0]['id']}",
                          json={"roles": role_ids, "password": password, "active": active})
            return found[0]["id"]
        return self._request("POST", "/api/v1/security/users/", json={
            "username": username, "first_name": "SharpSight", "last_name": "access probe",
            "email": f"{username}@sharpsight.local", "active": active, "password": password,
            "roles": role_ids})["id"]

    def set_account_active(self, user_id: int, active: bool) -> None:
        self._request("PUT", f"/api/v1/security/users/{user_id}", json={"active": active})

    def list_accounts(self) -> list[dict[str, Any]]:
        return self._list("security/users", [])

    def set_account_roles(self, user_id: int, role_ids: list[int]) -> None:
        self._request("PUT", f"/api/v1/security/users/{user_id}", json={"roles": role_ids})

    def create_account(self, username: str, first_name: str, last_name: str, email: str, role_ids: list[int],
                       password: str) -> int:
        return self._request("POST", "/api/v1/security/users/", json={
            "username": username, "first_name": first_name, "last_name": last_name, "email": email,
            "active": True, "password": password, "roles": role_ids})["id"]

    def delete_account(self, user_id: int) -> None:
        self._request("DELETE", f"/api/v1/security/users/{user_id}", expect=(200, 404))

    # ── embedding ───────────────────────────────────────────────────────────────────────────────────────────
    def get_embedded(self, dashboard_id: int) -> Optional[dict[str, Any]]:
        try:
            return self._request("GET", f"/api/v1/dashboard/{dashboard_id}/embedded")["result"]
        except SupersetError as exc:
            if exc.status == 404:
                return None
            raise

    def set_embedded(self, dashboard_id: int, allowed_domains: list[str]) -> dict[str, Any]:
        """The configuration is accepted and kept even with EMBEDDED_SUPERSET off, as it is in this kit
        (measured by adapter_check): the flag gates the embedded PAGE, not this record."""
        return self._request("POST", f"/api/v1/dashboard/{dashboard_id}/embedded",
                             json={"allowed_domains": allowed_domains})["result"]

    # ── data ────────────────────────────────────────────────────────────────────────────────────────────────
    def chart_data(self, query_context: dict[str, Any]) -> dict[str, Any]:
        """Run a query the way a chart does — through Superset, its dataset, its saved metrics, its security.
        Returns the first query's outcome with how long it took and how much came back."""
        started = time.monotonic()
        answer = self._session_or_login().post(f"{self.base_url}/api/v1/chart/data", json=query_context,
                                               timeout=self.timeout)
        if answer.status_code == 401:
            self.login()
            answer = self._session.post(f"{self.base_url}/api/v1/chart/data", json=query_context,
                                        timeout=self.timeout)
        duration_ms = int((time.monotonic() - started) * 1000)
        outcome: dict[str, Any] = {"http_status": answer.status_code, "duration_ms": duration_ms,
                                   "bytes": len(answer.content), "error": None, "rowcount": 0, "data": [],
                                   "columns": [], "query": None}
        try:
            body = answer.json()
        except ValueError:
            outcome["error"] = answer.text[:300]
            return outcome
        if answer.status_code != 200:
            outcome["error"] = self._reason(answer)
            return outcome
        result = (body.get("result") or [{}])[0]
        outcome.update(error=result.get("error"), rowcount=result.get("rowcount", 0),
                       data=result.get("data") or [], columns=result.get("colnames") or [],
                       query=result.get("query"))
        return outcome

    def _session_or_login(self) -> requests.Session:
        if self._session is None:
            self.login()
        return self._session
