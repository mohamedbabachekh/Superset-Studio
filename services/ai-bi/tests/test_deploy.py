"""Taking a version to another Superset — and, mostly, what stops it.

A deployment is mechanical: the package that was validated, imported where it is wanted, repointed,
granted its audience, and then read back. What matters in a test is the order of the refusals, because each
one stands between a reviewed dashboard and somebody else's production.

The Superset at the other end is a stand-in here. The real thing is proved by `bash ai-bi.sh deploy` against
a kit, and by the end-to-end script (scripts/e2e.sh).
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from aibi.deploy import Deployer, Target
from aibi.models import DashboardSpec, ProjectVersion

SPEC = {
    "dashboard": {"id": "cem-qoe", "template": "executive", "title": "CEM QoE Overview", "version": "1.0.0"},
    "filters": ["date"],
    "components": [
        {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"},
        {"slot": "trend_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date",
         "dataset": "cem.qoe_daily"},
    ]}


class FakeSuperset:
    """Just enough Superset to be deployed to — and to be caught not doing its part."""

    def __init__(self, version: str = "6.1.0", charts: int = 2, chart_fails: bool = False):
        self._version, self._charts, self._chart_fails = version, charts, chart_fails
        self.imported = []
        self.databases = []
        self.roles_set = None

    def version(self): return self._version

    def ensure_database(self, name, uri, sql_lab=True):
        self.databases.append((name, uri))
        return 7

    def import_dashboard(self, bundle, overwrite=True, passwords=None):
        self.imported.append({"bytes": len(bundle), "passwords": passwords})

    def get_dashboard(self, id_or_slug): return {"id": 42, "slug": id_or_slug}

    def ensure_role(self, name): return abs(hash(name)) % 1000

    def update_dashboard(self, dashboard_id, payload): self.roles_set = payload.get("roles")

    def dashboard_charts(self, dashboard_id):
        return [{"slice_name": f"chart {n}", "query_context": json.dumps({"queries": [{}]})}
                for n in range(self._charts)]

    def dashboard_roles(self, dashboard_id): return ["sharpsight_bi_developer", "sharpsight_bi_reviewer",
                                                     "sharpsight_cem_analyst"]

    def chart_data(self, query_context):
        if self._chart_fails:
            raise RuntimeError("no such column: qoe_score")
        return {"result": [{"rowcount": 31}]}


def _package(tmp_path: Path, with_export: bool = True, built_with: str = "6.1.0") -> Path:
    path = tmp_path / "cem-qoe-1.0.0.ssbi"
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("manifest.yaml", yaml.safe_dump({"built_with": {"superset": built_with}}))
        if with_export:
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, "w") as export:
                export.writestr("export/dashboards/cem-qoe.yaml", "slug: cem-qoe")
            package.writestr("superset/dashboard_export.zip", inner.getvalue())
    return path


def _version(path: Path, status: str = "APPROVED_FOR_UAT", passed: bool = True) -> ProjectVersion:
    return ProjectVersion(
        project_id="cem-qoe", version="1.0.0", status=status, created_by="dev.user", request_id="r",
        validation={"passed": passed}, dashboard={"id": 7},
        package={"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})


@pytest.fixture()
def deployer(registry, policy, tmp_path):
    saved = {}
    store = SimpleNamespace(get_project=lambda pid: saved.get(pid, {"project_id": pid}),
                            save_project=lambda pid, body, at: saved.__setitem__(pid, body))
    audit = SimpleNamespace(record=lambda **kw: None)
    lifecycle = SimpleNamespace(
        package_path=lambda record: tmp_path / record.package["file"],
        package_problem=lambda record: None,
        specification_at=lambda pid, version: DashboardSpec(**SPEC))
    made = Deployer(store, audit, lifecycle, policy, registry,
                    adapter=lambda target: made.superset)
    made.superset = FakeSuperset()
    made.saved = saved
    return made


@pytest.fixture()
def target():
    return Target(name="run", url="https://bi.example.com", user="svc", password="x",
                  database_name="SharpSight BI", database_uri="postgresql+psycopg2://run/warehouse")


def _deploy(deployer, target, record, gate="UAT"):
    return deployer.deploy(record, target, gate, "dev.user", "req", "dep1")


def test_a_version_nobody_signed_off_does_not_leave_the_kit(deployer, target, tmp_path):
    record = _version(_package(tmp_path), status="READY_FOR_REVIEW")
    done = _deploy(deployer, target, record)
    assert done.status == "failed" and "already approved for it" in done.error
    assert deployer.superset.imported == [], "nothing was sent"


def test_a_version_that_did_not_pass_validation_does_not_leave_either(deployer, target, tmp_path):
    record = _version(_package(tmp_path), passed=False)
    done = _deploy(deployer, target, record)
    assert done.status == "failed" and "validation did not pass" in done.error


def test_prod_takes_a_version_signed_off_for_prod_and_not_one_signed_off_for_uat(deployer, target, tmp_path):
    for_uat = _version(_package(tmp_path), status="APPROVED_FOR_UAT")
    assert "APPROVED_FOR_UAT" in deployer.refusal(for_uat, target, "PROD")
    for_prod = _version(_package(tmp_path), status="APPROVED_FOR_PROD")
    assert deployer.refusal(for_prod, target, "PROD") is None


def test_a_package_that_is_not_the_file_that_was_validated_is_refused(deployer, target, tmp_path):
    path = _package(tmp_path)
    record = _version(path)
    path.write_bytes(path.read_bytes() + b"tampered")          # same name, different bytes
    done = _deploy(deployer, target, record)
    assert done.status == "failed" and "not the file that was validated" in done.error
    assert done.step("read_package").status == "failed"


def test_a_package_without_superset_s_own_export_cannot_be_deployed(deployer, target, tmp_path):
    record = _version(_package(tmp_path, with_export=False))
    done = _deploy(deployer, target, record)
    assert "carries no Superset export" in done.error


def test_a_package_built_against_another_superset_is_not_carried_across(deployer, target, tmp_path):
    record = _version(_package(tmp_path, built_with="6.2.0"))
    done = _deploy(deployer, target, record)
    assert done.status == "failed" and "matrix has not" in done.error
    assert done.step("check_compatibility").status == "failed"


def test_a_deployment_imports_repoints_grants_and_reads_back(deployer, target, tmp_path):
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "succeeded", done.error
    assert [step.status for step in done.steps] == ["done"] * len(done.steps)
    # the connection is repointed AFTER the import, because an import keeps an existing connection's URI
    assert deployer.superset.databases == [("SharpSight BI", target.database_uri)] * 2
    assert deployer.superset.imported[0]["passwords"] == {
        "databases/SharpSight BI.yaml": target.database_uri}
    # the audience is granted, which an export never carries
    assert deployer.superset.roles_set and len(deployer.superset.roles_set) >= 1
    assert done.dashboard["url"].endswith("/superset/dashboard/cem-qoe/")
    # and it is recorded where the Studio can show it
    assert deployer.saved["cem-qoe"]["deployments"][0]["status"] == "succeeded"


def test_a_chart_that_cannot_draw_fails_the_deployment(deployer, target, tmp_path):
    deployer.superset = FakeSuperset(chart_fails=True)
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "failed" and "cannot draw" in done.error
    assert any(check["status"] == "fail" for check in done.checks)


def test_a_dashboard_that_arrived_short_of_its_charts_fails_too(deployer, target, tmp_path):
    deployer.superset = FakeSuperset(charts=1)                 # the specification has two
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "failed" and "1 of 2 charts arrived" in done.error


def test_every_deployment_is_recorded_whether_it_worked_or_not(deployer, target, tmp_path):
    _deploy(deployer, target, _version(_package(tmp_path), status="READY_FOR_REVIEW"))
    _deploy(deployer, target, _version(_package(tmp_path)))
    kept = deployer.saved["cem-qoe"]["deployments"]
    assert [one["status"] for one in kept] == ["failed", "succeeded"]
    assert all(one["by"] == "dev.user" and one["target"] == "run" for one in kept)


def test_the_link_it_leaves_behind_is_the_one_a_person_can_open(deployer, tmp_path):
    """The engine reaches a target by whatever name resolves for IT — a container name, a private address.
    A link recorded for somebody to click has to be the one THEY can open, so a target may name both."""
    inside = Target(name="run", url="http://run-superset:8088", user="svc", password="x",
                    database_name="SharpSight BI", database_uri="postgresql+psycopg2://run/warehouse",
                    public_url="http://127.0.0.1:3004")
    done = _deploy(deployer, inside, _version(_package(tmp_path)))
    assert done.status == "succeeded"
    assert done.dashboard["url"] == "http://127.0.0.1:3004/superset/dashboard/cem-qoe/"


def test_without_a_public_url_the_link_is_where_the_engine_went(deployer, target, tmp_path):
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.dashboard["url"] == "https://bi.example.com/superset/dashboard/cem-qoe/"
