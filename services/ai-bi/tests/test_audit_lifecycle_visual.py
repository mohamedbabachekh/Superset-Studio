from __future__ import annotations

import io
import json
import zipfile

import yaml

from aibi.audit import AuditLog
from aibi.lifecycle import asset_version, canonical_assets
from aibi.models import DashboardSpec
from aibi.visual_qa_agent import Render, VisualQaAgent


# ── audit ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_no_secret_and_no_row_reaches_the_audit_log(tmp_path):
    log = AuditLog(tmp_path, secrets=["s3cr3t-reader-password"])
    log.record(action="tool:execute_readonly_sql", user="dev.user", request_id="r1",
               sql="select 1 -- connect with s3cr3t-reader-password",
               detail={"password": "hunter2", "api_token": "abc", "dsn": "host=x password=y",
                       "rows": [["Oriental", 3.4], ["Tadla", 3.1]], "row_count": 2,
                       "nested": {"authorization": "Bearer zzz", "note": "uses s3cr3t-reader-password"}})
    text = log.path.read_text(encoding="utf-8")
    for leaked in ("s3cr3t-reader-password", "hunter2", "Bearer zzz", "Oriental", "host=x"):
        assert leaked not in text
    record = log.read()[0]
    assert record["detail"]["rows"] == "[2 rows withheld]" and record["detail"]["row_count"] == 2
    assert record["detail"]["password"] == "[redacted]"


def test_the_audit_chain_notices_an_edit_and_a_deletion(tmp_path):
    log = AuditLog(tmp_path)
    for i in range(5):
        log.record(action=f"a{i}", user="dev.user", request_id="r1")
    assert log.verify() == {"ok": True, "records": 5}

    lines = log.path.read_text(encoding="utf-8").splitlines()
    edited = json.loads(lines[2])
    edited["user"] = "someone.else"
    log.path.write_text("\n".join([*lines[:2], json.dumps(edited), *lines[3:]]) + "\n", encoding="utf-8")
    assert log.verify() == {"ok": False, "records": 2, "broken_at_seq": 3}

    log.path.write_text("\n".join([*lines[:2], *lines[3:]]) + "\n", encoding="utf-8")
    assert log.verify()["broken_at_seq"] == 4


def test_the_chain_continues_across_a_restart(tmp_path):
    AuditLog(tmp_path).record(action="first", user="u", request_id="r")
    second = AuditLog(tmp_path)
    second.record(action="second", user="u", request_id="r")
    assert second.verify() == {"ok": True, "records": 2}
    assert [r["seq"] for r in second.read()] == [1, 2]


# ── the fingerprint of what was validated ───────────────────────────────────────────────────────────────────
def _export(folder: str, title: str = "CEM QoE Overview", expression=None, **marks) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{folder}/metadata.yaml", yaml.safe_dump({"timestamp": folder}))
        archive.writestr(f"{folder}/dashboards/cem.yaml", yaml.safe_dump({"dashboard_title": title, "slug": "cem", **marks}))
        archive.writestr(f"{folder}/charts/kpi.yaml", yaml.safe_dump({"slice_name": "QoE", "viz_type": "big_number_total"}))
        archive.writestr(f"{folder}/datasets/bi/mart.yaml", yaml.safe_dump(
            {"table_name": "mart", "columns": [{"column_name": "date", "expression": expression, "extra": None}]}))
    return buffer.getvalue()


def test_the_fingerprint_does_not_care_how_the_engine_spells_empty():
    """Refreshing a dataset turned every column's `expression: null` into `''`: a restored, identical
    dashboard was refused as « changed after validation »."""
    validated = asset_version(_export("dashboard_export_1", expression=None))
    assert validated == asset_version(_export("dashboard_export_2", expression=""))
    assert validated != asset_version(_export("dashboard_export_2", expression="date::date"))


def _real_shape(chart_id: int, uuid: str, dataset_uuid: str, scope=None, name: str = "QoE Score") -> bytes:
    """An export shaped like the engine's own (read from a real one): ids in file names, in the layout, in
    the filters' scopes, in each chart's parameters."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        root = f"dashboard_export_{chart_id}"
        archive.writestr(f"{root}/metadata.yaml", yaml.safe_dump({"version": "1.0.0"}))
        archive.writestr(f"{root}/tags.yaml", yaml.safe_dump({"tags": []}))
        archive.writestr(f"{root}/dashboards/CEM_QoE_Overview_7.yaml", yaml.safe_dump({
            "dashboard_title": "CEM QoE Overview", "slug": "cem-qoe-overview", "uuid": "dash-" + uuid,
            "position": {"CHART-kpi_1": {"type": "CHART", "id": "CHART-kpi_1",
                                         "meta": {"chartId": chart_id, "uuid": uuid, "width": 4, "sliceName": name}}},
            "metadata": {"shared_label_colors": [], "native_filter_configuration": [{
                "id": "NATIVE_FILTER-cem-qoe-overview-region", "name": "Region",
                "targets": [{"column": {"name": "region"}, "datasetUuid": dataset_uuid}],
                "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
                "chartsInScope": [chart_id] if scope is None else scope}]}}))
        archive.writestr(f"{root}/charts/QoE_Score_{chart_id}.yaml", yaml.safe_dump({
            "slice_name": name, "viz_type": "big_number_total", "uuid": uuid, "dataset_uuid": dataset_uuid,
            "params": {"datasource": f"{chart_id + 100}__table", "metric": "cem__qoe_score"}}))
        archive.writestr(f"{root}/datasets/BI/mart_cem_qoe_daily_{chart_id + 100}.yaml", yaml.safe_dump({
            "schema": "bi", "table_name": "mart_cem_qoe_daily", "uuid": dataset_uuid,
            "metrics": [{"metric_name": "cem__qoe_score", "expression": "SUM(a) / SUM(b)"}]}))
    return buffer.getvalue()


def test_a_chart_recreated_with_another_id_is_the_same_dashboard():
    """A restore re-creates a deleted chart: new id, new file name in the export. Compared byte for byte,
    the identical dashboard was refused as changed."""
    validated = asset_version(_real_shape(218, "uuid-a", "ds-1"))
    assert validated == asset_version(_real_shape(301, "uuid-b", "ds-2"))


def test_what_a_person_changes_still_changes_the_fingerprint():
    validated = asset_version(_real_shape(218, "uuid-a", "ds-1"))
    assert validated != asset_version(_real_shape(218, "uuid-a", "ds-1", name="QoE Score (edited by hand)"))
    assert validated != asset_version(_real_shape(218, "uuid-a", "ds-1", scope=[]))      # a filter taken off a chart
    told = canonical_assets(_real_shape(218, "uuid-a", "ds-1"))
    flt = told["dashboard cem-qoe-overview"]["metadata"]["native_filter_configuration"][0]
    assert flt["chartsInScope"] == ["CHART-kpi_1"] and flt["targets"][0]["dataset"] == "bi.mart_cem_qoe_daily"
    assert told["chart CHART-kpi_1"]["dataset"] == "bi.mart_cem_qoe_daily"
    assert "218" not in str(told) and "uuid-a" not in str(told)


def test_the_sign_in_token_is_read_from_the_form_as_the_engine_writes_it():
    """The attribute sits on its own line in 6.1's page; the browser no longer visits that page at all."""
    html = '<form>\n  <input type="hidden"\n      name="csrf_token"\n      id="csrf_token"\n      value="IjVm.as-Ef_9"\n  />'
    assert VisualQaAgent._CSRF.search(html).group(1) == "IjVm.as-Ef_9"
    assert VisualQaAgent._CSRF.search("<form><input name='username'></form>") is None


def test_the_fingerprint_follows_content_not_export_time_or_promotion_marks():
    validated = asset_version(_export("dashboard_export_20261003T101010"))
    assert validated == asset_version(_export("dashboard_export_20261004T090000"))
    assert validated == asset_version(_export("dashboard_export_20261004T090000", published=True,
                                              certified_by="release.manager", is_managed_externally=True))
    assert validated != asset_version(_export("dashboard_export_20261004T090000", title="Edited by hand"))


# ── visual QA ───────────────────────────────────────────────────────────────────────────────────────────────
POLICIES = {"visual": {"blank_ratio": 0.995, "min_px_per_category": 16}}


def _spec() -> DashboardSpec:
    return DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"}, components=[
        {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"},
        {"slot": "kpi_2", "type": "big_number", "metric": "cem.active_users", "dataset": "cem.qoe_daily"},
        {"slot": "trend_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date", "dataset": "cem.qoe_daily"},
        {"slot": "ranking_1", "type": "bar", "metric": "cem.qoe_score", "dimension": "region", "dataset": "cem.qoe_daily"}])


CHARTS = {"kpi_1": {"id": 1}, "kpi_2": {"id": 2}, "trend_1": {"id": 3}, "ranking_1": {"id": 4}}


def _component(chart_id, x, y, w, h, **extra):
    return {"chart_id": chart_id, "rect": {"x": x, "y": y, "w": w, "h": h}, "clipped": [], "error": None,
            "loading": False, "blank_ratio": 0.9, **extra}


def _render(components) -> Render:
    return Render(viewport="desktop", width=1440, screenshot="dashboard-desktop.png", load_ms=4000,
                  components=components)


def _data(rows=16):
    return {"kpi_1": {"rowcount": 1, "data": []}, "kpi_2": {"rowcount": 1, "data": []},
            "trend_1": {"rowcount": 90, "data": []},
            "ranking_1": {"rowcount": rows, "data": [{"region": f"Region {i}"} for i in range(rows)]}}


def _good():
    return [_component(1, 0, 100, 700, 128), _component(2, 720, 100, 700, 128),
            _component(3, 0, 240, 1420, 352), _component(4, 0, 600, 700, 416)]


def test_a_clean_render_passes(registry):
    agent = VisualQaAgent("http://x", "u", "p", POLICIES)
    report = agent.check(_render(_good()), _spec(), registry.template("executive"), CHARTS, _data())
    assert report.passed, [f"{c.name}: {c.subject} {c.detail}" for c in report.failures]


def test_overlap_misalignment_and_emptiness_are_seen(registry):
    agent = VisualQaAgent("http://x", "u", "p", POLICIES)
    template = registry.template("executive")
    bad = _good()
    bad[1] = _component(2, 650, 108, 700, 150)                       # over kpi_1, lower, taller
    bad[2] = _component(3, 0, 240, 1420, 352, blank_ratio=0.999)     # mandatory, and drew nothing
    failed = {c.name: c for c in agent.check(_render(bad), _spec(), template, CHARTS, _data()).failures}
    assert "kpi_1 × kpi_2" in failed["no component overlaps another"].detail
    assert "cards do not start on one line" in failed["layout follows the template"].detail
    assert failed["component is not empty"].slot == "trend_1"


def test_a_missing_chart_and_a_chart_in_error_are_failures(registry):
    agent = VisualQaAgent("http://x", "u", "p", POLICIES)
    components = _good()[:3] + [_component(4, 0, 600, 700, 416, error="Unexpected error")]
    report = agent.check(_render(components[1:]), _spec(), registry.template("executive"), CHARTS, _data())
    failed = {c.name: c.detail for c in report.failures}
    assert failed["every component is on the page"] == "kpi_1"
    assert "Unexpected error" in failed["component rendered"]


def test_a_render_that_failed_is_a_finding_not_a_pass(registry):
    agent = VisualQaAgent("http://x", "u", "p", POLICIES)
    render = Render(viewport="desktop", width=1440, screenshot=None, load_ms=0, error="TimeoutError: 90000 ms")
    report = agent.check(render, _spec(), registry.template("executive"), CHARTS, _data())
    assert [c.code for c in report.failures] == ["render_failed"]


def test_crowded_labels_are_fixed_by_showing_what_fits(registry):
    agent = VisualQaAgent("http://x", "u", "p", POLICIES)
    template, spec = registry.template("executive"), _spec()
    report = agent.check(_render(_good()), spec, template, CHARTS, _data(rows=60))
    crowded = next(c for c in report.failures if c.code == "labels_too_dense")
    assert crowded.slot == "ranking_1"
    fixes = agent.propose_fixes(report, spec, template, {})
    assert [(f.slot, f.code) for f in fixes] == [("ranking_1", "labels_too_dense")]
    fixes[0].apply(spec)
    assert spec.component("ranking_1").options.row_limit == crowded.evidence["fits"] == 19
    # the same finding is not « fixed » twice: once the option is set, the engine has nothing more to try
    assert agent.propose_fixes(report, spec, template, {}) == []


def test_text_too_small_to_read_fails_and_a_clipped_dashboard_title_warns(registry):
    """Both seen on a real render of the operational template: a KPI caption at 8 px, and « Operatio… »."""
    agent = VisualQaAgent("http://x", "u", "p", {"visual": {**POLICIES["visual"], "min_font_px": 9}})
    template = registry.template("executive")
    components = _good()
    components[0]["smallest_text"] = {"px": 8, "text": "score, 1 to 5"}
    components[1]["smallest_text"] = {"px": 10, "text": "users per day"}
    render = _render(components)
    render.header = {"text": "CEM QoE regional Operations", "needs": 284, "has": 281}
    report = agent.check(render, _spec(), template, CHARTS, _data())
    small = [c for c in report.failures if c.code == "text_too_small"]
    assert [(c.slot, c.detail) for c in small] == [("kpi_1", "« score, 1 to 5 » is drawn at 8 px, under 9")]
    assert [c.code for c in report.warnings] == ["dashboard_title_clipped"]
    assert "needs 284 px" in report.warnings[0].detail

    render.header = {"text": "CEM QoE Overview", "needs": 187, "has": 187}
    components[0]["smallest_text"] = {"px": 10, "text": "score, 1 to 5"}
    assert agent.check(render, _spec(), template, CHARTS, _data()).warnings == []


def test_a_clipped_title_is_shortened_from_the_registry(registry):
    agent = VisualQaAgent("http://x", "u", "p", POLICIES)
    template, spec = registry.template("executive"), _spec()
    components = _good()
    components[3]["clipped"] = [{"text": "QoE Score by region", "needs": 400, "has": 200, "in_title": True}]
    report = agent.check(_render(components), spec, template, CHARTS, _data())
    fixes = agent.propose_fixes(report, spec, template, {"cem.qoe_score": "QoE"})
    fixes[0].apply(spec)
    assert spec.component("ranking_1").title == "QoE by region"
