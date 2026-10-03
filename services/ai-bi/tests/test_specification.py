from __future__ import annotations

import pytest
from pydantic import ValidationError

from aibi.chart_agent import ChartAgent
from aibi.dataset_agent import DatasetAgent
from aibi.models import ComponentSpec, DashboardSpec


def _specified(registry, dashboards, semantic, policy, requirement):
    """Draft → resolve → specify, the way the pipeline does it once the geographic dataset exists."""
    identity = policy.identify("dev.user")
    template = registry.template(requirement.template)
    metrics, _ = semantic.find_metrics(requirement)
    agent = DatasetAgent(registry, semantic)
    candidates = agent.discover(requirement, policy.permitted_datasets(identity), metrics, "cem-qoe")
    drafts, notes = dashboards.draft(requirement, template, metrics)
    gaps = []
    for draft in drafts:
        dataset, gap = agent.resolve(draft.need(), candidates)
        if dataset is not None:
            draft.dataset = dataset.id
            draft.dimension = draft.column or (dataset.columns_with_role(draft.role)[0] if draft.role else None)
        else:
            gaps.append((draft, gap))
    spec, spec_notes = dashboards.specify("cem-qoe", requirement, template, drafts)
    return spec, gaps, notes + spec_notes, identity, template, agent, candidates


def test_the_certified_mart_is_preferred_and_the_map_is_what_is_missing(registry, dashboards, semantic, policy, requirement):
    spec, gaps, _, _, _, agent, _ = _specified(registry, dashboards, semantic, policy, requirement)
    assert {c.dataset for c in spec.components} == {"cem.qoe_daily"}          # never the uncertified site layer
    assert [(d.slot, g.need.role, g.base.id) for d, g in gaps] == [("map_1", "geo_region", "cem.qoe_daily")]
    provider = agent.provider_for(gaps[0][1], policy.permitted_datasets(policy.identify("dev.user")))
    assert provider[0].id == "cem.region" and provider[1] == ["region"] and provider[2] == "region_iso"


def test_the_briefs_request_becomes_the_briefs_specification(with_generated, dashboards, semantic, policy, requirement):
    spec, gaps, notes, identity, _, _, _ = _specified(with_generated, dashboards, semantic, policy, requirement)
    assert gaps == []
    assert spec.dashboard.id == "cem-qoe-overview" and spec.dashboard.title == "CEM QoE Overview"
    assert spec.filters == ["date", "region", "rat"]
    got = [(c.slot, c.type, c.metric, c.dimension, c.dataset) for c in spec.components]
    assert got == [
        ("kpi_1", "big_number", "cem.qoe_score", None, "cem.qoe_daily"),
        ("kpi_2", "big_number", "cem.active_users", None, "cem.qoe_daily"),
        ("kpi_3", "big_number", "cem.degraded_users", None, "cem.qoe_daily"),
        ("trend_1", "line", "cem.qoe_score", "date", "cem.qoe_daily"),
        ("distribution_1", "histogram", "cem.qoe_score", None, "cem.qoe_daily"),
        ("ranking_1", "bar", "cem.qoe_score", "region", "cem.qoe_daily"),
        ("map_1", "map", "cem.qoe_score", "region_iso", "cem.cem_qoe__qoe_daily_region_iso"),
    ]
    report = dashboards.validate(spec, identity)
    assert report.passed, [f"{c.name}: {c.subject} {c.detail}" for c in report.failures]
    assert notes == []


def test_a_generated_dataset_is_its_projects_own(with_generated, dashboards, semantic, policy, requirement, geo_plan):
    """Another project asking for the same map does not build on this project's draft, and cannot overwrite
    it: it finds the gap again and plans a model, and a dataset, under its own name."""
    from aibi.dbt_agent import DbtAgent
    from aibi.registry import RegistryError

    registry = with_generated
    identity = policy.identify("dev.user")
    metrics, _ = semantic.find_metrics(requirement)
    agent = DatasetAgent(registry, semantic)
    mine = agent.discover(requirement, policy.permitted_datasets(identity), metrics, "cem-qoe")
    theirs = agent.discover(requirement, policy.permitted_datasets(identity), metrics, "cem-qoe-regional")
    assert "cem.cem_qoe__qoe_daily_region_iso" in {c.dataset.id for c in mine}
    assert "cem.cem_qoe__qoe_daily_region_iso" not in {c.dataset.id for c in theirs}

    drafts, _ = dashboards.draft(requirement, registry.template("executive"), metrics)
    map_draft = next(d for d in drafts if d.slot == "map_1")
    _, gap = agent.resolve(map_draft.need(), theirs)
    provider = agent.provider_for(gap, policy.permitted_datasets(identity))
    plan = DbtAgent(registry).plan("cem-qoe-regional", [gap], [provider])
    assert plan.name == "aigen_cem_qoe_regional__qoe_daily_region_iso"
    assert plan.dataset_id == "cem.cem_qoe_regional__qoe_daily_region_iso"

    stolen = DbtAgent(registry).dataset_for(geo_plan, "cem-qoe-regional", "bi_dev")
    with pytest.raises(RegistryError, match="was generated for BI project cem-qoe"):
        registry.register_generated(stolen)


def test_a_specification_has_no_room_for_arbitrary_state():
    with pytest.raises(ValidationError):
        ComponentSpec(slot="kpi_1", type="big_number", metric="cem.qoe_score", form_data={"viz_type": "anything"})
    with pytest.raises(ValidationError):
        DashboardSpec(dashboard={"id": "x", "template": "executive", "title": "X"}, components=[], css="body{}")
    with pytest.raises(ValidationError):
        ComponentSpec(slot="kpi_1", type="big_number", metric="cem.qoe_score", options={"row_limit": 10 ** 9})
    with pytest.raises(ValidationError):
        DashboardSpec(dashboard={"id": "Not A Slug", "template": "executive", "title": "X"}, components=[])


def _spec(**component):
    base = {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"}
    trend = {"slot": "trend_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date", "dataset": "cem.qoe_daily"}
    return DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"},
                         components=[base, trend, component] if component else [base, trend])


@pytest.mark.parametrize("component,failing", [
    ({"slot": "trend_1", "type": "pie", "metric": "cem.qoe_score", "dimension": "region", "dataset": "cem.qoe_daily"},
     "one component per slot"),
    ({"slot": "ranking_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date", "dataset": "cem.qoe_daily"},
     "visualization allowed in slot"),
    ({"slot": "ranking_1", "type": "bar", "metric": "cem.churn", "dimension": "region", "dataset": "cem.qoe_daily"},
     "metric is registered"),
    ({"slot": "ranking_1", "type": "bar", "metric": "cem.qoe_score", "dimension": "city", "dataset": "cem.qoe_daily"},
     "dimension is a column of the dataset"),
    ({"slot": "map_1", "type": "map", "metric": "cem.qoe_score", "dimension": "region", "dataset": "cem.qoe_daily"},
     "dimension has the role the visualization needs"),
    ({"slot": "ranking_1", "type": "bar", "metric": "cem.qoe_score", "dimension": "region", "dataset": "cem.region"},
     "metric is valid on the dataset"),
    ({"slot": "slot_99", "type": "bar", "metric": "cem.qoe_score", "dimension": "region", "dataset": "cem.qoe_daily"},
     "slot exists in template"),
])
def test_what_the_validator_refuses(dashboards, policy, component, failing):
    report = dashboards.validate(_spec(**component), policy.identify("dev.user"))
    assert failing in {c.name for c in report.failures}


def test_a_mandatory_slot_left_empty_is_refused(dashboards, policy):
    spec = DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"}, components=[
        {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"}])
    failed = {c.name: c.detail for c in dashboards.validate(spec, policy.identify("dev.user")).failures}
    assert failed.get("mandatory slots filled") == "trend_1"


def test_an_approved_but_uninstalled_visualization_is_refused_before_the_engine(registry, dashboards, policy, registry_copy):
    registry.templates["executive"].slots["ranking_1"].accepts.append("telecom_qoe")
    report = dashboards.validate(_spec(slot="ranking_1", type="telecom_qoe", metric="cem.qoe_score",
                                       dimension="region", dataset="cem.qoe_daily"), policy.identify("dev.user"))
    assert "visualization installed" in {c.name for c in report.failures}


def test_a_requester_without_the_grant_fails_validation(dashboards, policy):
    report = dashboards.validate(_spec(), policy.identify("guest.user"))
    assert "requester may read the dataset" in {c.name for c in report.failures}


def test_an_unapproved_theme_is_refused(dashboards, policy):
    spec = _spec()
    spec.theme.color_scheme = "neon"
    assert "theme uses approved tokens" in {c.name for c in dashboards.validate(spec, policy.identify("dev.user")).failures}


# ── what the engine is given ────────────────────────────────────────────────────────────────────────────────
def _charts(spec):
    return {c.slot: {"id": 100 + i, "uuid": f"u{i}", "name": c.slot} for i, c in enumerate(spec.components)}


def test_the_layout_is_the_templates_and_empty_slots_give_their_room_away(with_generated, dashboards, semantic, policy, requirement):
    spec, *_ , template, _, _ = _specified(with_generated, dashboards, semantic, policy, requirement)
    position = dashboards.layout(spec, template, _charts(spec))
    assert position["GRID_ID"]["children"] == ["ROW-kpis", "ROW-trend", "ROW-breakdown"]
    kpis = [position[c]["meta"] for c in position["ROW-kpis"]["children"]]
    assert [k["width"] for k in kpis] == [4, 4, 4]                # three KPIs in a row of four slots of three
    assert {k["height"] for k in kpis} == {16}
    assert [position[c]["meta"]["width"] for c in position["ROW-trend"]["children"]] == [8, 4]
    assert all(sum(position[c]["meta"]["width"] for c in position[row]["children"]) == 12
               for row in position["GRID_ID"]["children"])


def test_filters_are_scoped_to_the_charts_that_have_the_column(with_generated, dashboards, semantic, policy, requirement):
    spec, *_ = _specified(with_generated, dashboards, semantic, policy, requirement)
    charts = _charts(spec)
    filters = dashboards.native_filters(spec, {"cem.qoe_daily": 7, "cem.cem_qoe__qoe_daily_region_iso": 8}, charts)
    assert [(f["name"], f["filterType"]) for f in filters] == [
        ("Date", "filter_time"), ("Region", "filter_select"), ("RAT", "filter_select")]
    region = filters[1]
    assert region["targets"] == [{"datasetId": 7, "column": {"name": "region"}}]      # the certified dataset
    assert sorted(region["chartsInScope"]) == sorted(c["id"] for c in charts.values())
    assert region["scope"]["excluded"] == []


def test_the_stylesheet_holds_token_values_and_nothing_else(registry, dashboards):
    template = registry.template("executive")
    css = dashboards.css(template)
    tokens = registry.tokens
    assert tokens.colors["surface_muted"] in css and tokens.colors["primary"] in css
    assert f"font-size: {tokens.typography['heading_s']['size_px']}px" in css
    for line in css.splitlines()[1:]:
        assert line.startswith(".dashboard-"), line           # scoped to the dashboard, never `body` or `*`
    assert "@import" not in css and "url(" not in css and "<" not in css


def test_charts_reference_saved_metrics_never_formulas(with_generated, registry, dashboards, semantic, policy, requirement):
    spec, *_, template, _, _ = _specified(with_generated, dashboards, semantic, policy, requirement)
    agent = ChartAgent(registry)
    for component in spec.components:
        dataset, metric = registry.dataset(component.dataset), registry.metric(component.metric)
        form = agent.form_data(component, dataset, metric, 7, template, "inwi", "superset_seq_1")
        assert "SUM(" not in str(form)
        if component.type != "histogram":
            assert metric.key in (form.get("metric"), *(form.get("metrics") or []))
    by_slot = {c.slot: c for c in spec.components}
    map_form = agent.form_data(by_slot["map_1"], registry.dataset("cem.cem_qoe__qoe_daily_region_iso"),
                               registry.metric("cem.qoe_score"), 8, template, "inwi", "superset_seq_1")
    assert map_form["viz_type"] == "country_map" and map_form["select_country"] == "morocco"
    assert map_form["entity"] == "region_iso"


# ── every key a registry file carries is enforced ───────────────────────────────────────────────────────────
def test_a_metric_is_only_broken_down_by_what_the_registry_allows(with_generated, registry, dashboards, policy):
    registry.metrics["cem.qoe_score"].dimensions[:] = ["date", "rat"]            # no longer by region
    dev = policy.identify("dev.user")
    ranking = _spec(slot="ranking_1", type="bar", metric="cem.qoe_score", dimension="region", dataset="cem.qoe_daily")
    failed = {c.name: c.detail for c in dashboards.validate(ranking, dev).failures}
    assert "its dimensions are date, rat" in failed["metric may be broken down by the dimension"]
    # and the map's code falls with the region it is a function of
    on_map = _spec(slot="map_1", type="map", metric="cem.qoe_score", dimension="region_iso",
                   dataset="cem.cem_qoe__qoe_daily_region_iso")
    assert "metric may be broken down by the dimension" in {c.name for c in dashboards.validate(on_map, dev).failures}


def test_a_joined_column_is_allowed_through_the_key_that_determines_it(with_generated, dashboards, policy):
    assert with_generated.dataset("cem.cem_qoe__qoe_daily_region_iso").columns["region_iso"].determined_by == ["region"]
    on_map = _spec(slot="map_1", type="map", metric="cem.qoe_score", dimension="region_iso",
                   dataset="cem.cem_qoe__qoe_daily_region_iso")
    assert dashboards.validate(on_map, policy.identify("dev.user")).passed


def test_a_template_slot_narrower_than_its_own_minimum_stops_the_load(registry_copy):
    import yaml

    from aibi.registry import Registry, RegistryError

    path = registry_copy / "templates" / "telecom-kpi.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["responsive"]["min_slot_width"]["kpi"] = 3              # its KPI slots are 2 wide
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(RegistryError, match="slot kpi_1 is 2 columns wide, under the template's own minimum of 3"):
        Registry(registry_copy)


def test_no_registry_file_carries_a_key_nothing_reads(registry):
    """`spacing.gutter`, `typography.dashboard_title` and a metric's `higher_is_better` were declared and did
    nothing. The models forbid unknown keys, so this passing means they are gone from the files too."""
    for template in registry.templates.values():
        assert set(template.spacing) <= {"card_padding"}
        assert set(template.typography) <= {"chart_title", "kpi_value"}
    assert not hasattr(registry.metric("cem.qoe_score"), "higher_is_better")


def test_dbts_own_semantic_layer_is_read_and_set_beside_the_registry(tmp_path, registry):
    import json

    from aibi.dbt_runner import semantic_layer

    keys = [m.key for m in registry.metrics.values()]
    assert semantic_layer(tmp_path, keys) == {"available": False, "semantic_models": [], "metrics": [], "also_in_registry": []}
    (tmp_path / "semantic_manifest.json").write_text(json.dumps({
        "semantic_models": [{"name": "qoe"}], "metrics": [{"name": "qoe_score"}, {"name": "revenue"}]}))
    read = semantic_layer(tmp_path, keys)
    assert read["available"] and read["semantic_models"] == ["qoe"]
    assert read["also_in_registry"] == ["qoe_score"]          # one number, two definitions: said, not resolved
