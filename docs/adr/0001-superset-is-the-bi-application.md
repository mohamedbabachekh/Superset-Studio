# ADR 0001 — Superset is the BI application; SharpSight extends it

*Status: accepted.*

## Context

The first BI Studio brief described a SharpSight-built authoring interface: its own canvas, its own chart
configuration, its own dashboard rendering. The revised brief (2026-10-03, §0) reversed that, and its first
five rules forbid rebuilding Explore, SQL Lab, the dashboard builder, a second chart configuration engine and
a second renderer.

Behind the rule is an arithmetic that does not change: Superset's chart and dashboard runtime is tens of
thousands of lines, maintained by a project with its own release train. A second one would have to be kept
level with it for ever, and every Superset upgrade would be a migration of ours.

## Decision

Superset is the BI application and the runtime. SharpSight is the enterprise layer around it: projects,
governance, the semantic registry, dbt, git, validation, versions, packages, promotion, audit.

Anything a person can already do in Superset is done in Superset.

## Consequences

- What SharpSight builds is what Superset does not have: the registries, the pipeline, the gates, the
  package, the record. None of it draws a chart.
- Where SharpSight needs a surface of its own, it is for its own objects (a project, a draft, a version) —
  never for a chart or a dashboard.
- An upgrade of Superset is an upgrade, not a port. What it can break is listed in `registry/compatibility.yaml`
  and checked by `ai-bi.sh check-adapter`.
- The cost is that what Superset cannot do, this platform cannot do either, until Superset can — or until a
  thin, documented fork is justified (ADR 0002).
