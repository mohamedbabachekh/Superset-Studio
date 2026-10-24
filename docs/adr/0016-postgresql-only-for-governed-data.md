# ADR 0016 — Governed data is PostgreSQL only, and the matrix says so

*Status: accepted.*

## Context

The kit's Superset has drivers for Oracle, SQL Server, Hive and the CEM's own JDBC dialect — a workbench
should reach what a person has. The engine is a different matter: it generates dbt models, reads SQL with a
dialect, and talks to a warehouse through a read-only role.

## Decision

The engine's governed data is **PostgreSQL only** — the dbt profile, the SQL policy's dialect and the data
gateway. `registry/compatibility.yaml` lists Trino, Spark, Oracle, SQL Server and Hadoop as `not_tested`, and
`/health` reports any component outside its tested versions.

## Consequences

- What the engine says about a dataset is true of a warehouse it has actually been run against.
- A connector is added by testing it, not by declaring it: the matrix is a statement someone made after
  running the kit's verification.
- The kit's own Superset is unaffected: a person may still connect Oracle or the CEM and work in SQL Lab.
