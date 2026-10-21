"""Where a language model plugs in — and, today, the statement that none does.

WHICH MODEL IS AN OPEN DECISION (2026-10-03: left for later, on purpose). The engine therefore runs on a
deterministic planner, and this file is the whole surface a model will ever get:

  · it is handed a request and the registries' PUBLIC metadata — names, descriptions, synonyms. Never a
    credential, never a row from a classified column;
  · it answers with a `Requirement` (aibi/models.py): template, metrics, views and filters, all named in the
    registry's own terms. That structure is validated exactly as the deterministic planner's is;
  · anything it wants to DO, it asks the tool layer for (aibi/tools.py). It has no other hands.

So wiring a model means writing one class with a `parse()` method and naming it in AIBI_LLM_PROVIDER. Nothing
downstream — policy, dbt, the specification, validation, review — knows or cares which of the two wrote the
requirement, which is also why the deterministic path is a fair test of everything a model will go through.
"""
from __future__ import annotations

from typing import Protocol

from .models import Requirement
from .registry import Registry


class RequirementParser(Protocol):
    #: what the audit log records as « AI model » for every action taken on this parser's reading
    name: str

    def parse(self, prompt: str) -> Requirement: ...


class ProviderNotWired(RuntimeError):
    pass


def get_parser(provider: str, registry: Registry) -> RequirementParser:
    if provider in ("", "none", "deterministic"):
        from .planner import DeterministicParser

        return DeterministicParser(registry)
    raise ProviderNotWired(
        f"AIBI_LLM_PROVIDER={provider}: no model provider is wired in yet. Set it to `none` to use the "
        "deterministic planner, or add a RequirementParser for it in aibi/llm.py."
    )
