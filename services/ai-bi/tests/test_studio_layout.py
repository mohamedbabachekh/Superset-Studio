"""The Studio's layout: what a canvas may rearrange, what it may not, and that nothing is lost on the way.

These hold the one structural change the Studio asked for — the layout stops belonging to the template and
starts belonging to the specification (aibi/layout.py, docs/superset-studio.md §4). The round trip below is
the assertion that makes a canvas safe to build at all: a layout written for Superset and read back is the
layout that went in.
"""
from __future__ import annotations

import pytest

from aibi.layout import from_positions, problems, resolve, source
from aibi.models import DashboardSpec, LayoutRow, SlotPlacement, TemplateDef


@pytest.fixture()
def spec(registry) -> DashboardSpec:
    """The demo's own specification, as the engine writes it: four KPIs, a trend, a distribution, a ranking."""
    return DashboardSpec(**{
        "dashboard": {"id": "cem-qoe", "template": "executive", "title": "CEM QoE Overview"},
        "filters": ["date"],
        "components": [
            {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"},
            {"slot": "kpi_2", "type": "big_number", "metric": "cem.active_users", "dataset": "cem.qoe_daily"},
            {"slot": "kpi_3", "type": "big_number", "metric": "cem.degraded_users", "dataset": "cem.qoe_daily"},
            {"slot": "trend_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date",
             "dataset": "cem.qoe_daily"},
            {"slot": "distribution_1", "type": "histogram", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"},
            {"slot": "ranking_1", "type": "bar", "metric": "cem.qoe_score", "dimension": "region",
             "dataset": "cem.qoe_daily"},
        ]})


def _row(name: str, height: int, slots: list[tuple[str, int]]) -> LayoutRow:
    return LayoutRow(row=name, height=height, slots=[SlotPlacement(id=s, width=w) for s, w in slots])


def test_a_specification_without_a_layout_is_laid_out_by_its_template(registry, spec):
    """Every version stored before the Studio carries no layout, and must restore exactly as it was."""
    template = registry.template("executive")
    assert spec.layout == []
    assert source(spec) == "template"
    assert resolve(spec, template) == template.layout
    assert problems(spec, template) == []


def test_a_canvas_may_reorder_rows_resize_charts_and_reorder_a_row(registry, spec):
    template = registry.template("executive")
    spec.layout = [
        _row("breakdown", 52, [("map_1", 6), ("ranking_1", 6)]),          # the sections, in another order
        _row("kpis", 20, [("kpi_2", 2), ("kpi_1", 2), ("kpi_3", 4), ("kpi_4", 4)]),   # reordered and resized
        _row("trend", 40, [("distribution_1", 4), ("trend_1", 8)]),       # swapped within the row
    ]
    assert problems(spec, template) == []
    assert source(spec) == "specification"
    assert [row.row for row in resolve(spec, template)] == ["breakdown", "kpis", "trend"]


@pytest.mark.parametrize("rows, says", [
    ([_row("summary", 16, [("kpi_1", 3)])], "not one of executive's rows"),
    ([_row("kpis", 16, [("kpi_1", 7), ("kpi_2", 7)])], "14 columns wide: the grid has 12"),
    ([_row("kpis", 16, [("kpi_1", 1)])], "a kpi needs 2"),
    ([_row("trend", 40, [("trend_1", 2)])], "a trend needs 4"),
    ([_row("kpis", 16, [("kpi_1", 3)]), _row("trend", 40, [("kpi_1", 4), ("trend_1", 8)])], "is on two rows"),
    ([_row("kpis", 16, [("kpi_1", 3)]), _row("kpis", 16, [("kpi_2", 3)])], "appears 2 times"),
    ([_row("kpis", 16, [("kpi_1", 3)]), _row("trend", 40, [("trend_1", 8), ("map_1", 4)])],
     "does not leave the section it is accountable to"),
    ([_row("kpis", 16, [("kpi_1", 3), ("nowhere", 3)])], "not a slot of executive"),
])
def test_what_a_layout_may_not_do_is_said_in_words_a_person_can_act_on(registry, spec, rows, says):
    template = registry.template("executive")
    spec.layout = rows
    found = problems(spec, template)
    assert any(says in problem for problem in found), found


def test_a_chart_with_no_place_on_the_layout_is_refused(registry, spec):
    """A canvas that forgets a chart would silently stop drawing it — the one failure a person cannot see."""
    template = registry.template("executive")
    spec.layout = [_row("kpis", 16, [("kpi_1", 3), ("kpi_2", 3), ("kpi_3", 3)]),
                   _row("trend", 40, [("trend_1", 8), ("distribution_1", 4)])]
    assert any("ranking_1 has a chart and no place" in problem for problem in problems(spec, template))


def test_a_section_is_not_cut_in_two(registry):
    """A section is announced by one header above its first row: its rows stay together. No template of the
    registry has a section of two rows yet, so the rule is held against a template made here."""
    template = TemplateDef(**{
        "template": {"id": "two-row", "name": "Two rows in one section", "version": "0.1"},
        "layout": [{"row": "a", "height": 16, "slots": [{"id": "kpi_1", "width": 6}]},
                   {"row": "b", "height": 16, "slots": [{"id": "kpi_2", "width": 6}]},
                   {"row": "c", "height": 40, "slots": [{"id": "trend_1", "width": 12}]}],
        "slots": {"kpi_1": {"purpose": "kpi", "accepts": ["big_number"], "mandatory": True},
                  "kpi_2": {"purpose": "kpi", "accepts": ["big_number"]},
                  "trend_1": {"purpose": "trend", "accepts": ["line"]}},
        "theme": {"color_scheme": "sharpsight", "sequential_scheme": "superset_seq_1"},
        "responsive": {"viewports": [{"name": "desktop", "width": 1440}], "min_slot_width": {"kpi": 2, "chart": 4}},
        "sections": [{"id": "headline", "rows": ["a", "b"]}, {"id": "evolution", "rows": ["c"]}]})
    spec = DashboardSpec(**{
        "dashboard": {"id": "two-row", "template": "two-row", "title": "Two rows"},
        "components": [{"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"},
                       {"slot": "kpi_2", "type": "big_number", "metric": "cem.active_users",
                        "dataset": "cem.qoe_daily"},
                       {"slot": "trend_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date",
                        "dataset": "cem.qoe_daily"}]})
    # the rows of one section, with another section's row between them
    spec.layout = [_row("a", 16, [("kpi_1", 6)]), _row("c", 40, [("trend_1", 12)]), _row("b", 16, [("kpi_2", 6)])]
    assert any("in two pieces" in problem for problem in problems(spec, template))
    # and a chart moved onto the OTHER row of its own section is allowed
    spec.layout = [_row("a", 16, [("kpi_1", 6), ("kpi_2", 6)]), _row("b", 16, [("kpi_1", 6)]),
                   _row("c", 40, [("trend_1", 12)])]
    assert [p for p in problems(spec, template) if "is on two rows" not in p] == []


def test_a_layout_written_for_superset_and_read_back_is_the_same_layout(registry, dashboards, spec):
    """The round trip. Without it a canvas is a promise nobody checked: Superset holds rows of charts with a
    width in twelfths and a height in grid units, and that is all a layout may say."""
    template = registry.template("executive")
    spec.components.append(type(spec.components[0])(**{"slot": "kpi_4", "type": "big_number",
                                                       "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"}))
    spec.components.append(type(spec.components[0])(**{"slot": "map_1", "type": "table",
                                                       "metric": "cem.qoe_score", "dimension": "region",
                                                       "dataset": "cem.qoe_daily"}))
    spec.layout = [
        _row("breakdown", 52, [("map_1", 7), ("ranking_1", 5)]),
        _row("kpis", 20, [("kpi_4", 2), ("kpi_3", 2), ("kpi_2", 4), ("kpi_1", 4)]),
        _row("trend", 40, [("distribution_1", 5), ("trend_1", 7)]),
    ]
    assert problems(spec, template) == []
    charts = {c.slot: {"id": n + 100, "name": c.slot, "uuid": f"u{n}"} for n, c in enumerate(spec.components)}
    position = dashboards.layout(spec, template, charts)
    assert from_positions(position) == spec.layout


def test_the_template_s_own_layout_round_trips_too(registry, dashboards, spec):
    template = registry.template("executive")
    charts = {c.slot: {"id": n + 100, "name": c.slot, "uuid": f"u{n}"} for n, c in enumerate(spec.components)}
    position = dashboards.layout(spec, template, charts)
    # the template's rows, less the slots no chart was made for: a dashboard draws what it has
    expected = [_row("kpis", 16, [("kpi_1", 4), ("kpi_2", 4), ("kpi_3", 4)]),
                _row("trend", 44, [("trend_1", 8), ("distribution_1", 4)]),
                _row("breakdown", 52, [("ranking_1", 12)])]
    assert from_positions(position) == expected


def test_validation_refuses_a_layout_the_template_does_not_allow(registry, dashboards, policy, spec):
    """Level 1 of the brief's validation (§53.1). A draft whose layout is impossible never reaches Superset."""
    identity = policy.identify("dev.user")
    assert dashboards.validate(spec, identity).passed
    named = "layout is the template's, or a change of it the template allows"
    assert all(c.status == "pass" for c in dashboards.validate(spec, identity).checks if c.name == named)
    spec.layout = [_row("kpis", 16, [("kpi_1", 1)])]
    report = dashboards.validate(spec, identity)
    failed = [c for c in report.failures if c.name == named]
    assert failed and "a kpi needs 2" in failed[0].detail
    assert not report.passed


def test_a_layout_without_a_row_for_a_chart_says_it_would_not_be_drawn(registry, spec):
    """What the copilot's `_place` must never leave behind: a chart whose row the layout does not carry."""
    template = registry.template("executive")
    spec.layout = [_row("kpis", 16, [("kpi_1", 3), ("kpi_2", 3), ("kpi_3", 3)]),
                   _row("trend", 44, [("trend_1", 8), ("distribution_1", 4)])]
    assert any("ranking_1 has a chart and no place on the layout" in p for p in problems(spec, template))
