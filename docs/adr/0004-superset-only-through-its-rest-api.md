# ADR 0004 — Superset is touched only through its REST API

*Status: accepted.*

## Context

Everything the engine makes — datasets, charts, dashboards, filters, roles, row-level rules, tags, embedded
configuration — exists inside Superset's metadata database. Writing to that database directly is faster and
is what a hurried integration does. The brief forbids it (§23: « Never modify Superset's metadata database »).

## Decision

Every write and every read of a Superset object goes through its REST API, as the service account
(`aibi/superset_adapter.py`). The engine holds no connection to Superset's metadata database.

## Consequences

- An upgrade of Superset that changes a table does not break the engine; one that changes an endpoint does,
  and `ai-bi.sh check-adapter` is what finds out, by driving copy, export, embedded configuration, delete and
  import on a copy.
- Some things are therefore impossible until the API offers them, and are listed rather than hacked.
- The adapter is the only module that knows Superset's vocabulary. Nothing above it says « slice »,
  « form_data » or « position_json ».
- The Studio inherits this: it asks the engine, and the engine asks Superset (ADR 0011).
