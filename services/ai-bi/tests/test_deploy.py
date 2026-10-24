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
from aibi.superset_adapter import SupersetError

SPEC = {
    "dashboard": {"id": "cem-qoe", "template": "executive", "title": "CEM QoE Overview", "version": "1.0.0"},
    "filters": ["date"],
    "components": [
        {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"},
        {"slot": "trend_1", "type": "line", "metric": "cem.qoe_score", "dimension": "date",
         "dataset": "cem.qoe_daily"},
    ]}


class FakeSuperset:
    """Just enough Superset to be deployed to — and to be caught not doing its part. It KEEPS what it is
    given (roles, a dashboard's audience, row-level rules), so what a deployment reads back is what was
    written, the way a real target's would be."""

    def __init__(self, version: str = "6.1.0", charts: int = 2, chart_fails: bool = False):
        self._version, self._charts, self._chart_fails = version, charts, chart_fails
        self.imported = []
        self.databases = []
        self.roles_set = None
        self.roles: dict[str, int] = {}
        self.rules: dict[int, dict[str, Any]] = {}
        self.writes: list[str] = []                       # every rule written, as "POST name" / "PUT name"
        # the datasets the import created there: (schema, table) → id
        self.datasets = {("marts", "mart_cem_qoe_daily"): 501}
        self.looked_up: list[tuple] = []
        self.refuse_rules = False                         # a service account not allowed row-level security
        self.drops_clauses = False                        # a target that answers 200 and keeps something else
        self.refuse_rule: str = ""                        # one rule's write refused, the others accepted
        self.refuse_grant = False                         # the dashboard's audience refused once, rules accepted
        self.events: list[str] = []                       # the order things were written in
        # the OTHER dashboards there: id → the engine roles granted it, and the datasets each is drawn from
        self.others: dict[int, list[str]] = {}
        self.drawn_from: dict[int, list[int]] = {}

    def version(self): return self._version

    def ensure_database(self, name, uri, sql_lab=True):
        self.databases.append((name, uri))
        return 7

    def import_dashboard(self, bundle, overwrite=True, passwords=None):
        self.events.append(f"import (granted {sorted(self.roles_set or [])})")
        self.imported.append({"bytes": len(bundle), "passwords": passwords})

    def get_dashboard(self, id_or_slug):
        names = {role_id: name for name, role_id in self.roles.items()}
        return {"id": 42, "slug": id_or_slug,
                "roles": [{"id": role_id, "name": names.get(role_id, f"role {role_id}")}
                          for role_id in self.roles_set or []]}

    def ensure_role(self, name):
        return self.roles.setdefault(name, 100 + len(self.roles))

    def update_dashboard(self, dashboard_id, payload):
        if "roles" in payload:
            if self.refuse_grant and payload["roles"]:    # once: putting the previous grant back works
                self.refuse_grant = False
                raise SupersetError("500 Internal Server Error: dashboard", status=500)
            self.events.append(f"grant {payload['roles']}")
            self.roles_set = payload.get("roles")

    def dashboard_roles(self, dashboard_id):
        if dashboard_id in self.others:
            return sorted(self.others[dashboard_id])
        return sorted(role["name"] for role in self.get_dashboard(dashboard_id)["roles"])

    def dataset_dashboards(self, dataset_id):
        return sorted({42, *(d for d, drawn in self.drawn_from.items() if dataset_id in drawn)})

    def dashboard_role_ids(self, dashboard_id):
        return sorted(role["id"] for role in self.get_dashboard(dashboard_id)["roles"])

    def find_dataset(self, database_id, schema, table):
        self.looked_up.append((database_id, schema, table))
        found = self.datasets.get((schema, table))
        return {"id": found} if found else None

    def row_rules(self, prefix):
        return [rule for rule in self.rules.values() if rule["name"].startswith(prefix)]

    def upsert_row_rule(self, name, engine_dataset_id, role_id, clause, group_key, description):
        if self.refuse_rules or name == self.refuse_rule:
            raise SupersetError("403 Forbidden: rowlevelsecurity", status=403)
        self.events.append(f"rule {name}")
        body = {"name": name, "clause": "1 = 1" if self.drops_clauses else clause,
                "tables": [{"id": engine_dataset_id}], "roles": [{"id": role_id}], "group_key": group_key,
                "filter_type": "Regular"}
        existing = next((rule for rule in self.rules.values() if rule["name"] == name), None)
        if existing is None:
            rule_id = 900 + len(self.rules)
            self.rules[rule_id] = {"id": rule_id, **body}
            self.writes.append(f"POST {name}")
            return rule_id, "created"
        if {key: existing.get(key) for key in body} == body:
            return existing["id"], "unchanged"
        existing.update(body)
        self.writes.append(f"PUT {name}")
        return existing["id"], "updated"

    def delete_row_rule(self, rule_id):
        self.events.append(f"delete {rule_id}")
        self.rules.pop(rule_id, None)

    def dashboard_charts(self, dashboard_id):
        return [{"slice_name": f"chart {n}", "query_context": json.dumps({"queries": [{}]})}
                for n in range(self._charts)]

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
                # where the import puts the dataset: the schema a row-level rule finds it by in the target
                export.writestr("export/datasets/SharpSight_BI/mart_cem_qoe_daily.yaml",
                                yaml.safe_dump({"table_name": "mart_cem_qoe_daily", "schema": "marts"}))
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
                  database_name="SharpSight BI", database_uri="postgresql+psycopg2://run/warehouse", gate="UAT")


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


def test_the_gate_is_the_target_s_and_asking_for_uat_does_not_lower_it(deployer, tmp_path):
    """A production target needs the release manager's approval whatever the request says: a version one
    reviewer signed off for UAT, sent with gate « UAT », is refused there, and nothing is imported."""
    prod = Target(name="prod", url="https://bi.example.com", user="svc", password="x",
                  database_name="SharpSight BI", database_uri="postgresql+psycopg2://prod/warehouse", gate="PROD")
    for_uat = _version(_package(tmp_path), status="APPROVED_FOR_UAT")
    assert "APPROVED_FOR_UAT" in deployer.refusal(for_uat, prod, "UAT")
    done = _deploy(deployer, prod, for_uat, gate="UAT")
    assert done.status == "failed" and deployer.superset.imported == []
    assert deployer.refusal(_version(_package(tmp_path), status="APPROVED_FOR_PROD"), prod, "UAT") is None


def test_a_target_that_names_no_gate_is_held_to_prod(deployer, tmp_path):
    unnamed = Target(name="other", url="https://bi.example.com", user="svc", password="x",
                     database_name="SharpSight BI", database_uri="postgresql+psycopg2://other/warehouse")
    assert unnamed.gate == "PROD"
    assert "APPROVED_FOR_UAT" in deployer.refusal(_version(_package(tmp_path)), unnamed, "UAT")


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
                    public_url="http://127.0.0.1:3004", gate="UAT")
    done = _deploy(deployer, inside, _version(_package(tmp_path)))
    assert done.status == "succeeded"
    assert done.dashboard["url"] == "http://127.0.0.1:3004/superset/dashboard/cem-qoe/"


def test_without_a_public_url_the_link_is_where_the_engine_went(deployer, target, tmp_path):
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.dashboard["url"] == "https://bi.example.com/superset/dashboard/cem-qoe/"


# ── the audience and the row-level rules, written to the target and read back ──────────────────────────────
def _wanted(deployer):
    spec = DashboardSpec(**SPEC)
    datasets = [deployer.registry.datasets[d] for d in spec.datasets()]
    metrics = [deployer.registry.metrics[m] for m in spec.metrics()]
    return deployer.policy.audience(datasets, metrics), deployer.policy.row_rules(datasets, metrics)


def test_the_row_level_rules_are_written_to_the_target_on_its_own_dataset(deployer, target, tmp_path):
    audience, rules = _wanted(deployer)
    assert rules, "the policy restricts a role on cem.qoe_daily: there is a rule to carry"
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "succeeded", done.error
    held = {rule["name"]: rule for rule in deployer.superset.rules.values()}
    assert sorted(held) == sorted(f"sharpsight:{r['role']}:{r['dataset']}" for r in rules)
    for rule in rules:
        kept = held[f"sharpsight:{rule['role']}:{rule['dataset']}"]
        assert kept["clause"] == rule["clause"]
        # on the dataset the IMPORT created in the target, found in the schema the export recorded
        assert kept["tables"] == [{"id": 501}]
        assert kept["roles"] == [{"id": deployer.superset.roles[f"sharpsight_{rule['role']}"]}]
    assert (7, "marts", "mart_cem_qoe_daily") in deployer.superset.looked_up
    assert deployer.superset.dashboard_roles(42) == sorted(f"sharpsight_{r}" for r in audience)
    assert f"{len(rules)} row-level rules read back" in done.step("apply_access").detail


def test_deploying_twice_writes_nothing_twice(deployer, target, tmp_path):
    record = _version(_package(tmp_path))
    _deploy(deployer, target, record)
    first = list(deployer.superset.writes)
    done = _deploy(deployer, target, record)
    assert done.status == "succeeded", done.error
    assert deployer.superset.writes == first, "the second deployment found every rule as it should be"
    assert len(deployer.superset.rules) == len(_wanted(deployer)[1])


def test_a_rule_the_policy_no_longer_has_is_withdrawn_from_the_target(deployer, target, tmp_path):
    deployer.superset.rules[1] = {"id": 1, "name": "sharpsight:former_role:cem.qoe_daily", "clause": "1 = 0",
                                  "tables": [{"id": 501}], "roles": [{"id": 1}]}
    deployer.superset.rules[2] = {"id": 2, "name": "sharpsight:other:other.dataset", "clause": "1 = 0",
                                  "tables": [{"id": 9}], "roles": [{"id": 1}]}
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "succeeded", done.error
    assert 1 not in deployer.superset.rules
    assert 2 in deployer.superset.rules, "another project's rule is not this deployment's to remove"


def test_a_target_that_refuses_row_level_security_fails_the_deployment_and_says_why(deployer, target, tmp_path):
    deployer.superset.refuse_rules = True
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "failed" and "run refused the audience or the row-level rules" in done.error
    assert done.step("apply_access").status == "failed"
    assert done.step("verify_data").status == "pending", "nothing is checked as if it were open to anyone"


def test_a_target_that_keeps_something_else_is_caught_by_the_read_back(deployer, target, tmp_path):
    deployer.superset.drops_clauses = True
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "failed" and "does not hold the access SharpSight decided" in done.error
    assert "is not what the policy says" in done.error


def test_a_rule_whose_dataset_did_not_arrive_fails_the_deployment(deployer, target, tmp_path):
    deployer.superset.datasets = {}
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "failed" and "has nothing to hang from" in done.error
    assert deployer.superset.rules == {}


def _reader_of_another_dashboard(registry):
    """A role a DIFFERENT dashboard over cem.qoe_daily is for: granted the dataset and restricted on it, but
    not cleared for this dashboard's internal metric, so it is not in this one's audience."""
    from aibi.models import RoleDef
    registry.rbac.roles["public_reader"] = RoleDef(capabilities=["bi.read"], datasets=["cem.*"],
                                                    classifications=["public"])
    registry.rbac.row_filters["public_reader"] = {"cem.qoe_daily": "region = 'Oriental'"}
    return "sharpsight:public_reader:cem.qoe_daily"


def test_a_rule_another_dashboard_over_the_same_dataset_needs_is_kept(deployer, target, tmp_path):
    """The withdraw clean-up once deleted every rule on a dataset of this dashboard whose role was not in
    THIS audience — so the audience of another dashboard over the same dataset saw every row. Only a rule the
    policy itself no longer declares is withdrawn."""
    shared = _reader_of_another_dashboard(deployer.registry)
    assert "public_reader" not in _wanted(deployer)[0]
    deployer.superset.rules[3] = {"id": 3, "name": shared, "clause": "region = 'Oriental'",
                                  "tables": [{"id": 501}], "roles": [{"id": 1}]}
    deployer.superset.rules[1] = {"id": 1, "name": "sharpsight:former_role:cem.qoe_daily", "clause": "1 = 0",
                                  "tables": [{"id": 501}], "roles": [{"id": 1}]}
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "succeeded", done.error
    assert 3 in deployer.superset.rules, "another dashboard's audience keeps its row-level rule"
    assert 1 not in deployer.superset.rules, "a rule the policy withdrew is still withdrawn"


def _withdrawn_rule(deployer):
    deployer.superset.rules[1] = {"id": 1, "name": "sharpsight:former_role:cem.qoe_daily", "clause": "1 = 0",
                                  "tables": [{"id": 501}], "roles": [{"id": 1}]}


def test_a_withdrawn_rule_is_kept_while_another_dashboard_still_grants_its_role(deployer, target, tmp_path):
    """The policy no longer has the role, but dashboard B over the same dataset was not applied again and is
    still granted to it: deleting the rule would show B's audience every row."""
    _withdrawn_rule(deployer)
    deployer.superset.others[43] = ["sharpsight_former_role"]
    deployer.superset.drawn_from[43] = [501]
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "succeeded", done.error
    assert 1 in deployer.superset.rules, "dashboard #43 still filters its rows through it"
    assert "sharpsight:former_role:cem.qoe_daily" in done.step("apply_access").detail
    # once B is no longer granted to the role, the next run withdraws it
    deployer.superset.others[43] = ["sharpsight_somebody_else"]
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "succeeded", done.error
    assert 1 not in deployer.superset.rules


def test_a_dashboard_over_another_dataset_does_not_keep_a_withdrawn_rule(deployer, target, tmp_path):
    _withdrawn_rule(deployer)
    deployer.superset.others[43] = ["sharpsight_former_role"]
    deployer.superset.drawn_from[43] = [999]
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "succeeded", done.error
    assert 1 not in deployer.superset.rules


def test_a_withdrawn_rule_whose_dashboards_cannot_be_checked_is_kept(deployer, target, tmp_path):
    _withdrawn_rule(deployer)

    def refuses(dataset_id):
        raise SupersetError("403 Forbidden: dataset", status=403)
    deployer.superset.dataset_dashboards = refuses
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "succeeded", done.error
    assert 1 in deployer.superset.rules, "not known to be free of other dashboards: it keeps restricting"


def test_every_rule_is_written_before_the_dashboard_is_granted(deployer, target, tmp_path):
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "succeeded", done.error
    events = deployer.superset.events
    grant = next(i for i, event in enumerate(events) if event.startswith("grant"))
    assert any(event.startswith("rule") for event in events)
    assert all(i < grant for i, event in enumerate(events) if event.startswith("rule"))


def test_a_rule_write_that_fails_leaves_the_dashboard_ungranted(deployer, target, tmp_path):
    _, rules = _wanted(deployer)
    deployer.superset.refuse_rule = f"sharpsight:{rules[0]['role']}:{rules[0]['dataset']}"
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "failed" and done.step("apply_access").status == "failed"
    assert deployer.superset.roles_set is None, "nobody was granted a dashboard whose rows are not filtered"
    assert not any(event.startswith("grant") for event in deployer.superset.events)


def test_a_grant_that_fails_is_taken_back_to_what_it_was(deployer, target, tmp_path):
    deployer.superset.roles_set = [7]                          # what the dashboard was granted to before
    deployer.superset.refuse_grant = True
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "failed" and "the previous grant was put back" in done.error
    # what it was just before the grant: withdrawn ahead of the import, and not handed back to the old
    # audience over charts the new version brought
    assert deployer.superset.roles_set == []


def test_a_redeployment_takes_the_dashboard_from_its_old_audience_before_importing(deployer, target, tmp_path):
    """An overwrite import keeps the dashboard's roles (they are not in an export): the previous version's
    audience held the new charts before their rules existed, and kept them when a later step failed."""
    deployer.superset.roles_set = [7]                          # granted by the previous deployment
    deployer.superset.refuse_rules = True                      # and this one fails after the import
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "failed" and done.step("apply_access").status == "failed"
    assert deployer.superset.events[:2] == ["grant []", "import (granted [])"]
    assert deployer.superset.roles_set == [], "the old audience does not keep the new content unfiltered"
    assert "previous audience (1 roles) was withdrawn first" in done.step("import_assets").detail


def test_a_redeployment_that_works_grants_the_new_audience_after_its_rules(deployer, target, tmp_path):
    deployer.superset.roles_set = [7]
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "succeeded", done.error
    audience, _ = _wanted(deployer)
    assert deployer.superset.dashboard_roles(42) == sorted(f"sharpsight_{r}" for r in audience)
    events = deployer.superset.events
    assert events[0] == "grant []" and events[1].startswith("import")
    regrant = max(i for i, event in enumerate(events) if event.startswith("grant ["))
    assert all(i < regrant for i, event in enumerate(events) if event.startswith("rule"))


def test_a_dashboard_whose_audience_cannot_be_withdrawn_is_not_imported_over(deployer, target, tmp_path):
    deployer.superset.roles_set = [7]
    keeps = deployer.superset.update_dashboard
    deployer.superset.update_dashboard = lambda d, payload: keeps(d, {"roles": payload["roles"] or [7]})
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "failed" and "after its audience was withdrawn: nothing was imported" in done.error
    assert deployer.superset.imported == []


def test_a_grant_read_back_that_is_not_the_one_decided_is_taken_back(deployer, target, tmp_path):
    keeps = deployer.superset.update_dashboard

    def widens(dashboard_id, payload):                        # a target that adds a role of its own
        keeps(dashboard_id, {"roles": [*payload["roles"], 999]} if payload["roles"] else payload)
    deployer.superset.update_dashboard = widens
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "failed" and "the previous grant was put back" in done.error
    assert deployer.superset.roles_set == []


@pytest.mark.parametrize("missing", ["dataset", "metric"])
def test_a_version_using_what_the_registry_no_longer_has_is_refused_before_any_access(
        deployer, target, tmp_path, missing):
    """Leaving an unknown dataset or metric out would decide the audience over less than the dashboard
    shows — wider — and the read-back would then certify it."""
    if missing == "dataset":
        del deployer.registry.datasets["cem.qoe_daily"]
    else:
        del deployer.registry.metrics["cem.qoe_score"]
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    assert done.status == "failed" and "which the registry no longer has" in done.error
    assert ("dataset cem.qoe_daily" if missing == "dataset" else "metric cem.qoe_score") in done.error
    assert done.step("apply_access").status == "failed"
    assert deployer.superset.roles_set is None and deployer.superset.rules == {}


def test_the_audience_check_is_what_the_access_step_read_back(deployer, target, tmp_path):
    done = _deploy(deployer, target, _version(_package(tmp_path)))
    audience, _ = _wanted(deployer)
    check = next(c for c in done.checks if c["check"] == "audience as SharpSight decided")
    assert check == {"check": "audience as SharpSight decided", "status": "pass",
                     "detail": ", ".join(sorted(f"sharpsight_{r}" for r in audience))}


# ── a list is read to its end ───────────────────────────────────────────────────────────────────────────────
def test_more_than_a_hundred_rules_are_all_read():
    """Superset answers 100 entries a page. A read-back of the first page only called every rule past it
    missing, and a clean-up never saw the stale ones there."""
    import prison
    from aibi.superset_adapter import SupersetAdapter

    held = [{"id": n, "name": f"sharpsight:role{n}:cem.qoe_daily"} for n in range(250)]
    asked = []

    class Answer:
        status_code = 200
        content = b"x"

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class Session:
        def request(self, method, url, timeout=None, params=None, **kwargs):
            query = prison.loads(params["q"])
            # an unordered query is paged unpredictably: every page is asked for in one order
            assert (query["order_column"], query["order_direction"]) == ("name", "asc")
            asked.append(query["page"])
            start = query["page"] * query["page_size"]
            return Answer({"count": len(held), "result": held[start:start + query["page_size"]]})

    adapter = SupersetAdapter("http://bi", "svc", "x")
    adapter._session = Session()
    assert [rule["id"] for rule in adapter.row_rules("sharpsight:")] == list(range(250))
    assert asked == [0, 1, 2]
    held[:] = held[:100]                                      # exactly one full page: no second one is asked
    asked.clear()
    assert len(adapter.row_rules("sharpsight:")) == 100 and asked == [0]


def test_every_list_is_read_in_an_order_its_api_accepts():
    """A list without an order is a LIMIT/OFFSET over no ORDER BY: pages may repeat entries and skip others.
    `id` is refused as an order for row-level rules, so each resource has its own column."""
    import prison
    from aibi.superset_adapter import SupersetAdapter

    seen = {}

    class Session:
        def request(self, method, url, timeout=None, params=None, **kwargs):
            query = prison.loads(params["q"])
            seen[url.split("/api/v1/", 1)[1].rstrip("/")] = (query["order_column"], query["order_direction"])
            return SimpleNamespace(status_code=200, content=b"x", json=lambda: {"count": 0, "result": []})

    adapter = SupersetAdapter("http://bi", "svc", "x")
    adapter._session = Session()
    adapter.row_rules("sharpsight:"), adapter.find_role("r"), adapter.find_database("d")
    adapter.find_dataset(1, "marts", "t"), adapter.find_dashboards("t"), adapter.list_accounts()
    assert seen == {"rowlevelsecurity": ("name", "asc"), "security/roles": ("name", "asc"),
                    "database": ("database_name", "asc"), "dataset": ("table_name", "asc"),
                    "dashboard": ("dashboard_title", "asc"), "security/users": ("username", "asc")}


# ── the kit's own Superset goes through the very same helper ────────────────────────────────────────────────
def test_the_kit_applies_access_with_the_helper_a_deployment_uses(registry, policy, tmp_path, monkeypatch):
    import aibi.deploy
    import aibi.tools
    from aibi.audit import AuditLog
    from aibi.store import Store
    from aibi.tools import ToolContext, Toolbox

    assert aibi.deploy.apply_access is aibi.tools.apply_access
    superset = FakeSuperset()
    shared = _reader_of_another_dashboard(registry)
    superset.rules[3] = {"id": 3, "name": shared, "clause": "region = 'Oriental'",
                         "tables": [{"id": 501}], "roles": [{"id": 1}]}
    store = Store(tmp_path / "s.sqlite")
    tools = Toolbox(registry=registry, policy=policy, gateway=None, dbt=None, git=None, superset=superset,
                    store=store, audit=AuditLog(tmp_path / "t"), visual=None, state_dir=tmp_path,
                    bi_database_name="BI", bi_database_uri="", superset_public_url="", dbt_dev_schema="bi_dev")
    store.save_project("cem-qoe", {"project_id": "cem-qoe", "tenant": "default", "spec": SPEC, "spec_valid": True,
                                   "engine": {"dashboard": {"id": 42}, "datasets": {"cem.qoe_daily": 501}}}, "now")
    calls = []
    real = aibi.tools.apply_access
    monkeypatch.setattr(aibi.tools, "apply_access", lambda *a, **kw: calls.append(a) or real(*a, **kw))
    ctx = ToolContext(identity=policy.identify("dev.user"), request_id="r", ai_model="none", project_id="cem-qoe")
    access = tools.apply_superset_access(ctx)
    assert len(calls) == 1
    audience, rules = _wanted(SimpleNamespace(registry=registry, policy=policy))
    names = sorted(f"sharpsight:{r['role']}:{r['dataset']}" for r in rules)
    assert access == {"roles": audience, "row_rules": names}
    assert 3 in superset.rules, "the kit keeps another dashboard's rule too"
    events = superset.events
    grant = next(i for i, event in enumerate(events) if event.startswith("grant"))
    assert all(i < grant for i, event in enumerate(events) if event.startswith("rule"))
    # and a rule write the kit's Superset refuses leaves the dashboard as it was: not granted
    superset.roles_set, superset.events, superset.rules, superset.refuse_rule = None, [], {}, names[0]
    with pytest.raises(SupersetError):
        tools.apply_superset_access(ctx)
    assert superset.roles_set is None
