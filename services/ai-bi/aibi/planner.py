"""From a sentence to a plan.

Two things live here: the reading of a request into a `Requirement`, and the list of steps every generation
goes through. The reading below is deterministic — a governed vocabulary (registry/lexicon.yaml) matched
against the request, longest phrase first — because the choice of a language model is still open. It is
narrower than a model would be and it never invents: what it does not recognise it REPORTS in
`Requirement.unresolved`, and the run says so rather than guessing.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from .models import Requirement, StepRecord, ViewIntent
from .registry import Registry, RegistryError

# The generation, step by step. The numbers are the brief's (section 9) and they are what a run reports.
PLAN: list[tuple[str, str]] = [
    ("parse_requirement", "Parse the requirement"),
    ("find_datasets", "Find certified datasets"),
    ("find_metrics", "Find certified metrics"),
    ("identify_missing_data", "Identify missing data"),
    ("determine_transformations", "Determine transformations"),
    ("generate_dbt_model", "Generate the dbt model"),
    ("dbt_compile", "Compile with dbt"),
    ("dbt_test", "Run dbt tests"),
    ("dbt_run", "Run the model"),
    ("register_dataset", "Register the resulting dataset"),
    ("generate_specification", "Generate the dashboard specification"),
    ("validate_specification", "Validate the specification"),
    ("create_superset_assets", "Create Superset datasets, charts and dashboard"),
    ("retrieve_dashboard_data", "Retrieve the dashboard's data"),
    ("validate_query_results", "Validate the query results"),
    ("render_screenshot", "Render a screenshot"),
    ("visual_qa", "Run visual QA"),
    ("fix_problems", "Fix detected problems"),
    ("re_render", "Re-render"),
    ("store_version", "Store the final version"),
    ("git_commit", "Create the Git commit"),
    ("mark_ready_for_review", "Mark READY_FOR_REVIEW"),
]


def new_steps() -> list[StepRecord]:
    return [StepRecord(number=i + 1, key=key, title=title) for i, (key, title) in enumerate(PLAN)]


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "dashboard"


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in phrase.lower().split()]
    return re.compile(r"(?<![\w])" + r"[\s\-]+".join(words) + r"(?![\w])")


def match_vocabulary(text: str, vocabulary: Iterable[tuple[str, str]]) -> list[tuple[int, int, str]]:
    """(start, end, key) for every phrase found in `text`, the LONGEST phrase winning where two overlap —
    so « degraded users » is one metric and does not also count as a mention of « users »."""
    lowered = text.lower()
    taken = [False] * len(lowered)
    found: list[tuple[int, int, str]] = []
    for phrase, key in sorted(vocabulary, key=lambda item: len(item[0]), reverse=True):
        for match in _phrase_pattern(phrase).finditer(lowered):
            start, end = match.span()
            if any(taken[start:end]):
                continue
            for i in range(start, end):
                taken[i] = True
            found.append((start, end, key))
    return sorted(found)


class DeterministicParser:
    name = "none (deterministic planner)"

    _SUBJECT = re.compile(r"\b(?:create|build|make|generate|design)\s+(?:an?|the)\s+(.+?)\s+dashboard\b", re.I)
    _TEMPLATE = re.compile(r"\b(?:using|use|with|on|from)\s+the\s+([\w\- ]+?)\s+template\b", re.I)
    _SHOW = re.compile(r"^\s*(?:and\s+)?(?:show|display|include|plot|visuali[sz]e|present)\b[:\s]*(.*)$", re.I | re.S)

    def __init__(self, registry: Registry):
        self.registry = registry
        lexicon = registry.lexicon
        self._intents = [(phrase, intent) for intent, body in lexicon.get("intents", {}).items()
                         for phrase in body.get("phrases", [])]
        self._dimensions = [(phrase, column) for column, phrases in lexicon.get("dimensions", {}).items()
                            for phrase in phrases]
        self._grains = [(phrase, grain) for grain, phrases in lexicon.get("time_grains", {}).items()
                        for phrase in phrases]
        self._metrics = [(phrase, metric.id) for metric in registry.metrics.values()
                         for phrase in [metric.name, *metric.synonyms]]

    def parse(self, prompt: str) -> Requirement:
        text = " ".join(prompt.split())
        unresolved: list[str] = []

        subject_match = self._SUBJECT.search(text)
        subject = subject_match.group(1).strip() if subject_match else "Untitled"

        template: Optional[str] = None
        template_match = self._TEMPLATE.search(text)
        if template_match:
            try:
                template = self.registry.template(template_match.group(1)).id
            except RegistryError:
                unresolved.append(f"template « {template_match.group(1)} » is not a registered template")

        metrics = list(dict.fromkeys(key for _, _, key in match_vocabulary(text, self._metrics)))
        if not metrics:
            unresolved.append("no registered metric is named in the request")

        views: list[ViewIntent] = []
        filters: list[str] = []
        for sentence in re.split(r"(?<=[.;!?])\s+|\n+", text):
            clause = sentence.strip().rstrip(".;!?")
            if not clause:
                continue
            if re.search(r"\bfilters?\b", clause, re.I):
                for _, _, column in match_vocabulary(clause, self._dimensions):
                    if column not in filters:
                        filters.append(column)
                continue
            show = self._SHOW.match(clause)
            if not show:
                continue
            for item in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", show.group(1)):
                item = item.strip()
                if not item:
                    continue
                intents = match_vocabulary(item, self._intents)
                if not intents:
                    unresolved.append(f"« {item} » is not a view the planner knows")
                    continue
                dimensions = [key for _, _, key in match_vocabulary(item, self._dimensions)]
                item_metrics = [key for _, _, key in match_vocabulary(item, self._metrics)]
                grains = [key for _, _, key in match_vocabulary(item, self._grains)]
                intent = intents[0][2]
                views.append(ViewIntent(
                    intent=intent, phrase=item,
                    metric=item_metrics[0] if item_metrics else None,
                    # « daily » names a grain for a trend, not a dimension to break it down by
                    dimension=next((d for d in dimensions if not (intent == "trend" and d == "date")), None),
                    time_grain=grains[0] if grains else None,
                    prefer=self.registry.lexicon.get("intents", {}).get(intent, {}).get("prefers"),
                ))

        return Requirement(prompt=prompt, subject=subject, template=template, metrics=metrics, views=views,
                           filters=filters, unresolved=unresolved, parser=self.name)
