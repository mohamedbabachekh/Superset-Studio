"""The only road from the engine to the data: validated, capped, timed, and read-only twice over.

    AI → tools → policy engine → THIS → a read-only role

The connection's role cannot write (bootstrap.sql sets default_transaction_read_only on the role itself), the
session asks for a read-only transaction anyway, and the statement that runs is never the one that was
written: it is the validated query, with row-level policies applied, inside a hard row limit.

Rows come back to the caller. They do not go to the audit log, and the caller decides what a model may see.
"""
from __future__ import annotations

import datetime as dt
import decimal
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import psycopg2

from .policy_engine import Identity, PolicyEngine, PolicyError, SqlVerdict


@dataclass
class QueryResult:
    sql: str
    executed_sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    duration_ms: int
    verdict: SqlVerdict
    purpose: str = ""

    def records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row)) for row in self.rows]

    def scalar(self) -> Any:
        return self.rows[0][0] if self.rows else None


@dataclass
class QueryLog:
    """What a request ran, kept so the SQL validation can account for every statement afterwards."""
    entries: list[dict[str, Any]] = field(default_factory=list)


def _plain(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, memoryview)):
        return "<binary>"
    return value


class DataGateway:
    def __init__(self, dsn: str, policy: PolicyEngine):
        self._dsn = dsn
        self.policy = policy
        limits = policy.registry.policies.get("sql", {})
        self.timeout_ms = int(limits.get("timeout_ms", 30000))
        self.max_rows = int(limits.get("max_rows", 10000))

    def run(self, sql: str, identity: Identity, limit: Optional[int] = None, purpose: str = "",
            log: Optional[QueryLog] = None) -> QueryResult:
        verdict = self.policy.validate_sql(sql, identity)
        if not verdict.ok:
            if log is not None:
                log.entries.append({"sql": sql, "purpose": purpose, "verdict": verdict, "refused": True})
            raise PolicyError("the query was refused: " + "; ".join(verdict.problems), "sql_refused")
        cap = min(int(limit or self.max_rows), self.max_rows)
        executed = self.policy.secure_sql(sql, identity, cap + 1)

        started = time.monotonic()
        connection = psycopg2.connect(self._dsn, connect_timeout=10, application_name="sharpsight-ai-bi")
        try:
            connection.set_session(readonly=True, autocommit=False)
            with connection.cursor() as cursor:
                cursor.execute("set local statement_timeout = %s", (self.timeout_ms,))
                cursor.execute(executed)
                columns = [d.name for d in cursor.description]
                fetched = cursor.fetchmany(cap + 1)
        finally:
            connection.rollback()
            connection.close()
        duration_ms = int((time.monotonic() - started) * 1000)

        truncated = len(fetched) > cap
        rows = [[_plain(v) for v in row] for row in fetched[:cap]]
        result = QueryResult(sql=sql, executed_sql=executed, columns=columns, rows=rows, row_count=len(rows),
                             truncated=truncated, duration_ms=duration_ms, verdict=verdict, purpose=purpose)
        if log is not None:
            log.entries.append({"sql": sql, "purpose": purpose, "verdict": verdict, "refused": False,
                                "duration_ms": duration_ms, "row_count": len(rows), "truncated": truncated})
        return result

    def ping(self) -> bool:
        connection = psycopg2.connect(self._dsn, connect_timeout=5)
        try:
            with connection.cursor() as cursor:
                cursor.execute("select 1")
                return cursor.fetchone()[0] == 1
        finally:
            connection.close()
