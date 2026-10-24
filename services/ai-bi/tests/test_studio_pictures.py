"""Who may see the Studio's tiles, and who may ask for new ones.

A tile is cut from a render made by the engine's service account (aibi/picture.py), and that account reads
every row: Superset's row-level rules hang from roles, and it is an administrator. So a tile may be shown
only to someone who would see exactly that in Superset — a role in the dashboard's audience and no row-level
rule on what it reads — and a render, which is a browser and every chart's query, is a developer's to ask for.

The service these tests give `aibi.main` is a stand-in holding only what these routes touch.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from aibi import main
from aibi.policy_engine import PolicyError
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
                      "datasets": {"cem.qoe_daily": 3},
                      "charts": {"kpi_1": {"id": 11}, "trend_1": {"id": 12}}}}


@pytest.fixture()
def service(registry, policy, tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "control-plane.sqlite")
    store.save_project("cem-qoe", json.loads(json.dumps(PROJECT)), "2026-10-24T00:00:00Z")
    tile = tmp_path / "kpi_1.png"
    tile.write_bytes(b"\x89PNG every row")
    renders: list[int] = []

    def cut(project_id, dashboard_id, charts):
        renders.append(dashboard_id)
        return {"taken_at": "2026-10-24T00:00:00Z", "slots": {slot: {} for slot in charts}}

    stand_in = SimpleNamespace(
        registry=registry, policy=policy, store=store, renders=renders,
        pictures=SimpleNamespace(cut=cut, tile=lambda project_id, slot: tile,
                                 taken=lambda project_id: {"taken_at": "2026-10-24T00:00:00Z"}))
    monkeypatch.setattr(main, "service", stand_in)
    return stand_in


def test_a_developer_and_the_dashboard_s_full_audience_see_the_tiles(service, policy):
    for user in ("dev.user", "cem.analyst"):
        response = main.picture("cem-qoe", "kpi_1", policy.identify(user))
        assert Path(response.path).read_bytes().startswith(b"\x89PNG")


def test_someone_a_row_level_rule_limits_is_not_shown_the_service_account_s_rows(service, policy):
    """east.analyst sees three regions in Superset. The tile shows all sixteen, so it is not theirs."""
    east = policy.identify("east.analyst")
    with pytest.raises(PolicyError) as refused:
        main.picture("cem-qoe", "kpi_1", east)
    assert refused.value.code == "picture_denied"


def test_someone_outside_the_audience_is_not_shown_the_tiles(service, policy):
    with pytest.raises(PolicyError):
        main.picture("cem-qoe", "kpi_1", policy.identify("guest.user"))


def test_a_metric_classified_above_a_role_takes_that_role_out_of_the_tiles_too(service, policy, registry):
    """The audience variant: a restricted metric leaves cem_analyst out of the dashboard, and of its tiles."""
    score = registry.metrics["cem.qoe_score"]
    registry.metrics["cem.qoe_score"] = score.model_copy(update={"classification": "confidential"})
    with pytest.raises(PolicyError):
        main.picture("cem-qoe", "kpi_1", policy.identify("cem.analyst"))


def test_the_studio_payload_names_no_tiles_to_someone_who_may_not_see_them(service, policy):
    project = service.store.get_project("cem-qoe")
    assert main._sees_the_picture(project, policy.identify("dev.user")) is True
    assert main._sees_the_picture(project, policy.identify("east.analyst")) is False


def test_only_a_developer_who_sees_every_row_may_start_a_render(service, policy):
    assert main.take_pictures("cem-qoe", policy.identify("dev.user"))["slots"]
    assert service.renders == [7]
    for user in ("guest.user", "cem.analyst", "east.analyst"):
        with pytest.raises(PolicyError) as refused:
            main.take_pictures("cem-qoe", policy.identify(user))
        assert "bi.generate" in str(refused.value)
    assert service.renders == [7], "no one else started a browser"


def test_a_dataset_the_registry_does_not_know_clears_nobody(service, policy):
    project = service.store.get_project("cem-qoe")
    project["engine"]["datasets"]["cem.gone"] = 9
    assert main._sees_the_picture(project, policy.identify("dev.user")) is False
    with pytest.raises(HTTPException):
        main.picture("no-such-project", "kpi_1", policy.identify("dev.user"))
