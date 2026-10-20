# ADR 0002 — Native first, then the extension — and no fork

*Status: accepted.*

## Context

The revised brief asks (§27) for a decision tree: a public extension API first; a supported API next; then
« do not build »; and only then a thin, isolated fork. On 2026-10-04 the extension system of the pinned image
was read — `apache-superset-core 0.1.0rc3`, behind `ENABLE_EXTENSIONS`, off by default — and what it offers
is in [`../ai-bi.md` §3](../ai-bi.md): contribution points **in SQL Lab only**, commands, a backend API.
Nothing on dashboards, in Explore or on datasets; no chat; no semantic-layer module; no `fastmcp`.

## Decision

**Native first, then the extension, and no fork.** In order:

1. Superset's own features, used as they are (tags, badges, roles, dashboards, Explore, SQL Lab).
2. What a `.supx` extension may contribute on the pinned version: a backend API, commands, a SQL Lab panel.
3. Supported application surfaces that are not the extension API — a Flask blueprint page, a menu entry, the
   configuration layer (ADR 0005). Each one is recorded as something to re-check at every upgrade.
4. A fork: not taken.

## Consequences

- Five of SharpSight's records are Superset dashboards, not screens of ours; the menu is Flask-AppBuilder's;
  the panel is in SQL Lab because that is where a panel may be.
- Everything used that is not the extension API is listed in [`../ai-bi.md` §3](../ai-bi.md) (« what is used
  that is not the extension API »), with what it relies on.
- When Superset's extension API grows — the upstream pull request for dashboards and Explore was open on
  2026-10-04 — some of what is in (3) can move to (2). That is a cheaper move than un-forking would have been.
