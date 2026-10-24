"""SharpSight's own memory of BI projects: runs, versions, the BI engine's ids, lineage.

A STAND-IN for the platform's database. In the target architecture these tables live in SharpSight's
PostgreSQL behind the portal; here they are one SQLite file in the service's volume, with the same content,
so the control plane's knowledge survives a Superset that is wiped, upgraded or replaced.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from .models import ProjectVersion, RunRecord

_SCHEMA = """
create table if not exists projects (project_id text primary key, body text not null, updated_at text not null);
create table if not exists versions (project_id text not null, version text not null, body text not null,
                                     created_at text not null, primary key (project_id, version));
create table if not exists runs (id text primary key, project_id text, body text not null, started_at text not null);
create table if not exists lineage (environment text not null, unique_id text not null, name text not null,
                                    body text not null, primary key (environment, unique_id));
"""


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    # ── projects ────────────────────────────────────────────────────────────────────────────────────────────
    def get_project(self, project_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as db:
            row = db.execute("select body from projects where project_id = ?", (project_id,)).fetchone()
        return json.loads(row["body"]) if row else None

    def save_project(self, project_id: str, body: dict[str, Any], updated_at: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("insert into projects values (?, ?, ?) on conflict(project_id) do update set "
                       "body = excluded.body, updated_at = excluded.updated_at",
                       (project_id, json.dumps(body, ensure_ascii=False), updated_at))

    def delete_project(self, project_id: str) -> None:
        """The project and its versions. Its runs stay: what was attempted is history, not state."""
        with self._lock, self._connect() as db:
            db.execute("delete from versions where project_id = ?", (project_id,))
            db.execute("delete from projects where project_id = ?", (project_id,))

    def list_projects(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("select body from projects order by updated_at desc").fetchall()
        return [json.loads(r["body"]) for r in rows]

    # ── versions ────────────────────────────────────────────────────────────────────────────────────────────
    def save_version(self, version: ProjectVersion) -> None:
        with self._lock, self._connect() as db:
            db.execute("insert into versions values (?, ?, ?, ?) on conflict(project_id, version) do update set "
                       "body = excluded.body",
                       (version.project_id, version.version, version.model_dump_json(), version.created_at))

    def get_version(self, project_id: str, version: str) -> Optional[ProjectVersion]:
        with self._connect() as db:
            row = db.execute("select body from versions where project_id = ? and version = ?",
                             (project_id, version)).fetchone()
        return ProjectVersion.model_validate_json(row["body"]) if row else None

    def list_versions(self, project_id: str) -> list[ProjectVersion]:
        with self._connect() as db:
            rows = db.execute("select body from versions where project_id = ?", (project_id,)).fetchall()
        versions = [ProjectVersion.model_validate_json(r["body"]) for r in rows]
        return sorted(versions, key=lambda v: version_key(v.version))

    def latest_version(self, project_id: str) -> Optional[ProjectVersion]:
        versions = self.list_versions(project_id)
        return versions[-1] if versions else None

    # ── runs ────────────────────────────────────────────────────────────────────────────────────────────────
    def save_run(self, run: RunRecord) -> None:
        with self._lock, self._connect() as db:
            db.execute("insert into runs values (?, ?, ?, ?) on conflict(id) do update set "
                       "body = excluded.body, project_id = excluded.project_id",
                       (run.id, run.project_id, run.model_dump_json(), run.started_at))

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        with self._connect() as db:
            row = db.execute("select body from runs where id = ?", (run_id,)).fetchone()
        return RunRecord.model_validate_json(row["body"]) if row else None

    def list_runs(self, project_id: Optional[str] = None, limit: int = 20) -> list[RunRecord]:
        with self._connect() as db:
            if project_id:
                rows = db.execute("select body from runs where project_id = ? order by started_at desc limit ?",
                                  (project_id, limit)).fetchall()
            else:
                rows = db.execute("select body from runs order by started_at desc limit ?", (limit,)).fetchall()
        return [RunRecord.model_validate_json(r["body"]) for r in rows]

    # ── lineage ─────────────────────────────────────────────────────────────────────────────────────────────
    def save_lineage(self, nodes: list[dict[str, Any]]) -> None:
        with self._lock, self._connect() as db:
            for node in nodes:
                db.execute("insert into lineage values (?, ?, ?, ?) on conflict(environment, unique_id) do update "
                           "set body = excluded.body, name = excluded.name",
                           (node["environment"], node["unique_id"], node["name"],
                            json.dumps(node, ensure_ascii=False)))

    def list_lineage(self) -> list[dict[str, Any]]:
        """Every dbt model and seed the engine knows — the production build's, and the generated ones."""
        with self._connect() as db:
            rows = db.execute("select body from lineage order by environment desc, name").fetchall()
        return [json.loads(r["body"]) for r in rows]

    def lineage_for(self, model_name: str) -> Optional[dict[str, Any]]:
        """A model's own record: the production build's when it knows the model, the workspace's otherwise."""
        with self._connect() as db:
            rows = db.execute("select body from lineage where name = ? order by environment desc",
                              (model_name,)).fetchall()
        return json.loads(rows[0]["body"]) if rows else None

    def lineage_by_id(self, unique_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("select body from lineage where unique_id = ? order by environment desc",
                              (unique_id,)).fetchall()
        return json.loads(rows[0]["body"]) if rows else None
