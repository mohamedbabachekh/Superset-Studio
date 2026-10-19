# ADR 0008 — A tile is a photograph; Preview is the real dashboard

*Status: accepted.*

## Context

A canvas must show something true in every tile while tiles are moved. Four ways were weighed:

1. a renderer of ours — forbidden by the brief, and it would answer a different question than the dashboard;
2. Superset's own screenshot endpoints — they cannot work in this kit: its image carries no browser;
3. a browser page per chart — a chart's own address in 6.1.0 is Explore, a page built for editing, and
   nothing promises it renders standalone;
4. the dashboard, photographed once, cut up.

## Decision

Tiles are **cut out of one picture of the real dashboard**, taken in the engine's own browser (Playwright and
a headless Chromium, already in its image for visual QA), signed in as its service account
(`aibi/picture.py`). Preview opens the live dashboard in Superset for anything interactive.

## Consequences

- A tile is as old as the picture it came from; the canvas says when it was taken, and « Tiles » takes another.
- One page load gives every tile: the expensive part is paid once, not once per chart.
- A chart that was not drawn is named as missing rather than shown blank.
- Filters, cross-filters and tooltips are not on the canvas, and are not pretended at: that is Preview.
