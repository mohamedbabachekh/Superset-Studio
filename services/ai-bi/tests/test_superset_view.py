"""The engine's records, shown by Superset: what is copied, who may open it, and the gate as a tag."""
import io
import zipfile

import yaml

from aibi.audit import AuditLog
from aibi.lifecycle import asset_version
from aibi.store import Store
from aibi.superset_view import DASHBOARDS, GATE_TAG, SYSTEM_TAG, TABLES, VERSION_TAG, SupersetView


class _Superset:
    """What the view asks of the BI engine, and what it would change there."""
    def __init__(self, dashboards=None, tags=None, charts=None):
        self.dashboards = dashboards or {}          # id or slug → dashboard
        self.tags = tags or {}                      # id → [tag]
        self.charts = charts or {}                  # id → number of charts
        self.calls: list[tuple] = []

    def get_dashboard(self, key):
        return self.dashboards.get(key)

    def dashboard_tags(self, dashboard_id):
        return list(self.tags.get(dashboard_id, []))

    def dashboard_charts(self, dashboard_id):
        return [{"id": n} for n in range(self.charts.get(dashboard_id, 0))]

    def tag_dashboard(self, dashboard_id, names):
        self.calls.append(("tag", dashboard_id, names))
        self.tags.setdefault(dashboard_id, []).extend(names)

    def untag_dashboard(self, dashboard_id, name):
        self.calls.append(("untag", dashboard_id, name))
        self.tags[dashboard_id].remove(name)

    def tag(self, kind, object_id, names):
        self.calls.append((kind, object_id, names))


def _view(registry, tmp_path, superset) -> SupersetView:
    store = Store(tmp_path / "store.db")
    store.save_project("cem-radio", {"project_id": "cem-radio", "tenant": "default", "title": "CEM Radio",
                                     "template": "operational", "created_by": "dev.user",
                                     "attempt": {"status": "VALIDATION_FAILED", "error": "2 checks failed"},
                                     "engine": {"dashboard": {"id": 7, "url": "http://bi/superset/dashboard/cem-radio/"},
                                                "charts": {"kpi_1": {"id": 11, "name": "QoE Score"}, "gone": {"id": None}},
                                                "datasets": {"cem.qoe_daily": 21}}},
                       "2026-10-03T10:00:00+00:00")
    audit = AuditLog(tmp_path / "audit")
    audit.record(action="tool:validate_dashboard", user="dev.user", request_id="r1", project_id="cem-radio",
                 outcome="error", detail={"error": "2 checks failed"})
    audit.record(action="approval:UAT", user="guest.user", request_id="r2", outcome="refused",
                 detail={"reason": "guest.user may not do this"})
    return SupersetView(registry, store, audit, superset, tmp_path, "dsn-not-used", ("records", "uri-not-used"))


def test_the_copy_says_where_a_project_stands_and_whose_it_is(registry, tmp_path):
    rows = _view(registry, tmp_path, _Superset()).records()
    assert set(rows) == set(TABLES)
    project = dict(zip([name for name, _ in TABLES["projects"]], rows["projects"][0]))
    # no version yet: the gate is where its only attempt stopped
    assert (project["tenant"], project["project_id"], project["gate"], project["environment"]) == \
        ("default", "cem-radio", "VALIDATION_FAILED", "DEV")
    assert project["version"] is None and project["last_error"] == "2 checks failed"
    trail = [dict(zip([name for name, _ in TABLES["audit"]], row)) for row in rows["audit"]]
    assert [(r["seq"], r["action"], r["outcome"], r["note"]) for r in trail] == [
        (1, "tool:validate_dashboard", "error", "2 checks failed"),
        (2, "approval:UAT", "refused", "guest.user may not do this")]
    # a record of a project is its project's tenant's; one without a project, its user's
    assert [r["tenant"] for r in trail] == ["default", "default"]
    assert rows["published"][0][1:] == ("intact", 2, 1)
    # every row of every table has exactly the columns the table is created with
    assert all(len(row) == len(TABLES[table]) for table, found in rows.items() for row in found)


def test_each_dashboard_over_the_records_has_the_audience_its_capability_names(registry, tmp_path):
    view = _view(registry, tmp_path, _Superset())
    assert view.audience("sharpsight-bi-factory") == ["sharpsight_bi_developer", "sharpsight_bi_release_manager",
                                                      "sharpsight_bi_reviewer"]
    # the API asks audit.read for the trail: so does the dashboard over its copy. A developer is not in it.
    assert view.audience("sharpsight-audit-trail") == ["sharpsight_auditor"]
    # in the order of the menu, and every other one is the factory's
    assert list(DASHBOARDS) == ["sharpsight-bi-factory", "sharpsight-data-quality", "sharpsight-impact-analysis",
                                "sharpsight-releases", "sharpsight-audit-trail"]
    assert all(view.audience(slug) == view.audience("sharpsight-bi-factory")
               for slug in DASHBOARDS if slug != "sharpsight-audit-trail")
    # every chart is drawn from a table the engine writes, and from columns that table has
    for slug, view_ in DASHBOARDS.items():
        for row in (r for r in view_["rows"] if not isinstance(r, str)):
            for key, table, title, width, height, form in row:
                columns = {name for name, _ in TABLES[table]}
                assert set(form.get("all_columns", [])) | set(form.get("groupby", [])) <= columns, (slug, key)
            assert sum(chart[3] for chart in row) == 12, (slug, "a row is twelve columns wide")


def test_the_registries_and_dbt_are_rows_too(registry, tmp_path):
    view = _view(registry, tmp_path, _Superset())
    view.store.save_lineage([{"environment": "prod", "unique_id": "model.x.mart_cem_qoe_daily", "name": "mart_cem_qoe_daily",
                              "resource_type": "model", "path": "models/marts/cem/mart_cem_qoe_daily.sql",
                              "materialized": "table", "relation": '"devkit"."bi"."mart_cem_qoe_daily"', "tags": [],
                              "depends_on": [{"name": "int_cem__qoe_site_daily"}],
                              "tests": [{"name": "not_null_date", "status": "pass"}, {"name": "unique_grain", "status": "fail"}]}])
    rows = view.records()
    datasets = {row[1]: dict(zip([name for name, _ in TABLES["datasets"]], row)) for row in rows["datasets"]}
    daily = datasets["cem.qoe_daily"]
    assert (daily["certified"], daily["domain"], daily["dbt_model"]) == ("yes", "cem", "mart_cem_qoe_daily")
    assert (daily["tests"], daily["tests_passed"], daily["tests_not_passed"]) == (2, 1, 1)
    model = dict(zip([name for name, _ in TABLES["dbt_models"]], rows["dbt_models"][0]))
    assert (model["layer"], model["relation"], model["built_from"], model["feeds_dataset"]) == \
        ("marts", "devkit.bi.mart_cem_qoe_daily", "int_cem__qoe_site_daily", "cem.qoe_daily")
    assert sorted(row[2:] for row in rows["dbt_tests"]) == [("not_null_date", "pass"), ("unique_grain", "fail")]
    assert {row[0] for row in rows["metrics"]} == set(registry.metrics)
    assert all(len(row) == len(TABLES[table]) for table, found in rows.items() for row in found)


def test_a_dashboard_s_tags_are_kept_exact_and_a_person_s_own_are_left_alone(registry, tmp_path):
    superset = _Superset(dashboards={7: {"id": 7}}, tags={7: ["gate:DRAFT", "radio", "env:UAT", "project:cem-radio", "domain:cem"]})
    view = _view(registry, tmp_path, superset)
    changed = view.mark_projects()
    # the gate moved and so did the environment; a domain is not said on a dashboard (it hid the gate in
    # Superset's list); the project and a person's own tag were already there
    assert sorted(superset.tags[7]) == ["env:DEV", "gate:VALIDATION_FAILED", "project:cem-radio", "radio"]
    assert [(c["project_id"], c["tag"], sorted(c["was"])) for c in changed] == [
        ("cem-radio", GATE_TAG + "VALIDATION_FAILED", ["domain:cem", "env:UAT", "gate:DRAFT", "project:cem-radio"])]
    # its charts say which project they are of, its datasets their domain and whether they are certified
    assert ("chart", 11, ["project:cem-radio", "domain:cem"]) in superset.calls
    assert ("dataset", 21, ["domain:cem", "sharpsight:certified"]) in superset.calls
    superset.calls.clear()
    assert view.mark_projects() == [] and superset.calls == []       # already right: Superset is not written to


def test_a_project_whose_dashboard_is_gone_is_passed_over(registry, tmp_path):
    superset = _Superset()                                           # dashboard 7 was deleted in the BI engine
    assert _view(registry, tmp_path, superset).mark_projects() == [] and superset.calls == []


def test_a_system_dashboard_is_made_again_when_it_is_an_older_version_s_or_lost_a_chart(registry, tmp_path):
    slug = "sharpsight-audit-trail"
    charts = sum(len(row) for row in DASHBOARDS[slug]["rows"] if not isinstance(row, str))
    in_place = _Superset(dashboards={slug: {"id": 3}}, tags={3: [SYSTEM_TAG, VERSION_TAG]}, charts={3: charts})
    assert _view(registry, tmp_path, in_place)._in_place(slug) == 3
    older = _Superset(dashboards={slug: {"id": 3}}, tags={3: [SYSTEM_TAG, "sharpsight:view-v0"]}, charts={3: charts})
    assert _view(registry, tmp_path, older)._in_place(slug) is None
    short = _Superset(dashboards={slug: {"id": 3}}, tags={3: [SYSTEM_TAG, VERSION_TAG]}, charts={3: charts - 1})
    assert _view(registry, tmp_path, short)._in_place(slug) is None
    assert _view(registry, tmp_path, _Superset())._in_place(slug) is None


def _export(tags) -> bytes:
    """A dashboard as Superset 6.1 exports it: its tags are in the dashboard's own file (measured)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        marks = {} if tags is None else {"tags": ["project:cem-qoe"]}
        dashboard = {"dashboard_title": "CEM QoE Overview", "slug": "cem-qoe-overview", "uuid": "d-1",
                     "position": {"CHART-kpi_1": {"type": "CHART", "id": "CHART-kpi_1",
                                                  "meta": {"chartId": 5, "uuid": "c-1", "width": 4}}}}
        if tags is not None:
            dashboard["tags"] = tags
        archive.writestr("export/metadata.yaml", yaml.safe_dump({"version": "1.0.0"}))
        archive.writestr("export/dashboards/CEM_QoE_Overview_1.yaml", yaml.safe_dump(dashboard))
        archive.writestr("export/charts/QoE_Score_5.yaml", yaml.safe_dump({
            "slice_name": "QoE Score", "viz_type": "big_number_total", "uuid": "c-1", "dataset_uuid": "ds-1",
            "params": {"datasource": "9__table", "metric": "cem__qoe_score"}, **marks,
            # a chart approved for PROD is certified with its dashboard: a mark, like the dashboard's own
            **({} if tags is None else {"certified_by": "release.manager (SharpSight)"})}))
        archive.writestr("export/datasets/BI/mart_cem_qoe_daily_9.yaml", yaml.safe_dump({
            "schema": "bi", "table_name": "mart_cem_qoe_daily", "uuid": "ds-1", **marks}))
    return buffer.getvalue()


def test_a_tag_is_not_a_change_made_after_validation():
    """Showing a gate tags the dashboard. Measured on Superset 6.1: the export then differs — and before this
    was left out of the fingerprint, every tagged dashboard was refused at its next gate as « changed »."""
    validated = asset_version(_export(None))
    assert validated == asset_version(_export(["gate:READY_FOR_REVIEW"]))
    assert validated == asset_version(_export(["gate:UAT", "a tag a person added"]))
