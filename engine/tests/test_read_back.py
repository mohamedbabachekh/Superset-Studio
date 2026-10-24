"""A chart a person edited in Superset's own Explore, read back in the specification's words — and the rule
of who sees what in Superset, held equal on the two sides that state it."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from aibi.audit import AuditLog
from aibi.chart_agent import ChartAgent
from aibi.models import ComponentSpec, DashboardSpec
from aibi.store import Store
from aibi.superset_view import DASHBOARDS, SupersetView
from aibi.tools import Toolbox

KPI = {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"}
TREND = {"slot": "trend_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date", "dataset": "cem.qoe_daily"}
BAR = {"slot": "ranking_1", "type": "bar", "metric": "cem.qoe_score", "dimension": "region", "dataset": "cem.qoe_daily"}


def _as_superset_holds(registry, component, **edited):
    """The chart as the engine stores it for this component — then as a person left it in Explore."""
    agent, template = ChartAgent(registry), registry.template("executive")
    spec = ComponentSpec(**component)
    payload = agent.payload(spec, registry.dataset(spec.dataset), registry.metric(spec.metric), 5, 9, template,
                            template.theme.color_scheme, template.theme.sequential_scheme, "p")
    name = edited.pop("slice_name", payload["slice_name"])
    return agent, spec, {"params": json.dumps({**json.loads(payload["params"]), **edited}), "slice_name": name}


def test_a_chart_nobody_touched_reads_back_as_itself(registry):
    for component in (KPI, TREND, BAR, {"slot": "distribution_1", "type": "histogram", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"},
                      {"slot": "distribution_1", "type": "pie", "metric": "cem.active_users", "dimension": "rat", "dataset": "cem.qoe_daily"},
                      {**BAR, "title": "QoE by region, as the CEM team calls it"}):      # a title of its own is kept
        agent, spec, chart = _as_superset_holds(registry, component)
        read = agent.read_back(spec, chart, registry.template("executive").slots[spec.slot].purpose, {5: "cem.qoe_daily"})
        assert (read["changed"], read["refused"]) == ({}, []), (component["slot"], read)
        assert read["component"] == spec


def test_what_was_changed_in_explore_is_said_in_the_specifications_words(registry):
    agent, spec, chart = _as_superset_holds(registry, BAR, row_limit=5, order_desc=False, slice_name="Bottom 5 regions")
    read = agent.read_back(spec, chart, "ranking", {5: "cem.qoe_daily"})
    assert read["changed"]["row_limit"] == {"specification": None, "superset": 5}
    assert read["changed"]["sort"] == {"specification": None, "superset": "asc"}
    assert read["changed"]["title"]["superset"] == "Bottom 5 regions"
    adopted = read["component"]
    assert (adopted.options.row_limit, adopted.options.sort, adopted.title) == (5, "asc", "Bottom 5 regions")
    agent, spec, chart = _as_superset_holds(registry, TREND, time_grain_sqla="P1W")
    assert agent.read_back(spec, chart, "trend", {5: "cem.qoe_daily"})["changed"] == {"time_grain": {"specification": None, "superset": "P1W"}}
    agent, spec, chart = _as_superset_holds(registry, KPI, metric="cem__active_users")
    read = agent.read_back(spec, chart, "kpi", {5: "cem.qoe_daily"})
    assert read["changed"]["metric"] == {"specification": "cem.qoe_score", "superset": "cem.active_users"} and "title" not in read["changed"]


@pytest.mark.parametrize("component, edited, why", [
    (KPI, {"metric": {"expressionType": "SQL", "sqlExpression": "MAX(qoe_score)", "label": "Best"}}, "an ad-hoc metric (« Best »)"),
    (KPI, {"metric": "count"}, "metric « count » is not in the metric registry"),
    (BAR, {"viz_type": "sankey_v2"}, "« sankey_v2 » is not an approved visualization"),
    (BAR, {"metrics": ["cem__qoe_score", "cem__active_users"]}, "one metric per chart, and it has 2"),
    (BAR, {"datasource": "77__table"}, "a dataset that is not one of this project's governed datasets"),
    (BAR, {"row_limit": 5000}, "5000 rows: a chart shows at most 1000"),
    (TREND, {"x_axis": "region"}, "a line is drawn over time (date), not over « region »"),
    (TREND, {"time_grain_sqla": "PT1H"}, "time grain « PT1H »: day, week or month")])
def test_what_cannot_be_said_in_governed_terms_is_refused_not_approximated(registry, component, edited, why):
    agent, spec, chart = _as_superset_holds(registry, component, **edited)
    read = agent.read_back(spec, chart, registry.template("executive").slots[spec.slot].purpose, {5: "cem.qoe_daily"})
    assert read["component"] is None and why in "; ".join(read["refused"])


def test_superset_is_compared_with_the_specification_chart_by_chart(registry, policy, tmp_path):
    holds = {}

    class _Engine:
        def get_chart(self, chart_id):
            return holds.get(chart_id)

    tools = Toolbox(registry=registry, policy=policy, gateway=None, dbt=None, git=None, superset=_Engine(),
                    store=Store(tmp_path / "s.sqlite"), audit=AuditLog(tmp_path / "t"), visual=None, state_dir=tmp_path,
                    bi_database_name="BI", bi_database_uri="", superset_public_url="", dbt_dev_schema="bi_dev")
    spec = DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"}, components=[KPI, TREND, BAR])
    for chart_id, component in zip((1, 2, 3), (KPI, TREND, BAR)):
        _, _, holds[chart_id] = _as_superset_holds(registry, component)
    body = {"spec": spec.model_dump(), "engine": {"datasets": {"cem.qoe_daily": 5},
                                                  "charts": {"kpi_1": {"id": 1}, "trend_1": {"id": 2}, "ranking_1": {"id": 3}}}}
    assert all(c["in_step"] for c in tools.superset_changes(body))
    form = json.loads(holds[3]["params"])
    holds[3] = {"params": json.dumps({**form, "row_limit": 7, "color_scheme": "supersetColors", "show_legend": True}),
                "slice_name": holds[3]["slice_name"]}
    del holds[2]                                                                 # deleted by hand in Superset
    found = {c["slot"]: c for c in tools.superset_changes(body)}
    assert found["kpi_1"]["in_step"]
    assert found["trend_1"]["refused"] == ["the chart no longer exists in Superset"]
    ranking = found["ranking_1"]
    assert ranking["changed"] == {"row_limit": {"specification": None, "superset": 7}}
    assert ranking["not_carried"] == ["color_scheme", "show_legend"]             # a specification has no place for them
    assert ranking["adopted"]["options"]["row_limit"] == 7 and not ranking["in_step"]
    # what is taken is a component the specification accepts as its own
    assert ComponentSpec(**ranking["adopted"]).options.row_limit == 7


def test_what_was_changed_on_the_dashboard_itself_is_named_before_it_is_undone(registry, policy, tmp_path):
    """A commit applies the specification again. A row moved in Superset's dashboard editor, a chart added by
    hand, a filter changed there: a specification has no place for them, so the commit undoes them — and the
    person is told which, before, instead of finding out."""
    from aibi.dashboard_agent import DashboardAgent
    from aibi.semantic_agent import SemanticAgent

    spec = DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"}, filters=["region"],
                         components=[KPI, TREND, BAR])
    engine = {"dashboard": {"id": 9}, "datasets": {"cem.qoe_daily": 5},
              "charts": {"kpi_1": {"id": 1, "name": "QoE Score"}, "trend_1": {"id": 2, "name": "Trend"},
                         "ranking_1": {"id": 3, "name": "Ranking"}}}
    template = registry.template("executive")
    agent = DashboardAgent(registry, policy, SemanticAgent(registry))
    position = agent.layout(spec, template, engine["charts"])
    filters = agent.native_filters(spec, engine["datasets"], engine["charts"], template)
    live = {"id": 9, "position_json": json.dumps(position), "json_metadata": json.dumps({"native_filter_configuration": filters})}
    on_it = [{"id": 1, "slice_name": "QoE Score"}, {"id": 2, "slice_name": "Trend"}, {"id": 3, "slice_name": "Ranking"}]

    class _Engine:
        def get_dashboard(self, key):
            return live

        def dashboard_charts(self, key):
            return on_it

    tools = Toolbox(registry=registry, policy=policy, gateway=None, dbt=None, git=None, superset=_Engine(),
                    store=Store(tmp_path / "s.sqlite"), audit=AuditLog(tmp_path / "t"), visual=None, state_dir=tmp_path,
                    bi_database_name="BI", bi_database_uri="", superset_public_url="", dbt_dev_schema="bi_dev")
    body = {"spec": spec.model_dump(), "engine": engine}
    assert tools.dashboard_differences(body) == []                    # as the engine left it: nothing to name

    on_it.append({"id": 44, "slice_name": "Throughput by site"})      # a chart dragged in from Superset's own list
    row = next(key for key, node in position.items() if isinstance(node, dict) and node.get("type") == "ROW")
    chart = position[position[row]["children"][0]]
    chart["meta"]["width"] = chart["meta"]["width"] + 1               # a chart made wider in the dashboard editor
    live["position_json"] = json.dumps(position)
    live["json_metadata"] = json.dumps({"native_filter_configuration": [*filters, {"name": "Site"}]})
    found = tools.dashboard_differences(body)
    assert found[0] == "added by hand, and in no slot of the template: « Throughput by site »"
    assert found[1].startswith("the layout is no longer the template's")
    assert found[2].startswith("its filters are ") and "Site" in found[2] and found[2].endswith(
        "the specification's are " + ", ".join(sorted(f["name"] for f in filters)))


def _addon():
    """studio/addon/sharpsight_bi.py runs inside Superset, not here; its rule is read from its file. It is
    beside the registry in a checkout, and mounted into the engine's container."""
    here = Path(__file__).resolve()
    for folder in (Path("/app/studio/addon"), *(parent / "studio" / "addon" for parent in here.parents)):
        if (folder / "sharpsight_bi.py").is_file():
            loaded = importlib.util.spec_from_file_location("sharpsight_bi_addon", folder / "sharpsight_bi.py")
            module = importlib.util.module_from_spec(loaded)
            loaded.loader.exec_module(module)
            return module
    return None


def test_superset_and_the_engine_state_the_same_rule_of_who_sees_what(registry, registry_copy, tmp_path):
    """The audience of each dashboard over the records is written twice: the engine grants the dashboard
    (aibi/people.py), Superset shows the menu entry (the add-on). One edited alone would show an entry that
    opens nothing, or hide one that opens."""
    addon = _addon()
    if addon is None:
        pytest.skip("studio/addon/ is not beside this checkout")
    shown = addon.audiences(registry_copy / "security" / "rbac.yaml")
    view = SupersetView(registry, Store(tmp_path / "s.sqlite"), AuditLog(tmp_path / "a"), None, tmp_path, "", ("", ""))
    leads_to = {name: href.strip("/").split("/")[-1] for name, _, href, capabilities in addon.LINKS
                if capabilities and "/superset/dashboard/" in href}
    assert set(leads_to.values()) == set(DASHBOARDS)
    # the entries that are pages of SharpSight's own, not dashboards: their audience is the add-on's to state
    assert sorted(shown[addon.PROJECTS]) == ["sharpsight_bi_developer", "sharpsight_bi_release_manager",
                                             "sharpsight_bi_reviewer"]
    for entry, slug in leads_to.items():
        assert sorted(shown[entry]) == view.audience(slug), entry
    # the documentation: a role granted a dataset, or one of the audiences above — and not a role granted none
    assert "sharpsight_cem_analyst" in shown[addon.DBT_DOCS] and "sharpsight_viewer" not in shown[addon.DBT_DOCS]
    assert addon.audiences(tmp_path / "no-such-file.yaml") == {name: set() for name in shown}
