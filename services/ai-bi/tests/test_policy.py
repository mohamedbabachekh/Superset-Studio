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
    assert policy.row_policies(dev, "cem.qoe_daily") == "region = 'Oriental'"
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


# ── who a dashboard is for, and which rows they see ─────────────────────────────────────────────────────────
EAST = "region in ('Oriental', 'Fès - Boulemane', 'Taza - Al Hoceima - Taounate')"
GENERATED = "cem.cem_qoe__qoe_daily_region_iso"


def test_a_dashboard_is_for_the_roles_granted_every_one_of_its_datasets(with_generated, policy):
    datasets = [with_generated.dataset("cem.qoe_daily"), with_generated.dataset(GENERATED)]
    assert policy.audience(datasets) == ["bi_developer", "bi_release_manager", "bi_reviewer", "cem_analyst",
                                         "cem_regional_analyst"]
    # an auditor reads the trail, a viewer reads nothing of CEM: neither is granted the dashboard
    assert not {"auditor", "viewer"} & set(policy.audience(datasets))
    assert policy.audience([]) == []


def test_a_role_granted_only_some_of_the_datasets_is_not_in_the_audience(registry_copy):
    path = registry_copy / "security" / "rbac.yaml"
    rbac = yaml.safe_load(path.read_text(encoding="utf-8"))
    rbac["roles"]["mart_only"] = {"capabilities": ["bi.read"], "datasets": ["cem.qoe_daily"], "classifications": ["public"]}
    path.write_text(yaml.safe_dump(rbac, allow_unicode=True), encoding="utf-8")
    registry = Registry(registry_copy)
    policy = PolicyEngine(registry)
    assert "mart_only" in policy.audience([registry.dataset("cem.qoe_daily")])
    assert "mart_only" not in policy.audience([registry.dataset("cem.qoe_daily"), registry.dataset("cem.region")])


def test_a_row_policy_follows_a_dataset_into_what_is_derived_from_it(with_generated, policy):
    """The generated map dataset is the mart plus a column. Without this, the regional analyst's map would
    show the whole country while every other chart showed three regions."""
    mart, generated = with_generated.dataset("cem.qoe_daily"), with_generated.dataset(GENERATED)
    assert policy.role_row_policy("cem_regional_analyst", mart) == EAST
    assert policy.role_row_policy("cem_regional_analyst", generated) == EAST
    assert policy.role_row_policy("cem_analyst", generated) is None
    assert policy.row_rules([mart, generated]) == [
        {"role": "cem_regional_analyst", "dataset": "cem.qoe_daily", "clause": EAST},
        {"role": "cem_regional_analyst", "dataset": GENERATED, "clause": EAST}]
    east = policy.identify("east.analyst")
    secured = policy.secure_sql(f"select region_iso, count(*) from {generated.relation} group by region_iso", east, 50)
    assert "region IN ('Oriental'" in secured and generated.relation in secured


def test_a_derived_dataset_that_lost_the_policys_column_shows_the_role_nothing(with_generated, policy):
    generated = with_generated.dataset(GENERATED)
    blind = generated.model_copy(update={
        "id": "cem.cem_qoe__no_region", "source_model": "aigen_cem_qoe__no_region", "grain": ["date", "rat"],
        "columns": {k: v for k, v in generated.columns.items() if k != "region"}})
    with_generated.register_generated(blind)
    assert policy.role_row_policy("cem_regional_analyst", blind) == "1 = 0"
    assert policy.role_row_policy("cem_analyst", blind) is None


def test_an_unrestricted_role_does_not_lift_a_restricted_one(registry_copy):
    """The BI engine applies a row-level rule to whoever holds its role, whatever else they hold. The data
    gateway must give the same rows."""
    path = registry_copy / "security" / "rbac.yaml"
    rbac = yaml.safe_load(path.read_text(encoding="utf-8"))
    rbac["users"]["both.roles"] = {"type": "human", "tenant": "default", "roles": ["cem_regional_analyst", "cem_analyst"]}
    rbac["users"]["two.regions"] = {"type": "human", "tenant": "default", "roles": ["cem_regional_analyst", "south"]}
    rbac["roles"]["south"] = {"capabilities": ["bi.read"], "datasets": ["cem.*"], "classifications": ["public"]}
    rbac["row_filters"]["south"] = {"cem.qoe_daily": "region = 'Oued el Dahab'"}
    path.write_text(yaml.safe_dump(rbac, allow_unicode=True), encoding="utf-8")
    registry = Registry(registry_copy)
    policy = PolicyEngine(registry)
    mart = registry.dataset("cem.qoe_daily")
    assert policy.row_policy(policy.identify("both.roles"), mart) == EAST
    assert policy.row_policy(policy.identify("two.regions"), mart) == f"({EAST}) or (region = 'Oued el Dahab')"
    assert policy.row_policy(policy.identify("cem.analyst"), mart) is None
    # an auditor's role grants no CEM dataset, so it neither restricts nor lifts
    assert policy.row_policy(policy.identify("lead.reviewer"), mart) is None


def test_validation_reports_access_that_is_not_what_sharpsight_grants(with_generated, policy):
    from aibi.data_gateway import QueryLog
    from aibi.models import DashboardSpec
    from aibi.sql_agent import SqlAgent
    from aibi.validation_agent import ValidationAgent

    agent = ValidationAgent(with_generated, policy, SqlAgent(), store=None)
    spec = DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"}, components=[
        {"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"}])
    dev = policy.identify("dev.user")

    def checks(**access):
        base = {"differences": [], "granted": {"roles": ["cem_analyst"], "row_rules": ["r"]},
                "outsider": {"account": "sharpsight-probe-outsider", "dashboard": 404, "data": {"cem.qoe_daily": 403}},
                "outsider_refused": True}
        report = agent.security(spec, dev, QueryLog(), None, access={**base, **access})
        return {c.name: c for c in report.checks}

    good = checks()
    assert good["BI engine access is what SharpSight grants"].status == "pass"
    assert good["the BI engine refuses an account with no SharpSight role"].status == "pass"

    drifted = checks(differences=["the dashboard is granted to no role; SharpSight grants it to sharpsight_cem_analyst"])
    assert drifted["BI engine access is what SharpSight grants"].status == "fail"
    assert "granted to no role" in drifted["BI engine access is what SharpSight grants"].detail

    open_door = checks(outsider={"account": "sharpsight-probe-outsider", "dashboard": 200, "data": {"cem.qoe_daily": 200}},
                       outsider_refused=False)
    assert open_door["the BI engine refuses an account with no SharpSight role"].status == "fail"


def test_what_counts_as_refused_and_as_served():
    from aibi.tools import Toolbox

    assert Toolbox.refused({"dashboard": 404, "data": {"a": 403, "b": 403}})
    assert not Toolbox.refused({"dashboard": 404, "data": {"a": 200}})          # the door shut, the window open
    assert not Toolbox.refused({"dashboard": 200, "data": {"a": 403}})
    assert Toolbox.served({"dashboard": 200, "data": {"a": 200}, "rows": {"a": {"same": True}}})
    assert not Toolbox.served({"dashboard": 200, "data": {"a": 200}, "rows": {"a": {"same": False}}})   # too many rows, or too few
    assert not Toolbox.served({"dashboard": 404, "data": {"a": 200}, "rows": {}})
    assert Toolbox.engine_role("cem_analyst") == "sharpsight_cem_analyst"
    assert Toolbox.rule_name("cem_regional_analyst", "cem.qoe_daily") == "sharpsight:cem_regional_analyst:cem.qoe_daily"
