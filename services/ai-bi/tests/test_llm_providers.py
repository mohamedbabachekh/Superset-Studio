"""The model seam, held to the one promise that matters: a model may read more freely than the deterministic
planner, but it can never name a governed thing the registry does not have. Everything here runs offline —
no key, no network, no SDK — because the governance is in `coerce_requirement`, not in the provider's I/O.

The real end-to-end, against a model that is actually called, is `AIBI_LLM_PROVIDER=claude bash ai-bi.sh demo`.
"""
from __future__ import annotations

import json

import pytest

from aibi.llm import (ClaudeParser, OpenAIParser, ProviderNotWired, _ModelParser, coerce_requirement,
                      get_parser, vocabulary_card)
from aibi.planner import DeterministicParser


def test_default_provider_is_the_deterministic_planner(registry):
    assert isinstance(get_parser("none", registry), DeterministicParser)
    assert isinstance(get_parser("", registry), DeterministicParser)
    assert isinstance(get_parser("deterministic", registry), DeterministicParser)


def test_an_unknown_provider_is_refused_in_words(registry):
    with pytest.raises(ProviderNotWired) as caught:
        get_parser("llama", registry)
    assert "claude" in str(caught.value) and "openai" in str(caught.value)


def test_claude_and_openai_dispatch_to_their_parsers(registry):
    assert isinstance(get_parser("claude", registry), ClaudeParser)
    assert isinstance(get_parser("anthropic", registry), ClaudeParser)  # an alias
    assert get_parser("claude", registry).model == "claude-opus-5-5"     # a sane default
    assert isinstance(get_parser("openai", registry, model="gpt-x"), OpenAIParser)


def test_openai_needs_a_model_named(registry):
    with pytest.raises(ProviderNotWired) as caught:
        get_parser("openai", registry)
    assert "AIBI_LLM_MODEL" in str(caught.value)


def test_selecting_a_provider_without_its_sdk_says_which_pip_fixes_it(registry):
    # the SDKs are not installed in the test image; selecting one must fail with a remedy, not a traceback
    for provider, package in (("claude", "anthropic"), ("openai", "openai")):
        parser = get_parser(provider, registry, model="m")
        with pytest.raises(ProviderNotWired) as caught:
            parser.parse("Create a dashboard")
        assert f"pip install {package}" in str(caught.value)


def test_the_vocabulary_card_is_public_metadata_only(registry):
    card = vocabulary_card(registry)
    # the ids a model may use are there
    metric_ids = {m["id"] for m in card["metrics"]}
    assert "cem.qoe_score" in metric_ids
    assert "executive" in {t["id"] for t in card["templates"]}
    assert "trend" in card["intents"] and "region" in card["dimensions"]
    # and nothing secret leaks: a card is JSON-serialisable and carries no classification or data value
    text = json.dumps(card)
    assert "classification" not in text
    for metric in card["metrics"]:
        assert set(metric) <= {"id", "name", "synonyms", "unit", "description"}


# ── a model that returns canned JSON: the provider's I/O replaced, the governance kept ──────────────────────

class FakeParser(_ModelParser):
    provider = "fake"

    def __init__(self, registry, answer):
        super().__init__(registry, model="fake-1")
        self._answer = answer

    def _complete(self, system, prompt):
        self.seen_system = system
        return self._answer


def test_a_models_governed_answer_becomes_a_valid_requirement(registry):
    answer = json.dumps({
        "subject": "CEM quality of experience",
        "template": "executive",
        "metrics": ["cem.qoe_score", "cem.active_users"],
        "views": [{"intent": "trend", "phrase": "how QoE moves day to day",
                   "metric": "cem.qoe_score", "time_grain": "P1D"}],
        "filters": ["region", "rat"],
        "brand": None,
        "unresolved": [],
    })
    requirement = FakeParser(registry, answer).parse("Show me how our quality is doing")
    assert requirement.template == "executive"
    assert requirement.metrics == ["cem.qoe_score", "cem.active_users"]
    assert requirement.filters == ["region", "rat"]
    assert requirement.views[0].intent == "trend" and requirement.views[0].time_grain == "P1D"
    assert requirement.parser == "fake:fake-1"
    assert requirement.unresolved == []
    # the governed vocabulary actually reached the model, in its system prompt
    fake = FakeParser(registry, answer)
    fake.parse("x")
    assert "cem.qoe_score" in fake.seen_system and "executive" in fake.seen_system


def test_a_model_that_invents_is_not_trusted_it_is_reported(registry):
    answer = json.dumps({
        "subject": "made up",
        "template": "there_is_no_such_template",
        "metrics": ["cem.qoe_score", "revenue_per_user"],
        "views": [{"intent": "trend", "phrase": "revenue", "metric": "revenue_per_user"},
                  {"intent": "teleport", "phrase": "beam it up"}],
        "filters": ["region", "customer_name"],
        "brand": "megacorp",
    })
    requirement = FakeParser(registry, answer).parse("invent things")
    # the governed things survive
    assert requirement.metrics == ["cem.qoe_score"]
    assert requirement.filters == ["region"]
    assert requirement.template is None
    assert requirement.brand is None
    assert len(requirement.views) == 1  # the unknown intent is dropped, not drawn
    # and every invention is accounted for, not silently swallowed
    joined = " | ".join(requirement.unresolved)
    assert "there_is_no_such_template" in joined
    assert "revenue_per_user" in joined
    assert "customer_name" in joined
    assert "megacorp" in joined
    assert "teleport" in joined or "beam it up" in joined


def test_a_prefer_hint_defaults_from_the_intent_when_the_model_omits_it(registry):
    # the `share` intent prefers a pie in the lexicon; a model that names the intent but no prefer gets it
    answer = json.dumps({"subject": "mix", "metrics": ["cem.active_users"],
                         "views": [{"intent": "share", "phrase": "the split by region",
                                    "metric": "cem.active_users", "dimension": "region"}]})
    requirement = FakeParser(registry, answer).parse("show the split")
    assert requirement.views[0].prefer == "pie"


def test_non_json_from_a_model_is_the_models_fault_and_said_so(registry):
    with pytest.raises(ProviderNotWired):
        FakeParser(registry, "I'm sorry, I can't do that.").parse("x")


def test_a_fenced_json_object_is_still_read(registry):
    fenced = "```json\n" + json.dumps({"subject": "s", "metrics": ["cem.qoe_score"]}) + "\n```"
    requirement = FakeParser(registry, fenced).parse("x")
    assert requirement.metrics == ["cem.qoe_score"]


def test_coerce_is_the_same_discipline_as_the_deterministic_planner(registry):
    # an empty answer still yields a well-formed requirement that honestly says nothing was found
    requirement = coerce_requirement({}, "nothing useful", registry, parser="fake:fake-1")
    assert requirement.metrics == []
    assert any("no registered metric" in u for u in requirement.unresolved)
