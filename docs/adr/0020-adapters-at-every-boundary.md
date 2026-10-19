# ADR 0020 — Every external system is reached through an adapter

*Status: accepted.*

## Context

The control plane touches Superset, git, dbt, a warehouse, a browser and a filesystem. Each has its own
vocabulary and its own way of breaking, and each changes on its own schedule.

## Decision

**One module per boundary, and nothing above it speaks that system's language.**

| Boundary | Adapter | What it hides |
|---|---|---|
| Superset | `superset_adapter.py` | the REST API, sessions, retries. Nothing above it says « slice », « form_data » or « position_json » |
| the warehouse | `data_gateway.py` | the read-only role, the row-level rules, the withheld columns |
| dbt | `dbt_runner.py`, `dbt_build.py` | the command line, the profiles, the artifacts |
| git | `git_adapter.py` | the repository, the branches, the commits |
| the browser | `visual_qa_agent.py`, `picture.py` | Playwright, sessions, screenshots |
| a language model | `llm.py` | the seam itself: a model produces the same structures the deterministic reading does |

## Consequences

- An upgrade of Superset is read in one file, and `ai-bi.sh check-adapter` drives that file against a real
  instance.
- A second warehouse is a gateway and a dialect, not a rewrite — which is why
  [ADR 0016](0016-postgresql-only-for-governed-data.md) is a statement about testing rather than about design.
- The tests above the adapters need no Superset, no dbt and no warehouse: 212 of them run in fourteen seconds.
- The cost is one indirection on every call, and the discipline to keep it: a shortcut through an adapter is
  what the next upgrade finds.
