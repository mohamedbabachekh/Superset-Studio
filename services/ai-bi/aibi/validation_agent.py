"""The validation engine: data, SQL, BI, security, performance. (The visual checks are visual_qa_agent's.)

A check is a MEASUREMENT with a verdict, never an opinion: it names what it looked at, says what it found, and
is recorded whether it passed or not — a report listing only failures cannot be told from a report that did
not look. One failure anywhere keeps a version out of review, and out of UAT.

Several checks here are PROBES: they ask the guards to refuse something a hostile or careless caller would
try — a DELETE, another tenant's identity, a table outside the registry — and fail if the guard lets it
through. Nothing in a probe reaches the database; it is the refusal that is being tested.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .data_gateway import QueryLog, QueryResult
from .models import DashboardSpec, DatasetDef, MetricDef, TemplateDef, ValidationReport
from .policy_engine import Identity, PolicyEngine
from .registry import Registry
from .sql_agent import SqlAgent
from .store import Store

RunSql = Callable[[str, str], QueryResult]

# registry type → what PostgreSQL may call it
_PHYSICAL = {
    "date": ("date",),
    "string": ("text", "character varying", "character"),
    "decimal": ("numeric", "double precision", "real"),
    "bigint": ("bigint", "integer", "smallint", "numeric"),
    "timestamp": ("timestamp with time zone", "timestamp without time zone"),
    "boolean": ("boolean",),
}

_HOSTILE = [
    ("a DELETE", "delete from {relation}"),
    ("an UPDATE", "update {relation} set region = 'x'"),
    ("a DROP", "drop table {relation}"),
    ("two statements in one", "select 1; drop table {relation}"),
    ("SELECT … INTO", "select * into scratch from {relation}"),
    ("a function that waits", "select pg_sleep(600) from {relation}"),
    ("a function that reads files", "select pg_read_file('/etc/passwd')"),
    ("the system catalog", "select rolname, rolpassword from pg_catalog.pg_authid"),
    ("a table outside the registry", "select * from cem_landing.site_kpi_daily"),
]


class ValidationAgent:
    def __init__(self, registry: Registry, policy: PolicyEngine, sql: SqlAgent, store: Store):
        self.registry = registry
        self.policy = policy
        self.sql = sql
        self.store = store

    # ── data ────────────────────────────────────────────────────────────────────────────────────────────────
    def data(self, datasets: list[DatasetDef], metrics_on: dict[str, list[MetricDef]], run_sql: RunSql) -> ValidationReport:
        report = ValidationReport()
        minimum = int(self.registry.policies.get("data", {}).get("min_rows", 1))
        for dataset in datasets:
            ds = dataset.id
            try:
                shape = run_sql(self.sql.shape(dataset, dataset.columns), f"schema of {ds}")
            except Exception as exc:  # noqa: BLE001
                report.add("data", "schema", "fail", ds, f"a registered column is missing: {_short(exc)}")
                continue
            lineage = self.store.lineage_for(dataset.source_model) or {}
            physical = lineage.get("columns", {})
            mismatched = [f"{name} is {physical[name]['type']}, registered as {col.type}"
                          for name, col in dataset.columns.items()
                          if name in physical and not _compatible(col.type, physical[name]["type"])]
            report.add("data", "schema", "fail" if mismatched else "pass", ds,
                       "; ".join(mismatched) or f"{len(shape.columns)} registered columns present",
                       typed_from_catalog=bool(physical))

            rows = run_sql(self.sql.row_count(dataset), f"row count of {ds}").scalar() or 0
            report.add("data", "row count", "pass" if rows >= minimum else "fail", ds, f"{rows} rows", rows=rows)
            if rows < minimum:
                continue

            required = list(dict.fromkeys([*dataset.grain, *dataset.columns_with_role("measure"),
                                           *dataset.columns_with_role("geo_region")]))
            if required:
                counts = run_sql(self.sql.null_counts(dataset, required), f"nulls in {ds}").records()[0]
                holes = {k: v for k, v in counts.items() if v}
                report.add("data", "nulls", "fail" if holes else "pass", ds,
                           ", ".join(f"{k}: {v} null" for k, v in holes.items())
                           or f"none in {', '.join(required)}")

            if dataset.grain:
                repeated = run_sql(self.sql.duplicate_groups(dataset), f"duplicates in {ds}").scalar() or 0
                report.add("data", "duplicates", "fail" if repeated else "pass", ds,
                           f"{repeated} repeated ({', '.join(dataset.grain)})" if repeated
                           else f"({', '.join(dataset.grain)}) is unique")

            if dataset.freshness_sla_minutes and self.sql.freshness(dataset):
                age = run_sql(self.sql.freshness(dataset), f"freshness of {ds}").scalar()
                fresh = age is not None and float(age) <= dataset.freshness_sla_minutes
                report.add("data", "freshness", "pass" if fresh else "fail", ds,
                           f"{float(age or 0):.0f} minutes old, SLA {dataset.freshness_sla_minutes}",
                           age_minutes=round(float(age or 0), 1))
            else:
                report.add("data", "freshness", "skipped", ds, "no freshness SLA is declared for this dataset")

            ranges = self.sql.range_violations(dataset)
            if ranges:
                out = {k: v for k, v in run_sql(ranges, f"ranges in {ds}").records()[0].items() if v}
                report.add("data", "ranges", "fail" if out else "pass", ds,
                           ", ".join(f"{k}: {v} out of range" for k, v in out.items()) or "all within declared ranges")
            for name, column in dataset.columns.items():
                if column.accepted_values:
                    odd = run_sql(self.sql.unexpected_values(dataset, name, column.accepted_values),
                                  f"accepted values of {ds}.{name}").scalar() or 0
                    report.add("data", "accepted values", "fail" if odd else "pass", f"{ds}.{name}",
                               f"{odd} rows outside {column.accepted_values}" if odd else "all values accepted")

            for metric in metrics_on.get(ds, []):
                value = run_sql(self.sql.metric_query(dataset, [metric]), f"{metric.id} on {ds}").scalar()
                ok, why = _in_range(value, metric.valid_range)
                breakdown = dataset.time_column or (dataset.grain[0] if dataset.grain else None)
                if ok and breakdown:
                    series = run_sql(self.sql.metric_query(dataset, [metric], [breakdown]),
                                     f"{metric.id} by {breakdown} on {ds}").records()
                    bad = [r for r in series if not _in_range(r[metric.key], metric.valid_range)[0]]
                    ok, why = not bad, (f"{len(bad)} of {len(series)} {breakdown} values out of range" if bad
                                        else f"{why}; valid on each of {len(series)} {breakdown} values")
                report.add("data", "metric validity", "pass" if ok else "fail", f"{metric.id} on {ds}", why)
        return report

    # ── SQL ─────────────────────────────────────────────────────────────────────────────────────────────────
    def sql_checks(self, log: QueryLog, identity: Identity, model_verdicts: dict[str, Any],
                   timeout_ms: int, max_rows: int) -> ValidationReport:
        report = ValidationReport()
        entries = log.entries
        for criterion, name in (("syntax", "syntax"), ("allowed_database", "allowed database"),
                                ("read_only", "read-only"), ("forbidden_operations", "forbidden operations")):
            bad = [e for e in entries if not e["verdict"].checks.get(criterion, False)]
            report.add("sql", name, "fail" if bad else "pass", f"{len(entries)} statements",
                       "; ".join(e["purpose"] for e in bad[:5]) or "every statement the engine ran passed")
        ran = [e for e in entries if not e["refused"]]
        slowest = max((e["duration_ms"] for e in ran), default=0)
        report.add("sql", "timeout", "pass" if slowest < timeout_ms else "fail", f"{len(ran)} statements",
                   f"slowest {slowest} ms, limit {timeout_ms} ms enforced on the connection", slowest_ms=slowest)
        cut = [e for e in ran if e["truncated"]]
        largest = max((e["row_count"] for e in ran), default=0)
        report.add("sql", "result size", "fail" if cut else "pass", f"{len(ran)} statements",
                   "; ".join(e["purpose"] for e in cut[:5]) or f"largest result {largest} rows, cap {max_rows}")

        for model, verdict in model_verdicts.items():
            report.add("sql", "generated model is a governed select", "pass" if verdict.ok else "fail", model,
                       "; ".join(verdict.problems) or "one select over ref()s of datasets the requester may read")

        # the guard itself, tried
        any_dataset = next((d for d in self.registry.datasets.values() if self.policy.can_read(identity, d)), None)
        relation = any_dataset.relation if any_dataset else "bi.some_table"
        let_through = [what for what, statement in _HOSTILE
                       if self.policy.validate_sql(statement.format(relation=relation), identity).ok]
        report.add("sql", "hostile statements are refused", "fail" if let_through else "pass",
                   f"{len(_HOSTILE)} probes", "accepted: " + ", ".join(let_through) if let_through
                   else "every probe was refused before reaching the database")
        return report

    # ── BI ──────────────────────────────────────────────────────────────────────────────────────────────────
    def bi(self, spec: DashboardSpec, template: TemplateDef, engine_datasets: dict[str, dict[str, Any]],
           data: dict[str, dict[str, Any]], filter_probes: list[dict[str, Any]],
           renders: list[Any]) -> ValidationReport:
        report = ValidationReport()
        for dataset_id in spec.datasets():
            engine = engine_datasets.get(dataset_id)
            report.add("bi", "dataset exists", "pass" if engine else "fail", dataset_id,
                       f"BI engine dataset {engine['id']}" if engine else "not found in the BI engine")
            if not engine:
                continue
            saved = {m["metric_name"]: m["expression"] for m in engine.get("metrics", [])}
            for metric_id in spec.metrics():
                metric = self.registry.metric(metric_id)
                if not any(c.metric == metric_id and c.dataset == dataset_id for c in spec.components):
                    continue
                same = saved.get(metric.key) == metric.expression
                report.add("bi", "metrics resolve", "pass" if same else "fail", f"{metric_id} on {dataset_id}",
                           "saved on the dataset with the registry's expression" if same else
                           ("missing from the dataset" if metric.key not in saved
                            else "the expression in the BI engine differs from the registry's"))

        mandatory = {slot for slot, body in template.slots.items() if body.mandatory}
        for component in spec.components:
            outcome = data.get(component.slot)
            subject = f"{component.slot} ({component.type})"
            if outcome is None:
                report.add("bi", "chart query succeeds", "fail", subject, "no data was retrieved", slot=component.slot)
                continue
            ok = outcome["http_status"] == 200 and not outcome["error"]
            report.add("bi", "chart query succeeds", "pass" if ok else "fail", subject,
                       str(outcome["error"] or f"{outcome['rowcount']} rows in {outcome['duration_ms']} ms"),
                       slot=component.slot, code=None if ok else "chart_error")
            if ok:
                empty = outcome["rowcount"] == 0
                status = "pass" if not empty else ("fail" if component.slot in mandatory else "warn")
                report.add("bi", "no empty mandatory chart", status, subject,
                           "returned no row" if empty else f"{outcome['rowcount']} rows",
                           slot=component.slot, code="empty_component" if empty else None)

        for render in renders:
            if render.error:
                report.add("bi", "dashboard query succeeds", "fail", render.viewport, render.error)
                continue
            charts = [c for c in render.calls if c["kind"] == "chart"]
            failed = [c for c in charts if c["status"] != 200 or c["error"]]
            report.add("bi", "dashboard query succeeds", "fail" if failed or not charts else "pass",
                       render.viewport,
                       "; ".join(f"chart {c['chart_id']}: {c['status']} {c['error'] or ''}".strip() for c in failed[:4])
                       or (f"{len(charts)} chart requests made by the dashboard itself, all succeeded"
                           if charts else "the dashboard made no data request"),
                       requests=len(charts))

        for probe in filter_probes:
            subject = f"{probe['filter']} on {probe['dataset']}"
            filtered, baseline = probe["filtered"], probe["baseline"]
            if filtered["http_status"] != 200 or filtered["error"]:
                report.add("bi", "filters work", "fail", subject, str(filtered["error"] or filtered["http_status"]))
            elif filtered["rowcount"] == 0:
                report.add("bi", "filters work", "fail", subject, f"filtering on {probe['value']!r} returned nothing")
            elif probe["expect_change"] and filtered["data"] == baseline["data"]:
                report.add("bi", "filters work", "fail", subject,
                           f"filtering on {probe['value']!r} changed nothing: the filter is not applied")
            else:
                report.add("bi", "filters work", "pass", subject, f"applied {probe['value']!r} through the BI engine")
        return report

    def dashboard_filters(self, spec: DashboardSpec, charts: dict[str, dict[str, Any]], renders: list[Any],
                          filtered: Optional[Any], applied: Optional[dict[str, Any]]) -> ValidationReport:
        """The filters as CONFIGURED on the dashboard — their targets, their scopes — tried on the dashboard:
        it is opened with every filter set, and each chart in scope must then ask for something else than it
        did unfiltered, and still get an answer."""
        report = ValidationReport()
        if not spec.filters:
            return report
        base = next((r for r in renders if not r.error), None)
        what = ", ".join(f"{k}={v!r}" for k, v in (applied or {}).items())
        if filtered is None or filtered.error or base is None:
            report.add("bi", "filters work", "fail", "on the dashboard",
                       (filtered.error if filtered is not None else None) or "the filtered dashboard was not rendered")
            return report
        before = {c["chart_id"]: c for c in base.calls if c["kind"] == "chart"}
        after = {c["chart_id"]: c for c in filtered.calls if c["kind"] == "chart"}
        for component in spec.components:
            dataset = self.registry.dataset(component.dataset)
            reached = [f for f in (applied or {}) if f in dataset.columns]
            chart_id = (charts.get(component.slot) or {}).get("id")
            if not reached or chart_id is None:
                continue
            subject = f"on the dashboard → {component.slot}"
            answer = after.get(chart_id)
            if answer is None:
                report.add("bi", "filters work", "fail", subject, "the chart made no request once filtered",
                           slot=component.slot)
            elif answer["status"] != 200 or answer["error"]:
                report.add("bi", "filters work", "fail", subject,
                           f"{answer['status']} {answer['error'] or ''}".strip(), slot=component.slot)
            elif answer["digest"] == (before.get(chart_id) or {}).get("digest"):
                report.add("bi", "filters work", "fail", subject,
                           f"with {what} the chart received exactly what it receives unfiltered", slot=component.slot)
            else:
                report.add("bi", "filters work", "pass", subject, f"with {what}: {answer['rowcount']} rows",
                           slot=component.slot)
        return report

    # ── security ────────────────────────────────────────────────────────────────────────────────────────────
    def security(self, spec: DashboardSpec, identity: Identity, log: QueryLog,
                 connection: Optional[dict[str, Any]], access: Optional[dict[str, Any]] = None) -> ValidationReport:
        report = ValidationReport()
        datasets = [self.registry.dataset(ds) for ds in spec.datasets()]

        denied = [d.id for d in datasets if not self.policy.can_read(identity, d)]
        report.add("security", "RBAC", "fail" if denied else "pass", identity.user,
                   "no grant on " + ", ".join(denied) if denied
                   else f"roles {', '.join(identity.roles)} grant every dataset of the dashboard")

        touched = sorted({ds for e in log.entries for ds in e["verdict"].datasets})
        refused = [e["purpose"] for e in log.entries if e["refused"]]
        stranger = Identity(user="probe", type="human", tenant=identity.tenant, roles=(), capabilities=frozenset(),
                            dataset_patterns=(), classifications=frozenset())
        leaks = [d.id for d in datasets
                 if self.policy.validate_sql(f"select count(*) from {d.relation}", stranger).ok]
        report.add("security", "unauthorized dataset access", "fail" if leaks or refused else "pass",
                   f"{len(touched)} datasets read",
                   ("a user with no grant could read " + ", ".join(leaks)) if leaks else
                   ("the engine itself was refused: " + "; ".join(refused[:3])) if refused else
                   "every dataset read is granted to the requester, and a user with no grant is refused each of them")

        applied = []
        for dataset in datasets:
            predicate = self.policy.row_policies(identity, dataset.id)
            if predicate is None:
                continue
            secured = self.policy.secure_sql(f"select count(*) from {dataset.relation}", identity, 1)
            applied.append((dataset.id, predicate.lower().replace(" ", "") in secured.lower().replace(" ", "")))
        missing = [ds for ds, ok in applied if not ok]
        report.add("security", "RLS", "fail" if missing else "pass", identity.user,
                   "policy not applied on " + ", ".join(missing) if missing else
                   (f"row-level policy applied on {', '.join(ds for ds, _ in applied)}" if applied
                    else "no row-level policy is declared for this requester on these datasets"))

        foreign = [d.id for d in datasets if d.tenant != identity.tenant]
        other = Identity(user="probe", type="human", tenant="__another_tenant__", roles=identity.roles,
                         capabilities=identity.capabilities, dataset_patterns=identity.dataset_patterns,
                         classifications=identity.classifications)
        crossing = [d.id for d in datasets if self.policy.can_read(other, d)]
        report.add("security", "tenant isolation", "fail" if foreign or crossing else "pass", identity.tenant,
                   ("datasets of another tenant: " + ", ".join(foreign)) if foreign else
                   ("readable from another tenant: " + ", ".join(crossing)) if crossing else
                   "every dataset belongs to the requester's tenant, and the same roles in another tenant are refused")

        if access is not None:
            granted = access["granted"]
            report.add("security", "BI engine access is what SharpSight grants",
                       "fail" if access["differences"] else "pass", "the dashboard",
                       "; ".join(access["differences"]) or
                       f"granted to {', '.join(granted.get('roles', [])) or 'no role'}; "
                       f"{len(granted.get('row_rules', []))} row-level rule(s) in place")
            probe = access["outsider"]
            report.add("security", "the BI engine refuses an account with no SharpSight role",
                       "pass" if access["outsider_refused"] else "fail", probe["account"],
                       f"dashboard → {probe['dashboard']}; data → "
                       + ", ".join(f"{ds}: {status}" for ds, status in probe["data"].items()))

        if connection is not None:
            writable = [k for k in ("allow_dml", "allow_ctas", "allow_cvas", "allow_file_upload") if connection.get(k)]
            report.add("security", "BI engine connection is read-only", "fail" if writable else "pass",
                       str(connection.get("database_name", "")),
                       "it allows " + ", ".join(writable) if writable else "no DML, no CREATE TABLE AS, no upload")
        return report

    # ── performance ─────────────────────────────────────────────────────────────────────────────────────────
    def performance(self, data: dict[str, dict[str, Any]], renders: list[Any]) -> ValidationReport:
        report = ValidationReport()
        limits = self.registry.policies.get("performance", {})
        query_ms = int(limits.get("chart_query_ms", 5000))
        load_ms = int(limits.get("dashboard_load_ms", 45000))
        max_queries = int(limits.get("max_queries", 25))
        max_rows = int(limits.get("max_result_rows", 5000))
        max_bytes = int(limits.get("max_result_bytes", 1000000))

        slow = {slot: o["duration_ms"] for slot, o in data.items() if o["duration_ms"] > query_ms}
        slowest = max((o["duration_ms"] for o in data.values()), default=0)
        report.add("performance", "query duration", "fail" if slow else "pass", f"{len(data)} chart queries",
                   ", ".join(f"{s}: {ms} ms" for s, ms in slow.items()) or f"slowest {slowest} ms, budget {query_ms}",
                   slowest_ms=slowest)
        for render in renders:
            if render.error:
                continue
            report.add("performance", "dashboard load duration", "pass" if render.load_ms <= load_ms else "fail",
                       render.viewport, f"{render.load_ms} ms, budget {load_ms}", load_ms=render.load_ms)
            report.add("performance", "query count", "pass" if len(render.calls) <= max_queries else "fail",
                       render.viewport, f"{len(render.calls)} requests, budget {max_queries}",
                       queries=len(render.calls))
            heavy = [c for c in render.calls
                     if (c["rowcount"] or 0) > max_rows or (c["bytes"] or 0) > max_bytes]
            biggest = max(((c["bytes"] or 0) for c in render.calls), default=0)
            report.add("performance", "result size", "fail" if heavy else "pass", render.viewport,
                       "; ".join(f"chart {c['chart_id']}: {c['rowcount']} rows, {c['bytes']} bytes" for c in heavy[:4])
                       or f"largest response {biggest} bytes, budget {max_bytes}", largest_bytes=biggest)
        return report


def _compatible(registered: str, physical: str) -> bool:
    physical = physical.lower()
    return any(physical.startswith(candidate) for candidate in _PHYSICAL.get(registered, (registered,)))


def _in_range(value: Any, valid: Optional[list[Optional[float]]]) -> tuple[bool, str]:
    if value is None:
        return False, "the metric is null"
    low, high = (list(valid or []) + [None, None])[:2]
    if low is not None and float(value) < low:
        return False, f"{float(value):.4g} is under {low}"
    if high is not None and float(value) > high:
        return False, f"{float(value):.4g} is over {high}"
    return True, f"{float(value):.4g}"


def _short(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:200]
