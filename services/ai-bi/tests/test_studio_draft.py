"""The draft: the third state the Studio adds, and the rules around it.

A draft is what a person is editing — neither the committed version nor what Superset holds (the brief's
§50.2). It is kept with its project, it carries a token so two people cannot overwrite each other silently
(§50.3), it changes nothing in Superset by existing, and it becomes a version only through the pipeline that
every other commit goes through.

The service these tests give `aibi.main` is a stand-in holding only what these routes touch: the registry,
the store, the policy and an audit log. The end-to-end run is `bash ai-bi.sh demo` against the real things.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aibi import main
from aibi.audit import AuditLog
from aibi.dashboard_agent import DashboardAgent
from aibi.lifecycle import LifecycleError
from aibi.models import DashboardSpec
from aibi.store import Store

SPEC = {
    "dashboard": {"id": "cem-qoe", "template": "executive", "title": "CEM QoE Overview"},
    "filters": ["date"],
    "components": [
        {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"},
        {"slot": "trend_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date",
         "dataset": "cem.qoe_daily"},
    ]}
PROJECT = {"project_id": "cem-qoe", "title": "CEM QoE", "template": "executive", "tenant": "default",
           "created_by": "dev.user", "spec": SPEC, "spec_valid": True,
           "engine": {"dashboard": {"id": 7, "url": "http://localhost:3002/superset/dashboard/7/"},
                      "charts": {"kpi_1": {"id": 11}, "trend_1": {"id": 12}}}}


@pytest.fixture()
def service(registry, policy, semantic, tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "control-plane.sqlite")
    store.save_project("cem-qoe", json.loads(json.dumps(PROJECT)), "2026-10-19T00:00:00Z")
    stand_in = SimpleNamespace(
        registry=registry, policy=policy, store=store,
        audit=AuditLog(tmp_path / "audit", []),
        tools=SimpleNamespace(dashboards=DashboardAgent(registry, policy, semantic)),
        pictures=SimpleNamespace(taken=lambda project_id: None))
    monkeypatch.setattr(main, "service", stand_in)
    return stand_in


@pytest.fixture()
def dev(policy):
    return policy.identify("dev.user")


def _layout(spec: dict) -> dict:
    """The same specification, with a layout a person placed: the KPI row widened, the rows swapped."""
    return {**spec, "layout": [
        {"row": "trend", "height": 40, "slots": [{"id": "trend_1", "width": 12}]},
        {"row": "kpis", "height": 20, "slots": [{"id": "kpi_1", "width": 6}]}]}


def test_the_studio_draws_the_version_until_there_is_a_draft(service, dev):
    payload = main.studio("cem-qoe", dev)
    assert payload["editing"] == "version" and payload["draft"] is None and payload["token"] == "none"
    assert payload["layout_of"] == "template"
    assert [row["row"] for row in payload["layout"]] == ["kpis", "trend", "breakdown"]
    assert payload["project"]["dashboard"].endswith("/dashboard/7/")
    assert payload["project"]["may_edit"] is True
    # the governance the drawer of the brief's section 19 shows, per chart, from the registry
    kpi = next(c for c in payload["components"] if c["slot"] == "kpi_1")
    assert kpi["governance"]["metric_certified"] is True and kpi["governance"]["dataset_owner"]
    assert kpi["section"] == "headline" and kpi["purpose"] == "kpi" and kpi["mandatory"] is True
    # and the template's constraints, which are what the canvas may rearrange
    assert payload["template"]["grid"]["columns"] == 12
    assert payload["template"]["min_slot_width"] == {"kpi": 2, "chart": 4}
    # and the specification itself, which is what the workspace edits and sends back
    assert payload["spec"]["dashboard"]["id"] == "cem-qoe" and payload["spec"]["layout"] == []


def test_a_draft_is_kept_shown_and_thrown_away_without_touching_superset(service, dev):
    answer = main.save_draft("cem-qoe", main.Draft(spec=_layout(SPEC), token="none"), dev)
    assert answer["problems"] == [] and answer["layout_of"] == "specification"
    token = answer["token"]

    payload = main.studio("cem-qoe", dev)
    assert payload["editing"] == "draft" and payload["token"] == token
    assert payload["draft"]["saved_by"] == "dev.user"
    assert [row["row"] for row in payload["layout"]] == ["trend", "kpis"]
    # what Superset holds is untouched: the project's own specification still has no layout
    assert service.store.get_project("cem-qoe")["spec"] == SPEC

    assert main.discard_draft("cem-qoe", dev) == {"discarded": True, "token": "none"}
    assert main.studio("cem-qoe", dev)["editing"] == "version"
    assert main.discard_draft("cem-qoe", dev)["discarded"] is False


def test_a_save_against_an_older_draft_is_refused_not_merged(service, dev):
    """Two people with the page open: the second save is told to reload, and nothing is lost quietly."""
    first = main.save_draft("cem-qoe", main.Draft(spec=_layout(SPEC), token="none"), dev)
    with pytest.raises(main.HTTPException) as refused:
        main.save_draft("cem-qoe", main.Draft(spec=SPEC, token="none"), dev)
    assert refused.value.status_code == 409 and "saved by someone else" in refused.value.detail
    assert main.studio("cem-qoe", dev)["token"] == first["token"]


def test_a_draft_that_is_not_a_specification_is_refused(service, dev):
    with pytest.raises(main.HTTPException) as refused:
        main.save_draft("cem-qoe", main.Draft(spec={"dashboard": {"id": "cem-qoe"}}, token="none"), dev)
    assert refused.value.status_code == 422
    with pytest.raises(main.HTTPException) as other:
        main.save_draft("cem-qoe", main.Draft(spec={**SPEC, "dashboard": {**SPEC["dashboard"], "id": "something"}},
                                              token="none"), dev)
    assert other.value.status_code == 422 and "not another" in other.value.detail


def test_validate_reads_the_draft_and_says_what_is_wrong_with_it(service, dev):
    main.save_draft("cem-qoe", main.Draft(spec=_layout(SPEC), token="none"), dev)
    assert main.validate_draft("cem-qoe", dev)["summary"]["passed"] is True
    # a layout the template does not allow: a KPI narrower than its minimum
    narrow = {**_layout(SPEC)}
    narrow["layout"] = [{"row": "kpis", "height": 20, "slots": [{"id": "kpi_1", "width": 1}]},
                        {"row": "trend", "height": 40, "slots": [{"id": "trend_1", "width": 12}]}]
    main.save_draft("cem-qoe", main.Draft(spec=narrow, token=main.studio("cem-qoe", dev)["token"]), dev)
    report = main.validate_draft("cem-qoe", dev)
    assert report["of"] == "draft" and report["summary"]["passed"] is False
    assert any("a kpi needs 2" in problem for problem in report["problems"])


def test_a_draft_whose_layout_the_template_refuses_is_not_committed(service, dev):
    impossible = {**SPEC, "layout": [{"row": "kpis", "height": 20, "slots": [{"id": "kpi_1", "width": 1}]},
                                     {"row": "trend", "height": 40, "slots": [{"id": "trend_1", "width": 12}]}]}
    main.save_draft("cem-qoe", main.Draft(spec=impossible, token="none"), dev)
    with pytest.raises(LifecycleError) as refused:
        main.commit_draft("cem-qoe", main.Commit(), None, dev)
    assert "not one the template allows" in str(refused.value)


def test_a_project_with_no_draft_has_nothing_to_commit(service, dev):
    with pytest.raises(LifecycleError) as refused:
        main.commit_draft("cem-qoe", main.Commit(), None, dev)
    assert "no draft to commit" in str(refused.value)


def test_a_person_without_the_capability_edits_nothing(service, policy):
    """Who may do what is the registry's rule (registry/security/rbac.yaml), applied to the person Superset
    says is signed in — the Studio asks the engine, and the engine decides."""
    from aibi.policy_engine import PolicyError
    reviewer = policy.identify("lead.reviewer")
    assert main.studio("cem-qoe", reviewer)["project"]["may_edit"] is False
    with pytest.raises(PolicyError):
        main.save_draft("cem-qoe", main.Draft(spec=_layout(SPEC), token="none"), reviewer)


def test_the_token_changes_with_the_draft_and_not_otherwise(service, dev):
    first = main.save_draft("cem-qoe", main.Draft(spec=_layout(SPEC), token="none"), dev)["token"]
    again = main.save_draft("cem-qoe", main.Draft(spec=_layout(SPEC), token=first), dev)["token"]
    assert again == first
    changed = main.save_draft("cem-qoe", main.Draft(spec=SPEC, token=again), dev)["token"]
    assert changed != first and changed != "none"
