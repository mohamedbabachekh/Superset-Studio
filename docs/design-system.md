# The Studio's design system

What the workspace is built from, and the rules it obeys. It is small on purpose: a design system for one
workspace inside someone else's application is a liability when it grows past what that workspace needs.

The references it was drawn against — **Linear** for navigation and the keyboard, **Figma** for the shape of
the editor (asset tree, canvas, contextual properties), **Retool** for how a component is wired to governed
data, and an **AI that lives in the editor** rather than beside it — are references, not interfaces to copy.
Where this departs from them, §6 says so and why.

---

## 1 · One source for the values

Every colour comes from `registry/design/tokens.yaml`, which is also what the generated dashboards and
Superset's own theme wear. The stylesheet restates them as CSS custom properties and **never writes a colour
anywhere else** — which is what makes a second theme possible at all.

| Token | Light | Dark | What it is for |
|---|---|---|---|
| `--ss-primary` | `#0972BC` | `#4AA3E0` | the action colour: a primary button, a selection, a focus ring |
| `--ss-primary-wash` / `-line` | `#EAF4FC` / `#BFDDF4` | `#11283E` / `#2A4D6B` | the quiet ground and border of anything selected |
| `--ss-success` / `-wash` / `-ink` | `#37A15E` | `#4CB97A` | passed, certified, committed |
| `--ss-warning` / `-wash` / `-ink` | `#F2B72D` | `#E8B64A` | not certified, unsaved, « review needed » |
| `--ss-error` / `-wash` / `-ink` | `#D64545` | `#E46A6A` | failed, refused, destructive |
| `--ss-text` / `-muted` / `-faint` | `#1F2430` / `#5B6472` / `#8A94A6` | `#E6E9EF` / `#A4ADBD` / `#788296` | three weights of type, and no more |
| `--ss-surface` / `-sunken` / `-raised` | `#FFFFFF` / `#F5F6F9` / `#FCFDFE` | `#151A24` / `#0F131B` / `#1A2130` | a card, the ground it sits on, a header inside it |
| `--ss-border` / `-line` | `#E3E6EC` / `#F0F2F5` | `#2A3040` / `#1E2430` | a card's edge, and a divider inside one |
| `--ss-radius` / `-sm` / `-gap` | `12px` / `8px` / `18px` | — | one corner, one smaller corner, one gap |
| `--ss-shadow` / `-lg` | two steps | two steps | a card, and something floating over it |

**Naming.** The brief's own names map onto these: *Midnight* is `--ss-text` (and the dark theme's ground),
*Action* is `--ss-primary`, *Canvas* is `--ss-sunken`, and *Success / Warning / Error* keep their names. The
tokens are not renamed in code, because the file they come from is shared with the dashboards and with
Superset's theme: one source matters more than one vocabulary.

**Type** is Inter at 13px/1.55, with 11px for labels and 10px for the small upper-case headings. Three sizes,
three weights, no more. **Spacing** is a 4px scale, used through `--ss-gap` and the handful of paddings below.

**Light is the default**; dark is one button in the bar (`◐`), kept per person in their browser. The page is
drawn from the tokens, so the two themes are one stylesheet and nothing is themed twice.

## 2 · The components

The six groups the direction names, and what each is in this codebase. Everything is a class on plain DOM —
there is no component framework ([ADR 0014](adr/0014-no-build-step-and-no-second-react.md)).

| Group | Here | Where |
|---|---|---|
| **Navigation** | the rail (`.ss-rail`), the areas of the workspace (`.ss-areas`: Design, Data, Filters, Interactions), the command bar (`.ss-bar`, `.ss-crumb`, `.ss-group`), the command palette (`.ss-palette`), the explorer tree (`.ss-tree`), tabs (`.ss-tabs`) | `view.js` · `ui.js::palette` |
| **Data** | the metric picker (`.ss-pick`), the dataset and metric lists with certification marks (`.ss-cert`), the filter picker, the lineage panel | the gallery, the explorer, the dashboard inspector's *Filters*, the inspector's *Data & dbt* |
| **Builder** | the chart-kind row (`.ss-kindrow`), the device widths (`.ss-devices`), the component shelf (`.ss-cards`, `.ss-kind`, an `icon()` per chart kind, draggable), the canvas (`.ss-canvas`, `.ss-row`) in its zoomable frame (`.ss-frame`, `.ss-viewport`), a chart tile (`.ss-tile`, `.ss-shot`) with its selection chrome (`.ss-label`, `.ss-handles`), the resize grip (`.ss-grip`), a free slot (`.ss-empty`), both inspectors (`.ss-field`, `.ss-two`) | `view.js` |
| **AI** | the prompt composer, the suggestion chips (`.ss-chip`), the proposal card with its changes and its validation (`.ss-proposal`, `.ss-changes`) | `view.js::copilot` |
| **Governance** | the governance list (`.ss-gov`), status tags (`.ss-tag`), check lists (`.ss-checks`), callouts (`.ss-callout`) | the chart inspector's *Governance*, the dashboard inspector's *Security* (who may open it, and the row-level rules), the drawer |
| **Lifecycle** | the environment and gate tags, the version table (`.ss-table`), the draft state in the bar, the export action, the deployment sheet (`.ss-steps` — what a deployment will do, numbered, in order) and the deployment record (`.ss-panel` with its steps and `.ss-readback`, what the target answered afterwards) | the bar, the drawer's *Releases* and *Deployments* |

Shared: `node()` (the element maker), `button()`, `field()`, `select()`, `pair()`, `tag()`, `toast()`,
`modal()`, `palette()`, `shortcuts()` — all of `ui.js`, 174 lines.

## 3 · The rules

1. **Strong colour is reserved** for an action, a selection, an error, and a state that changed. Everything
   else is surface, border and three weights of grey.
2. **Nothing is only colour.** A certified metric has a mark as well as a colour; a failing check has the word
   *fail*; a disabled control has a `title` saying why.
3. **Every control that is disabled says why**, in its tooltip — « save the draft first », « every slot
   already holds a chart », « this project is read-only here ».
4. **Every move the mouse can make, the keyboard can make**: thirteen shortcuts, a card at `?`, a palette at
   `⌘K`, and arrow keys over the canvas. Focus is always visible (`:focus-visible`, 2px, the action colour).
5. **Destructive and irreversible are different.** Taking a chart off the dashboard is one undo away and says
   so in a toast; throwing a draft away asks first; committing a version asks what changed.
6. **Loading, empty, error and refused are states of the design, not edge cases**: a skeleton while the
   payload is in flight, a named empty state for a dashboard with no charts and for a search that matches
   nothing, a reason when a project cannot be opened, and a banner when the registry says this person may read
   and not change.
7. **The dashboard is shown as a reader will meet it**: its title, its description, and the filters it
   carries as the chips they will become — on the canvas, above the first section, not as a control panel.
8. **Configuration is contextual**: the inspector shows the selected chart and nothing else, with governance
   behind its own tab rather than spread over the page.
9. **The canvas never claims more than it supports.** What it cannot do, Superset's own editor does, and the
   drawer says so in plain words.
10. **The canvas says what it is doing.** A tile under the pointer names itself; a selected one is dashed,
   carries eight handles and its slot's label; and the twelve columns a drop will snap to are drawn while
   something is being moved or resized, and only then.
11. **Motion is 120ms or nothing**, and `prefers-reduced-motion` turns it off.
12. **A toast is not a log.** It says what happened and offers the one action that answers it (*Undo*,
    *Reload*); anything longer belongs in the drawer.

## 3b · What a tile looks like, and where that comes from

A tile shows what **Superset drew** — a photograph in Compose, the live chart in Live. Nothing in this
stylesheet styles a chart, and nothing should: a KPI that reads as a big number with a delta and a sparkline
reads that way because Superset's `big_number_total` draws it that way.

`studio/preview.py` draws stand-ins, in SVG, so the workspace can be looked at without a Superset — a line
with its area, a histogram, a ranking with its values, a KPI with its delta. They are **invented numbers**,
and they are drawn to look like what Superset draws for the same slot, because a canvas full of flat
rectangles says nothing about whether the workspace around it reads well.

## 4 · The layout

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ command bar · project / dashboard · env · version · gate · draft state · actions│
├──────────────┬────────────────────────────────────────┬────────────────────────┤
│ explorer     │ canvas                                  │ inspector              │
│ outline      │ sections, rows, tiles, free slots       │ content / chart /      │
│ free slots   │                                         │ governance / data      │
│ parked       │                                         ├────────────────────────┤
│ datasets     │                                         │ copilot                │
│ metrics      │                                         │ chips · proposal       │
├──────────────┴────────────────────────────────────────┴────────────────────────┤
│ drawer · validation · changes · last run · releases · what this is              │
└────────────────────────────────────────────────────────────────────────────────┘
```

268px / flexible / 380px, each side pane collapsible. **Under 1240px the side panes become drawers over the
canvas** rather than columns squeezed beside it, and they start closed: a canvas in a third of a laptop's
width is worse than no canvas.

## 5 · What to build against

- `studio/studio.css` — the tokens and every class above, 310 lines, one file on purpose.
- `studio/ui.js` — the primitives. If a pane needs something new, it goes here, not into the pane.
- `studio/preview.py --check` — the workspace driven in a browser against a stand-in engine. A new component
  with a behaviour gets a promise there; it is nine seconds, and it is what caught a rule for text fields
  stretching radio buttons and a focus handler cancelling its own click.

## 6 · Where this departs from the references

| | |
|---|---|
| **The canvas is not Figma's** | it edits a specification, not pixels, and it supports what Superset's own dashboard model can store — rows and twelfths, not free coordinates ([ADR 0007](adr/0007-the-layout-belongs-to-the-specification.md)). There are no alignment guides because there is nothing to align: a twelfth is a twelfth, and every drop snaps by construction. Zoom and the template's own viewport widths are there, because *how wide it will be read* is a real question |
| **The library is not a component palette** | it offers the **kinds of chart the registry approves**, each saying which of this template's slots accept it and whether it is installed in this BI engine — not buttons, inputs and containers. Dragging one onto a row does not place a component: it opens the gallery on the free slot of that row's section, because a chart without a governed metric is not a chart |
| **The rail is a short cut, not a router** | the owner chose to have one ([ADR 0023](adr/0023-a-rail-of-short-cuts-inside-the-studio.md)); its SharpSight half is built server-side from the same rule the menu obeys, and nothing in it is a page only the rail can reach |
| **The inspector's controls propose** | dataset, kind, metric and dimension are dropdowns, as the reference has them — and changing one asks for a proposal with its validation rather than editing the draft, because those four are the registry's to allow ([ADR 0012](adr/0012-the-copilot-is-deterministic-and-proposes.md)) |
| **The inspector has little style** | a colour, a legend, an axis has no place in a specification, so the Style tab a design tool would have is a sentence explaining that Explore owns them and a commit sets them back |
| **The drawer shows no data** | the brief asks for a permission-checked sample; a developer's Superset account is given no database in SQL Lab, and this page is not a way round that decision ([ADR 0013](adr/0013-no-data-preview-in-the-studio.md)) — **the owner can overrule this in one tab and one route** |
| **The AI proposes, never applies** | Retool and Figma edit live; here every AI change is a proposal with a diff and an explicit Apply ([ADR 0012](adr/0012-the-copilot-is-deterministic-and-proposes.md)) |
| **Live charts come from Superset, not from us** | the canvas has two modes ([ADR 0022](adr/0022-two-canvases-compose-and-live.md)): *Compose* edits the draft as tiles that can be moved — each a photograph, because the draft is not in Superset yet — and *Live* frames Superset's own standalone dashboard with the Studio's chrome drawn over it, a box per chart read from that page's own DOM. Real charts, real data, no second renderer |
| **No document tabs** | the reference opens several things at once; a Studio page is one project's dashboard, reached from the projects page, and the browser's own tabs do the rest |
| **No component library is published** | these classes serve one workspace. If a second SharpSight surface needs them, that is when they become a package — not before |
