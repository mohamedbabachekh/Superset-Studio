"""Whose colours a dashboard wears: SharpSight's own by default, a client's when the request says so."""
from __future__ import annotations

from aibi.cli import DEMO
from aibi.dataset_agent import DatasetAgent
from aibi.models import DashboardSpec
from aibi.planner import DeterministicParser

BLUE, MAGENTA = "#0972BC", "#E6007E"


def _specified(registry, dashboards, semantic, policy, requirement):
    identity = policy.identify("dev.user")
    template = registry.template(requirement.template)
    metrics, _ = semantic.find_metrics(requirement)
    agent = DatasetAgent(registry, semantic)
    candidates = agent.discover(requirement, policy.permitted_datasets(identity), metrics, "cem-qoe")
    drafts, _ = dashboards.draft(requirement, template, metrics)
    for draft in drafts:
        dataset, _gap = agent.resolve(draft.need(), candidates)
        if dataset is not None:
            draft.dataset = dataset.id
            draft.dimension = draft.column or (dataset.columns_with_role(draft.role)[0] if draft.role else None)
    return dashboards.specify("cem-qoe", requirement, template, [d for d in drafts if d.dataset]), template


def test_sharpsight_s_own_colours_lead(registry):
    tokens = registry.tokens
    assert tokens.colors["primary"] == BLUE and tokens.default_brand == "sharpsight"
    assert tokens.brands["sharpsight"].accent == tokens.colors["primary"]
    assert tokens.brands["inwi"].accent == MAGENTA and tokens.brands["inwi"].color_scheme == "inwi"
    # every template draws in SharpSight's series; the client's is nobody's default
    assert {template.theme.color_scheme for template in registry.templates.values()} == {"sharpsight"}
    assert all(brand.color_scheme in tokens.color_schemes for brand in tokens.brands.values())


def test_a_request_wears_a_client_s_colours_only_when_it_says_so(registry, dashboards, semantic, policy):
    parser = DeterministicParser(registry)
    plain = parser.parse(DEMO)
    assert plain.brand is None
    (spec, notes), template = _specified(registry, dashboards, semantic, policy, plain)
    assert spec.theme.brand is None and dashboards.scheme(spec, template) == "sharpsight"
    assert BLUE in dashboards.css(template) and MAGENTA not in dashboards.css(template)

    for wording in ("In inwi's colours.", "Use the inwi brand.", "In inwi’s colors."):
        asked = parser.parse(DEMO + "\n\n" + wording)
        assert asked.brand == "inwi" and not asked.unresolved, wording
    (spec, notes), template = _specified(registry, dashboards, semantic, policy, asked)
    assert spec.theme.brand == "inwi" and dashboards.scheme(spec, template) == "inwi"
    assert any("colours of inwi" in note for note in notes)
    worn = dashboards.css(template, spec.theme.brand)
    assert MAGENTA in worn and BLUE not in worn                   # the executive template's accent is « primary »
    # a brand is an accent and a series: a template whose accent is another token keeps it
    geographic = registry.template("geographic")
    assert dashboards.css(geographic, "inwi") == dashboards.css(geographic)
    # a series named outright in the specification wins over the brand's
    spec.theme.color_scheme = "supersetColors"
    assert dashboards.scheme(spec, template) == "supersetColors"


def test_a_brand_nobody_registered_is_refused_before_superset_is_called(registry, dashboards, policy):
    spec = DashboardSpec(dashboard={"id": "t", "template": "executive", "title": "T"}, theme={"brand": "acme"},
                         components=[{"slot": "kpi_1", "type": "big_number", "metric": "cem.qoe_score", "dataset": "cem.qoe_daily"}])
    report = dashboards.validate(spec, policy.identify("dev.user"))
    assert any(check.name == "theme uses approved tokens" and check.status == "fail" for check in report.checks)
