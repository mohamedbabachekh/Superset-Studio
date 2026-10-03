from __future__ import annotations

import pytest
import yaml

from aibi.dbt_agent import DbtAgent
from aibi.planner import DeterministicParser, match_vocabulary, new_steps, slugify
from aibi.registry import Registry, RegistryError
from aibi.semantic_agent import d3_format


def test_the_registries_load_and_hold_together(registry):
    assert {"cem.qoe_daily", "cem.region", "cem.qoe_site_daily"} <= set(registry.datasets)
    assert set(registry.templates) == {"executive", "telecom-kpi", "operational", "geographic", "customer-experience",
                                       "management"}
    assert registry.metric("cem.qoe_score").certified
    assert registry.template("Executive").id == "executive"
    assert registry.template("executive-telecom-v1").id == "executive"      # the brief's own name for it


def test_an_unclassified_column_stops_the_load(registry_copy):
    path = registry_copy / "datasets" / "cem.qoe_daily.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    del document["columns"]["qoe_score"]["classification"]
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(RegistryError, match="classification"):
        Registry(registry_copy)


def test_a_template_cannot_name_a_colour_scheme_nobody_approved(registry_copy):
    path = registry_copy / "templates" / "executive.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["theme"]["color_scheme"] = "neon"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(RegistryError, match="neon is not approved"):
        Registry(registry_copy)


def test_a_metric_must_point_at_columns_that_exist(registry_copy):
    path = registry_copy / "metrics" / "cem.qoe_score.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["metric"]["lineage"]["columns"] = ["mos"]
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(RegistryError, match="column mos is not in cem.qoe_daily"):
        Registry(registry_copy)


def test_a_generated_dataset_is_never_certified_and_never_replaces_a_governed_one(registry, geo_plan):
    dataset = DbtAgent(registry).dataset_for(geo_plan, "cem-qoe", "bi_dev")
    assert not dataset.certified and dataset.environment == "dev" and dataset.relation.startswith("bi_dev.")
    with pytest.raises(RegistryError, match="cannot be registered as certified"):
        registry.register_generated(dataset.model_copy(update={"certified": True}))
    with pytest.raises(RegistryError, match="governed dataset"):
        registry.register_generated(dataset.model_copy(update={"id": "cem.qoe_daily"}))


# ── the planner ─────────────────────────────────────────────────────────────────────────────────────────────
def test_the_briefs_request_is_read_as_the_brief_means_it(requirement):
    assert requirement.subject == "CEM QoE"
    assert requirement.template == "executive"
    assert requirement.metrics == ["cem.qoe_score", "cem.active_users", "cem.degraded_users"]
    assert [(v.intent, v.dimension, v.metric) for v in requirement.views] == [
        ("trend", None, None), ("ranking", "region", None), ("distribution", None, "cem.qoe_score"), ("geo", None, None)]
    assert requirement.views[0].time_grain == "P1D"
    assert requirement.filters == ["date", "region", "rat"]
    assert requirement.unresolved == []
    # « the CEM Executive template » is not a registered name; it is read, and the reading is said
    assert requirement.assumptions == ["template « CEM Executive » read as Executive overview (executive)"]
    assert slugify(requirement.subject) == "cem-qoe"


def test_the_longest_phrase_wins(registry):
    parser = DeterministicParser(registry)
    only_degraded = parser.parse("Create a CEM dashboard using the executive template. Use Degraded Users.")
    assert only_degraded.metrics == ["cem.degraded_users"]
    found = match_vocabulary("degraded users and active users", [("users", "u"), ("degraded users", "d"), ("active users", "a")])
    assert [key for _, _, key in found] == ["d", "a"]


def test_what_is_not_understood_is_reported_not_guessed(registry):
    parser = DeterministicParser(registry)
    read = parser.parse("Create a CEM QoE dashboard using the cinematic template. Use QoE Score. "
                        "Show a sankey of churn and daily trend.")
    assert read.template is None
    assert any("cinematic" in note for note in read.unresolved)
    assert any("sankey of churn" in note for note in read.unresolved)
    assert [v.intent for v in read.views] == ["trend"]


def test_a_request_naming_no_metric_says_so(registry):
    read = DeterministicParser(registry).parse("Create a sales dashboard using the executive template. Show revenue.")
    assert read.metrics == []
    assert any("no registered metric" in note for note in read.unresolved)


def test_the_plan_has_the_briefs_twenty_two_steps():
    steps = new_steps()
    assert len(steps) == 22
    assert steps[0].key == "parse_requirement" and steps[21].key == "mark_ready_for_review"
    assert [s.number for s in steps] == list(range(1, 23))


def test_formats_become_d3():
    assert d3_format("0.00") == ".2f"
    assert d3_format("#,##0") == ",.0f"
    assert d3_format("0.0%") == ".1%"
    assert d3_format("#,##0", compact=True) == ".3~s"
