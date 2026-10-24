# ADR 0007 — The layout belongs to the specification, in Superset's own shape

*Status: accepted.*

## Context

Until the Studio the **template** owned the layout: `position_json` was rebuilt from the template at every
build, and a row moved in Superset's dashboard editor was named and undone. A canvas cannot live with that —
what a person drags has to be what the next build draws.

The brief's own specification (§50.1) places components at `x, y, w, h`. What Superset stores is a tree:
`GRID` → `ROW` → `CHART`, a width in twelfths and a height in grid units. There is nowhere to put a `y`.

## Decision

A `DashboardSpec` may carry a **layout of its own**, in the template's own shape (`LayoutRow`,
`SlotPlacement`) — which is Superset's shape. `aibi/layout.py` is the only place that says which layout is in
force, what a canvas may change about it, and how to read one back out of Superset.

A specification that carries no layout is laid out by its template, exactly as before, so every version stored
until now restores as it was.

## Consequences

- A chart moved on the canvas survives a commit, a build and a reload.
- No free coordinates are offered, because none can be saved; a layout written for Superset and read back is
  the same layout, which a test asserts.
- The template keeps everything else: which slots exist, what each accepts, what is mandatory, the minimum
  widths, the grid, the sections.
- The half of this that is still the owner's: a layout moved in **Superset's own** dashboard editor is still
  named and undone. `aibi/layout.py::from_positions` is the reading it would take to adopt it instead.
