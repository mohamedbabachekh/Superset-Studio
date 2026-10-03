from __future__ import annotations

import pytest
import yaml

from aibi.dbt_agent import DbtAgent
from aibi.policy_engine import PolicyEngine, PolicyError
from aibi.registry import Registry
from aibi.validation_agent import _HOSTILE

GOOD = "select region, sum(active_users) as users from bi.mart_cem_qoe_daily where rat = '4G' group by region"


@pytest.fixture()
def dev(policy):
    return policy.identify("dev.user")


def test_a_governed_select_is_allowed(policy, dev):
    verdict = policy.validate_sql(GOOD, dev)
    assert verdict.ok, verdict.problems
    assert verdict.datasets == ["cem.qoe_daily"]
    assert all(verdict.checks.values())


def test_a_cte_is_not_mistaken_for_a_table(policy, dev):
    sql = "with daily as (select date, sum(active_users) u from bi.mart_cem_qoe_daily group by date) select * from daily"
    assert policy.validate_sql(sql, dev).ok


@pytest.mark.parametrize("what,statement", _HOSTILE)
def test_everything_hostile_is_refused(policy, dev, what, statement):
    verdict = policy.validate_sql(statement.format(relation="bi.mart_cem_qoe_daily"), dev)
    assert not verdict.ok, f"{what} was let through"


def test_refusals_say_why(policy, dev):
    assert "not a read" in " ".join(policy.validate_sql("delete from bi.mart_cem_qoe_daily", dev).problems) or \
           "only a SELECT" in " ".join(policy.validate_sql("delete from bi.mart_cem_qoe_daily", dev).problems)
    assert "forbidden function: pg_sleep" in " ".join(
        policy.validate_sql("select pg_sleep(5) from bi.mart_cem_qoe_daily", dev).problems)
    assert "not a registered dataset" in " ".join(policy.validate_sql("select * from bi.secrets", dev).problems)
    assert "syntax" in " ".join(policy.validate_sql("selec region frm", dev).problems)


def test_a_user_with_no_grant_reads_nothing(policy):
    guest = policy.identify("guest.user")
    verdict = policy.validate_sql(GOOD, guest)
    assert not verdict.ok and "no access to dataset cem.qoe_daily" in " ".join(verdict.problems)
    assert policy.permitted_datasets(guest) == []
    with pytest.raises(PolicyError):
        policy.require_dataset(guest, "cem.qoe_daily")


def test_an_unknown_user_is_nobody(policy):
    with pytest.raises(PolicyError, match="not a known SharpSight user"):
        policy.identify("mallory")


def test_another_tenant_is_refused_with_the_same_roles(registry_copy):
    path = registry_copy / "security" / "rbac.yaml"
    rbac = yaml.safe_load(path.read_text(encoding="utf-8"))
    rbac["tenants"].append("other")
    rbac["users"]["other.dev"] = {"type": "human", "tenant": "other", "roles": ["bi_developer"]}
    path.write_text(yaml.safe_dump(rbac), encoding="utf-8")
    policy = PolicyEngine(Registry(registry_copy))
    assert not policy.validate_sql(GOOD, policy.identify("other.dev")).ok


def test_a_classified_column_cannot_be_named_or_starred(registry_copy):
    path = registry_copy / "datasets" / "cem.qoe_site_daily.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["columns"]["site_id"]["classification"] = "pii"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    policy = PolicyEngine(Registry(registry_copy))
    dev = policy.identify("dev.user")
    table = "bi.int_cem__qoe_site_daily"
    assert not policy.validate_sql(f"select site_id from {table}", dev).ok
    assert not policy.validate_sql(f"select * from {table}", dev).ok
    assert policy.validate_sql(f"select region, count(*) from {table} group by region", dev).ok
    dataset = policy.registry.dataset("cem.qoe_site_daily")
    assert "site_id" in policy.withheld_columns(dev, dataset)
    assert "site_id" not in policy.readable_columns(dev, dataset)


def test_row_level_policies_are_applied_to_what_runs(registry_copy):
    path = registry_copy / "security" / "rbac.yaml"
    rbac = yaml.safe_load(path.read_text(encoding="utf-8"))
    rbac["row_filters"] = {"bi_developer": {"cem.qoe_daily": "region = 'Oriental'"}}
    path.write_text(yaml.safe_dump(rbac), encoding="utf-8")
    policy = PolicyEngine(Registry(registry_copy))
    dev = policy.identify("dev.user")
    secured = policy.secure_sql(GOOD, dev, 100)
    assert "region = 'Oriental'" in secured
    assert secured.rstrip().endswith("limit 100")
    assert policy.row_policies(dev, "cem.qoe_daily") == "(region = 'Oriental')"
    # the AI's own identity shares the role, so it is restricted too
    assert policy.row_policies(policy.identify("sharpsight-ai"), "cem.qoe_daily") is not None


def test_every_query_runs_inside_a_row_limit(policy, dev):
    assert policy.secure_sql(GOOD, dev, 11).rstrip().endswith("limit 11")


# ── generated dbt models ────────────────────────────────────────────────────────────────────────────────────
def test_the_generated_model_passes_its_own_policy(registry, policy, dev, geo_plan):
    text = DbtAgent(registry).render_sql(geo_plan, "cem-qoe", "req-1")
    verdict = policy.validate_dbt_model(text, dev)
    assert verdict.ok, verdict.problems
    assert set(verdict.datasets) == {"cem.qoe_daily", "cem.region"}
    assert "{{ ref('mart_cem_qoe_daily') }}" in text and "left join joined_1 on base.region = joined_1.region" in text


@pytest.mark.parametrize("bad,why", [
    ("{{ config(materialized='table', post_hook='drop schema bi cascade') }}\nselect * from {{ ref('dim_region') }}", "post_hook"),
    ("{{ config(materialized='view') }}\nselect * from {{ ref('dim_region') }}", "not allowed for a generated model"),
    ("select * from {{ ref('dim_region') }} where 1 = {{ run_query('delete from x') }}", "run_query"),
    ("select * from {{ ref('some_private_model') }}", "not the model of a registered dataset"),
    ("select * from {{ source('cem_landing', 'site_kpi_daily') }}", "ref() and config() only"),
    ("select '{{ env_var(\"DBT_PASSWORD\") }}' as leaked from {{ ref('dim_region') }}", "env_var"),
    ("select * from {{ ref('dim_region') }}; drop table bi.dim_region", "exactly one statement"),
])
def test_a_generated_model_cannot_run_anything_but_its_select(policy, dev, bad, why):
    verdict = policy.validate_dbt_model(bad, dev)
    assert not verdict.ok
    assert why in " ".join(verdict.problems)


def test_a_generated_model_cannot_read_what_its_requester_cannot(registry, policy, geo_plan):
    text = DbtAgent(registry).render_sql(geo_plan, "cem-qoe", "req-1")
    assert not policy.validate_dbt_model(text, policy.identify("guest.user")).ok


# ── approvals ───────────────────────────────────────────────────────────────────────────────────────────────
def test_who_may_approve_what(policy):
    reviewer, manager = policy.identify("lead.reviewer"), policy.identify("release.manager")
    policy.require_approver(reviewer, "UAT", "dev.user")
    policy.require_approver(manager, "PROD", "dev.user")
    with pytest.raises(PolicyError, match="bi.approve_prod"):
        policy.require_approver(reviewer, "PROD", "dev.user")
    with pytest.raises(PolicyError, match="bi.approve_uat"):
        policy.require_approver(policy.identify("dev.user"), "UAT", "dev.user")


def test_the_requester_cannot_approve_their_own_dashboard_for_production(policy):
    with pytest.raises(PolicyError, match="second person"):
        policy.require_approver(policy.identify("release.manager"), "PROD", "release.manager")


def test_the_ai_can_never_approve(registry_copy):
    path = registry_copy / "security" / "rbac.yaml"
    rbac = yaml.safe_load(path.read_text(encoding="utf-8"))
    rbac["users"]["sharpsight-ai"]["roles"] = ["bi_release_manager"]        # even handed every capability
    path.write_text(yaml.safe_dump(rbac), encoding="utf-8")
    policy = PolicyEngine(Registry(registry_copy))
    with pytest.raises(PolicyError, match="only a person"):
        policy.require_approver(policy.identify("sharpsight-ai"), "UAT", "dev.user")
