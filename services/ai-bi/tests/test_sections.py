"""Sections: what a template's rows are FOR, and what a dashboard says of each — another title, a description,
an owner, a filter of its own, hidden. No database, no BI engine: the rules only."""
from __future__ import annotations

import pytest
import yaml

from aibi.models import DashboardSpec, SectionSpec
from aibi.registry import Registry, RegistryError
from aibi.visual_qa_agent import Render, VisualQaAgent


@pytest.fixture()
def dev(policy):
    return policy.identify("dev.user")


def _spec(*components, template="executive", sections=None, filters=None, pid="t") -> DashboardSpec:
    return DashboardSpec(dashboard={"id": pid, "template": template, "title": "T"}, filters=filters or [],
                         components=list(components), sections=sections or [])


KPI = {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"}
TREND = {"slot": "trend_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date", "dataset": "cem.qoe_daily"}
BAR = {"slot": "ranking_1", "type": "bar", "metric": "cem.qoe_score", "dimension": "region", "dataset": "cem.qoe_daily"}


# ── the template's sections ─────────────────────────────────────────────────────────────────────────────────
def test_a_templates_sections_own_every_row_exactly_once(registry, registry_copy):
    executive = registry.template("executive")
    assert [(s.id, s.title, s.rows) for s in executive.section_list()] == [
        ("headline", "Executive KPIs", ["kpis"]), ("evolution", "Trend and distribution", ["trend"]),
        ("breakdown", "Where it stands", ["breakdown"])]
    assert executive.slots_in("breakdown") == ["ranking_1", "map_1"] and executive.section_of("kpi_2") == "headline"
    path = registry_copy / "templates" / "executive.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["sections"].pop()                                  # the breakdown row now belongs to no section
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(RegistryError, match="sections must name every row exactly once"):
        Registry(registry_copy)


def test_the_engine_names_every_section_of_its_template(registry, dashboards, semantic, requirement):
    template = registry.template("executive")
    metrics, _ = semantic.find_metrics(requirement)
    drafts, _ = dashboards.draft(requirement, template, metrics)
    for draft in drafts:
        draft.dataset, draft.dimension = "cem.qoe_daily", draft.column or ("date" if draft.role == "time" else draft.dimension)
    spec, _ = dashboards.specify("cem-qoe", requirement, template, [d for d in drafts if d.slot != "map_1"])
    assert [s.id for s in spec.sections] == ["headline", "evolution", "breakdown"]     # every section is named


# ── a dashboard's sections ──────────────────────────────────────────────────────────────────────────────────
def test_a_dashboard_renames_describes_and_owns_its_sections(registry, dashboards, dev):
    template = registry.template("executive")
    spec = _spec(KPI, TREND, BAR, sections=[SectionSpec(id="headline"),
                                            SectionSpec(id="breakdown", title="Regions", description="Who is ahead.", owner="CEM")])
    found = {s["id"]: s for s in dashboards.sections(spec, template)}
    assert (found["headline"]["title"], found["headline"]["declared"]) == ("Executive KPIs", True)
    assert (found["breakdown"]["title"], found["breakdown"]["owner"], found["evolution"]["declared"]) == ("Regions", "CEM", False)
    charts = {c.slot: {"id": i, "name": c.slot} for i, c in enumerate(spec.components)}
    grid = dashboards.layout(spec, template, charts)
    # a header for each section the specification names — the one it does not name has none
    assert grid["GRID_ID"]["children"] == ["HEADER-headline", "ROW-kpis", "ROW-trend", "HEADER-breakdown", "ROW-breakdown"]
    assert grid["HEADER-breakdown"]["meta"]["text"] == "Regions"
    assert dashboards.section_titles(spec, template, charts) == ["Executive KPIs", "Regions"]
    unknown = _spec(KPI, TREND, sections=[SectionSpec(id="appendix")])
    assert "section exists in template" in {c.name for c in dashboards.validate(unknown, dev).failures}


def test_a_hidden_section_keeps_its_charts_aside_and_off_the_dashboard(registry, dashboards, dev):
    template = registry.template("executive")
    hidden = SectionSpec(id="breakdown", visibility="hidden", parked=[BAR])
    spec = _spec(KPI, TREND, sections=[hidden])
    assert dashboards.validate(spec, dev).passed
    assert spec.datasets() == ["cem.qoe_daily"] and "ranking_1" not in [c.slot for c in spec.components]
    assert [(p.slot, p.metric) for p in spec.section("breakdown").parked] == [("ranking_1", "cem.qoe_score")]
    assert "HEADER-breakdown" not in dashboards.layout(spec, template, {"kpi_1": {"id": 1, "name": "k"}, "trend_1": {"id": 2, "name": "t"}})
    failed = lambda s: {c.name for c in dashboards.validate(s, dev).failures}  # noqa: E731
    assert "a hidden section is not on the dashboard" in failed(_spec(KPI, TREND, BAR, sections=[SectionSpec(id="breakdown", visibility="hidden")]))
    assert "a section holding a mandatory slot is not hidden" in failed(_spec(TREND, sections=[SectionSpec(id="headline", visibility="hidden", parked=[KPI])]))
    assert "only a hidden section keeps components aside" in failed(_spec(KPI, TREND, sections=[SectionSpec(id="breakdown", parked=[BAR])]))


def test_a_sections_filter_reaches_its_section_and_no_other(registry, dashboards, dev):
    template = registry.template("executive")
    spec = _spec(KPI, TREND, BAR, filters=["date"], sections=[SectionSpec(id="breakdown", filters=["region"])])
    assert dashboards.validate(spec, dev).passed
    assert dashboards.reach(spec, template) == {"kpi_1": ["date"], "trend_1": ["date"], "ranking_1": ["date", "region"]}
    charts = {"kpi_1": {"id": 1, "name": "k"}, "trend_1": {"id": 2, "name": "t"}, "ranking_1": {"id": 3, "name": "r"}}
    filters = {f["id"]: f for f in dashboards.native_filters(spec, {"cem.qoe_daily": 9}, charts, template)}
    scoped = filters["NATIVE_FILTER-t-breakdown-region"]
    assert (scoped["name"], scoped["chartsInScope"], scoped["scope"]["excluded"]) == ("Region · Where it stands", [3], [1, 2])
    assert filters["NATIVE_FILTER-t-date"]["chartsInScope"] == [1, 2, 3]
    state = dashboards.filter_state(spec, {"date": "Last week", "region": "Oriental"})
    assert set(state) == {"NATIVE_FILTER-t-date", "NATIVE_FILTER-t-breakdown-region"}
    failed = lambda s: {c.name for c in dashboards.validate(s, dev).failures}  # noqa: E731
    assert "a filter is set once" in failed(_spec(KPI, TREND, BAR, filters=["region"], sections=[SectionSpec(id="breakdown", filters=["region"])]))
    assert "section filter is a column of the section's datasets" in failed(_spec(KPI, TREND, BAR, sections=[SectionSpec(id="breakdown", filters=["site_id"])]))
    crowded = _spec(KPI, TREND, BAR, filters=["date", "rat"], template="management", sections=[
        SectionSpec(id="headline", filters=["region"]), SectionSpec(id="performance", filters=["region"])])
    assert "filter count within the template's limit" in failed(crowded)          # management allows three in all


# ── on the page ─────────────────────────────────────────────────────────────────────────────────────────────
def test_a_sections_header_is_not_a_hole_and_a_missing_one_is_a_failure(registry):
    card = lambda cid, x, y, w, h: {"chart_id": cid, "rect": {"x": x, "y": y, "w": w, "h": h}, "clipped": [], "error": None,  # noqa: E731
                                    "loading": False, "blank_ratio": 0.9}
    # the geometry measured on a render of the management template: a header is 64 px, and 16 px from each row
    header = lambda text, y: {"text": text, "rect": {"x": 16, "y": y, "w": 1408, "h": 64}}  # noqa: E731
    spec = _spec(KPI, TREND, BAR)
    charts = {"kpi_1": {"id": 1}, "trend_1": {"id": 2}, "ranking_1": {"id": 3}}
    data = {"kpi_1": {"rowcount": 1, "data": []}, "trend_1": {"rowcount": 90, "data": []}, "ranking_1": {"rowcount": 5, "data": []}}
    agent = VisualQaAgent("http://x", "u", "p", registry.policies)
    render = Render(viewport="desktop", width=1440, screenshot="d.png", load_ms=1, components=[
        card(1, 16, 217, 1408, 128), card(2, 16, 441, 1408, 352), card(3, 16, 889, 1408, 416)],
        headers=[header("Executive KPIs", 137), header("Trend and distribution", 361), header("Where it stands", 809)])
    titles = ["Executive KPIs", "Trend and distribution", "Where it stands"]
    report = agent.check(render, spec, registry.template("executive"), charts, data, titles)
    assert report.passed, [(c.name, c.detail) for c in report.failures]
    whitespace = next(c for c in report.checks if c.name == "no excessive whitespace")
    assert "rows at most 16 px apart" in whitespace.detail                      # 96 px between rows, 64 of them a header
    render.headers.pop()
    missing = agent.check(render, spec, registry.template("executive"), charts, data, titles)
    assert [(c.name, c.code) for c in missing.failures] == [("no excessive whitespace", "whitespace"),
                                                            ("every section is announced", "section_header_missing")]
