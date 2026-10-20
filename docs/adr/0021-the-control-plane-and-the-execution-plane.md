# ADR 0021 — SharpSight is the control plane; Superset and dbt are the execution plane

*Status: accepted.*

## Context

Two kinds of work exist here. One decides: what may be built, by whom, from which governed data, and whether
what came back is good enough to keep. The other executes: runs a query, draws a chart, renders a dashboard,
builds a model. Mixing them is how a BI platform becomes a fork of its own engine.

## Decision

**SharpSight owns the lifecycle and the policy. Superset and dbt own the execution.** The line is drawn in
code: the control plane never renders and never transforms; the execution plane never decides who may see
what, or whether a version is good.

```
SharpSight          projects · governance · AI orchestration · validation · versions · packages · promotion
      │             (decides, records, refuses)
      ▼
Superset · dbt      SQL, charts, dashboards, models, tests
                    (executes)
```

Promotion between environments moves an **immutable artifact** — the `.ssbi` package cut from the version
that was validated — not a person recreating a dashboard
([ADR 0017](0017-one-superset-so-promotion-is-a-gate.md)).

## Consequences

- Superset can be wiped, upgraded or replaced: what SharpSight knows survives in its own store and in git,
  and a version can be re-applied from there.
- Nothing about a dashboard is true because Superset says so: a version is a record of what was validated,
  and drift is reported rather than assumed away.
- A thing that executes is never asked to decide — which is why the AI's reach is the shape of a
  specification, and why the Studio proposes and the engine judges.
- The cost: two systems to keep in step, and a compatibility matrix to say when they are not.
