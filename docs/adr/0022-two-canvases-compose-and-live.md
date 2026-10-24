# ADR 0022 — Two canvases: Compose edits the specification, Live frames Superset's own dashboard

*Status: accepted.*

## Context

[ADR 0008](0008-a-tile-is-a-photograph.md) made a tile a photograph, because the alternative was a second
renderer. That is right, and it costs something real: a person composing a dashboard looks at pictures of
charts rather than at charts. Every visual builder they have used shows the thing itself, and the gap is felt
immediately — « I still can't see the same UI ».

The missing option was in front of us: **Superset will render the dashboard, in a frame, inside the
workspace**. Two facts from this repository make it possible, and neither is a guess:

- `?standalone=1` is the address the engine's own browser already loads to photograph a dashboard
  (`visual_qa_agent.py`) — a dashboard without Superset's chrome;
- the kit runs with `TALISMAN_ENABLED = False`, so no `X-Frame-Options` is set, and the page is the same
  origin as the Studio anyway.

## Decision

The canvas has **two modes**, switched in its own header.

| | |
|---|---|
| **Compose** (the default) | the draft: tiles that can be dragged, resized and reordered, each a photograph. This is where a layout is made, because the draft is not in Superset yet and cannot be |
| **Live** | Superset's own standalone dashboard in a frame, with the Studio's chrome drawn over it: a box per chart, its label, its handles when selected. Clicking a box selects that chart in the inspector |

The boxes are **read from the framed dashboard's own DOM** — same origin, so each chart holder is measured
rather than guessed, by the same `data-test-chart-id` the engine's visual QA reads. They follow the frame's
scrolling, and they are re-read as the charts settle.

Live shows what Superset holds. A draft is not in it until it is committed, and the mode says so.

## Consequences

- A person sees real charts, with real data, inside the workspace — and still no second renderer exists.
- The two modes answer two different questions, and neither pretends to be the other: *what will it be* and
  *what is it now*.
- Editing in Live is selection only. Resizing there would be editing a layout against a rendering of the
  previous one; the handles are shown because the selection is real, and the move is made in Compose.
- If the frame is ever another origin — a Superset behind its own domain — the dashboard still renders and
  only the boxes are lost. The code says so and does not break.
- **To verify against a running Superset**: that `?standalone=1` renders inside a frame as it does in a tab,
  and that the product's own Superset (where Talisman may be on) allows same-origin framing. Both are in
  `governance/registry/compatibility.yaml`.
