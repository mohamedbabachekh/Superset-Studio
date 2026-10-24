"""Where a language model plugs in — and the two that now may.

WHICH MODEL IS STILL AN OPEN DECISION, AND THE KIT STILL CALLS NOBODY BY DEFAULT. `AIBI_LLM_PROVIDER=none`
(the default) runs the deterministic planner, which needs no network, no key and no new dependency — the kit
ships and runs exactly as before. This file only makes it *possible* to put a model in front of that planner,
for a deployment that wants one. No model is bundled; none is chosen for anyone.

Two providers are wired: `claude` (Anthropic) and `openai`. Each is a thin reading step and nothing more:

  · it is handed the request and the registries' PUBLIC vocabulary — the names, synonyms, units and
    descriptions of the governed metrics, templates, intents, dimensions and brands. Never a credential,
    never a row of data, never a classified value;
  · it answers with the SAME `Requirement` (aibi/models.py) the deterministic planner produces — template,
    metrics, views, filters, all in the registry's own terms;
  · whatever it names that the registry does not know is NOT trusted: `coerce_requirement` drops it and
    records it in `Requirement.unresolved`, exactly as the deterministic planner reports what it could not
    place. A model cannot smuggle an ungoverned metric, template or column past this step, so everything
    downstream — policy, dbt, the specification, validation, review — is unchanged and does not know, or
    care, which of the three parsers wrote the requirement.

The provider SDKs (`anthropic`, `openai`) are OPTIONAL and imported only when their provider is selected, so
the offline default carries no extra weight. Selecting a provider whose SDK is absent says so, and names the
one `pip install` that fixes it.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional, Protocol

from .models import Requirement, ViewIntent
from .registry import Registry


class RequirementParser(Protocol):
    #: what the audit log records as « AI model » for every action taken on this parser's reading
    name: str

    def parse(self, prompt: str) -> Requirement: ...


class ProviderNotWired(RuntimeError):
    pass


# ── the governed vocabulary handed to a model, and the net under its answer ─────────────────────────────────

def vocabulary_card(registry: Registry) -> dict[str, Any]:
    """The registry's PUBLIC vocabulary, as a model is allowed to see it: what things are called and what
    they mean, never a credential and never a value from the data. This is the whole of what a model learns
    about the governed world; everything it returns is checked back against it."""
    lexicon = registry.lexicon
    return {
        "metrics": [
            {"id": m.id, "name": m.name, "synonyms": list(m.synonyms),
             "unit": m.unit, "description": m.documentation or m.description}
            for m in registry.metrics.values()
        ],
        "templates": [
            {"id": t.id, "name": t.template.name, "aliases": list(t.template.aliases),
             "description": t.template.description}
            for t in registry.templates.values()
        ],
        "intents": {key: body.get("phrases", []) for key, body in lexicon.get("intents", {}).items()},
        "dimensions": {column: phrases for column, phrases in lexicon.get("dimensions", {}).items()},
        "time_grains": {grain: phrases for grain, phrases in lexicon.get("time_grains", {}).items()},
        "brands": sorted(registry.tokens.brands),
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def coerce_requirement(data: dict[str, Any], prompt: str, registry: Registry, *, parser: str) -> Requirement:
    """A model's JSON, read back into a governed `Requirement`. Every id is checked against the registry;
    anything it does not know is dropped and reported in `unresolved`, so the reading can be freer than the
    deterministic planner's without ever being able to INVENT a governed thing. The same discipline the
    deterministic parser follows, applied to a model's words."""
    unresolved: list[str] = [str(u) for u in _as_list(data.get("unresolved"))]
    assumptions: list[str] = [str(a) for a in _as_list(data.get("assumptions"))]

    known_dimensions = set(registry.lexicon.get("dimensions", {}))
    known_intents = set(registry.lexicon.get("intents", {}))
    known_grains = set(registry.lexicon.get("time_grains", {}))

    subject = str(data.get("subject") or "Untitled").strip() or "Untitled"

    template: Optional[str] = None
    named_template = data.get("template")
    if named_template:
        if str(named_template) in registry.templates:
            template = str(named_template)
        else:
            unresolved.append(f"template « {named_template} » is not a registered template")

    metrics: list[str] = []
    for metric in _as_list(data.get("metrics")):
        metric = str(metric)
        if metric in registry.metrics:
            if metric not in metrics:
                metrics.append(metric)
        else:
            unresolved.append(f"metric « {metric} » is not a registered metric")
    if not metrics:
        unresolved.append("no registered metric is named in the request")

    filters: list[str] = []
    for column in _as_list(data.get("filters")):
        column = str(column)
        if column in known_dimensions:
            if column not in filters:
                filters.append(column)
        else:
            unresolved.append(f"filter « {column} » is not a known dimension")

    views: list[ViewIntent] = []
    for raw in _as_list(data.get("views")):
        if not isinstance(raw, dict):
            continue
        intent = str(raw.get("intent") or "").strip()
        phrase = str(raw.get("phrase") or intent).strip()
        if intent not in known_intents:
            unresolved.append(f"« {phrase or intent} » is not a view the engine knows")
            continue
        metric = raw.get("metric")
        metric = str(metric) if metric and str(metric) in registry.metrics else None
        if raw.get("metric") and metric is None:
            unresolved.append(f"metric « {raw.get('metric')} » of view « {phrase} » is not registered")
        dimension = raw.get("dimension")
        dimension = str(dimension) if dimension and str(dimension) in known_dimensions else None
        grain = raw.get("time_grain")
        grain = str(grain) if grain and str(grain) in known_grains else None
        prefer = raw.get("prefer")
        prefer = str(prefer) if prefer else registry.lexicon.get("intents", {}).get(intent, {}).get("prefers")
        views.append(ViewIntent(intent=intent, phrase=phrase or intent, metric=metric,
                                dimension=dimension, time_grain=grain, prefer=prefer))

    brand: Optional[str] = None
    named_brand = data.get("brand")
    if named_brand:
        if str(named_brand) in registry.tokens.brands:
            brand = str(named_brand)
        else:
            unresolved.append(f"brand « {named_brand} » is not a brand of the design tokens")

    # keep the order stable and each complaint once
    unresolved = list(dict.fromkeys(unresolved))
    return Requirement(prompt=prompt, subject=subject, template=template, metrics=metrics, views=views,
                       filters=filters, unresolved=unresolved, assumptions=assumptions, brand=brand,
                       parser=parser)


_SYSTEM = """\
You read a request for a business-intelligence dashboard and return ONLY a JSON object describing it in the \
governed vocabulary below. You invent nothing: use only the ids given here. If the request asks for something \
the vocabulary does not contain, name it as a short sentence in "unresolved" instead of guessing an id.

The JSON object has exactly these keys:
  subject:   a short noun phrase for what the dashboard is about (string)
  template:  the id of the template to use, or null
  metrics:   a list of metric ids
  views:     a list of {intent, phrase, metric, dimension, time_grain, prefer}, where intent is one of the \
intent keys, metric/dimension are ids or null, time_grain is a grain key or null, prefer is a visualization \
hint or null, and phrase is the words from the request this view came from
  filters:   a list of dimension column names to filter by
  brand:     a brand id whose colours were asked for, or null
  unresolved: a list of short sentences, one per thing the request asks for that is not in the vocabulary

Return the JSON object and nothing else — no prose, no code fence.

GOVERNED VOCABULARY (the only ids you may use):
"""


class _ModelParser:
    """The shape every provider shares: hand the model the request and the vocabulary, read its JSON back
    through `coerce_requirement`. A provider supplies `_complete`; the governance is here, once."""
    provider = ""

    def __init__(self, registry: Registry, *, model: str, api_key: str = "", base_url: str = "") -> None:
        self.registry = registry
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.name = f"{self.provider}:{model}"

    def _system(self) -> str:
        return _SYSTEM + json.dumps(vocabulary_card(self.registry), ensure_ascii=False, indent=2)

    def _complete(self, system: str, prompt: str) -> str:  # pragma: no cover — provider-specific I/O
        raise NotImplementedError

    def parse(self, prompt: str) -> Requirement:
        raw = self._complete(self._system(), prompt)
        return coerce_requirement(_extract_json(raw), prompt, self.registry, parser=self.name)


def _extract_json(text: str) -> dict[str, Any]:
    """The model is asked for a bare JSON object; be forgiving if it wraps it in a fence or a sentence, and
    clear about the one failure that is the model's and not ours."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text[3:] else text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ProviderNotWired("the model did not return a JSON object")
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ProviderNotWired(f"the model's answer is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProviderNotWired("the model returned JSON that is not an object")
    return parsed


class ClaudeParser(_ModelParser):
    """Anthropic's Claude, through the official `anthropic` SDK. The key is `AIBI_LLM_API_KEY` or, failing
    that, `ANTHROPIC_API_KEY`; the model defaults to `claude-opus-5-5`."""
    provider = "claude"

    def _complete(self, system: str, prompt: str) -> str:  # pragma: no cover — network
        try:
            import anthropic
        except ImportError as exc:  # noqa: F841
            raise ProviderNotWired(
                "AIBI_LLM_PROVIDER=claude needs the Anthropic SDK: `pip install anthropic`."
            ) from exc
        key = self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ProviderNotWired(
                "AIBI_LLM_PROVIDER=claude needs a key: set AIBI_LLM_API_KEY or ANTHROPIC_API_KEY."
            )
        kwargs: dict[str, Any] = {"api_key": key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        client = anthropic.Anthropic(**kwargs)
        message = client.messages.create(
            model=self.model, max_tokens=2048, system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in message.content if getattr(block, "type", "") == "text")


class OpenAIParser(_ModelParser):
    """OpenAI (and OpenAI-compatible endpoints, through `AIBI_LLM_BASE_URL`), via the official `openai` SDK.
    The key is `AIBI_LLM_API_KEY` or `OPENAI_API_KEY`; the model must be named (`AIBI_LLM_MODEL`), since this
    kit chooses none for anyone."""
    provider = "openai"

    def _complete(self, system: str, prompt: str) -> str:  # pragma: no cover — network
        try:
            import openai
        except ImportError as exc:  # noqa: F841
            raise ProviderNotWired(
                "AIBI_LLM_PROVIDER=openai needs the OpenAI SDK: `pip install openai`."
            ) from exc
        key = self.api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise ProviderNotWired(
                "AIBI_LLM_PROVIDER=openai needs a key: set AIBI_LLM_API_KEY or OPENAI_API_KEY."
            )
        kwargs: dict[str, Any] = {"api_key": key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        client = openai.OpenAI(**kwargs)
        completion = client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        return completion.choices[0].message.content or ""


_DEFAULT_MODEL = {"claude": "claude-opus-5-5", "openai": ""}


def get_parser(provider: str, registry: Registry, *, model: str = "", api_key: str = "",
               base_url: str = "") -> RequirementParser:
    provider = (provider or "none").strip().lower()
    if provider in ("", "none", "deterministic"):
        from .planner import DeterministicParser

        return DeterministicParser(registry)

    aliases = {"claude": "claude", "anthropic": "claude", "openai": "openai"}
    canonical = aliases.get(provider)
    if canonical is None:
        raise ProviderNotWired(
            f"AIBI_LLM_PROVIDER={provider}: unknown provider. Use `none` (the deterministic planner), "
            "`claude` or `openai`."
        )
    chosen = model or _DEFAULT_MODEL[canonical]
    if not chosen:
        raise ProviderNotWired(
            f"AIBI_LLM_PROVIDER={provider} needs a model: set AIBI_LLM_MODEL."
        )
    parser_class = {"claude": ClaudeParser, "openai": OpenAIParser}[canonical]
    return parser_class(registry, model=chosen, api_key=api_key, base_url=base_url)
