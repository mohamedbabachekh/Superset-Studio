"""The SQL the engine writes for itself: metric queries and the profiling behind data validation.

Every statement is assembled from registry entries — a dataset's relation, a metric's governed expression,
column names the registry knows — with identifiers quoted and values escaped. It then goes through the same
door as anyone's SQL: validated by the policy engine, run by the data gateway. Writing a query here grants
nothing.

When a language model is wired in, this is where its SQL would enter too, and that door does not move.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from .models import DatasetDef, MetricDef


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


class SqlAgent:
    def metric_query(self, dataset: DatasetDef, metrics: Iterable[MetricDef], dimensions: Iterable[str] = (),
                     filters: Optional[dict[str, list[Any]]] = None, order_by: Optional[str] = None,
                     descending: bool = True, limit: Optional[int] = None) -> str:
        dimensions = list(dimensions)
        select = [quote(d) for d in dimensions] + [f"{m.expression} as {quote(m.key)}" for m in metrics]
        sql = f"select {', '.join(select)}\nfrom {dataset.relation}"
        if filters:
            clauses = [f"{quote(col)} in ({', '.join(literal(v) for v in values)})"
                       for col, values in filters.items() if values]
            if clauses:
                sql += "\nwhere " + " and ".join(clauses)
        if dimensions:
            sql += "\ngroup by " + ", ".join(quote(d) for d in dimensions)
        if order_by:
            sql += f"\norder by {quote(order_by)} {'desc' if descending else 'asc'} nulls last"
        elif dimensions:
            sql += "\norder by " + ", ".join(quote(d) for d in dimensions)
        if limit:
            sql += f"\nlimit {int(limit)}"
        return sql

    def shape(self, dataset: DatasetDef, columns: Iterable[str]) -> str:
        """No rows: only proves that every registered column exists, and brings back their types."""
        return f"select {', '.join(quote(c) for c in columns)}\nfrom {dataset.relation}\nlimit 0"

    def sample(self, dataset: DatasetDef, columns: Iterable[str], limit: int) -> str:
        return f"select {', '.join(quote(c) for c in columns)}\nfrom {dataset.relation}\nlimit {int(limit)}"

    def row_count(self, dataset: DatasetDef) -> str:
        return f"select count(*) as row_count\nfrom {dataset.relation}"

    def null_counts(self, dataset: DatasetDef, columns: Iterable[str]) -> str:
        parts = [f"count(*) filter (where {quote(c)} is null) as {quote(c)}" for c in columns]
        return f"select {', '.join(parts)}\nfrom {dataset.relation}"

    def duplicate_groups(self, dataset: DatasetDef) -> str:
        grain = ", ".join(quote(c) for c in dataset.grain)
        return (f"select count(*) as duplicate_groups\nfrom (\n  select {grain}\n  from {dataset.relation}\n"
                f"  group by {grain}\n  having count(*) > 1\n) as repeated")

    def range_violations(self, dataset: DatasetDef) -> Optional[str]:
        parts = []
        for name, column in dataset.columns.items():
            if not column.range:
                continue
            low, high = (list(column.range) + [None, None])[:2]
            bounds = [f"{quote(name)} < {literal(low)}"] if low is not None else []
            if high is not None:
                bounds.append(f"{quote(name)} > {literal(high)}")
            if bounds:
                parts.append(f"count(*) filter (where {' or '.join(bounds)}) as {quote(name)}")
        return f"select {', '.join(parts)}\nfrom {dataset.relation}" if parts else None

    def unexpected_values(self, dataset: DatasetDef, column: str, accepted: list[str]) -> str:
        allowed = ", ".join(literal(v) for v in accepted)
        return (f"select count(*) as unexpected\nfrom {dataset.relation}\n"
                f"where {quote(column)} is not null and {quote(column)} not in ({allowed})")

    def freshness(self, dataset: DatasetDef) -> Optional[str]:
        column = dataset.freshness_column or dataset.time_column
        if not column:
            return None
        return (f"select extract(epoch from (now() - max({quote(column)}))) / 60.0 as age_minutes\n"
                f"from {dataset.relation}")

    def distinct_values(self, dataset: DatasetDef, column: str, limit: int = 50) -> str:
        return (f"select {quote(column)} as value, count(*) as rows\nfrom {dataset.relation}\n"
                f"where {quote(column)} is not null\ngroup by {quote(column)}\norder by rows desc, value\n"
                f"limit {int(limit)}")
