"""The rules behind BI Studio (the brief's sections 1 to 15): an asset library filed under domains, shared
charts and sections defined once, sections as first-class parts of a dashboard, where an asset is used and
what a change would touch, and the governance checklist. No database, no BI engine: the rules only."""
from __future__ import annotations

import pytest
import yaml

from aibi.assets import AssetError, Catalog
from aibi.audit import AuditLog
from aibi.dashboard_agent import DashboardAgent
from aibi.library import Library, LibraryError
from aibi.models import DashboardSpec, ProjectVersion, SectionSpec, utcnow
from aibi.policy_engine import PolicyEngine, PolicyError
from aibi.registry import Registry, RegistryError
from aibi.semantic_agent import SemanticAgent
from aibi.store import Store
from aibi.visual_qa_agent import Render, VisualQaAgent, _set_option


@pytest.fixture()
def studio(registry, policy, tmp_path):
    """A library, a catalogue and a dashboard agent that knows the library — over an empty store."""
    store = Store(tmp_path / "control-plane.sqlite")
    library = Library(store, registry, policy, AuditLog(tmp_path / "audit"))
    dashboards = DashboardAgent(registry, policy, SemanticAgent(registry), library)
    return type("Studio", (), {"store": store, "library": library, "dashboards": dashboards, "registry": registry,
                               "policy": policy, "catalog": Catalog(registry, policy, store, library),
                               "dev": policy.identify("dev.user"), "manager": policy.identify("release.manager")})()


RANKING = {"id": "qoe-by-region", "name": "QoE Score by region", "type": "bar", "metric": "cem.qoe_score", "dimension": "region"}


def _spec(*components, template="executive", sections=None, filters=None, pid="t") -> DashboardSpec:
    return DashboardSpec(dashboard={"id": pid, "template": template, "title": "T"}, filters=filters or [],
                         components=list(components), sections=sections or [])


KPI = {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"}
TREND = {"slot": "trend_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date", "dataset": "cem.qoe_daily"}
BAR = {"slot": "ranking_1", "type": "bar", "metric": "cem.qoe_score", "dimension": "region", "dataset": "cem.qoe_daily"}


def _project(studio, pid, spec, status=None, draft=None):
    studio.store.save_project(pid, {"project_id": pid, "tenant": "default", "title": pid, "created_by": "dev.user",
                                    "spec": spec.model_dump() if spec else None,
                                    "draft": {"spec": draft.model_dump()} if draft else None}, utcnow())
    if status:
        studio.store.save_version(ProjectVersion(project_id=pid, version="1.0.0", created_by="dev.user",
                                                 request_id="r", status=status))


# ── the registries the library is built on ──────────────────────────────────────────────────────────────────
def test_domains_are_logical_folders_and_every_asset_is_filed_once(registry, registry_copy):
    assert registry.domain_of("dataset", "cem.qoe_daily") == "qoe" and registry.domain_of("dataset", "cem.region") == "geography"
    assert registry.domain_of("metric", "cem.active_users") == "audience"
    document = yaml.safe_load((registry_copy / "domains.yaml").read_text(encoding="utf-8"))
    document["domains"]["geography"]["datasets"].append("cem.qoe_daily")          # filed twice
    document["domains"]["audience"]["metrics"].append("cem.revenue")              # not registered
    (registry_copy / "domains.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(RegistryError, match="cem.qoe_daily is in two domains") as refused:
        Registry(registry_copy)
    assert "metric cem.revenue is not registered" in str(refused.value)


def test_a_generated_dataset_is_filed_where_its_base_is(with_generated):
    assert with_generated.domain_of("dataset", "cem.cem_qoe__qoe_daily_region_iso") == "qoe"


def test_chart_templates_say_what_cannot_be_drawn_yet(registry):
    assert {"kpi", "time_series", "top_n", "distribution", "geographic", "detail_table", "variance",
            "target_vs_actual"} == set(registry.chart_templates)
    unavailable = {k: t.reason for k, t in registry.chart_templates.items() if t.status != "available"}
    assert set(unavailable) == {"variance", "target_vs_actual"} and all(unavailable.values())
    assert registry.chart_templates["top_n"].options == {"row_limit": 10, "sort": "desc"}


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


# ── shared charts and sections ──────────────────────────────────────────────────────────────────────────────
def test_a_shared_chart_is_a_governed_definition(studio):
    saved = studio.library.save(studio.dev, "chart", dict(RANKING), "r1")
    assert (saved.revision, saved.certified, saved.created_by, saved.owner) == (1, False, "dev.user", "dev.user")
    for bad, why in (({"type": "pie", "metric": "cem.degraded_rate", "dimension": "region"}, "shares of nothing"),
                     ({"type": "bar", "metric": "cem.qoe_score"}, "needs a dimension"),
                     ({"type": "bar", "metric": "cem.revenue", "dimension": "region"}, "not in the metric registry"),
                     ({"type": "bar", "metric": "cem.qoe_score", "dimension": "site_id"}, "gives cem.qoe_score by site_id"),
                     ({"type": "bar", "metric": "cem.qoe_score", "dimension": "loaded_at"}, "may not be broken down by loaded_at"),
                     ({"type": "sankey", "metric": "cem.qoe_score"}, "not an approved visualization"),
                     ({"type": "bar", "metric": "cem.qoe_score", "dimension": "region", "id": "Not A Slug"}, "lower-case words")):
        with pytest.raises(LibraryError, match=why):
            studio.library.save(studio.dev, "chart", {"id": "x", "name": "X", **bad}, "r")
    with pytest.raises(PolicyError, match="assets.write"):
        studio.library.save(studio.policy.identify("lead.reviewer"), "chart", dict(RANKING), "r")
    with pytest.raises(LibraryError, match="charts and sections"):
        studio.library.save(studio.dev, "dataset", {"id": "x"}, "r")


def test_certifying_is_another_persons_and_a_change_undoes_it(studio):
    studio.library.save(studio.dev, "chart", dict(RANKING), "r1")
    with pytest.raises(PolicyError, match="assets.certify"):
        studio.library.certify(studio.dev, "chart", "qoe-by-region", "r")
    mine = studio.library.save(studio.manager, "chart", {**RANKING, "id": "mine", "name": "Mine"}, "r")
    with pytest.raises(LibraryError, match="someone other than the person who made it"):
        studio.library.certify(studio.manager, "chart", mine.id, "r")
    assert studio.library.certify(studio.manager, "chart", "qoe-by-region", "r2").certified_by == "release.manager"
    # saved again unchanged, it is the same asset; changed, it is a new revision — and no longer certified
    assert studio.library.save(studio.dev, "chart", dict(RANKING), "r3").certified
    changed = studio.library.save(studio.dev, "chart", {**RANKING, "options": {"row_limit": 5}}, "r4")
    assert (changed.revision, changed.certified, changed.certified_by) == (2, False, None)
    assert [r["action"] for r in studio.library.audit.read()][-2:] == ["library:certify", "library:change"]


def test_an_asset_in_use_is_not_changed_unseen_and_not_deleted(studio):
    studio.library.save(studio.dev, "chart", dict(RANKING), "r1")
    _project(studio, "north", _spec(KPI, TREND, {**BAR, "asset": "qoe-by-region"}), status="PROD")
    impact = lambda kind, asset_id: studio.catalog.impact(kind, asset_id, "default")  # noqa: E731
    with pytest.raises(LibraryError, match="is in use") as stopped:
        studio.library.save(studio.dev, "chart", {**RANKING, "options": {"row_limit": 5}}, "r2", impact)
    assert stopped.value.impact["severity"] == "high" and stopped.value.impact["production"] == ["north"]
    assert studio.library.chart("qoe-by-region").revision == 1
    assert studio.library.save(studio.dev, "chart", {**RANKING, "options": {"row_limit": 5}}, "r3", impact, True).revision == 2
    with pytest.raises(LibraryError, match="is in use and is not deleted"):
        studio.library.delete(studio.dev, "chart", "qoe-by-region", "r4", impact)
    studio.library.save(studio.dev, "chart", {**RANKING, "id": "spare", "name": "Spare"}, "r5")
    studio.library.delete(studio.dev, "chart", "spare", "r6", impact)
    assert studio.library.chart("spare") is None


def test_a_shared_section_is_made_of_shared_charts(studio):
    with pytest.raises(LibraryError, match="shared chart qoe-by-region does not exist"):
        studio.library.save(studio.dev, "section", {"id": "regions", "name": "Regions", "charts": ["qoe-by-region"]}, "r")
    studio.library.save(studio.dev, "chart", dict(RANKING), "r1")
    section = studio.library.save(studio.dev, "section", {"id": "regions", "name": "Regions", "charts": ["qoe-by-region"]}, "r2")
    assert section.charts == ["qoe-by-region"]
    assert studio.catalog.usage("chart", "qoe-by-region", "default")["shared_sections"] == ["regions"]


# ── a component that references a shared chart ──────────────────────────────────────────────────────────────
def test_a_reference_is_written_out_and_must_stay_what_it_names(studio):
    studio.library.save(studio.dev, "chart", {**RANKING, "description": "Who is ahead."}, "r1")
    spec = _spec(KPI, TREND, {"slot": "ranking_1", "type": "table", "metric": "cem.active_users", "asset": "qoe-by-region"})
    studio.dashboards.resolve(spec, studio.dev)
    ranking = spec.component("ranking_1")
    assert (ranking.type, ranking.metric, ranking.dimension, ranking.title, ranking.dataset) == (
        "bar", "cem.qoe_score", "region", "QoE Score by region", "cem.qoe_daily")
    assert ranking.description == "Who is ahead."
    assert studio.dashboards.validate(spec, studio.dev).passed
    ranking.options.row_limit = 3                                  # edited here, and still claiming to be the shared one
    failed = {c.name: c.detail for c in studio.dashboards.validate(spec, studio.dev).failures}
    assert "it differs from the shared chart it names" in failed["component is its shared chart"]
    gone = _spec(KPI, TREND, {**BAR, "asset": "no-such-chart"})
    assert "shared chart exists" in {c.name for c in studio.dashboards.validate(gone, studio.dev).failures}


def test_the_engine_references_a_shared_chart_instead_of_writing_it_again(studio, requirement):
    studio.library.save(studio.dev, "chart", dict(RANKING), "r1")
    template = studio.registry.template("executive")
    metrics, _ = SemanticAgent(studio.registry).find_metrics(requirement)
    drafts, _ = studio.dashboards.draft(requirement, template, metrics)
    for draft in drafts:
        draft.dataset, draft.dimension = "cem.qoe_daily", draft.column or ("date" if draft.role == "time" else draft.dimension)
    spec, notes = studio.dashboards.specify("cem-qoe", requirement, template, [d for d in drafts if d.slot != "map_1"])
    assert spec.component("ranking_1").asset == "qoe-by-region"
    assert any("ranking_1 is the shared chart « QoE Score by region »" in n for n in notes)
    assert [s.id for s in spec.sections] == ["headline", "evolution", "breakdown"]     # every section is named


def test_a_correction_for_one_dashboard_stops_it_being_the_shared_chart(studio):
    spec = _spec(KPI, TREND, {**BAR, "asset": "qoe-by-region"})
    _set_option(spec, "ranking_1", row_limit=14)
    assert spec.component("ranking_1").asset is None and spec.component("ranking_1").options.row_limit == 14


# ── sections ────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_dashboard_renames_describes_and_owns_its_sections(studio):
    template = studio.registry.template("executive")
    spec = _spec(KPI, TREND, BAR, sections=[SectionSpec(id="headline"),
                                            SectionSpec(id="breakdown", title="Regions", description="Who is ahead.", owner="CEM")])
    found = {s["id"]: s for s in studio.dashboards.sections(spec, template)}
    assert (found["headline"]["title"], found["headline"]["declared"]) == ("Executive KPIs", True)
    assert (found["breakdown"]["title"], found["breakdown"]["owner"], found["evolution"]["declared"]) == ("Regions", "CEM", False)
    charts = {c.slot: {"id": i, "name": c.slot} for i, c in enumerate(spec.components)}
    grid = studio.dashboards.layout(spec, template, charts)
    # a header for each section the specification names — the one it does not name has none
    assert grid["GRID_ID"]["children"] == ["HEADER-headline", "ROW-kpis", "ROW-trend", "HEADER-breakdown", "ROW-breakdown"]
    assert grid["HEADER-breakdown"]["meta"]["text"] == "Regions"
    assert studio.dashboards.section_titles(spec, template, charts) == ["Executive KPIs", "Regions"]
    unknown = _spec(KPI, TREND, sections=[SectionSpec(id="appendix")])
    assert "section exists in template" in {c.name for c in studio.dashboards.validate(unknown, studio.dev).failures}


def test_a_hidden_section_keeps_its_charts_aside_and_off_the_dashboard(studio):
    template = studio.registry.template("executive")
    hidden = SectionSpec(id="breakdown", visibility="hidden", parked=[BAR])
    spec = _spec(KPI, TREND, sections=[hidden])
    assert studio.dashboards.validate(spec, studio.dev).passed
    assert spec.datasets() == ["cem.qoe_daily"] and "ranking_1" not in [c.slot for c in spec.components]
    slot = next(s for sec in studio.dashboards.canvas(spec, template) for row in sec["rows"] for s in row["slots"] if s["slot"] == "ranking_1")
    assert slot["parked"] and slot["component"]["metric"] == "cem.qoe_score"
    assert "HEADER-breakdown" not in studio.dashboards.layout(spec, template, {"kpi_1": {"id": 1, "name": "k"}, "trend_1": {"id": 2, "name": "t"}})
    failed = lambda s: {c.name for c in studio.dashboards.validate(s, studio.dev).failures}  # noqa: E731
    assert "a hidden section is not on the dashboard" in failed(_spec(KPI, TREND, BAR, sections=[SectionSpec(id="breakdown", visibility="hidden")]))
    assert "a section holding a mandatory slot is not hidden" in failed(_spec(TREND, sections=[SectionSpec(id="headline", visibility="hidden", parked=[KPI])]))
    assert "only a hidden section keeps components aside" in failed(_spec(KPI, TREND, sections=[SectionSpec(id="breakdown", parked=[BAR])]))


def test_a_sections_filter_reaches_its_section_and_no_other(studio):
    template = studio.registry.template("executive")
    spec = _spec(KPI, TREND, BAR, filters=["date"], sections=[SectionSpec(id="breakdown", filters=["region"])])
    assert studio.dashboards.validate(spec, studio.dev).passed
    assert studio.dashboards.reach(spec, template) == {"kpi_1": ["date"], "trend_1": ["date"], "ranking_1": ["date", "region"]}
    charts = {"kpi_1": {"id": 1, "name": "k"}, "trend_1": {"id": 2, "name": "t"}, "ranking_1": {"id": 3, "name": "r"}}
    filters = {f["id"]: f for f in studio.dashboards.native_filters(spec, {"cem.qoe_daily": 9}, charts, template)}
    scoped = filters["NATIVE_FILTER-t-breakdown-region"]
    assert (scoped["name"], scoped["chartsInScope"], scoped["scope"]["excluded"]) == ("Region · Where it stands", [3], [1, 2])
    assert filters["NATIVE_FILTER-t-date"]["chartsInScope"] == [1, 2, 3]
    state = studio.dashboards.filter_state(spec, {"date": "Last week", "region": "Oriental"})
    assert set(state) == {"NATIVE_FILTER-t-date", "NATIVE_FILTER-t-breakdown-region"}
    failed = lambda s: {c.name for c in studio.dashboards.validate(s, studio.dev).failures}  # noqa: E731
    assert "a filter is set once" in failed(_spec(KPI, TREND, BAR, filters=["region"], sections=[SectionSpec(id="breakdown", filters=["region"])]))
    assert "section filter is a column of the section's datasets" in failed(_spec(KPI, TREND, BAR, sections=[SectionSpec(id="breakdown", filters=["site_id"])]))
    crowded = _spec(KPI, TREND, BAR, filters=["date", "rat"], template="management", sections=[
        SectionSpec(id="headline", filters=["region"]), SectionSpec(id="performance", filters=["region"])])
    assert "filter count within the template's limit" in failed(crowded)          # management allows three in all


def test_a_section_is_filled_from_a_shared_one_where_its_charts_fit(studio):
    studio.library.save(studio.dev, "chart", dict(RANKING), "r1")
    studio.library.save(studio.dev, "chart", {"id": "users", "name": "Active Users", "type": "big_number", "metric": "cem.active_users"}, "r2")
    studio.library.save(studio.dev, "section", {"id": "regions", "name": "Regional standing", "charts": ["qoe-by-region", "users"],
                                                "filters": ["region"]}, "r3")
    template = studio.registry.template("operational")
    spec = _spec(KPI, TREND, template="operational")
    notes = studio.dashboards.fill_section(spec, template, "breakdown", "regions")
    assert notes == ["ranking_1: shared chart « QoE Score by region »", "shared chart users found no slot in section breakdown that accepts it"]
    own = spec.section("breakdown")
    assert (own.asset, own.title, own.filters) == ("regions", "Regional standing", ["region"])
    studio.dashboards.resolve(spec, studio.dev)
    assert studio.dashboards.validate(spec, studio.dev).passed
    with pytest.raises(ValueError, match="no section appendix"):
        studio.dashboards.fill_section(spec, template, "appendix", "regions")


# ── the catalogue: what there is, where it is used, what a change would touch ───────────────────────────────
def test_the_library_shows_each_person_what_they_may_read(studio):
    studio.library.save(studio.dev, "chart", dict(RANKING), "r1")
    _project(studio, "north", _spec(KPI, TREND, {**BAR, "asset": "qoe-by-region"}), status="READY_FOR_REVIEW")
    cards = studio.catalog.cards(studio.dev)
    kinds = {c["kind"] for c in cards}
    assert kinds == {"dataset", "metric", "dimension", "chart", "dashboard", "template", "chart_template"}
    mart = next(c for c in cards if c["id"] == "cem.qoe_daily")
    assert (mart["status"], mart["domain_name"], mart["scope"], mart["used_by"], mart["facts"]["refresh"]) == (
        "certified", "Quality of Experience", "shared", {"dashboards": 1, "charts": 3}, "Daily")
    assert {(c["id"], c["scope"]) for c in cards if c["kind"] == "chart"} == {
        ("qoe-by-region", "shared"), ("north:kpi_1", "local"), ("north:trend_1", "local")}
    guest = studio.catalog.cards(studio.policy.identify("guest.user"))
    assert not [c for c in guest if c["kind"] in ("dataset", "metric", "dimension", "chart")]
    tree = studio.catalog.tree(studio.dev, "north")
    assert [f["name"] for f in tree["domains"]] == ["Quality of Experience", "Audience", "Geography"]
    studio.store.set_favorite("dev.user", "metric", "cem.qoe_score", True)
    assert [c["id"] for c in studio.catalog.tree(studio.dev)["favorites"]] == ["cem.qoe_score"]
    with pytest.raises(AssetError):
        studio.catalog.detail(studio.policy.identify("guest.user"), "dataset", "cem.qoe_daily")


def test_search_finds_every_kind_and_filters_five_ways(studio):
    studio.library.save(studio.dev, "chart", dict(RANKING), "r1")
    _project(studio, "north", _spec(KPI, TREND, {**BAR, "asset": "qoe-by-region"}, pid="north"), status="PROD")
    found = studio.catalog.search(studio.dev, "qoe")
    assert list(found["groups"]) == ["dataset", "metric", "chart"] and found["groups"]["metric"][0]["id"] == "cem.qoe_score"
    assert [c["id"] for c in studio.catalog.search(studio.dev, "technology")["groups"]["dimension"]] == ["rat"]   # a synonym
    assert set(studio.catalog.search(studio.dev, "", kind="metric", domain="audience")["groups"]["metric"][0]["id"].split()) == {"cem.active_users"}
    assert not studio.catalog.search(studio.dev, "", status="not_certified", kind="metric")["total"]
    used = studio.catalog.search(studio.dev, "", used_by="north")["groups"]
    assert [c["id"] for c in used["dataset"]] == ["cem.qoe_daily"] and [c["id"] for c in used["metric"]] == ["cem.qoe_score"]
    assert {c["id"] for c in used["chart"]} == {"qoe-by-region", "north:kpi_1", "north:trend_1"} and [c["id"] for c in used["template"]] == ["executive"]
    assert [c["id"] for c in studio.catalog.search(studio.dev, "", owner="cem", kind="dataset", scope="shared")["groups"]["dataset"]][0] == "cem.qoe_daily"


def test_impact_says_what_a_change_would_touch_and_whether_it_is_in_production(studio):
    _project(studio, "north", _spec(KPI, TREND, BAR, filters=["region"]), status="PROD")
    _project(studio, "south", _spec(KPI, TREND), status="UAT")
    _project(studio, "west", None, draft=_spec(KPI, TREND, template="operational"))
    dataset = studio.catalog.impact("dataset", "cem.qoe_daily", "default")
    assert dataset["counts"]["dashboards"] == 3 and dataset["counts"]["charts"] == 7 and dataset["counts"]["metrics"] == 4
    assert (dataset["severity"], dataset["production"], dataset["held"]) == ("high", ["north"], ["north", "south"])
    assert "may affect PRODUCTION assets: north" in dataset["message"]
    assert {d["id"]: d["draft_only"] for d in dataset["used_by"]["dashboards"]} == {"north": False, "south": False, "west": True}
    assert studio.catalog.impact("dimension", "region", "default")["counts"]["charts"] == 3      # the bar, and the filter on all three
    assert studio.catalog.impact("template", "operational", "default")["counts"]["dashboards"] == 1
    unused = studio.catalog.impact("metric", "cem.degraded_rate", "default")
    assert (unused["total"], unused["severity"], unused["message"]) == (0, "none", "It is not used by anything.")


def test_governance_is_a_checklist_a_developer_can_read(studio):
    studio.library.save(studio.dev, "chart", dict(RANKING), "r1")
    template = studio.registry.template("executive")
    spec = _spec(KPI, TREND, BAR, sections=[SectionSpec(id="headline")])
    items = {i["check"]: i for i in studio.catalog.governance(spec, template, studio.dev, {"passed": True, "checks": 200, "warnings": 0})}
    assert [i for i in items if items[i]["status"] == "pass"] == [
        "Datasets certified", "Metrics certified", "No broken dependencies", "Security configured", "Dataset refresh SLA",
        "Shared assets certified", "Validation"]
    assert items["Owner defined"]["detail"] == "the dashboard has no owner"
    assert "3 chart(s) have no description of their own" in items["Description complete"]["detail"]
    assert "ranking_1 is the same chart as shared « QoE Score by region »" in items["No duplicated asset"]["detail"]
    broken = _spec(KPI, TREND, {**BAR, "asset": "gone"})
    assert studio.catalog.governance(broken, template, studio.dev)[4] == {
        "check": "No broken dependencies", "status": "fail", "count": 1,
        "detail": "ranking_1 names shared chart gone, which does not exist"}


def test_the_outline_and_the_definition_are_the_dashboard_as_a_composition(studio):
    studio.library.save(studio.dev, "chart", dict(RANKING), "r1")
    template = studio.registry.template("executive")
    spec = _spec(KPI, TREND, {**BAR, "asset": "qoe-by-region"}, filters=["date"], sections=[SectionSpec(id="breakdown", owner="CEM")])
    _project(studio, "north", spec, status="READY_FOR_REVIEW")
    sections = studio.dashboards.sections(spec, template)
    outline = studio.catalog.outline(spec, template, sections, {"kpi_1": 2})
    assert [(s["title"], [c["title"] for c in s["charts"]]) for s in outline["sections"]] == [
        ("Executive KPIs", ["QoE Score"]), ("Trend and distribution", ["QoE Score, daily trend"]), ("Where it stands", ["QoE Score by region"])]
    assert outline["sections"][0]["charts"][0]["problems"] == 2 and outline["sections"][2]["charts"][0]["scope"] == "shared"
    assert outline["sections"][0]["empty_slots"] == ["kpi_2", "kpi_3", "kpi_4"] and outline["counts"] == {"sections": 3, "charts": 3}
    definition = studio.catalog.definition("north", studio.dev, sections, spec, template)
    assert list(definition) == ["metadata", "sections", "shared_assets", "visual_assets", "security", "deployment"]
    assert definition["metadata"]["status"] == "READY_FOR_REVIEW" and definition["shared_assets"]["charts"] == ["qoe-by-region"]
    assert [c["slot"] for c in definition["visual_assets"]["kpi_cards"]] == ["kpi_1"]
    assert "cem_analyst" in definition["security"]["audience"]


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


def test_the_engine_has_the_same_library_a_person_has(studio, tmp_path):
    from aibi.tools import Toolbox, ToolContext, ToolError

    tools = Toolbox(registry=studio.registry, policy=studio.policy, gateway=None, dbt=None, git=None, superset=None,
                    store=studio.store, audit=AuditLog(tmp_path / "tool-audit"), visual=None, state_dir=tmp_path,
                    bi_database_name="BI", bi_database_uri="", superset_public_url="", dbt_dev_schema="bi_dev")
    ctx = ToolContext(identity=studio.dev, request_id="r", ai_model="none")
    with pytest.raises(ToolError, match="no asset catalogue"):
        tools.search_assets(ctx, query="qoe")
    tools.catalog = studio.catalog
    studio.library.save(studio.dev, "chart", dict(RANKING), "r1")
    _project(studio, "north", _spec(KPI, TREND, {**BAR, "asset": "qoe-by-region"}), status="PROD")
    assert [c["id"] for c in tools.search_assets(ctx, query="region", kind="chart")["groups"]["chart"]] == ["qoe-by-region"]
    usage = tools.get_asset_usage(ctx, kind="chart", asset_id="qoe-by-region")
    assert (usage["severity"], usage["production"]) == ("high", ["north"])
    guest = ToolContext(identity=studio.policy.identify("guest.user"), request_id="r", ai_model="none")
    with pytest.raises(AssetError):
        tools.get_asset_usage(guest, kind="chart", asset_id="qoe-by-region")
    assert [r["action"] for r in tools.audit.read()][-3:] == ["tool:search_assets", "tool:get_asset_usage", "tool:get_asset_usage"]


# ── a chart edited in the engine's own editor, read back ────────────────────────────────────────────────────
def _as_superset_holds(registry, component, **edited):
    """The chart as the engine stores it for this component — then as a person left it in Explore."""
    import json

    from aibi.chart_agent import ChartAgent
    from aibi.models import ComponentSpec

    agent, template = ChartAgent(registry), registry.template("executive")
    spec = ComponentSpec(**component)
    payload = agent.payload(spec, registry.dataset(spec.dataset), registry.metric(spec.metric), 5, 9, template,
                            "inwi", "superset_seq_1", "p")
    name = edited.pop("slice_name", payload["slice_name"])
    return agent, spec, {"params": json.dumps({**json.loads(payload["params"]), **edited}), "slice_name": name}


def test_a_chart_nobody_touched_reads_back_as_itself(registry):
    for component in (KPI, TREND, BAR, {"slot": "distribution_1", "type": "histogram", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"},
                      {"slot": "distribution_1", "type": "pie", "metric": "cem.active_users", "dimension": "rat", "dataset": "cem.qoe_daily"},
                      {**BAR, "title": "QoE Score by region", "asset": "qoe-by-region"}):          # a shared chart: its name is its title
        agent, spec, chart = _as_superset_holds(registry, component)
        read = agent.read_back(spec, chart, registry.template("executive").slots[spec.slot].purpose, {5: "cem.qoe_daily"})
        assert (read["changed"], read["refused"]) == ({}, []), (component["slot"], read)
        assert read["component"] == spec


def test_what_was_changed_in_explore_is_said_in_the_specifications_words(registry):
    agent, spec, chart = _as_superset_holds(registry, {**BAR, "asset": "qoe-by-region", "title": "QoE Score by region"},
                                            row_limit=5, order_desc=False, slice_name="Bottom 5 regions")
    read = agent.read_back(spec, chart, "ranking", {5: "cem.qoe_daily"})
    assert read["changed"] == {"row_limit": {"specification": None, "superset": 5}, "sort": {"specification": None, "superset": "asc"},
                               "title": {"specification": "QoE Score by region", "superset": "Bottom 5 regions"}}
    adopted = read["component"]
    assert (adopted.options.row_limit, adopted.options.sort, adopted.title) == (5, "asc", "Bottom 5 regions")
    assert adopted.asset is None                       # changed: it is no longer the shared chart it referenced
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


def test_superset_is_compared_with_the_specification_chart_by_chart(studio, tmp_path):
    import json

    from aibi.tools import Toolbox

    holds = {}

    class _Engine:
        def get_chart(self, chart_id):
            return holds.get(chart_id)

    tools = Toolbox(registry=studio.registry, policy=studio.policy, gateway=None, dbt=None, git=None, superset=_Engine(),
                    store=studio.store, audit=AuditLog(tmp_path / "t"), visual=None, state_dir=tmp_path, bi_database_name="BI",
                    bi_database_uri="", superset_public_url="", dbt_dev_schema="bi_dev")
    spec = _spec(KPI, TREND, BAR)
    for chart_id, component in zip((1, 2, 3), (KPI, TREND, BAR)):
        _, _, holds[chart_id] = _as_superset_holds(studio.registry, component)
    body = {"spec": spec.model_dump(), "engine": {"datasets": {"cem.qoe_daily": 5},
                                                  "charts": {"kpi_1": {"id": 1}, "trend_1": {"id": 2}, "ranking_1": {"id": 3}}}}
    assert all(c["in_step"] for c in tools.superset_changes(body))
    form = json.loads(holds[3]["params"])
    holds[3] = {"params": json.dumps({**form, "row_limit": 7, "color_scheme": "supersetColors", "show_legend": True}), "slice_name": holds[3]["slice_name"]}
    del holds[2]                                                                 # deleted by hand in Superset
    found = {c["slot"]: c for c in tools.superset_changes(body)}
    assert found["kpi_1"]["in_step"]
    assert found["trend_1"]["refused"] == ["the chart no longer exists in Superset"]
    ranking = found["ranking_1"]
    assert ranking["changed"] == {"row_limit": {"specification": None, "superset": 7}}
    assert ranking["not_carried"] == ["color_scheme", "show_legend"]             # a specification has no place for them
    assert ranking["adopted"]["options"]["row_limit"] == 7 and not ranking["in_step"]
