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
import time
from typing import Any, Optional

import prison
import requests


class SupersetError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


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

    def _list(self, resource: str, filters: list[dict[str, Any]], columns: Optional[list[str]] = None) -> list[dict]:
        query: dict[str, Any] = {"filters": filters, "page_size": 100}
        if columns:
            query["columns"] = columns
        return self._request("GET", f"/api/v1/{resource}/", params={"q": prison.dumps(query)}).get("result", [])

    def health(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/health", timeout=10).status_code == 200
        except requests.RequestException:
            return False

    # ── database connections ────────────────────────────────────────────────────────────────────────────────
    def find_database(self, name: str) -> Optional[dict[str, Any]]:
        found = self._list("database", [{"col": "database_name", "opr": "eq", "value": name}])
        return found[0] if found else None

    def ensure_database(self, name: str, sqlalchemy_uri: str) -> int:
        """The read-only connection the engine's datasets hang from. Created once, and its URI kept current —
        a password rotated in .env reaches Superset on the next start."""
        payload = {"database_name": name, "sqlalchemy_uri": sqlalchemy_uri, "expose_in_sqllab": True,
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
        source = self.get_dashboard(dashboard_id) or {}
        answer = self._request("POST", f"/api/v1/dashboard/{dashboard_id}/copy/",
                               json={"dashboard_title": title, "duplicate_slices": duplicate_charts,
                                     "json_metadata": source.get("json_metadata") or "{}"})
        return answer["result"]["id"]

    def dashboard_charts(self, dashboard_id: int) -> list[dict[str, Any]]:
        return self._request("GET", f"/api/v1/dashboard/{dashboard_id}/charts").get("result", [])

    def dashboard_datasets(self, dashboard_id: int) -> list[dict[str, Any]]:
        return self._request("GET", f"/api/v1/dashboard/{dashboard_id}/datasets").get("result", [])

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

    # ── embedding ───────────────────────────────────────────────────────────────────────────────────────────
    def get_embedded(self, dashboard_id: int) -> Optional[dict[str, Any]]:
        try:
            return self._request("GET", f"/api/v1/dashboard/{dashboard_id}/embedded")["result"]
        except SupersetError as exc:
            if exc.status == 404:
                return None
            raise

    def set_embedded(self, dashboard_id: int, allowed_domains: list[str]) -> dict[str, Any]:
        """Needs EMBEDDED_SUPERSET, which this kit leaves off (superset_config.py): a workbench is not embedded."""
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
