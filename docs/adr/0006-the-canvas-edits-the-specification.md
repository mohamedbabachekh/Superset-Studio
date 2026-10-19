# ADR 0006 — The canvas edits the specification, not the dashboard

*Status: accepted.*

## Context

The brief asks for a canvas with drag, resize, sections and undo (§47.4) a few pages after forbidding a second
dashboard builder (§0). It resolves its own contradiction three times — « full replacement: not assumed »
(§46.2), « a controlled subset … and an « open in the native editor » fallback » (§47.4), and the three
options of §49.3 — but the resolution has to be chosen and held to.

## Decision

The canvas is an **editor for the `DashboardSpec`** — the object the engine already validates, versions,
packages and promotes. It never edits a Superset dashboard. Superset renders; the Studio says what should be
rendered; the engine's gates decide whether that may become a version.

## Consequences

- No second renderer, no second chart configuration engine, no second SQL Lab: the brief's first five rules
  hold by construction, not by discipline.
- What the canvas may change is what a specification can say. A colour, a legend, an axis, an ECharts option
  has no place in a specification — so Explore stays open, and what it changes there does not last.
- A dashboard built by hand in Superset is not a project and cannot be adopted by the Studio: a project starts
  with a request.
- The canvas is honest about its subset, and says so in its own drawer (« What this is »).
