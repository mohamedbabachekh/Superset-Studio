"""The « run » environment overlay, and the five variables that make a target.

docker-compose.run-target.yaml is how « deploy it to the run platform » can be TRIED on one machine: a second
Superset, with its own metadata database, its own accounts and nothing of the kit's in it. What it must get
right is the handover — the names the engine reads out of its environment (config.targets). A variable
renamed on one side and not the other would leave the Studio saying « no target is configured » and nothing
to show why, so both sides are read here, from the file itself.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from aibi.config import targets
from aibi.deploy import Target


def _overlay() -> dict:
    here = Path(__file__).resolve()
    for folder in (Path("/app"), *here.parents):
        found = folder / "docker-compose.run-target.yaml"
        if found.is_file():
            return yaml.safe_load(found.read_text(encoding="utf-8"))
    pytest.skip("docker-compose.run-target.yaml is not beside this checkout")


def test_the_overlay_adds_a_superset_of_its_own_and_touches_nothing_of_the_kit_s():
    services = _overlay()["services"]
    assert {"run-db-init", "run-superset-init", "run-superset"} <= set(services)
    # the only service of the kit's it may mention is the engine, and only to tell it the target exists
    assert set(services) - {"run-db-init", "run-superset-init", "run-superset"} == {"sharpsight-ai-bi"}
    assert set(services["sharpsight-ai-bi"]) == {"environment"}
    # its own metadata database, its own Superset home, its own Redis database: nothing shared but the server
    env = services["run-superset"]["environment"]
    assert "/runbi" in env["SUPERSET_META_URI"]
    assert env["SUPERSET_REDIS_URL"].endswith("/3")
    assert any(volume.startswith("run-home:") for volume in services["run-superset"]["volumes"])


def test_the_variables_it_hands_the_engine_are_the_ones_the_engine_reads(monkeypatch):
    given = _overlay()["services"]["sharpsight-ai-bi"]["environment"]
    assert set(given) == {f"AIBI_TARGET_RUN_{part}" for part in
                          ("URL", "USER", "PASSWORD", "DB_NAME", "DB_URI", "FROM_STUDIO", "PUBLIC_URL")}
    for name in list(os.environ):
        if name.startswith("AIBI_TARGET_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AIBI_TARGET_RUN_URL", "http://run-superset:8088")
    monkeypatch.setenv("AIBI_TARGET_RUN_USER", "run-admin")
    monkeypatch.setenv("AIBI_TARGET_RUN_PASSWORD", "x")
    monkeypatch.setenv("AIBI_TARGET_RUN_DB_NAME", "SharpSight BI (read-only)")
    monkeypatch.setenv("AIBI_TARGET_RUN_DB_URI", "postgresql+psycopg2://bi_reader:x@devkit-db:5432/devkit")
    monkeypatch.setenv("AIBI_TARGET_RUN_FROM_STUDIO", "true")
    monkeypatch.setenv("AIBI_TARGET_RUN_PUBLIC_URL", "http://127.0.0.1:3004")
    found = targets()
    assert set(found) == {"run"}
    target: Target = found["run"]
    assert target.url == "http://run-superset:8088" and target.from_studio is True
    assert target.database_name == "SharpSight BI (read-only)"
    # the engine goes to the container; the link it leaves behind is for a person
    assert target.where == "http://127.0.0.1:3004"


def test_a_target_without_its_warehouse_is_not_a_target(monkeypatch):
    for name in list(os.environ):
        if name.startswith("AIBI_TARGET_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AIBI_TARGET_HALF_URL", "https://bi.example.com")
    assert targets() == {}
