"""Dataset discovery: which governed dataset answers a request, and what is missing when none does.

It sees only what the requesting user may read — the list it works from comes out of the policy engine — and
it prefers a certified dataset every time one can answer. When none can, it does not reach for an uncertified
one quietly: it names the gap, and the dbt agent decides whether a transformation closes it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .models import DatasetDef, MetricDef, Requirement
from .registry import Registry
from .semantic_agent import SemanticAgent


@dataclass
class Candidate:
    dataset: DatasetDef
    score: float
    reasons: list[str] = field(default_factory=list)

    def describe(self) -> dict:
        return {"id": self.dataset.id, "certified": self.dataset.certified, "score": self.score,
                "reasons": self.reasons}


@dataclass
class Need:
    """What one component needs of a dataset: a metric, and possibly a column — named, or by its role."""
    purpose: str
    metric: MetricDef
    column: Optional[str] = None
    role: Optional[str] = None


@dataclass
class Gap:
    need: Need
    base: DatasetDef
    reason: str

    def describe(self) -> dict:
        return {"purpose": self.need.purpose, "metric": self.need.metric.id, "base_dataset": self.base.id,
                "missing": self.need.role or self.need.column, "reason": self.reason}


class DatasetAgent:
    def __init__(self, registry: Registry, semantic: SemanticAgent):
        self.registry = registry
        self.semantic = semantic

    def discover(self, requirement: Requirement, permitted: list[DatasetDef],
                 metrics: list[MetricDef]) -> list[Candidate]:
        words = {w for w in re.findall(r"[a-z0-9]+", requirement.subject.lower()) if len(w) > 1}
        wanted = {ds for metric in metrics for ds in metric.lineage.datasets}
        candidates: list[Candidate] = []
        for dataset in permitted:
            score, reasons = 0.0, []
            if dataset.certified:
                score += 3
                reasons.append("certified")
            haystack = " ".join([dataset.id, dataset.name, dataset.domain, *dataset.tags]).lower()
            matched = sorted(w for w in words if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", haystack))
            if matched:
                score += 2 * len(matched)
                reasons.append("matches " + ", ".join(matched))
            if dataset.id in wanted:
                score += 4
                reasons.append("in the lineage of a requested metric")
            if dataset.kind == "fact":
                score += 1
            if score > 0:
                candidates.append(Candidate(dataset, score, reasons))
        return sorted(candidates, key=lambda c: (-c.score, c.dataset.id))

    def resolve(self, need: Need, candidates: list[Candidate]) -> tuple[Optional[DatasetDef], Optional[Gap]]:
        """The dataset for one component: the best candidate on which the metric is valid and the needed column
        exists. Otherwise the gap, measured from the best dataset the metric IS valid on."""
        valid = [c for c in candidates if self.semantic.valid_on(need.metric, c.dataset)[0]]
        if not valid:
            return None, None
        for candidate in valid:
            dataset = candidate.dataset
            if need.role and not dataset.columns_with_role(need.role):
                continue
            if need.column and need.column not in dataset.columns:
                continue
            return dataset, None
        missing = f"a column with the role {need.role}" if need.role else f"the column {need.column}"
        return None, Gap(need=need, base=valid[0].dataset,
                         reason=f"no dataset the metric is valid on has {missing}")

    def provider_for(self, gap: Gap, permitted: list[DatasetDef]) -> Optional[tuple[DatasetDef, list[str], str]]:
        """A dimension dataset that can supply what is missing: it has the column, and a key the base shares.
        Certified providers first. Returns (dataset, join keys, column to add)."""
        options = []
        for dataset in permitted:
            if dataset.id == gap.base.id or dataset.kind != "dimension":
                continue
            columns = dataset.columns_with_role(gap.need.role) if gap.need.role else (
                [gap.need.column] if gap.need.column in dataset.columns else [])
            keys = [k for k in dataset.join_keys if k in gap.base.columns]
            if columns and keys and keys == dataset.join_keys:
                options.append((dataset, keys, columns[0]))
        options.sort(key=lambda option: (not option[0].certified, option[0].id))
        return options[0] if options else None
