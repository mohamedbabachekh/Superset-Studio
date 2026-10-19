"""The copilot: a request in words, answered with a proposal — and never with a change.

It reads with the same governed vocabulary the planner uses (registry/lexicon.yaml, the registered metrics,
the template's own slots), narrowed to what a person says about an existing dashboard. These hold it to two
things above all: that what it proposes is valid, and that what it does not understand it SAYS it does not
understand, rather than guessing.
"""
from __future__ import annotations

import pytest

from aibi.copilot import Copilot, diff
from aibi.models import DashboardSpec

SPEC = {
    "dashboard": {"id": "cem-qoe", "template": "executive", "title": "CEM QoE Overview"},
    "filters": ["date"],
    "components": [
        {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"},
        {"slot": "kpi_2", "type": "big_number", "metric": "cem.active_users", "dataset": "cem.qoe_daily"},
        {"slot": "trend_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date",
         "dataset": "cem.qoe_daily", "options": {"time_grain": "P1D"}},
        {"slot": "ranking_1", "type": "bar", "metric": "cem.qoe_score", "dimension": "region",
         "dataset": "cem.qoe_daily"},
    ]}


@pytest.fixture()
def copilot(registry, policy, dashboards) -> Copilot:
    return Copilot(registry, policy, dashboards)


@pytest.fixture()
def spec() -> DashboardSpec:
    return DashboardSpec(**SPEC)


@pytest.fixture()
def template(registry):
    return registry.template("executive")


@pytest.fixture()
def dev(policy):
    return policy.identify("dev.user")


def test_it_adds_a_view_to_a_free_slot_of_the_right_purpose(copilot, spec, template, dev):
    proposal = copilot.propose("add a distribution of QoE Score", spec, template, dev)
    assert proposal.changes and proposal.changes[0].slot == "distribution_1"
    assert "histogram" in proposal.changes[0].says
    assert proposal.validation["passed"] is True
    # the specification it proposes is complete: the dataset was resolved, not left to the page
    after = DashboardSpec(**proposal.spec)
    assert after.component("distribution_1").dataset == "cem.qoe_daily"
    # and nothing was changed: the specification it was given is untouched
    assert spec.component("distribution_1") is None


def test_it_refuses_when_the_template_has_no_free_slot_for_that_view(copilot, spec, template, dev):
    full = copilot.propose("add a trend of active users", spec, template, dev)
    assert not full.changes
    assert any("no free trend slot" in said for said in full.unresolved), full.unresolved


def test_it_refuses_a_metric_that_is_not_in_the_registry(copilot, spec, template, dev):
    proposal = copilot.propose("add a ranking of customer happiness", spec, template, dev)
    assert not proposal.changes
    assert any("no registered metric" in said for said in proposal.unresolved)


def test_it_says_when_it_did_not_understand_rather_than_guessing(copilot, spec, template, dev):
    proposal = copilot.propose("make it pop a bit more", spec, template, dev)
    assert not proposal.changes and proposal.spec is None
    assert any("was not recognised" in said for said in proposal.unresolved)
    assert "deterministic" in proposal.unresolved[0]


def test_it_changes_a_time_grain_an_order_and_a_top_n(copilot, spec, template, dev):
    weekly = copilot.propose("make the trend weekly", spec, template, dev)
    assert DashboardSpec(**weekly.spec).component("trend_1").options.time_grain == "P1W"
    top = copilot.propose("show only the top 5 on the ranking", spec, template, dev)
    assert DashboardSpec(**top.spec).component("ranking_1").options.row_limit == 5
    order = copilot.propose("order the ranking ascending", spec, template, dev)
    assert DashboardSpec(**order.spec).component("ranking_1").options.sort == "asc"


def test_all_the_kpis_means_every_kpi_slot(copilot, spec, template, dev):
    proposal = copilot.propose("make all the KPIs compact", spec, template, dev)
    after = DashboardSpec(**proposal.spec)
    assert [c.slot for c in proposal.changes] == ["kpi_1", "kpi_2"]
    assert after.component("kpi_1").options.number_format == "compact"
    assert after.component("trend_1").options.number_format is None


def test_it_renames_a_chart_named_by_its_purpose(copilot, spec, template, dev):
    proposal = copilot.propose("rename the ranking to Top regions by QoE", spec, template, dev)
    assert DashboardSpec(**proposal.spec).component("ranking_1").title == "Top regions by QoE"


def test_a_chart_taken_off_the_dashboard_is_parked_in_its_section(copilot, spec, template, dev):
    proposal = copilot.propose("remove the ranking", spec, template, dev)
    after = DashboardSpec(**proposal.spec)
    assert after.component("ranking_1") is None
    assert after.section("breakdown").parked[0].slot == "ranking_1"
    assert "no longer on the dashboard" in proposal.summary


def test_a_mandatory_slot_is_not_taken_off(copilot, spec, template, dev):
    proposal = copilot.propose("remove the trend", spec, template, dev)
    assert DashboardSpec(**proposal.spec).component("trend_1") is not None
    assert "mandatory" in proposal.changes[0].says


def test_it_widens_a_chart_into_the_columns_its_row_is_not_using(copilot, spec, template, dev):
    proposal = copilot.propose("make the trend wider", spec, template, dev)
    rows = DashboardSpec(**proposal.spec).layout
    trend = next(row for row in rows if row.row == "trend")
    # the distribution slot holds no chart on this dashboard, so the trend grows into its columns
    assert [(p.id, p.width) for p in trend.slots] == [("trend_1", 9)]
    assert proposal.problems == []


def test_it_widens_a_chart_by_taking_from_its_neighbour(copilot, spec, template, dev):
    proposal = copilot.propose("make kpi_1 wider", spec, template, dev)
    kpis = next(row for row in DashboardSpec(**proposal.spec).layout if row.row == "kpis")
    assert [(p.id, p.width) for p in kpis.slots] == [("kpi_1", 4), ("kpi_2", 2)]


def test_a_width_the_template_will_not_allow_is_refused_with_its_reason(copilot, spec, template, dev):
    narrow = copilot.propose("make kpi_1 narrower", spec, template, dev)   # a kpi may not go below 2
    narrow = copilot.propose("make kpi_1 narrower", DashboardSpec(**narrow.spec), template, dev)
    assert not narrow.changes
    assert any("already the smallest width" in said for said in narrow.unresolved), narrow.unresolved


def test_a_question_is_answered_from_the_registry_and_changes_nothing(copilot, spec, template, dev):
    proposal = copilot.propose("what is the QoE Score?", spec, template, dev)
    assert proposal.changes == [] and proposal.spec is None
    assert "cem.qoe_score" in proposal.answer and "owned by" in proposal.answer


def test_the_form_places_a_chart_the_same_way_the_words_do(copilot, spec, template, dev):
    proposal = copilot.compose("distribution_1", "cem.qoe_score", "histogram", None, None, spec, template, dev)
    assert proposal.changes[0].slot == "distribution_1"
    assert proposal.read_by == "the Studio's own form"
    assert DashboardSpec(**proposal.spec).component("distribution_1").type == "histogram"


def test_the_form_refuses_a_type_the_slot_does_not_accept(copilot, spec, template, dev):
    proposal = copilot.compose("distribution_1", "cem.qoe_score", "map", None, None, spec, template, dev)
    assert not proposal.changes
    assert any("does not accept a map" in said for said in proposal.unresolved)


def test_the_form_refuses_a_slot_that_already_holds_a_chart(copilot, spec, template, dev):
    proposal = copilot.compose("kpi_1", "cem.qoe_score", "big_number", None, None, spec, template, dev)
    assert any("already holds a chart" in said for said in proposal.unresolved)


def test_a_metric_the_person_is_not_cleared_for_is_refused(copilot, spec, template, policy, registry):
    """The copilot is not a way round the registry's rules: the policy engine answers for the person who
    asked, exactly as it does on the command line."""
    restricted = next((m for m in registry.metrics.values()
                       if not policy.may_use(policy.identify("cem.analyst"), m)), None)
    if restricted is None:
        pytest.skip("no metric in the registry is closed to cem.analyst")
    proposal = copilot.compose("distribution_1", restricted.id, None, None, None, spec, template,
                               policy.identify("cem.analyst"))
    assert not proposal.changes and any("not cleared" in said for said in proposal.unresolved)


def test_what_it_can_be_asked_is_drawn_from_the_project_itself(copilot, spec, template):
    asked = copilot.examples(spec, template)
    assert any("weekly" in example for example in asked) and len(asked) <= 6


def test_the_diff_says_what_one_specification_changes_about_another(copilot, spec, template, dev):
    proposal = copilot.propose("add a distribution of QoE Score", spec, template, dev)
    said = diff(spec, DashboardSpec(**proposal.spec), template)
    assert any(line.startswith("+ ") and "distribution_1" in line for line in said)
