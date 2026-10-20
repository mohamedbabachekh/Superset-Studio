# ADR 0025 — Four screens on one page, and the rail switches them

*Status: accepted (the owner's layouts, 2026-10-20).*

## Context

The Studio was one screen: a command bar, three panes and a drawer ([ADR 0005](0005-the-studio-is-a-page-not-a-panel.md),
[ADR 0023](0023-a-rail-of-short-cuts-inside-the-studio.md)). Everything a person could do was on it, which
meant the bar carried nine actions, the drawer carried six tabs, and the governed data — the datasets, the
metrics, the dbt behind them — had nowhere to be read except a tab of an inspector.

The owner then drew four screens, as layouts, and asked for them as they are:

1. **Dashboard Builder** — a left pane of components, the canvas, a right pane of chart properties;
2. **AI Copilot** — the conversation on the left, the dashboard it would make on the right;
3. **Data Modeling & Assets** — the models, the one being read, and its details;
4. **Release & Deployment** — the versions, and a release pipeline beside them.

## Decision

The Studio becomes **four screens on one page**, switched from the rail and by `1` `2` `3` `4`. They are
screens and not pages because they are one project: the draft being edited, the proposal just made and the
version about to be released are the same thing seen from four sides, and a page load between them would
throw away the first.

Everything the one screen held is kept, where the layouts put it:

- the bar splits in two — identity, search and the quiet icons on top; the screen's own tabs and its own
  actions below (the builder: undo, redo, everything-else, Preview, **Save**; the assistant: Preview, Edit
  in Studio, **Apply**; the releases: Export (.ssbi), **+ New Release**);
- **« + New Release » is the commit** and **« Run Validation » is the validation** — the two actions that
  left the bar are the two the release screen is for;
- the drawer's six tabs become the release screen's four, beside the pipeline and the validation results;
- the explorer becomes three tabs (Components, Templates, Outline), and the component library is grouped
  as a builder groups it: Charts, KPIs, Filters, Layout, Others;
- the inspector becomes **Chart Properties**: Data, Style, Interactions, Governance.

Three things in the layouts are **not** drawn as drawn, each for a reason already written down:

- **the data preview** under a model (layout 3) is a table of **columns** — name, type, role, classification,
  and whether this person may read it — and says why there is no sample
  ([ADR 0013](0013-no-data-preview-in-the-studio.md)). The engine can sample a dataset; the Studio is given
  no way to ask;
- **Run / Test / Build** on a dbt model (layout 3) is **Open dbt docs**: a governed model is built by the
  engine before any dashboard asks for it, and a page that could rebuild it would be a page that could
  change what a dashboard means;
- **the release pipeline** (layout 4) is a picture, not a set of buttons. DEV → UAT → PROD shows where each
  version stands; promoting stays on the command line, with a second person
  ([ADR 0010](0010-a-commit-goes-through-the-pipeline.md), [ADR 0017](0017-one-superset-so-promotion-is-a-gate.md)).

Two additions the layouts do not show, kept because they carry what the layouts have no room for: a fourth
inspector tab (**Governance**), and a rail group (**More in SharpSight**) for the record dashboards this
person may open.

## Consequences

- A person can be on a screen where the draft is not visible (the data, the releases) while it is unsaved.
  The draft state is therefore in the **top bar**, on every screen, and the beforeunload guard is unchanged.
- Four screens are four things to keep right; the browser harness (`studio/preview.py --check`) grew to
  eighty-two promises, and holds each screen to what it must show — including that **no row of data appears
  anywhere**, which is the easiest of these to lose by accident.
- Nothing new is asked of the engine except one route that already existed —
  `GET /datasets/{id}`, which carries a dataset's columns — added to the gateway's allowlist
  ([ADR 0011](0011-the-gateway-token-has-an-allowlist.md)).
- A redraw now restores focus and the caret, because a page with four screens and a search box in the bar is
  typed in far more than one with a search box in a pane.
