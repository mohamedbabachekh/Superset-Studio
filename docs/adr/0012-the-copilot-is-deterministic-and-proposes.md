# ADR 0012 — The copilot is deterministic, and proposes rather than changes

*Status: accepted.*

## Context

The brief wants an assistant in the workspace (§47.6) that « returns a structured proposal, not directly
mutate production state ». No language model has been chosen for this kit, which calls nobody; `aibi/llm.py`
is the seam where one plugs in.

## Decision

The copilot reads a request with the **same governed vocabulary the planner reads** (`registry/lexicon.yaml`,
the registered metrics, the template's slots), narrowed to the instructions a person gives an existing
dashboard: add a view, take one off, rename, a top N, a time grain, an order, a number format, a width, a
height, an order of rows, and « what is … ».

It answers with a **proposal**: what it would change, what it touches, whether it would pass validation, and
the specification as it would be. It changes nothing. Applying it saves that specification as the draft,
through the same route as a change made with the mouse.

What it does not recognise, it says it does not recognise.

## Consequences

- Everything it can propose is something a specification can carry, so it cannot write SQL, redefine a metric
  or touch a row-level rule — the brief's §51.3 rules hold by construction.
- The deterministic reading already exercises every guard a model will meet: when one is chosen, it produces
  the same `Proposal` and nothing downstream changes.
- It is narrower than a model, visibly: the examples it offers are drawn from the project itself, and a
  refusal names what it does understand.
- The same machinery serves the gallery — the manual path is the same proposal, from a form (§48.3: « do not
  force users to use AI ») — and, since 2026-10-20, the inspector's own controls: changing a chart's metric,
  dimension, dataset or kind from a dropdown asks for a proposal rather than editing the draft, because those
  four are the registry's to allow, not a field to type into. One road in, checked once.
