"""What was learned from the BI engine by running against it — each test here is a finding of 2026-10-03 that
cost a failed run or a wrong picture, written down so it cannot quietly come back."""
from __future__ import annotations

from aibi.chart_agent import ChartAgent
from aibi.models import ComponentSpec, DashboardSpec
from aibi.validation_agent import ValidationAgent
from aibi.visual_qa_agent import Render


def _component(**body) -> ComponentSpec:
    return ComponentSpec(**{"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score",
                            "dataset": "cem.qoe_daily", **body})


def test_a_time_range_is_sent_the_way_a_dashboard_sends_it(registry):
    """A query-level time_range REPLACES the range written in a TEMPORAL_RANGE clause. Sending the clause with
    « Last week » beside time_range « No filter » returned everything, and a working filter was reported broken."""
    agent, dataset, metric = ChartAgent(registry), registry.dataset("cem.qoe_daily"), registry.metric("cem.qoe_score")
    unfiltered = agent.query_context(_component(), dataset, metric, 7)["queries"][0]
    assert unfiltered["filters"] == [{"col": "date", "op": "TEMPORAL_RANGE", "val": "No filter"}]
    assert "time_range" not in unfiltered

    last_week = agent.query_context(_component(), dataset, metric, 7, time_range="Last week")["queries"][0]
    assert last_week["time_range"] == "Last week"
    assert last_week["filters"] == unfiltered["filters"]            # the clause stays « No filter »

    by_region = agent.query_context(_component(), dataset, metric, 7,
                                    [{"col": "region", "op": "IN", "val": ["Oriental"]}])["queries"][0]
    assert by_region["filters"][1] == {"col": "region", "op": "IN", "val": ["Oriental"]}


def test_a_ranking_reads_best_first_from_the_top(registry):
    """Horizontal bars are drawn bottom-up: sorted descending for display, the best region was at the bottom."""
    agent, template = ChartAgent(registry), registry.template("executive")
    ranking = _component(slot="ranking_1", type="bar", dimension="region")
    form = agent.form_data(ranking, registry.dataset("cem.qoe_daily"), registry.metric("cem.qoe_score"), 7,
                           template, "inwi", "superset_seq_1")
    assert form["orientation"] == "horizontal"
    assert form["x_axis_sort_asc"] is True       # ascending on screen = largest on top
    assert form["order_desc"] is True            # …while the rows FETCHED are still the largest ones


def test_the_templates_typography_token_sizes_the_headline_number(registry):
    agent, template = ChartAgent(registry), registry.template("executive")
    dataset, metric = registry.dataset("cem.qoe_daily"), registry.metric("cem.qoe_score")
    assert agent.form_data(_component(), dataset, metric, 7, template, "inwi", "superset_seq_1")["header_font_size"] == 0.6
    smaller = template.model_copy(update={"typography": {**template.typography, "kpi_value": "heading_l"}})
    assert agent.form_data(_component(), dataset, metric, 7, smaller, "inwi", "superset_seq_1")["header_font_size"] == 0.4


def test_every_template_gives_a_kpi_card_room_to_be_read(registry):
    """Under 128 px the engine draws a KPI's caption at 8 px (measured on the operational template)."""
    for template in registry.templates.values():
        for row in template.layout:
            if any(template.slots[p.id].purpose == "kpi" for p in row.slots):
                assert row.height * template.grid.row_unit_px >= 128, template.id


def test_reading_the_registries_needs_less_than_acting(policy):
    """A reviewer could not list datasets: every tool asked for `tools.use`, which a reviewer rightly lacks."""
    from aibi.tools import Toolbox

    needs = {spec.name: spec.capability for spec in Toolbox.specs()}
    reading = {"list_datasets", "get_dataset", "get_dataset_schema", "get_metric", "list_metrics", "get_dimension",
               "list_dashboard_templates", "get_dashboard_template", "validate_sql", "search_assets", "get_asset_usage"}
    assert {name for name, capability in needs.items() if capability == "bi.read"} == reading
    # everything that runs a query, builds a model or touches the BI engine
    assert {needs[name] for name in ("execute_readonly_sql", "get_dataset_sample", "generate_dbt_model",
                                     "run_dbt_model", "create_superset_chart", "update_superset_dashboard",
                                     "commit_project_version")} == {"tools.use"}
    reviewer = policy.identify("lead.reviewer")
    assert "bi.read" in reviewer.capabilities and "tools.use" not in reviewer.capabilities
    # the brief's tools (section 19) are all there, under its own names
    assert {"get_dataset", "get_dataset_schema", "get_dataset_sample", "list_datasets", "get_metric", "list_metrics",
            "get_dimension", "get_dashboard_template", "execute_readonly_sql", "validate_sql", "generate_dbt_model",
            "compile_dbt", "run_dbt_model", "run_dbt_test", "create_superset_dataset", "create_superset_chart",
            "create_superset_dashboard", "update_superset_dashboard", "get_dashboard_data", "validate_dashboard",
            "render_dashboard_screenshot", "commit_project_version", "create_deployment_package"} <= set(needs)


def test_the_dashboard_carries_no_colour_scheme_of_its_own(dashboards, registry):
    """A dashboard-level scheme is pushed into every chart, and the country map reads it as « colour by
    category »: the regions came out in six unrelated colours."""
    spec = DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"}, components=[_component()])
    metadata = dashboards.json_metadata(spec, registry.template("executive"), [])
    assert "color_scheme" not in metadata
    assert metadata["filter_bar_orientation"] == "HORIZONTAL" and metadata["cross_filters_enabled"] is True


def test_filter_state_is_what_the_dashboard_reads_from_its_address(with_generated, dashboards):
    spec = DashboardSpec(dashboard={"id": "cem-qoe-overview", "template": "executive", "title": "T"},
                         filters=["date", "region", "rat"], components=[_component()])
    mask = dashboards.filter_state(spec, {"date": "Last week", "region": "Oriental"})
    assert set(mask) == {"NATIVE_FILTER-cem-qoe-overview-date", "NATIVE_FILTER-cem-qoe-overview-region"}
    assert mask["NATIVE_FILTER-cem-qoe-overview-date"]["extraFormData"] == {"time_range": "Last week"}
    assert mask["NATIVE_FILTER-cem-qoe-overview-region"]["extraFormData"] == {
        "filters": [{"col": "region", "op": "IN", "val": ["Oriental"]}]}
    # and the ids are the ones the dashboard's filters were created with
    created = dashboards.native_filters(spec, {"cem.qoe_daily": 7}, {"kpi_1": {"id": 1}})
    assert {f["id"] for f in created} >= set(mask)


def _calls(**digests):
    return [{"kind": "chart", "chart_id": cid, "status": 200, "error": None, "digest": d, "rowcount": 1}
            for cid, d in digests.items()]


def test_filters_are_judged_on_the_dashboard_itself(registry, policy):
    from aibi.sql_agent import SqlAgent

    agent = ValidationAgent(registry, policy, SqlAgent(), store=None)
    spec = DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"}, filters=["region"], components=[
        _component(), _component(slot="trend_1", type="line", dimension="date")])
    charts = {"kpi_1": {"id": 1}, "trend_1": {"id": 2}}
    base = Render(viewport="desktop", width=1440, screenshot="x.png", load_ms=1, calls=_calls(**{"1": "a", "2": "b"}))
    base.calls = [{**c, "chart_id": int(c["chart_id"])} for c in base.calls]

    def filtered(**digests):
        render = Render(viewport="filtered", width=1440, screenshot="y.png", load_ms=1, calls=_calls(**digests))
        render.calls = [{**c, "chart_id": int(c["chart_id"])} for c in render.calls]
        return render

    good = agent.dashboard_filters(spec, charts, [base], filtered(**{"1": "a2", "2": "b2"}), {"region": "Oriental"})
    assert good.passed and len(good.checks) == 2

    ignored = agent.dashboard_filters(spec, charts, [base], filtered(**{"1": "a2", "2": "b"}), {"region": "Oriental"})
    assert [(c.slot, c.status) for c in ignored.checks] == [("kpi_1", "pass"), ("trend_1", "fail")]
    assert "exactly what it receives unfiltered" in ignored.failures[0].detail

    silent = agent.dashboard_filters(spec, charts, [base], filtered(**{"1": "a2"}), {"region": "Oriental"})
    assert "made no request" in silent.failures[0].detail

    never_rendered = agent.dashboard_filters(spec, charts, [base], None, None)
    assert not never_rendered.passed


# ── what the first renders of the other templates showed ────────────────────────────────────────────────────
CX = ("Create a CEM experience dashboard using the customer-experience template. Use Degraded Rate and QoE Score. "
      "Show degraded rate distribution, daily trend and regional ranking. Add region filter.")


def test_a_rate_is_never_drawn_as_shares_of_a_whole(registry, dashboards, semantic, policy):
    """The first customer-experience render had a donut of the degraded RATE by region: sixteen rates shown
    as « shares » of their own sum. The view is left out and said so; the mandatory slot is filled with what
    can be drawn."""
    from aibi.planner import DeterministicParser

    requirement = DeterministicParser(registry).parse(CX)
    template = registry.template("customer-experience")
    metrics, _ = semantic.find_metrics(requirement)
    assert [m.id for m in metrics] == ["cem.degraded_rate", "cem.qoe_score"]
    drafts, notes = dashboards.draft(requirement, template, metrics)
    distribution = next(d for d in drafts if d.slot == "distribution_1")
    assert (distribution.type, distribution.metric.id) == ("histogram", "cem.qoe_score")
    assert not any(d.type == "pie" for d in drafts)
    assert any("« degraded rate distribution » left out" in n for n in notes)
    assert any("distribution_1 is mandatory" in n and "QoE Score" in n for n in notes)


def test_a_pie_of_a_non_additive_metric_is_refused_by_the_validator(dashboards, policy, registry):
    def pie(metric):
        return DashboardSpec(dashboard={"id": "t", "template": "customer-experience", "title": "T"}, components=[
            _component(), _component(slot="trend_1", type="line", dimension="date"),
            _component(slot="distribution_1", type="pie", metric=metric, dimension="region")])

    dev = policy.identify("dev.user")
    failed = {c.name: c.detail for c in dashboards.validate(pie("cem.degraded_rate"), dev).failures}
    assert "shares of nothing" in failed["metric's parts add up to its whole"]
    assert dashboards.validate(pie("cem.active_users"), dev).passed            # users per region DO add up
    assert registry.metric("cem.active_users").additive and not registry.metric("cem.qoe_score").additive


def test_a_column_is_shown_under_the_name_people_read(registry):
    dataset = registry.dataset("cem.qoe_daily")
    assert [dataset.label(c) for c in ("date", "region", "rat", "qoe_score", "active_users")] == [
        "Date", "Region", "RAT", "QoE score", "Active users"]
    agent, template = ChartAgent(registry), registry.template("telecom-kpi")
    table = _component(slot="detail_1", type="table", dimension="rat")
    ranking = _component(slot="ranking_1", type="bar", dimension="region")
    metric = registry.metric("cem.qoe_score")
    assert agent.title(table, metric, "detail") == "QoE Score, detail by RAT"   # it read « by rat »
    assert agent.title(ranking, metric, "ranking") == "QoE Score by region"


def test_one_pixel_of_overflow_is_a_clipped_title(registry):
    """« CEM experience Customer Experien… » needed 372 px and had 371."""
    from aibi.visual_qa_agent import VisualQaAgent

    agent = VisualQaAgent("http://x", "u", "p", {"visual": {}})
    spec = DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"}, components=[_component()])
    render = Render(viewport="desktop", width=1440, screenshot="x.png", load_ms=1, components=[
        {"chart_id": 1, "rect": {"x": 0, "y": 0, "w": 400, "h": 128}, "clipped": [], "error": None, "loading": False,
         "blank_ratio": 0.9}])
    render.header = {"text": "CEM experience Customer Experience", "needs": 372, "has": 371}

    report = agent.check(render, spec, registry.template("executive"), {"kpi_1": {"id": 1}},
                         {"kpi_1": {"rowcount": 1, "data": []}})
    assert [c.code for c in report.warnings] == ["dashboard_title_clipped"]


def test_the_label_threshold_is_the_measured_one(registry):
    assert registry.policies["visual"]["min_px_per_category"] == 18


# ── area and pie, asked for ─────────────────────────────────────────────────────────────────────────────────
AUDIENCE = ("Create a CEM audience dashboard using the customer-experience template. Use Active Users and QoE Score. "
            "Show active users share by region, active users volume over time and regional ranking.")


def _drafted(registry, dashboards, semantic, prompt):
    from aibi.planner import DeterministicParser

    requirement = DeterministicParser(registry).parse(prompt)
    metrics, _ = semantic.find_metrics(requirement)
    drafts, notes = dashboards.draft(requirement, registry.template(requirement.template), metrics)
    return requirement, {d.slot: d for d in drafts}, notes


def test_a_share_is_a_pie_and_a_volume_is_an_area_when_asked(registry, dashboards, semantic):
    requirement, drafts, notes = _drafted(registry, dashboards, semantic, AUDIENCE)
    assert [(v.intent, v.prefer, v.dimension) for v in requirement.views] == [
        ("share", "pie", "region"), ("volume", "area", None), ("ranking", None, "region")]
    assert requirement.unresolved == []
    assert (drafts["distribution_1"].type, drafts["distribution_1"].column) == ("pie", "region")
    assert drafts["trend_1"].type == "area"
    assert not [n for n in notes if "drawn as" in n]


def test_a_share_of_what_does_not_add_up_is_drawn_otherwise_and_says_so(registry, dashboards, semantic):
    _, drafts, notes = _drafted(registry, dashboards, semantic, AUDIENCE.replace(
        "active users share by region", "QoE share by region"))
    assert drafts["distribution_1"].type == "histogram"
    assert any("« QoE share by region » is drawn as a histogram, not a pie" in n and "do not add up" in n for n in notes)


def test_a_preference_the_slot_does_not_accept_is_not_forced(registry, dashboards, semantic):
    """The executive template's trend slot accepts line and area; the operational one's too — but no template
    lets a pie into a ranking slot, whatever the request prefers."""
    template = registry.template("executive")
    metric = registry.metric("cem.active_users")
    made = dashboards._draft_for("ranking_1", "ranking", template, metric, "region", None, "", prefer="pie")
    assert made.type == "bar"


def test_an_area_starts_at_zero_and_a_pie_draws_every_part(registry):
    agent, template = ChartAgent(registry), registry.template("customer-experience")
    dataset, users = registry.dataset("cem.qoe_daily"), registry.metric("cem.active_users")
    area = agent.form_data(_component(slot="trend_1", type="area", metric=users.id, dimension="date"),
                           dataset, users, 7, template, "inwi", "superset_seq_1")
    line = agent.form_data(_component(slot="trend_1", type="line", metric=users.id, dimension="date"),
                           dataset, users, 7, template, "inwi", "superset_seq_1")
    assert area["viz_type"] == "echarts_area" and area["truncateYAxis"] is False
    assert line["truncateYAxis"] is True
    share = _component(slot="distribution_1", type="pie", metric=users.id, dimension="region")
    pie = agent.form_data(share, dataset, users, 7, template, "inwi", "superset_seq_1")
    assert pie["row_limit"] == 100 and pie["show_legend"] is False and pie["groupby"] == ["region"]
    # no legend, so a slice is named by its label or by nothing: with few parts every one is labelled
    labelled_from = lambda parts: agent.form_data(share, dataset, users, 7, template, "inwi", "superset_seq_1",  # noqa: E731
                                                  parts)["show_labels_threshold"]
    assert (labelled_from(4), labelled_from(8), labelled_from(16), labelled_from(None)) == (0, 0, 4, 4)
    assert agent.title(share, users, "distribution") == "Active Users, share by region"
