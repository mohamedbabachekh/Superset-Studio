# ADR 0015 — A section owns whole rows, and a chart does not leave its section

*Status: accepted.*

## Context

A template groups its rows into sections. A section has a title, a description, an owner and filters of its
own, and it is announced by one header above its first row. The canvas lets charts be moved.

## Decision

A layout may reorder the rows the template declares, move a chart between rows **of its own section**, change
widths and heights. It may not invent a row, scatter a section's rows, or move a chart across a section.

## Consequences

- A chart cannot quietly change what it is accountable to, or which section's filters reach it.
- With the templates as they stand — every section is exactly one row — this means a chart cannot be dragged
  to another row at all. The canvas refuses that drag and says why, rather than appearing broken.
- A template with a two-row section makes the move possible with no change to the rule; the rule is already
  tested against one.
- What a row may not do is checked in the engine, not only in the page: `aibi/layout.py::problems`.
