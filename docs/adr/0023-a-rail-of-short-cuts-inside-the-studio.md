# ADR 0023 — A rail of short cuts inside the Studio, and Superset's menu is still the authority

*Status: accepted (the owner's call, 2026-10-20).*

## Context

Every visual-builder reference the owner brought shows a left navigation rail: Home, Projects, Datasets,
Metrics, Charts, Dashboards, SQL Lab, AI, Deployments. The Studio had none, on purpose: those destinations
**are** Superset's own pages, already in its top bar, and a second navigation for the same places is two
navigations to keep in step.

That argument was put, with its cost, and the owner chose the rail: the product should feel like one
workspace, not like a page inside somebody else's application.

## Decision

The Studio page carries a narrow rail on its left, in two groups:

- **SharpSight** — Studio (where you are), and the entries of the SharpSight menu **this person may open**.
  The list is built by the add-on with the same `may()` rule the menu itself obeys and put into the page;
  the page never decides who sees what.
- **Superset** — Home, Datasets, Charts, Dashboards, SQL Lab, as plain links. Superset refuses them itself
  if they are not this person's.

It is deliberately quiet — 196px, muted, icons at 16px — and it collapses to a strip of icons under 1240px.

## Consequences

- One address is now reachable two ways. That is the cost, and it is accepted: the rail is a short cut, not
  a router, and nothing in it is a page only the rail can reach.
- The SharpSight half stays correct by construction (one rule, applied server-side). The Superset half is a
  static list, and is **the thing to check at an upgrade**: if Superset moves `/chart/list/`, the rail points
  at nothing while its own menu is right. It is listed in `registry/compatibility.yaml` with the rest.
- The rail does not know what Superset's menu holds for this person, so an entry they cannot open still
  appears (Superset then refuses it). Reading Superset's own menu to filter it would be reading an internal
  structure, which [ADR 0002](0002-native-first-then-the-extension-no-fork.md) is wary of.
