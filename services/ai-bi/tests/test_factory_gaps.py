"""The rules added for the brief's sections 18 to 32: the quality gates, a version in the specification, the
two tools, metric classification, the added checks, the audit fields, the compatibility matrix."""
from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from aibi import compatibility
from aibi.audit import AuditLog
from aibi.models import HELD, Attempt, DashboardSpec, ProjectVersion, RunRecord
from aibi.pipeline import VALIDATION_STEPS, _Gates
from aibi.planner import DeterministicParser, new_steps
from aibi.policy_engine import PolicyEngine, PolicyError
from aibi.registry import Registry, RegistryError
from aibi.sql_agent import SqlAgent
from aibi.tools import Toolbox, ToolContext, ToolError
from aibi.validation_agent import ValidationAgent
from aibi.visual_qa_agent import Render, VisualQaAgent


# ── the quality gates ───────────────────────────────────────────────────────────────────────────────────────
def test_a_version_is_in_one_of_the_ten_gates_and_remembers_the_way_there():
    record = ProjectVersion(project_id="p", version="1.0.0", created_by="dev.user", request_id="r")
    assert (record.status, record.state) == ("DRAFT", "DEV")
    for gate in ("AI_GENERATED", "VALIDATING", "READY_FOR_REVIEW", "APPROVED_FOR_UAT", "UAT", "APPROVED_FOR_PROD", "PROD"):
        record.move(gate, by="someone", note=gate.lower())
    assert [h.status for h in record.history][-3:] == ["UAT", "APPROVED_FOR_PROD", "PROD"]
    assert record.state == "PROD"
    with pytest.raises(ValidationError):
        ProjectVersion(project_id="p", version="1", created_by="x", request_id="r", status="ALMOST_READY")
    # the environment is derived from the gate, and survives being stored and read back
    again = ProjectVersion.model_validate_json(record.model_dump_json())
    assert (again.status, again.state, len(again.history)) == ("PROD", "PROD", 7)
    assert set(HELD) == {"APPROVED_FOR_UAT", "UAT", "APPROVED_FOR_PROD", "PROD"}


@pytest.mark.parametrize("state, review, status", [
    ("DEV", "READY_FOR_REVIEW", "READY_FOR_REVIEW"), ("UAT", "READY_FOR_REVIEW", "UAT"),
    ("PROD", "APPROVED", "PROD"), ("UAT", "REJECTED", "UAT_FAILED"), ("DEV", "REJECTED", "DRAFT")])
def test_a_record_stored_before_the_ten_gates_is_read_as_what_it_meant(state, review, status):
    old = {"project_id": "p", "version": "1.0.0", "created_by": "dev.user", "request_id": "r",
           "state": state, "review_status": review}
    assert ProjectVersion(**old).status == status


class _Projects:
    """A store and an audit log, in memory — enough for the gates to write on."""

    def __init__(self):
        self.projects, self.records = {}, []
        self.store, self.audit = self, self

    def get_project(self, project_id):
        return self.projects.get(project_id)

    def save_project(self, body):
        self.projects[body["project_id"]] = dict(body)

    def record(self, **fields):
        self.records.append(fields)


def _gates(policy):
    fake = _Projects()
    run = RunRecord(id="run1", request_id="req1", user="dev.user", prompt="…", ai_model="none", steps=new_steps())
    ctx = ToolContext(identity=policy.identify("dev.user"), request_id="req1", ai_model="none")
    return fake, run, _Gates(run, ctx, fake)


def test_a_failed_validation_leaves_its_record_on_the_project(policy):
    fake, run, gates = _gates(policy)
    fake.save_project({"project_id": "cem-qoe"})
    gates.open("cem-qoe")
    gates.move("AI_GENERATED", "specification cem-qoe-overview")
    gates.move("VALIDATING")
    gates.fail(validating=True, error="ValidationFailed: visual · labels are readable · ranking_1")
    attempt = Attempt(**fake.projects["cem-qoe"]["attempt"])
    assert attempt.status == run.quality_gate == "VALIDATION_FAILED"
    assert [h.status for h in attempt.history] == ["DRAFT", "AI_GENERATED", "VALIDATING", "VALIDATION_FAILED"]
    assert attempt.version is None and "labels are readable" in attempt.error
    assert attempt.history[0].by == "dev.user" and attempt.history[-1].by == "sharpsight-ai"
    assert [r["action"] for r in fake.records] == ["gate:AI_GENERATED", "gate:VALIDATING", "gate:VALIDATION_FAILED"]
    assert fake.records[-1]["outcome"] == "error"


def test_a_run_refused_before_it_had_a_project_marks_no_project(policy):
    """A regeneration refused because the project is in UAT must not overwrite that project's attempt."""
    fake, run, gates = _gates(policy)
    fake.save_project({"project_id": "cem-qoe", "attempt": {"status": "READY_FOR_REVIEW"}})
    gates.fail(validating=False, error="PipelineError: BI project cem-qoe v1.0.0 is UAT")
    assert fake.projects["cem-qoe"]["attempt"] == {"status": "READY_FOR_REVIEW"}
    assert run.quality_gate == "DRAFT" and fake.records == []


def test_what_passed_validation_and_could_not_be_stored_is_a_draft_again(policy):
    fake, run, gates = _gates(policy)
    fake.save_project({"project_id": "p"})
    gates.open("p")
    gates.move("AI_GENERATED")
    gates.move("VALIDATING")
    gates.fail(validating=False, error="GitError: the repository is locked")
    assert fake.projects["p"]["attempt"]["status"] == "DRAFT"
    assert "store_version" not in VALIDATION_STEPS and "visual_qa" in VALIDATION_STEPS


# ── the request and the specification ───────────────────────────────────────────────────────────────────────
def test_a_template_is_what_the_phrase_ends_with(registry):
    parse = DeterministicParser(registry).parse
    read = parse("Create a CEM QoE dashboard using the CEM Executive template. Use QoE Score.")
    assert read.template == "executive" and read.unresolved == []
    # the same two words the other way round name the other template: « cem » is customer-experience's alias
    assert parse("Create a QoE dashboard using the Executive CEM template. Use QoE Score.").template == "customer-experience"
    assert parse("Create a QoE dashboard using the Management template. Use QoE Score.").assumptions == []
    unknown = parse("Create a QoE dashboard using the Executive Sales template. Use QoE Score.")
    assert unknown.template is None and "« Executive Sales » is not a registered template" in unknown.unresolved[0]


BRIEF_SPEC = """
dashboard:
  id: cem-qoe-overview
  title: CEM QoE Overview
  template: executive-telecom-v1
  version: "1.0"
filters: [date, region, rat]
components:
  - {slot: kpi_1, type: big_number, metric: cem.qoe_score}
  - {slot: kpi_2, type: big_number, metric: cem.active_users}
  - {slot: trend_1, type: line, metric: cem.qoe_score, dimension: date}
  - {slot: ranking_1, type: bar, metric: cem.qoe_score, dimension: region}
  - {slot: map_1, type: map, metric: cem.qoe_score, dimension: site}
"""


def test_the_briefs_own_specification_is_accepted_and_judged(dashboards, policy):
    """Section 23's example, as written: a version in its header, the template under the brief's name, and
    no dataset on any component. It is read, its datasets are found in the registry, and what remains is
    the one thing this registry does not have — a column called `site` to draw a map by."""
    spec = DashboardSpec(**yaml.safe_load(BRIEF_SPEC))
    assert spec.dashboard.version == "1.0"
    dev = policy.identify("dev.user")
    assert dashboards.resolve(spec, dev) == ["kpi_1: cem.qoe_daily", "kpi_2: cem.qoe_daily",
                                             "trend_1: cem.qoe_daily", "ranking_1: cem.qoe_daily"]
    report = dashboards.validate(spec, dev)
    assert [(c.name, c.subject) for c in report.failures] == [("dataset is registered", "map_1 (map)")]
    assert "no dataset dev.user may read gives cem.qoe_score by site" in report.failures[0].detail
    spec.components.pop()
    assert dashboards.validate(spec, dev).passed


def test_a_version_in_the_specification_is_a_version_number():
    header = {"id": "t", "template": "executive", "title": "T"}
    for good in ("1", "1.0", "1.4.0"):
        assert DashboardSpec(dashboard={**header, "version": good}, components=[]).dashboard.version == good
    for bad in ("v1", "1.0-rc1", "latest", "1..0"):
        with pytest.raises(ValidationError):
            DashboardSpec(dashboard={**header, "version": bad}, components=[])


# ── the two tools ───────────────────────────────────────────────────────────────────────────────────────────
def _toolbox(registry, policy, tmp_path) -> Toolbox:
    return Toolbox(registry=registry, policy=policy, gateway=None, dbt=None, git=None, superset=None, store=None,
                   audit=AuditLog(tmp_path / "audit"), visual=None, state_dir=tmp_path, bi_database_name="BI",
                   bi_database_uri="", superset_public_url="", dbt_dev_schema="bi_dev")


def _ctx(policy, user="dev.user", project_id=None) -> ToolContext:
    return ToolContext(identity=policy.identify(user), request_id="req", ai_model="none", project_id=project_id)


def test_a_dimension_is_found_by_the_word_the_business_uses(registry, policy, tmp_path):
    tools = _toolbox(registry, policy, tmp_path)
    found = tools.get_dimension(_ctx(policy), dimension="regional")
    assert found["dimension"] == "region" and found["label"] == "Region"
    assert {"cem.qoe_daily", "cem.region"} <= set(found["datasets"])
    assert found["datasets"]["cem.qoe_daily"]["classification"] == "public"
    assert "cem.qoe_score" in found["metrics"]
    assert tools.get_dimension(_ctx(policy), dimension="RAT", dataset_id="cem.qoe_daily")["datasets"].keys() == {"cem.qoe_daily"}
    with pytest.raises(ToolError, match="not a dimension"):
        tools.get_dimension(_ctx(policy), dimension="qoe_score")            # a measure is not a dimension
    with pytest.raises(ToolError, match="not a dimension of any dataset guest.user may read"):
        tools.get_dimension(_ctx(policy, "guest.user"), dimension="region")
    last = tools.audit.read()[0]
    assert last["action"] == "tool:get_dimension" and "cem.qoe_score" in last["metrics"]


def test_a_package_is_asked_of_the_lifecycle_and_audited(registry, policy, tmp_path):
    tools = _toolbox(registry, policy, tmp_path)

    class _Store:
        def get_project(self, project_id):
            return {"project_id": project_id, "tenant": "default"}

    tools.store = _Store()
    with pytest.raises(ToolError, match="no packager"):
        tools.create_deployment_package(_ctx(policy, project_id="cem-qoe"))
    tools.packager = lambda project_id: {"file": f"{project_id}-1.0.0.ssbi", "sha256": "ab" * 32, "bytes": 10,
                                         "files": 3, "version": "1.0.0", "git_commit": "c0ffee"}
    assert tools.create_deployment_package(_ctx(policy, project_id="cem-qoe"))["file"] == "cem-qoe-1.0.0.ssbi"
    record = tools.audit.read()[-1]
    assert record["git_commit"] == "c0ffee" and record["deployment"]["package"]["sha256"] == "ab" * 32
    # the engine's own tools need tools.use: a reviewer may download a package, not have one built
    with pytest.raises(PolicyError):
        tools.create_deployment_package(_ctx(policy, "lead.reviewer", "cem-qoe"))


# ── metric classification ───────────────────────────────────────────────────────────────────────────────────
def _reclassify(registry_copy, metric: str, classification: str) -> None:
    path = registry_copy / "metrics" / f"{metric}.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if classification is None:
        del document["metric"]["classification"]
    else:
        document["metric"]["classification"] = classification
    path.write_text(yaml.safe_dump(document), encoding="utf-8")


def test_an_unclassified_metric_stops_the_load(registry_copy):
    _reclassify(registry_copy, "cem.qoe_score", None)
    with pytest.raises(RegistryError, match="classification"):
        Registry(registry_copy)


def test_a_metric_above_the_requesters_clearance_is_not_theirs_to_use(registry_copy, tmp_path):
    from aibi.dashboard_agent import DashboardAgent
    from aibi.semantic_agent import SemanticAgent

    _reclassify(registry_copy, "cem.degraded_users", "confidential")
    rbac = yaml.safe_load((registry_copy / "security" / "rbac.yaml").read_text(encoding="utf-8"))
    rbac["roles"]["cem_director"] = {"capabilities": ["bi.read"], "datasets": ["cem.*"],
                                     "classifications": ["public", "internal", "confidential"]}
    (registry_copy / "security" / "rbac.yaml").write_text(yaml.safe_dump(rbac), encoding="utf-8")
    registry = Registry(registry_copy)
    policy = PolicyEngine(registry)
    dev = policy.identify("dev.user")
    tools = _toolbox(registry, policy, tmp_path)

    assert "cem.degraded_users" not in [m["id"] for m in tools.list_metrics(_ctx(policy))]
    assert tools.get_metric(_ctx(policy), metric_id="cem.qoe_score")["classification"] == "internal"
    with pytest.raises(PolicyError, match="no access to metric cem.degraded_users"):
        tools.get_metric(_ctx(policy), metric_id="cem.degraded_users")

    spec = DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"}, components=[
        {"slot": "kpi_1", "type": "big_number", "metric": "cem.degraded_users", "dataset": "cem.qoe_daily"},
        {"slot": "trend_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date", "dataset": "cem.qoe_daily"}])
    report = DashboardAgent(registry, policy, SemanticAgent(registry)).validate(spec, dev)
    assert [(c.name, c.code) for c in report.failures] == [("requester is cleared for the metric", "unauthorized_metric")]

    # and the dashboard is for the roles cleared for every metric on it — here, one
    mart = [registry.dataset("cem.qoe_daily")]
    assert "cem_analyst" in policy.audience(mart, [registry.metric("cem.qoe_score")])
    assert policy.audience(mart, [registry.metric("cem.qoe_score"), registry.metric("cem.degraded_users")]) == ["cem_director"]
    assert policy.row_rules(mart, [registry.metric("cem.degraded_users")]) == []


def test_a_metric_says_everything_the_brief_asks_of_it(semantic, registry):
    described = semantic.describe(registry.metric("cem.qoe_score"))
    assert {"name", "expression", "aggregation", "dimensions", "format", "owner", "documentation", "certified",
            "version", "lineage", "classification"} <= set(described)


# ── the added checks ────────────────────────────────────────────────────────────────────────────────────────
class _Answer:
    def __init__(self, rows, columns=()):
        self._rows, self.columns = rows, list(columns)

    def records(self):
        return self._rows

    def scalar(self):
        return next(iter(self._rows[0].values())) if self._rows else None


class _NoLineage:
    def lineage_for(self, model):
        return None


def _data_checks(registry, policy, nulls):
    dataset = registry.dataset("cem.qoe_daily")

    def run_sql(sql, purpose):
        if purpose.startswith("schema"):
            return _Answer([], dataset.columns)
        if purpose.startswith("row count"):
            return _Answer([{"row_count": 1000}])
        if purpose.startswith("null rate"):
            return _Answer([{name: nulls.get(name, 0) for name in dataset.columns}])
        if purpose.startswith("freshness"):
            return _Answer([{"age": 5}])
        if purpose.startswith("ranges"):
            return _Answer([{}])
        return _Answer([{"value": 0}])

    report = ValidationAgent(registry, policy, SqlAgent(), _NoLineage()).data([dataset], {}, run_sql)
    return {c.name: c for c in report.checks if c.subject.startswith("cem.qoe_daily")}


def test_the_null_rate_is_none_where_it_matters_and_bounded_elsewhere(registry, policy):
    clean = _data_checks(registry, policy, {})
    assert clean["null rate"].status == "pass" and clean["null rate"].detail.startswith("0 % in date, region, rat")
    # loaded_at is neither grain nor measure: 4 % of nulls is within the policy's 5 %, 6 % is not
    assert _data_checks(registry, policy, {"loaded_at": 40})["null rate"].status == "pass"
    over = _data_checks(registry, policy, {"loaded_at": 60})["null rate"]
    assert over.status == "fail" and "loaded_at: 6.0% null, over 5%" in over.detail
    # one null in a measure is one too many
    holed = _data_checks(registry, policy, {"qoe_score": 1})["null rate"]
    assert holed.status == "fail" and "qoe_score: 1 null" in holed.detail
    assert "expected values" in {name for name in clean} and "accepted values" not in clean


def _dependencies(registry, policy, **changes):
    class _Lineage:
        def lineage_for(self, model):
            return {"relation": f"bi.{model}"}

    spec = DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"}, filters=["region"], components=[
        {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"}])
    engine = {"cem.qoe_daily": {"id": 5, "database": {"database_name": "BI"},
                                "columns": [{"column_name": "region"}]}}
    found = {"on_dashboard": [11], "charts": {"kpi_1": {"id": 11}}, "connection": "BI",
             "filters": [{"name": "Region", "column": "region", "dataset_id": 5}], **changes}
    report = ValidationAgent(registry, policy, SqlAgent(), _Lineage()).dependencies(spec, engine, found)
    assert {c.name for c in report.checks} == {"dependencies exist"}
    return [c.detail for c in report.failures]


def test_every_dependency_is_followed_down_to_the_dbt_model(registry, policy):
    assert _dependencies(registry, policy) == []
    assert _dependencies(registry, policy, on_dashboard=[]) == ["chart 11 is not on the dashboard"]
    assert "not on « Another »" in _dependencies(registry, policy, connection="Another")[0]
    assert "chart(s) the specification does not name: [12]" in _dependencies(registry, policy, on_dashboard=[11, 12])[0]
    assert "column rat of BI dataset 5, does not exist" in _dependencies(
        registry, policy, filters=[{"name": "RAT", "column": "rat", "dataset_id": 5}])[0]


def _probe(role=None, **changes):
    probe = {"account": "sharpsight-probe", "role": role, "dashboard": 404, "data": {"cem.qoe_daily": 403},
             "rows": {}, "direct": {"dashboard page": 404, "chart": 404, "dataset": 404, "SQL Lab": 403},
             "export": {"assets": {"dashboard": 404, "chart": 404}, "data": {"cem.qoe_daily": 403}}}
    return {**probe, **changes}


def test_what_may_be_exported_and_by_whom(registry, policy, tmp_path):
    tools = _toolbox(registry, policy, tmp_path)
    assert tools.export_problems(_probe()) == []
    assert tools.export_problems(_probe(export={"assets": {"dashboard": 200}, "data": {"cem.qoe_daily": 200}})) == [
        "the definition of the dashboard was exported", "the data of cem.qoe_daily was exported as a file"]
    # a role of the audience takes its own rows out — exactly those, and never the definition
    rows = {"cem.qoe_daily": {"by": "region", "data_gateway": 3, "exported": 3}}
    served = {"assets": {"dashboard": 500, "chart": 404}, "data": {"cem.qoe_daily": 200}}
    assert tools.export_problems(_probe("cem_regional_analyst", rows=rows, export=served)) == []
    leaked = {"cem.qoe_daily": {"by": "region", "data_gateway": 3, "exported": 16}}
    assert "has 16 values of region; the role's policy gives 3" in tools.export_problems(
        _probe("cem_regional_analyst", rows=leaked, export=served))[0]
    assert tools.export_problems(_probe("cem_analyst", rows=rows, export={**served, "assets": {"dashboard": 200}})) == [
        "the definition of the dashboard was exported"]


def test_a_direct_address_serves_the_dashboard_to_its_audience_and_nothing_else():
    assert Toolbox.direct_problems(_probe()) == []
    assert Toolbox.direct_problems(_probe(direct={"dashboard page": 200, "chart": 404})) == ["dashboard page was served (200)"]
    role = _probe("cem_analyst", direct={"dashboard page": 200, "chart": 404, "dataset": 404, "SQL Lab": 403})
    assert Toolbox.direct_problems(role) == []
    role["direct"]["dataset"] = 200                       # the dataset on its own, outside the dashboard
    assert Toolbox.direct_problems(role) == ["dataset was served (200)"]


def test_export_and_direct_access_are_two_checks_of_the_report(with_generated, policy):
    from aibi.data_gateway import QueryLog

    spec = DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"}, components=[
        {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"}])
    agent = ValidationAgent(with_generated, policy, SqlAgent(), store=None)
    refused = {"status": 401, "to_sign_in": False, "refused": True}

    def checks(**access):
        base = {"differences": [], "granted": {}, "outsider": _probe(), "outsider_refused": True,
                "export_policy": {"data": "audience", "assets": "nobody"}, "export_problems": [],
                "direct_problems": [], "anonymous": {"dashboard page": {"status": 302, "to_sign_in": True, "refused": True},
                                                     "chart": refused}}
        report = agent.security(spec, policy.identify("dev.user"), QueryLog(), None, access={**base, **access})
        return {c.name: c for c in report.checks}

    good = checks()
    assert good["export permissions"].status == good["direct URL access"].status == "pass"
    assert "dashboard page → sign-in page" in good["direct URL access"].detail
    assert checks(export_problems=["the data of cem.qoe_daily was exported as a file"])["export permissions"].status == "fail"
    open_page = checks(anonymous={"dashboard page": {"status": 200, "to_sign_in": False, "refused": False}})
    assert "dashboard page is served to someone not signed in" in open_page["direct URL access"].detail


def test_concurrency_is_a_budget_like_the_others(registry, policy):
    agent = ValidationAgent(registry, policy, SqlAgent(), store=None)
    data = {"kpi_1": {"duration_ms": 40}}
    load = {"users": 4, "queries": 28, "failed": 0, "slowest_ms": 900, "wall_ms": 1500}
    check = lambda c: {x.name: x for x in agent.performance(data, [], c).checks}.get("concurrency")  # noqa: E731
    assert check(None) is None
    assert check(load).status == "pass" and "4 users at once" in check(load).subject
    assert check({**load, "failed": 1}).status == "fail"
    assert check({**load, "slowest_ms": 9000}).status == "fail"


# ── whitespace and the template, on the page ────────────────────────────────────────────────────────────────
SPEC = DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"}, components=[
    {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"},
    {"slot": "kpi_2", "type": "big_number", "metric": "cem.active_users", "dataset": "cem.qoe_daily"},
    {"slot": "trend_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date", "dataset": "cem.qoe_daily"},
    {"slot": "ranking_1", "type": "bar", "metric": "cem.qoe_score", "dimension": "region", "dataset": "cem.qoe_daily"}])
CHARTS = {"kpi_1": {"id": 1}, "kpi_2": {"id": 2}, "trend_1": {"id": 3}, "ranking_1": {"id": 4}}
DATA = {"kpi_1": {"rowcount": 1, "data": []}, "kpi_2": {"rowcount": 1, "data": []},
        "trend_1": {"rowcount": 90, "data": []}, "ranking_1": {"rowcount": 5, "data": []}}


def _card(chart_id, x, y, w, h, viz_type, **extra):
    return {"chart_id": chart_id, "viz_type": viz_type, "rect": {"x": x, "y": y, "w": w, "h": h}, "clipped": [],
            "error": None, "loading": False, "blank_ratio": 0.9, "fill_ratio": 0.8, **extra}


def _page(registry, replace=None):
    cards = {1: _card(1, 16, 137, 696, 128, "big_number_total"), 2: _card(2, 728, 137, 696, 128, "big_number_total"),
             3: _card(3, 16, 281, 1408, 352, "echarts_timeseries_line"),
             4: _card(4, 16, 649, 1408, 416, "echarts_timeseries_bar"), **(replace or {})}
    agent = VisualQaAgent("http://x", "u", "p", registry.policies,
                          {kind: v.engine_type for kind, v in registry.visualizations.items()})
    render = Render(viewport="desktop", width=1440, screenshot="dashboard-desktop.png", load_ms=4000,
                    components=list(cards.values()))
    return agent.check(render, SPEC, registry.template("executive"), CHARTS, DATA)


def test_a_page_laid_out_as_the_template_says_has_neither_holes_nor_violations(registry):
    report = _page(registry)
    assert report.passed and report.warnings == [], [(c.name, c.detail) for c in report.failures + report.warnings]
    named = {c.name for c in report.checks}
    assert {"no excessive whitespace", "no template violation"} <= named


def test_a_hole_in_the_page_is_excessive_whitespace(registry):
    # the ranking pushed 120 px down, and drawn on half its row
    report = _page(registry, {4: _card(4, 16, 769, 696, 416, "echarts_timeseries_bar")})
    failed = {c.name: c.detail for c in report.failures}
    assert "136 px of nothing above row breakdown" in failed["no excessive whitespace"]
    assert "the cards cover" in failed["no excessive whitespace"]
    assert "ranking_1 is 5.9 columns wide, the template gives it 12" in failed["no template violation"]


def test_a_card_mostly_empty_is_a_warning_and_a_headline_number_is_not(registry):
    report = _page(registry, {1: _card(1, 16, 137, 696, 128, "big_number_total", fill_ratio=0.1),
                              3: _card(3, 16, 281, 1408, 352, "echarts_timeseries_line", fill_ratio=0.2)})
    assert report.passed
    assert [(c.code, c.slot) for c in report.warnings] == [("card_mostly_empty", "trend_1")]


def test_the_wrong_chart_in_a_slot_and_the_wrong_height_are_template_violations(registry):
    report = _page(registry, {3: _card(3, 16, 281, 1408, 300, "pie")})
    detail = next(c.detail for c in report.failures if c.name == "no template violation")
    assert "trend_1 is 300 px high, row trend is 352 px in the template" in detail
    assert "trend_1 shows a pie; the slot accepts line, area" in detail


# ── the audit record, and the matrix ────────────────────────────────────────────────────────────────────────
def test_the_audit_record_names_metrics_visual_qa_and_the_commit(tmp_path):
    log = AuditLog(tmp_path)
    plain = log.record(action="request", user="dev.user", request_id="r")
    assert (plain["metrics"], plain["visual_qa"], plain["git_commit"]) == ([], None, None)
    full = log.record(action="version:ready_for_review", user="dev.user", request_id="r",
                      metrics=["cem.qoe_score"], git_commit="abc123",
                      visual_qa={"renders": [{"viewport": "desktop"}], "failed": [], "warned": []})
    assert full["metrics"] == ["cem.qoe_score"] and full["git_commit"] == "abc123"
    assert full["visual_qa"]["renders"][0]["viewport"] == "desktop"
    assert log.verify() == {"ok": True, "records": 2}


def test_the_matrix_covers_a_version_by_its_prefix(registry):
    assert compatibility.covers("6.1", "6.1.0") and compatibility.covers("16", "16.4")
    assert not compatibility.covers("6.1", "6.10.0") and not compatibility.covers("6.1", "6.2.0")
    running = {"superset": "6.1.0", "dbt_core": "1.12.5", "dbt_postgres": "1.11.0", "python": "3.12.11",
               "postgresql": "16.4", "playwright": "1.63.0", "sqlglot": "27.29.0",
               "superset_extensions": "6.1.0", "superset_studio": "6.1.0"}
    good = compatibility.report(registry.compatibility, "0.3.0", running)
    assert good["ok"] and good["untested"] == []
    assert good["components"]["superset"]["verdict"] == "tested"
    assert good["components"]["airflow"]["status"] == "not_used" and "running" not in good["components"]["airflow"]
    assert good["connectors"]["postgresql"]["status"] == "tested"
    newer = compatibility.report(registry.compatibility, "0.3.0",
                                 {**running, "superset": "6.2.0", "postgresql": None})
    assert not newer["ok"] and newer["components"]["superset"]["verdict"] == "untested"
    assert newer["untested"] == ["superset 6.2.0, tested with 6.1", "postgresql (not detected), tested with 16"]
