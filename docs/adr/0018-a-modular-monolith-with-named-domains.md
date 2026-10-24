# ADR 0018 — A modular monolith with named domains, not microservices

*Status: accepted.*

## Context

The control plane has seven jobs that could each be a service: the project and asset registry, the AI
orchestrator, governance and policy, versions and packages, the validation engine, the release manager, and
the audit record. Splitting them early buys independent deployment and costs a network boundary, a schema
contract and a failure mode per split — paid every day, by a team that does not yet exist.

## Decision

**One backend, with domains that are separate in the code and not on the network.** `engine/aibi/` is
one FastAPI application and one process; inside it, each domain is its own module with its own vocabulary:

| Domain | Module |
|---|---|
| projects and assets | `registry.py`, `store.py` |
| AI orchestration | `planner.py`, `copilot.py`, `pipeline.py`, the agents |
| governance and policy | `policy_engine.py`, `people.py` |
| versions, packages, releases | `lifecycle.py`, `git_adapter.py` |
| validation | `validation_agent.py`, `visual_qa_agent.py` |
| audit | `audit.py` |
| execution adapters | `superset_adapter.py`, `dbt_runner.py`, `data_gateway.py` |

A module is split out when scale, isolation or ownership justifies it — not before.

## Consequences

- One deployment, one log, one transaction boundary; a change that crosses two domains is one commit.
- The boundaries are real even without a network: a domain is reached through its module's own functions, and
  nothing reaches into another's storage.
- Long work does not block the API: a generation, a commit and a picture run on their own thread with a run
  id, which is where a queue would go if one is ever needed.
- The cost is that one bad import can couple two domains quietly. The test suite's shape — one file per
  domain — is what notices.
