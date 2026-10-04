"""The engine's own records, shown by Superset.

Superset is the interface: the engine has no page. What it knows — which projects exist, the gate each stands
at, every check of its last validation, the way through the gates, the audit trail — is shown three ways, and
all three are Superset's own:

  TABLES      The records live in the engine's volume (a SQLite file, a JSON-lines audit trail, git). They are
              copied, whole, into the schema `bi_factory` of the warehouse, where the read-only connection
              Superset charts through can see them. A copy: nothing reads it back.
  DASHBOARDS  « BI Factory » and « Audit trail », made through Superset's REST API like any other dashboard,
              and reached from the SharpSight menu Superset's top bar carries (superset-addon/).
  TAGS        The gate a project stands at is a tag on its dashboard — `gate:UAT` — so Superset's own
              dashboard list shows it and filters on it. Certification at PROD is the lifecycle's.

None of it is a source of truth. Drop the schema, delete the two dashboards, take a tag off: the next refresh
puts it back. And a tag is not content: it is left out of the fingerprint a dashboard is validated under
(lifecycle._GOVERNANCE_KEYS), so showing a gate never looks like a change made after validation.

WHO MAY READ THE COPY. The API asks `audit.read` for the trail; the copy must not be a way round that.
  · It is read through a database role of its own (bi_records_reader) and a Superset connection of its own,
    closed to SQL Lab. The role every BI chart reads through cannot see the schema.
  · Each dashboard is granted — Superset's dashboard roles — to the BI-engine roles of those rbac.yaml gives
    the matching capability: the factory's to those who generate or approve, the trail's to those who hold
    audit.read. Nobody else opens them; an administrator of Superset does, as of everything.
  · They are NOT certified: in Superset's list that badge means « a person approved this for PROD ». What
    marks them as the engine's is a tag, `sharpsight:system`.
  · Every row carries its tenant. This kit has one workspace, so no row-level rule is set on these tables;
    with several, one rule per workspace on `tenant` is what is missing — said in docs/ai-bi.md.

A dashboard here is plain Superset charts over a table (a count, a table, bars over time) written as form
data, without a template or a specification: they show records, they are not BI projects, and nothing of a
project's own is made this way.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import psycopg2
from psycopg2.extras import execute_values

from .audit import AuditLog
from .models import DashboardSpec, utcnow
from .people import READS_THE_TRAIL, ROLE_PREFIX, WORKS_IN_THE_FACTORY
from .registry import Registry
from .store import Store
from .superset_adapter import SupersetAdapter, SupersetError

log = logging.getLogger("aibi.superset_view")

SCHEMA = "bi_factory"
GATE_TAG = "gate:"
EXTENSION = "sharpsight.bi"                 # superset-extension/: the panel of SQL Lab and its backend
MANAGED_TAGS = (GATE_TAG, "project:", "env:", "domain:")      # on a project's dashboard: kept exact
SYSTEM_TAG = "sharpsight:system"
VIEW_VERSION = 4                 # raise it when a dashboard below changes: it is then made again
VERSION_TAG = f"sharpsight:view-v{VIEW_VERSION}"
AUDIT_ROWS = 5000                # the newest records; the whole trail stays in the engine's volume

TABLES: dict[str, list[tuple[str, str]]] = {
    "projects": [("tenant", "text"), ("project_id", "text"), ("title", "text"), ("template", "text"), ("version", "text"),
                 ("gate", "text"), ("environment", "text"), ("created_by", "text"), ("gate_since", "timestamptz"),
                 ("checks", "integer"), ("failures", "integer"), ("warnings", "integer"), ("dashboard", "text"),
                 ("package", "text"), ("git_commit", "text"), ("last_request", "text"), ("last_error", "text")],
    "gate_history": [("tenant", "text"), ("project_id", "text"), ("version", "text"), ("step", "integer"),
                     ("gate", "text"), ("moved_by", "text"), ("moved_at", "timestamptz"), ("note", "text")],
    "checks": [("tenant", "text"), ("project_id", "text"), ("version", "text"), ("category", "text"),
               ("check_name", "text"), ("result", "text"), ("subject", "text"), ("detail", "text")],
    "requests": [("tenant", "text"), ("run_id", "text"), ("project_id", "text"), ("requested_by", "text"),
                 ("request", "text"), ("outcome", "text"), ("gate", "text"), ("version", "text"),
                 ("started_at", "timestamptz"), ("seconds", "integer"), ("error", "text")],
    "audit": [("tenant", "text"), ("seq", "integer"), ("at", "timestamptz"), ("actor", "text"), ("by_user", "text"),
              ("action", "text"), ("project_id", "text"), ("outcome", "text"), ("metrics", "text"),
              ("datasets", "text"), ("git_commit", "text"), ("note", "text")],
    "datasets": [("tenant", "text"), ("dataset_id", "text"), ("name", "text"), ("owner", "text"), ("domain", "text"),
                 ("certified", "text"), ("kind", "text"), ("environment", "text"), ("generated_for", "text"),
                 ("dbt_model", "text"), ("relation", "text"), ("version", "text"),
                 ("freshness_sla_minutes", "integer"), ("tests", "integer"), ("tests_passed", "integer"),
                 ("tests_not_passed", "integer"), ("used_by_dashboards", "integer")],
    "metrics": [("metric_id", "text"), ("name", "text"), ("owner", "text"), ("certified", "text"),
                ("classification", "text"), ("version", "text"), ("unit", "text"), ("used_by_dashboards", "integer")],
    "dbt_models": [("model", "text"), ("layer", "text"), ("environment", "text"), ("materialized", "text"),
                   ("relation", "text"), ("built_from", "text"), ("tests", "integer"), ("tests_passed", "integer"),
                   ("tests_not_passed", "integer"), ("feeds_dataset", "text")],
    "dbt_tests": [("model", "text"), ("environment", "text"), ("test", "text"), ("result", "text")],
    "usage": [("tenant", "text"), ("project_id", "text"), ("dashboard", "text"), ("gate", "text"),
              ("environment", "text"), ("slot", "text"), ("chart", "text"), ("chart_type", "text"),
              ("metric_id", "text"), ("dataset_id", "text"), ("dimension", "text"), ("dbt_model", "text")],
    "deployments": [("tenant", "text"), ("project_id", "text"), ("version", "text"), ("target", "text"),
                    ("decision", "text"), ("decided_by", "text"), ("decided_at", "timestamptz"), ("comment", "text"),
                    ("package", "text"), ("package_sha256", "text"), ("git_commit", "text")],
    "version_changes": [("tenant", "text"), ("project_id", "text"), ("version", "text"), ("from_version", "text"),
                        ("slot", "text"), ("what", "text"), ("was", "text"), ("now", "text"),
                        ("committed_by", "text"), ("committed_at", "timestamptz"), ("comment", "text")],
    "published": [("at", "timestamptz"), ("audit_chain", "text"), ("audit_records", "integer"),
                  ("projects", "integer")],
}


def _count(label: str, where: Optional[str] = None) -> dict[str, Any]:
    form: dict[str, Any] = {"viz_type": "big_number_total", "y_axis_format": ",d", "header_font_size": 0.4,
                            "subheader_font_size": 0.15,
                            "metric": {"expressionType": "SQL", "sqlExpression": "COUNT(*)", "label": label}}
    if where:
        form["adhoc_filters"] = [{"expressionType": "SQL", "clause": "WHERE", "sqlExpression": where}]
    return form


def _rows(columns: list[str], newest: Optional[str] = None, where: Optional[str] = None, limit: int = 1000,
          paged: bool = False) -> dict[str, Any]:
    """A table of rows as they are. `paged`: Superset asks for one page at a time — for a table that can be
    long, so that the browser is never sent all of it."""
    form: dict[str, Any] = {"viz_type": "table", "query_mode": "raw", "all_columns": columns, "row_limit": limit,
                            "include_search": True, "page_length": 25, "server_pagination": paged,
                            "table_timestamp_format": "%Y-%m-%d %H:%M:%S", "show_cell_bars": False}
    if paged:
        form["server_page_length"] = 50
    if newest:
        form["order_by_cols"] = [json.dumps([newest, False])]
    if where:
        form["adhoc_filters"] = [{"expressionType": "SQL", "clause": "WHERE", "sqlExpression": where}]
    return form


def _sum(label: str, condition: str) -> dict[str, Any]:
    return {"expressionType": "SQL", "label": label, "sqlExpression": f"SUM(CASE WHEN {condition} THEN 1 ELSE 0 END)"}


# A dashboard is rows of (key, table, title, width of 12, height, form) under headers. Plain Superset charts:
# a count, a table, a bar over time — there is no visualization of ours here.
DASHBOARDS: dict[str, dict[str, Any]] = {
    "sharpsight-bi-factory": {
        "title": "BI Factory",
        "audience": WORKS_IN_THE_FACTORY,
        "rows": [
            "Where every BI project stands",
            [("projects", "projects", "Projects", 2, 18, _count("projects")),
             ("review", "projects", "Awaiting review", 3, 18, _count("awaiting review", "gate = 'READY_FOR_REVIEW'")),
             ("uat", "projects", "In UAT", 2, 18, _count("in UAT", "gate IN ('APPROVED_FOR_UAT', 'UAT')")),
             ("prod", "projects", "In production", 2, 18, _count("in production", "gate = 'PROD'")),
             ("stopped", "projects", "Stopped at a gate", 3, 18,
              _count("stopped", "gate IN ('VALIDATION_FAILED', 'UAT_FAILED')"))],
            [("list", "projects", "Projects, and the gate each stands at", 12, 44,
              _rows(["project_id", "title", "version", "gate", "environment", "created_by", "gate_since", "checks",
                     "failures", "warnings", "template", "package", "last_error"], newest="gate_since"))],
            "What validation found",
            [("by-category", "checks", "Checks of each project's latest version", 6, 46,
              {"viz_type": "table", "query_mode": "aggregate", "groupby": ["project_id", "version", "category"],
               "metrics": [_sum("passed", "result = 'pass'"), _sum("warned", "result = 'warn'"),
                           _sum("failed", "result = 'fail'")],
               "row_limit": 500, "include_search": True, "page_length": 25, "show_cell_bars": False}),
             ("not-passed", "checks", "Checks that did not pass", 6, 46,
              _rows(["project_id", "version", "category", "check_name", "result", "subject", "detail"],
                    where="result <> 'pass'"))],
            "How each one got there",
            [("gates", "gate_history", "The way through the gates", 6, 50,
              _rows(["moved_at", "project_id", "version", "gate", "moved_by", "note"], newest="moved_at")),
             ("requests", "requests", "What was asked", 6, 50,
              _rows(["started_at", "requested_by", "project_id", "outcome", "gate", "version", "seconds", "request",
                     "error"], newest="started_at"))],
        ],
    },
    "sharpsight-data-quality": {
        "title": "Data Quality",
        "audience": WORKS_IN_THE_FACTORY,
        "rows": [
            "What the governed data is, and whether it passed its tests",
            [("datasets", "datasets", "Governed datasets", 3, 18, _count("datasets")),
             ("certified", "datasets", "Certified", 3, 18, _count("certified", "certified = 'yes'")),
             ("tests-passed", "dbt_tests", "dbt tests passed", 3, 18, _count("passed", "result = 'pass'")),
             ("tests-not-passed", "dbt_tests", "dbt tests not passed", 3, 18, _count("not passed", "result <> 'pass'"))],
            [("list", "datasets", "Datasets: owner, domain, certification, tests", 12, 40,
              _rows(["name", "dataset_id", "owner", "domain", "certified", "environment", "generated_for", "dbt_model",
                     "tests", "tests_passed", "tests_not_passed", "freshness_sla_minutes", "used_by_dashboards",
                     "version"]))],
            "dbt: the models behind them",
            [("models", "dbt_models", "Models, where each is built and what it is built from", 7, 44,
              _rows(["model", "layer", "environment", "materialized", "built_from", "tests", "tests_passed",
                     "tests_not_passed", "feeds_dataset"])),
             ("tests", "dbt_tests", "Every dbt test, and how it ended", 5, 44,
              _rows(["model", "test", "result", "environment"]))],
            "The data checks of each project's latest validation",
            [("data-checks", "checks", "Null rates, ranges, row counts, freshness", 12, 44,
              _rows(["project_id", "version", "check_name", "result", "subject", "detail"], where="category = 'data'"))],
        ],
    },
    "sharpsight-impact-analysis": {
        "title": "Impact Analysis",
        "audience": WORKS_IN_THE_FACTORY,
        "rows": [
            "What a change to a dataset or a metric would touch",
            [("per-dataset", "usage", "Dashboards and charts per dataset", 6, 40,
              {"viz_type": "table", "query_mode": "aggregate", "groupby": ["dataset_id", "dbt_model"],
               "metrics": [{"expressionType": "SQL", "label": "dashboards", "sqlExpression": "COUNT(DISTINCT project_id)"},
                           {"expressionType": "SQL", "label": "charts", "sqlExpression": "COUNT(*)"}],
               "row_limit": 500, "include_search": True, "page_length": 25, "show_cell_bars": False}),
             ("per-metric", "usage", "Dashboards and charts per metric", 6, 40,
              {"viz_type": "table", "query_mode": "aggregate", "groupby": ["metric_id"],
               "metrics": [{"expressionType": "SQL", "label": "dashboards", "sqlExpression": "COUNT(DISTINCT project_id)"},
                           {"expressionType": "SQL", "label": "charts", "sqlExpression": "COUNT(*)"}],
               "row_limit": 500, "include_search": True, "page_length": 25, "show_cell_bars": False})],
            [("where-used", "usage", "Where each one is used — search a dataset, a metric or a model", 12, 56,
              _rows(["dataset_id", "dbt_model", "metric_id", "dimension", "chart", "chart_type", "dashboard",
                     "project_id", "gate", "environment"]))],
            "Upstream: what each model is built from",
            [("lineage", "dbt_models", "dbt lineage", 12, 40,
              _rows(["model", "layer", "built_from", "feeds_dataset", "environment", "relation"]))],
        ],
    },
    "sharpsight-releases": {
        "title": "Releases",
        "audience": WORKS_IN_THE_FACTORY,
        "rows": [
            "What was approved, by whom, and what shipped",
            [("to-uat", "deployments", "Approved for UAT", 4, 18, _count("approved for UAT", "target = 'UAT' AND decision = 'approved'")),
             ("to-prod", "deployments", "Approved for PROD", 4, 18, _count("approved for PROD", "target = 'PROD' AND decision = 'approved'")),
             ("rejected", "deployments", "Rejected", 4, 18, _count("rejected", "decision = 'rejected'"))],
            [("decisions", "deployments", "Every decision, newest first", 12, 44,
              _rows(["decided_at", "project_id", "version", "target", "decision", "decided_by", "comment", "package",
                     "package_sha256", "git_commit"], newest="decided_at"))],
            "What a reviewer is asked to approve: what each commit took from Superset",
            [("changes", "version_changes", "Chart by chart: what was specified, and what it is now", 12, 40,
              _rows(["committed_at", "project_id", "from_version", "version", "slot", "what", "was", "now",
                     "committed_by", "comment"], newest="committed_at"))],
            "The package of each version, and where it stands",
            [("packages", "projects", "Versions and their packages", 6, 44,
              _rows(["project_id", "version", "gate", "environment", "package", "git_commit", "gate_since"],
                    newest="gate_since")),
             ("promotions", "gate_history", "Through the release gates", 6, 44,
              _rows(["moved_at", "project_id", "version", "gate", "moved_by", "note"], newest="moved_at",
                    where="gate IN ('APPROVED_FOR_UAT', 'UAT', 'UAT_FAILED', 'APPROVED_FOR_PROD', 'PROD')"))],
        ],
    },
    "sharpsight-audit-trail": {
        "title": "Audit trail",
        "audience": READS_THE_TRAIL,
        "rows": [
            "Every action, by whom, and how it ended",
            [("records", "audit", "Records shown", 3, 18, _count("records")),
             ("refused", "audit", "Refused", 3, 18, _count("refused", "outcome = 'refused'")),
             ("errors", "audit", "Ended in error", 3, 18, _count("errors", "outcome = 'error'")),
             ("chain", "published", "The chain, when this was published", 3, 18,
              _rows(["audit_chain", "audit_records", "at"]))],
            [("per-day", "audit", "Actions per day", 12, 40,
              {"viz_type": "echarts_timeseries_bar", "x_axis": "at", "time_grain_sqla": "P1D",
               "metrics": [{"expressionType": "SQL", "sqlExpression": "COUNT(*)", "label": "actions"}],
               "groupby": ["outcome"], "stack": "Stack", "row_limit": 10000, "show_legend": True,
               "x_axis_time_format": "%Y-%m-%d", "y_axis_format": ",d", "rich_tooltip": True})],
            [("trail", "audit", "The trail, newest first", 12, 70,
              _rows(["seq", "at", "actor", "by_user", "action", "project_id", "outcome", "metrics", "datasets",
                     "git_commit", "note"], newest="seq", limit=AUDIT_ROWS, paged=True))],
        ],
    },
}


class SupersetView:
    def __init__(self, registry: Registry, store: Store, audit: AuditLog, superset: SupersetAdapter,
                 projects_repo: Path, writer_dsn: str, records_database: tuple[str, str]):
        self.registry = registry
        self.store = store
        self.audit = audit
        self.superset = superset                 # a session of its own: this runs beside a generation
        self.projects_repo = projects_repo
        self._dsn = writer_dsn
        self._records_database = records_database
        self._lock = threading.Lock()
        self._changed = threading.Event()
        self._tables_ready = False
        self._dashboards_ready = False
        self._checks: dict[tuple[str, str, Optional[str]], list[dict[str, Any]]] = {}
        self._tagged: set[tuple[str, int]] = set()      # charts and datasets already given their tags
        self.last: dict[str, Any] = {}
        self.error: Optional[str] = None

    # ── the records, as rows ────────────────────────────────────────────────────────────────────────────────
    def _validation_checks(self, project_id: str, version: str, commit: Optional[str]) -> list[dict[str, Any]]:
        """A version's validation report is in the project's git repository, under its tag — and never changes."""
        key = (project_id, version, commit)
        if key not in self._checks:
            done = subprocess.run(["git", "-c", "safe.directory=*", "show",
                                   f"{project_id}/v{version}:{project_id}/tests/validation_report.json"],
                                  cwd=str(self.projects_repo), capture_output=True)
            self._checks[key] = json.loads(done.stdout).get("checks", []) if done.returncode == 0 else []
        return self._checks[key]

    def records(self) -> dict[str, list[tuple]]:
        rows: dict[str, list[tuple]] = {name: [] for name in TABLES}
        tenant_of_project: dict[str, Optional[str]] = {}
        tenant_of_user = {name: user.tenant for name, user in self.registry.rbac.users.items()}
        for body in self.store.list_projects():
            project_id, tenant = body["project_id"], body.get("tenant")
            tenant_of_project[project_id] = tenant
            latest = self.store.latest_version(project_id)
            attempt = body.get("attempt") or {}
            found = latest.validation if latest else {}
            rows["projects"].append((
                tenant, project_id, body.get("title"), body.get("template"), latest.version if latest else None,
                latest.status if latest else attempt.get("status") or "DRAFT", latest.state if latest else "DEV",
                body.get("created_by"), latest.history[-1].at if latest and latest.history else None,
                found.get("checks"), found.get("failures"), found.get("warnings"),
                (body.get("engine") or {}).get("dashboard", {}).get("url"),
                (latest.package or {}).get("file") if latest else None,
                (latest.git_commit or "")[:12] if latest else None,
                attempt.get("status"), (attempt.get("error") or "")[:300] or None))
            for version in self.store.list_versions(project_id):
                rows["gate_history"] += [(tenant, project_id, version.version, step, change.status, change.by,
                                          change.at, change.note) for step, change in enumerate(version.history, 1)]
            if latest:
                rows["checks"] += [(tenant, project_id, latest.version, check.get("category"), check.get("name"),
                                    check.get("status"), str(check.get("subject") or "")[:200],
                                    str(check.get("detail") or "")[:500])
                                   for check in self._validation_checks(project_id, latest.version, latest.git_commit)]
        for run in self.store.list_runs(limit=500):
            seconds = None
            if run.ended_at:
                seconds = int((datetime.fromisoformat(run.ended_at) - datetime.fromisoformat(run.started_at)).total_seconds())
            rows["requests"].append((tenant_of_project.get(run.project_id or "") or tenant_of_user.get(run.user),
                                     run.id, run.project_id, run.user, " ".join(run.prompt.split())[:500], run.status,
                                     run.quality_gate, run.version, run.started_at, seconds,
                                     (run.error or "")[:300] or None))
        for record in self.audit.read(limit=AUDIT_ROWS):
            detail = record.get("detail") or {}
            note = next((str(detail[k]) for k in ("purpose", "error", "note", "comment", "reason") if detail.get(k)), "")
            rows["audit"].append((tenant_of_project.get(record.get("project_id") or "") or tenant_of_user.get(record.get("user")),
                                  record.get("seq"), record.get("timestamp"), record.get("actor"), record.get("user"),
                                  record.get("action"), record.get("project_id"), record.get("outcome"),
                                  ", ".join(record.get("metrics") or []), ", ".join(record.get("datasets") or []),
                                  (record.get("git_commit") or "")[:12] or None, " ".join(note.split())[:300]))
        # ── the registries and dbt, as rows: what Data Quality and Impact Analysis are drawn from ──────────
        nodes = self.store.list_lineage()
        node_of = {node["name"]: node for node in reversed(nodes)}            # production's, when it knows the model
        used_dataset: dict[str, set[str]] = {}
        used_metric: dict[str, set[str]] = {}
        for body in self.store.list_projects():
            project_id, latest = body["project_id"], self.store.latest_version(body["project_id"])
            charts = (body.get("engine") or {}).get("charts", {})
            try:
                spec = DashboardSpec(**body["spec"]) if body.get("spec") else None
            except ValueError:
                spec = None                                                   # an older engine's: not read as usage
            for component in spec.components if spec else []:
                dataset = self.registry.datasets.get(component.dataset or "")
                used_dataset.setdefault(component.dataset or "", set()).add(project_id)
                used_metric.setdefault(component.metric, set()).add(project_id)
                rows["usage"].append((
                    body.get("tenant"), project_id, spec.dashboard.title,
                    latest.status if latest else (body.get("attempt") or {}).get("status") or "DRAFT",
                    latest.state if latest else "DEV", component.slot,
                    (charts.get(component.slot) or {}).get("name") or component.title, component.type,
                    component.metric, component.dataset, component.dimension,
                    dataset.source_model if dataset else None))
            for version in self.store.list_versions(project_id):
                package = version.package or {}
                rows["deployments"] += [(body.get("tenant"), project_id, version.version, a.target, a.decision, a.by,
                                         a.at, a.comment, package.get("file"), (package.get("sha256") or "")[:16] or None,
                                         (version.git_commit or "")[:12] or None) for a in version.approvals]

        def tests_of(node: Optional[dict[str, Any]]) -> tuple[int, int, int]:
            tests = (node or {}).get("tests", [])
            passed = sum(1 for test in tests if test.get("status") == "pass")
            return len(tests), passed, len(tests) - passed

        for dataset in self.registry.datasets.values():
            rows["datasets"].append((
                dataset.tenant, dataset.id, dataset.name, dataset.owner, dataset.domain or None,
                "yes" if dataset.certified else "no", dataset.kind, dataset.environment, dataset.project,
                dataset.source_model, f"{dataset.db_schema}.{dataset.source_model}" if dataset.db_schema else None,
                dataset.version, dataset.freshness_sla_minutes, *tests_of(node_of.get(dataset.source_model)),
                len(used_dataset.get(dataset.id, ()))))
        for metric in self.registry.metrics.values():
            rows["metrics"].append((metric.id, metric.name, metric.owner, "yes" if metric.certified else "no",
                                    metric.classification, metric.version, metric.unit or None,
                                    len(used_metric.get(metric.id, ()))))
        feeds = {dataset.source_model: dataset.id for dataset in self.registry.datasets.values()}
        for node in nodes:
            path = node.get("path") or ""
            layer = ("generated" if "ai_generated" in node.get("tags", []) else
                     "seed" if node.get("resource_type") == "seed" else
                     next((part for part in ("staging", "intermediate", "marts") if part in path), "model"))
            rows["dbt_models"].append((node["name"], layer, node.get("environment"), node.get("materialized"),
                                       (node.get("relation") or "").replace('"', ""),
                                       ", ".join(parent["name"] for parent in node.get("depends_on", [])) or None,
                                       *tests_of(node), feeds.get(node["name"])))
            rows["dbt_tests"] += [(node["name"], node.get("environment"), test.get("name"), test.get("status") or "not run")
                                  for test in node.get("tests", [])]
        # what each commit took from Superset — the trail has it, and the run says which version it became
        became = {run.request_id: run.version for run in self.store.list_runs(limit=500)}
        for record in self.audit.read(action="commit:edited_in_superset", limit=AUDIT_ROWS):
            detail, project_id = record.get("detail") or {}, record.get("project_id")
            base = (tenant_of_project.get(project_id or ""), project_id, became.get(record.get("request_id")),
                    detail.get("from_version"))
            tail = (record.get("user"), record.get("timestamp"), detail.get("comment") or None)
            for slot, changes in (detail.get("taken") or {}).items():
                rows["version_changes"] += [(*base, slot, field, str(change.get("specification")),
                                             str(change.get("superset")), *tail) for field, change in changes.items()]
            for slot, settings in (detail.get("set_back") or {}).items():
                rows["version_changes"].append((*base, slot, "set back (no place in a specification)",
                                                ", ".join(settings), None, *tail))
            for undone in detail.get("undone_on_the_dashboard") or []:
                rows["version_changes"].append((*base, "the dashboard itself", "undone by the commit", undone, None, *tail))
        chain = self.audit.verify()
        rows["published"].append((utcnow(), "intact" if chain.get("ok") else f"BROKEN at record {chain.get('broken_at_seq')}",
                                  chain.get("records"), len(rows["projects"])))
        return rows

    # ── 1 · tables ──────────────────────────────────────────────────────────────────────────────────────────
    def publish(self) -> dict[str, int]:
        rows = self.records()
        connection = psycopg2.connect(self._dsn, connect_timeout=10, application_name="sharpsight-ai-bi view")
        try:
            with connection, connection.cursor() as cursor:          # one transaction: never read half-written
                for table, columns in TABLES.items():
                    if not self._tables_ready:                        # the definition may be an older one's
                        cursor.execute(f"drop table if exists {SCHEMA}.{table}")
                    cursor.execute(f"create table if not exists {SCHEMA}.{table} ("
                                   + ", ".join(f"{name} {kind}" for name, kind in columns) + ")")
                    cursor.execute(f"truncate {SCHEMA}.{table}")
                    if rows[table]:
                        execute_values(cursor, f"insert into {SCHEMA}.{table} values %s", rows[table])
        finally:
            connection.close()
        self._tables_ready = True
        return {table: len(found) for table, found in rows.items()}

    # ── 2 · dashboards ──────────────────────────────────────────────────────────────────────────────────────
    def audience(self, slug: str) -> list[str]:
        """The BI-engine roles a dashboard over the records is granted to: those of the rbac.yaml roles that
        hold one of the capabilities it is for."""
        return sorted(ROLE_PREFIX + name for name, role in self.registry.rbac.roles.items()
                      if any(capability in role.capabilities for capability in DASHBOARDS[slug]["audience"]))

    def _in_place(self, slug: str) -> Optional[int]:
        """The dashboard's id when it is there, is this version's, and has all its charts."""
        existing = self.superset.get_dashboard(slug)
        if not existing:
            return None
        wanted = sum(len(row) for row in DASHBOARDS[slug]["rows"] if not isinstance(row, str))
        if VERSION_TAG in self.superset.dashboard_tags(existing["id"]) and \
                len(self.superset.dashboard_charts(existing["id"])) == wanted:
            return existing["id"]
        return None

    def ensure_dashboards(self) -> list[str]:
        """Make the two dashboards if they are not there, or are an older version's. Returns what was made."""
        made: list[str] = []
        database_id = self.superset.ensure_database(*self._records_database, sql_lab=False)
        datasets: dict[str, int] = {}
        for table in TABLES:
            found = self.superset.find_dataset(database_id, SCHEMA, table)
            datasets[table] = found["id"] if found else self.superset.create_dataset(database_id, SCHEMA, table)
            if not found:
                self.superset.update_dataset(datasets[table], {
                    "description": f"The AI BI engine's own records — {table.replace('_', ' ')}. A copy, rewritten whole.",
                    "is_managed_externally": True, "external_url": f"sharpsight://factory/{table}"})
        for slug, view in DASHBOARDS.items():
            dashboard_id = self._in_place(slug)
            if dashboard_id is None:
                dashboard_id = self._make(slug, view, datasets)
                made.append(slug)
            # granted to its audience every time: a role taken off by hand in Superset comes back, like a tag
            wanted_roles = self.audience(slug)
            if self.superset.dashboard_roles(dashboard_id) != wanted_roles:
                self.superset.update_dashboard(dashboard_id, {
                    "roles": [self.superset.ensure_role(name) for name in wanted_roles]})
        self._dashboards_ready = True
        return made

    def _make(self, slug: str, view: dict[str, Any], datasets: dict[str, int]) -> int:
        existing = self.superset.get_dashboard(slug)
        if existing:
            for chart in self.superset.dashboard_charts(existing["id"]):
                self.superset.delete_chart(chart["id"])
            self.superset.delete_dashboard(existing["id"])
        for table in {chart[1] for row in view["rows"] if not isinstance(row, str) for chart in row}:
            self.superset.refresh_dataset(datasets[table])
        dashboard_id = self.superset.create_dashboard({
            "dashboard_title": view["title"], "slug": slug, "published": True, "is_managed_externally": True,
            "external_url": f"sharpsight://factory/{slug}"})
        position: dict[str, Any] = {
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
            "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
            "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": view["title"]}},
        }
        for number, row in enumerate(view["rows"], 1):
            if isinstance(row, str):
                node = f"HEADER-{number}"
                position[node] = {"type": "HEADER", "id": node, "children": [], "parents": ["ROOT_ID", "GRID_ID"],
                                  "meta": {"text": row, "headerSize": "SMALL_HEADER",
                                           "background": "BACKGROUND_TRANSPARENT"}}
                position["GRID_ID"]["children"].append(node)
                continue
            row_id = f"ROW-{number}"
            position[row_id] = {"type": "ROW", "id": row_id, "children": [], "parents": ["ROOT_ID", "GRID_ID"],
                                "meta": {"background": "BACKGROUND_TRANSPARENT"}}
            position["GRID_ID"]["children"].append(row_id)
            for key, table, title, width, height, form in row:
                chart_id = self.superset.create_chart({
                    "slice_name": title, "viz_type": form["viz_type"], "datasource_id": datasets[table],
                    "datasource_type": "table", "dashboards": [dashboard_id],
                    "params": json.dumps({**form, "datasource": f"{datasets[table]}__table"}, ensure_ascii=False),
                    "description": f"The AI BI engine's own records ({SCHEMA}.{table}).",
                    "is_managed_externally": True, "external_url": f"sharpsight://factory/{slug}/{key}"})
                chart = self.superset.get_chart(chart_id) or {}
                node = f"CHART-{key}"
                position[node] = {"type": "CHART", "id": node, "children": [],
                                  "parents": ["ROOT_ID", "GRID_ID", row_id],
                                  "meta": {"chartId": chart_id, "width": width, "height": height,
                                           "sliceName": title, "uuid": chart.get("uuid")}}
                position[row_id]["children"].append(node)
        self.superset.update_dashboard(dashboard_id, {
            "position_json": json.dumps(position),
            "json_metadata": json.dumps({"cross_filters_enabled": True, "refresh_frequency": 0})})
        self.superset.tag_dashboard(dashboard_id, [SYSTEM_TAG, VERSION_TAG])
        return dashboard_id

    # ── 3 · the gate, in Superset's own dashboard list ──────────────────────────────────────────────────────
    def marks(self, body: dict[str, Any]) -> list[str]:
        """What Superset's own dashboard list should say of a project's dashboard: the environment and the gate
        it stands in, and its project. Three, and in that order of the alphabet, on purpose: the list shows the
        first two tags of a dashboard and counts the rest (seen on 6.1) — with a domain tag as well, the gate
        was the one hidden. The domain is said where it describes something: on the charts and the datasets."""
        latest = self.store.latest_version(body["project_id"])
        gate = latest.status if latest else (body.get("attempt") or {}).get("status") or "DRAFT"
        return [GATE_TAG + gate, f"project:{body['project_id']}", f"env:{latest.state if latest else 'DEV'}"]

    def domains(self, body: dict[str, Any]) -> list[str]:
        datasets = (body.get("engine") or {}).get("datasets", {})
        return sorted({self.registry.datasets[d].domain for d in datasets
                       if d in self.registry.datasets and self.registry.datasets[d].domain})

    def mark_projects(self) -> list[dict[str, Any]]:
        """Tags, in Superset's own lists. On a dashboard they are kept EXACT — the gate moves, the environment
        with it — and a tag a person added is left alone. On its charts and datasets they never change
        (which project, which domain), so they are added once and not asked about again."""
        changed: list[dict[str, Any]] = []
        for body in self.store.list_projects():
            engine = body.get("engine") or {}
            dashboard_id = engine.get("dashboard", {}).get("id")
            if dashboard_id is None or self.superset.get_dashboard(dashboard_id) is None:
                continue
            wanted = self.marks(body)
            ours = [tag for tag in self.superset.dashboard_tags(dashboard_id) if tag.startswith(MANAGED_TAGS)]
            if sorted(ours) != sorted(wanted):
                for tag in ours:
                    if tag not in wanted:
                        self.superset.untag_dashboard(dashboard_id, tag)
                missing = [tag for tag in wanted if tag not in ours]
                if missing:
                    self.superset.tag_dashboard(dashboard_id, missing)
                changed.append({"project_id": body["project_id"], "dashboard": dashboard_id, "tag": wanted[0],
                                "tags": wanted, "was": ours})
            chart_tags = [f"project:{body['project_id']}", *(f"domain:{domain}" for domain in self.domains(body))]
            for chart in engine.get("charts", {}).values():
                if chart.get("id") is not None and ("chart", chart["id"]) not in self._tagged:
                    self.superset.tag("chart", chart["id"], chart_tags)
                    self._tagged.add(("chart", chart["id"]))
            for dataset_id, engine_id in engine.get("datasets", {}).items():
                dataset = self.registry.datasets.get(dataset_id)
                if dataset is None or ("dataset", engine_id) in self._tagged:
                    continue
                self.superset.tag("dataset", engine_id, [tag for tag in (
                    f"domain:{dataset.domain}" if dataset.domain else None,
                    "sharpsight:certified" if dataset.certified else "sharpsight:generated") if tag])
                self._tagged.add(("dataset", engine_id))
        return changed

    # ── keeping it current ──────────────────────────────────────────────────────────────────────────────────
    def refresh(self) -> dict[str, Any]:
        with self._lock:
            published = self.publish()
            # two questions to Superset, so that a dashboard deleted there comes back at the next refresh
            if self._dashboards_ready and any(self.superset.get_dashboard(slug) is None for slug in DASHBOARDS):
                self._dashboards_ready = False
            made = [] if self._dashboards_ready else self.ensure_dashboards()
            tagged = self.mark_projects()
            for change in tagged:
                self.audit.record(action="superset:gate_tag", user="system", actor="system", request_id="-",
                                  project_id=change["project_id"],
                                  superset_assets=[{"type": "dashboard", "id": change["dashboard"],
                                                    "action": "tagged", "tag": change["tag"]}],
                                  detail={"note": ", ".join(change["tags"]) + " in Superset's dashboard list", "was": change["was"]})
            loaded = {found.get("id"): found.get("version") for found in self.superset.extensions()}
            self.last = {"at": utcnow(), "rows": published, "dashboards_made": made, "gates_tagged": len(tagged),
                         # Superset's loader fails quietly — it prints, and starts without the extension:
                         # so it is asked, each time, whether sharpsight-bi.supx is among what it loaded
                         "extension": f"sharpsight.bi {loaded[EXTENSION]}" if EXTENSION in loaded else None}
            self.error = None
            return self.last

    def watch(self) -> None:
        """Refresh when something happened — every action leaves an audit record, and the trail says so
        (AuditLog.on_record): nothing is polled. A burst of records is one refresh. A failure — Superset
        restarting, the warehouse away — is kept for /health to show and tried again within a minute: what
        Superset shows is late, never wrong for long."""
        self.audit.on_record = self._changed.set

        def loop() -> None:
            self._changed.set()                               # once at start: the records as they stand
            while True:
                self._changed.wait(timeout=60 if self.error else None)
                threading.Event().wait(2)                     # let a burst finish
                self._changed.clear()
                try:
                    self.refresh()
                except (psycopg2.Error, SupersetError, OSError, ValueError, KeyError) as error:
                    self._dashboards_ready = False
                    self.error = f"{type(error).__name__}: {str(error)[:300]}"
                    log.warning("the records could not be shown in Superset — %s", self.error)
        threading.Thread(target=loop, name="superset-view", daemon=True).start()
