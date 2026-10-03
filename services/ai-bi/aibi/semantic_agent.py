"""The governed metrics: which one a request means, where it may be computed, how it is handed to the BI engine.

The rule this agent exists to keep: a number on a dashboard is a REGISTERED metric. It does not write a
formula. When a request names something the registry does not hold, it says so and stops there.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from .models import DatasetDef, MetricDef, Requirement
from .registry import Registry

# The registry writes formats the way a spreadsheet does; the BI engine wants d3.
_D3 = {"#,##0": ",.0f", "#,##0.0": ",.1f", "#,##0.00": ",.2f", "0": ".0f", "0.0": ".1f", "0.00": ".2f",
       "0.000": ".3f", "0%": ".0%", "0.0%": ".1%", "0.00%": ".2%"}


def d3_format(fmt: str, compact: bool = False) -> str:
    if compact:
        return ".3~s"
    return _D3.get(fmt, ",.2f")


class SemanticAgent:
    def __init__(self, registry: Registry):
        self.registry = registry

    def find_metrics(self, requirement: Requirement) -> tuple[list[MetricDef], list[str]]:
        """The requested metrics, and what to tell the user about them. Certified ones are used as they are;
        an uncertified one is used and flagged; one that is not registered is refused, never improvised."""
        found: list[MetricDef] = []
        notes: list[str] = []
        for metric_id in requirement.metrics:
            metric = self.registry.metrics.get(metric_id)
            if metric is None:
                notes.append(f"{metric_id} is not a registered metric and was not used")
                continue
            if not metric.certified:
                notes.append(f"{metric.id} is registered but NOT certified")
            found.append(metric)
        return found, notes

    def valid_on(self, metric: MetricDef, dataset: DatasetDef) -> tuple[bool, str]:
        """May this metric be computed on this dataset? On the datasets its lineage names; and on one DERIVED
        from them, provided it still has the columns and was not re-grained — a join that fanned the rows out
        would silently inflate every sum."""
        missing = [c for c in metric.lineage.columns if c not in dataset.columns]
        if missing:
            return False, f"{dataset.id} lacks {', '.join(missing)}"
        if dataset.id in metric.lineage.datasets:
            return True, "named in the metric's lineage"
        for parent_id in dataset.derived_from:
            parent = self.registry.datasets.get(parent_id)
            if parent_id in metric.lineage.datasets and parent is not None and parent.grain == dataset.grain:
                return True, f"derived from {parent_id} at the same grain"
        return False, f"{dataset.id} is outside the metric's lineage"

    def engine_metric(self, metric: MetricDef) -> dict[str, Any]:
        """The metric as the BI engine stores it on a dataset — same expression, and a certification badge
        that names the registry entry it came from."""
        certification = ({"certification": {"certified_by": metric.owner,
                                            "details": f"SharpSight metric {metric.id} v{metric.version}"}}
                         if metric.certified else {"warning_markdown": "Not a certified SharpSight metric"})
        return {
            "metric_name": metric.key,
            "verbose_name": metric.name,
            "expression": metric.expression,
            "d3format": d3_format(metric.format),
            "description": " ".join(metric.documentation.split()),
            "extra": json.dumps(certification),
        }

    def describe(self, metric: MetricDef) -> dict[str, Any]:
        """Everything section 21 asks a metric to carry: business definition, expression, aggregation,
        dimensions, formatting, owner, documentation, certification, version, lineage, classification."""
        return {"id": metric.id, "name": metric.name, "certified": metric.certified, "owner": metric.owner,
                "version": metric.version, "expression": metric.expression, "format": metric.format,
                "unit": metric.unit, "aggregation": metric.aggregation, "additive": metric.additive,
                "dimensions": metric.dimensions, "classification": metric.classification,
                "documentation": " ".join(metric.documentation.split()),
                "lineage": metric.lineage.model_dump()}

    @staticmethod
    def columns_in(expression: str, known: list[str]) -> list[str]:
        words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression))
        return [c for c in known if c in words]

    def primary(self, metrics: list[MetricDef]) -> Optional[MetricDef]:
        return metrics[0] if metrics else None
