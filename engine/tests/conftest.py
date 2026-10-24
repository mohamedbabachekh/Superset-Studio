"""These tests need no database, no dbt and no BI engine: they hold the engine's RULES to account — what it
understands, what it refuses, what it derives. The end-to-end run is `bash ai-bi.sh demo`, against the real things.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from aibi.dashboard_agent import DashboardAgent
from aibi.dbt_agent import DbtAgent
from aibi.models import JoinPlan, ModelPlan
from aibi.planner import DeterministicParser
from aibi.policy_engine import PolicyEngine
from aibi.registry import Registry
from aibi.semantic_agent import SemanticAgent

DEMO = ("Create a CEM QoE dashboard using the CEM Executive template.\n\n"
        "Use QoE Score, Active Users and Degraded Users.\n\n"
        "Show daily trend, regional ranking, QoE distribution and geographic view.\n\n"
        "Add Date, Region and RAT filters.")


def _registry_dir() -> Path:
    # in the container the registry is mounted at /app/governance/registry; in a checkout it is governance/registry
    # at the root, two folders above this one (engine/tests/)
    parents = Path(__file__).resolve().parents
    candidates = [os.environ.get("AIBI_REGISTRY_DIR"), "/app/governance/registry",
                  parents[2] / "governance" / "registry" if len(parents) > 2 else None]
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    raise RuntimeError("the registry folder was not found")


@pytest.fixture()
def registry_copy(tmp_path: Path) -> Path:
    target = tmp_path / "registry"
    shutil.copytree(_registry_dir(), target)
    return target


@pytest.fixture()
def registry(registry_copy: Path, tmp_path: Path) -> Registry:
    return Registry(registry_copy, generated_dir=tmp_path / "generated")


@pytest.fixture()
def policy(registry: Registry) -> PolicyEngine:
    return PolicyEngine(registry)


@pytest.fixture()
def semantic(registry: Registry) -> SemanticAgent:
    return SemanticAgent(registry)


@pytest.fixture()
def dashboards(registry: Registry, policy: PolicyEngine, semantic: SemanticAgent) -> DashboardAgent:
    return DashboardAgent(registry, policy, semantic)


@pytest.fixture()
def requirement(registry: Registry):
    return DeterministicParser(registry).parse(DEMO)


@pytest.fixture()
def geo_plan() -> ModelPlan:
    return ModelPlan(
        name="aigen_cem_qoe__qoe_daily_region_iso", dataset_id="cem.cem_qoe__qoe_daily_region_iso",
        description="CEM QoE Daily with region_iso", base_dataset="cem.qoe_daily", base_model="mart_cem_qoe_daily",
        columns=["date", "region", "rat", "qoe_score", "active_users", "degraded_users", "loaded_at"],
        joins=[JoinPlan(dataset="cem.region", model="dim_region", on=["region"], add_columns=["region_iso"])],
        grain=["date", "region", "rat"], reason="no dataset the metric is valid on has a column with the role geo_region")


@pytest.fixture()
def with_generated(registry: Registry, geo_plan: ModelPlan) -> Registry:
    """The registry as it stands after the engine built and registered the geographic model."""
    registry.register_generated(DbtAgent(registry).dataset_for(geo_plan, "cem-qoe", "bi_dev"))
    return registry
