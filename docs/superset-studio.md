# Superset Studio — the next expansion

**A reading of `SharpSight_BI_Studio_AI_Visual_Builder_Spec_V3.md` against the kit as it stands.** Written
2026-10-19 against commit `9473f76`. Nothing here was run: it is read from the brief, from this repository and
from what [`docs/ai-bi.md`](ai-bi.md) and [`docs/lessons.md`](lessons.md) already measured. Every claim that
still needs a command to settle it is marked **spike**.

**Since 2026-10-19 this document has a second half in the repository.** Stages 0 to 4 of §10 are built: the
Studio is a page of Superset's own at `/sharpsight/studio/<project>/`, the layout belongs to the
specification, a draft is saved, validated and committed through the pipeline every other commit goes
through, charts are added from a gallery, and a deterministic copilot answers a request with a proposal.
§11 below says exactly what was built and what is owed; [`spec-v3-coverage.md`](spec-v3-coverage.md) takes
the brief section by section; [`adr/`](adr/) records every decision underneath it. Everything in §1 to §9 is
the reading it was built from, unchanged.

---

## 1 · What V3 adds

V3 is two briefs in one file.

| | |
|---|---|
| **§0–§45** — « Superset-native revision » | the brief this kit already implements. Its proof-of-concept list (§38) is scored item by item in [`docs/ai-bi.md` §3](ai-bi.md); its rule « check every extension API against the pinned version first » (§45) is why `registry/compatibility.yaml` records what `apache-superset-core 0.1.0rc3` actually offers |
| **§46–§60** — « AI Visual Builder » | **new.** A visual authoring workspace: project explorer, dashboard canvas with drag/drop and resize, contextual inspector, a persistent AI copilot that answers with a reviewable diff, a validation drawer — and the draft → commit → package → UAT → PROD lifecycle around it |

So the expansion is not a new engine. The engine, the registries, the policies, the validation, the versions,
the `.ssbi` package, the promotion gates all exist and are verified. **What does not exist is a place where a
person composes a dashboard and converses about it.** Today a project starts with a request on the command
line, and Superset's own screens are where it is looked at.

## 2 · The contradiction in the brief, and how it is read

V3 §0 forbids rebuilding Explore, SQL Lab, the dashboard builder, a second chart configuration engine and a
second renderer. V3 §47.4 then asks for a canvas with a grid, drag/drop, resize, sections, undo/redo,
responsive preview, snap-to-grid, alignment guides and canvas-level theme controls — which is a dashboard
builder.

The brief resolves it itself, in three places, and those three sentences are the design:

- §46.2: « Full replacement of every Superset dashboard-editor behavior — **not assumed**. Prototype first;
  use native editor fallback for unsupported operations. »
- §47.4: « A custom canvas may represent and edit a **controlled subset** of dashboard layout state … For
  unsupported or advanced dashboard operations, provide an **Open in native Superset editor** action rather
  than corrupting or dropping unknown configuration. »
- §49.3: three options — (1) native editor + side panel, (2) a custom canvas writing only the supported subset
  with native fallback, (3) a thin fork as a last resort.

**The reading taken here: the canvas edits a *specification*, not a dashboard.** It is an editor for the
`DashboardSpec` this engine already has — the thing a commit versions and a package carries — and Superset
renders the result. That keeps every principle of §0 intact: there is no second renderer, no second chart
configuration engine, no second SQL Lab. It also happens to be option (2), the one the brief recommends, and
it needs no fork.

What the canvas is **not**: a general editor for any Superset dashboard. A dashboard built by hand in Superset
is not a project ([`docs/ai-bi.md` §9](ai-bi.md)), and a canvas does not change that.

## 3 · Where the workspace lives — already proved

V3 assumes an extension view (§25, §49.1). On the pinned image that is the one thing that cannot be had:
measured 2026-10-04, `apache-superset-core 0.1.0rc3` offers contribution points **in SQL Lab only** —
`sqllab.leftSidebar`, `rightSidebar`, `panels`, `editor`, `statusBar`, `results`, `queryHistory`. Nothing on
dashboards, nothing in Explore, no chat contribution. A workspace cannot be a panel of SQL Lab.

The answer landed in the repository yesterday, as a spike, and it is the expansion's foundation: commit
`9473f76`, **a SharpSight page inside Superset** at `/sharpsight/projects/`. Superset's own `spa.html` draws
the top bar; the page is below it; the person's Superset session, theme and address are the same ones; leaving
by a link of the bar takes the page away. No fork, no second application, no second sign-in.

```
Superset's top bar  (its own spa.html, its own application)
└── /sharpsight/studio/<project>          ← the workspace, a blueprint route of superset-addon/
      ├── project / asset explorer        → /extensions/sharpsight/bi/…  (the person's session)
      ├── canvas                          → the draft specification, and pictures of its charts
      ├── contextual inspector            → the registry: datasets, metrics, dimensions, templates
      ├── AI copilot                      → proposals, each a diff of the draft
      └── validation drawer               → the engine's own report, as `validate` already produces it
```

Everything the page asks goes through the extension's gateway (`/extensions/sharpsight/bi/…`), which is where
**who is asking** is already settled correctly: read from Superset's session on the server, never from the
request, and handed to the engine, which applies `registry/security/rbac.yaml` to that person. The expansion
adds routes to a gateway that already has the hard part right.

Three things about that page are **spikes** before anything is built on it:

1. The page loads Superset's whole application to get its bar (the `menu` entry alone fails in 6.1.0 —
   « useThemeContext must be used within a ThemeProvider »), and needed CSS to stop that application taking
   the window's height. A full React workspace under that bar means **two roots on one page**. Does the
   workspace mount its own React, or take Superset's through module federation's share scope (react 17.0.2,
   antd 5.27.6, as the extension's panel does)? Taking Superset's is what makes it wear Superset's theme for
   free; it also couples the workspace to a release candidate's share scope.
2. Does the bar's client-side navigation stay well-behaved when the page below it holds unsaved work? The
   present page simply removes itself. A workspace must ask first.
3. `TALISMAN_ENABLED = False` in this kit, so no CSP is enforced here. A product Superset that turns Talisman
   on will need `frame-ancestors` and `img-src` for §5's preview. Check it there, not here.

## 4 · The one internal change that cannot be avoided: layout becomes data

Today, **the template owns the layout**. `TemplateDef.layout` is a list of rows, each row a list of slots; a
specification fills slots. `aibi/superset_adapter.py` builds `position_json` from the template at every build,
and a commit *undoes* a row moved in Superset's dashboard editor — named, then set back. That is deliberate and
documented.

A canvas cannot live with it. If a person drags a chart and the next build puts it back, the canvas is a lie.

So the expansion's first real decision, and it is structural:

| | Today | With the canvas |
|---|---|---|
| geometry | the template's rows and slots | the **specification's**: a grid, and `x, y, w, h` per component, as V3 §50.1 writes it |
| the template | fixed geometry | a **starting layout and a set of constraints**: which purposes exist, which slots are mandatory, what each accepts, minimum widths, the grid's columns and row unit (`Grid`, `Responsive` already carry these) |
| validation Level 1 | slots are filled | the layout *satisfies the template*: no overlap, inside the grid, mandatory purposes present, minimum widths honoured, sections still own whole rows or are redefined |
| a commit | sets back what Superset shows | **keeps the layout the canvas produced**, and still refuses what cannot be a governed chart |
| Superset's own dashboard editor | open, and what is done in it does not last | the honest options are: closed on a project's dashboard, or read back as the draft's layout. It cannot stay silently undone once a canvas exists |

This is the first of the three decisions [`docs/ai-bi.md` §9](ai-bi.md) puts to the owner — « whether a layout
made by hand should be kept ». **The Visual Builder decides it.** It must be settled before stage 2 below,
because the specification's schema, the validator and the commit all turn on it.

Two safeguards the brief asks for and this kit should honour literally (V3 §47.4, §59): **preserve unknown
configuration** rather than dropping it on a round trip, and keep an **Open in native Superset editor** action
for everything the canvas does not claim. What a commit takes is a short list today (kind, metric, dimension,
dataset, top N, order, time grain, bins, title); the canvas widens it one field at a time, each with a test,
and names the rest instead of silently discarding it.

## 5 · What the canvas shows, without a second renderer

Three ways to put something truthful in a tile, measured against what this kit has:

| | How | Good for | Cost |
|---|---|---|---|
| **a picture** | the engine's Playwright + headless Chromium — already in `services/ai-bi/Dockerfile`, already used by `visual_qa_agent.py`. Superset's own screenshot endpoints **cannot** work here: its image carries no browser (measured) | every chart kind, with no renderer of ours | static; a cache and an invalidation rule; seconds per chart |
| **numbers** | `POST /api/v1/chart/data`, which `superset_adapter.py` already calls | KPI tiles, tables, a sparkline — drawn from values, not from a chart library | nothing new, for simple kinds only |
| **the real thing** | an iframe of the dashboard Superset renders (`?standalone=`) | **Preview**: what the reader will see, filters and cross-filters included | not editable; one dashboard, not one tile |

**Recommendation: tiles are pictures (plus numbers where a tile is a number), Preview is the real dashboard in
an iframe.** Never a chart renderer of ours — that is V3 §0 principle 5, and it is also the line that keeps
this maintainable across a Superset upgrade.

A new engine route is wanted for it: a picture of one chart, cached by the chart's query key, invalidated when
the draft changes that chart. **Spike**: how long one picture takes on the kit's own hardware, and whether the
headless shell renders a chart alone (it renders dashboards today — a chart's own URL is a different page).

## 6 · The copilot: what exists, and what is new

V3 §51.2 lists sixteen tool contracts. Most are `aibi/tools.py` under another name:

| V3 §51.2 | Here |
|---|---|
| `search_project_assets`, `get_dataset_schema`, `get_certified_metric`, `get_template` | `list_datasets`, `get_dataset`, `get_dataset_schema`, `get_metric`, `list_metrics`, `get_dimension`, `list_dashboard_templates`, `get_dashboard_template` |
| `preview_readonly_query`, `validate_sql` | `execute_readonly_sql` (through the read-only gateway, `bi_reader`), `validate_sql` (sqlglot reads every statement) |
| `create_draft_dbt_change`, `run_dbt_compile`, `run_dbt_test` | `generate_dbt_model` and the runner, in the project's own workspace and branch |
| `create_or_update_superset_chart/_dashboard`, `render_dashboard_preview`, `run_dashboard_validation` | `apply_specification`, the adapter, `validation_agent`, `visual_qa_agent` |
| `get_asset_diff`, `commit_project_version`, `build_ssbi_package`, `deploy_release_to_uat` | `/projects/{id}/changes`, `commit`, the `.ssbi` package, `lifecycle.py`'s gates |

So the copilot is **a surface over a toolbox that exists**, with one audited entry point and a policy engine in
front of it. Three things are genuinely new:

1. **A proposal.** The AI must answer with a diff against the draft and never mutate it: summary, assets
   affected, datasets and metrics referenced, SQL/dbt if any, risks, then Preview / Apply / Reject (V3 §47.6).
   Today the unit of work is a *run* that produces a version. A proposal is smaller and reversible.
2. **A draft to propose against** — §7 below.
3. **A model.** `aibi/llm.py` is the seam and it is still empty, on purpose. The deterministic planner produces
   the same `Requirement` a model would, so everything downstream is already exercised. What is *not* settled
   is where a model runs: this kit ships offline and **calls nobody**. An API breaks that promise; a local
   model changes the bundle's size and the hardware it asks for. That is the owner's call (§14).

Two of V3's §51.3 policies deserve to be code and not prose, since both are already half-built: **AI cannot
approve its own release** (approving is on the command line, on purpose — keep it there) and **AI cannot change
a certified metric's formula or RLS** (the policy engine refuses an ad-hoc metric today; the same refusal
covers a copilot).

## 7 · Drafts: the third state

V3 §50.2 names three states. This kit has two.

```
   draft specification   ← NEW: editable, autosaved, one per project per person-or-branch
   Superset assets       ← exists (the adapter writes them; `changes` reads them back)
   release manifest      ← exists (a version, its package, its checksums, its gates)
```

The draft is what the canvas edits, what a proposal diffs against, what `validate` runs on, and what a commit
turns into a version. Three properties it needs, each from V3 §50.3: optimistic concurrency (a token with every
save; never silently overwrite), a warning when a shared asset changed under it, and reload / compare / branch
as actions rather than a lock.

Half of the comparison logic exists: the SQL Lab panel already shows « Superset shows something other than the
validated version », chart by chart, with what a commit would take and what it would refuse. With a draft there
are three sides, not two — draft, Superset, last version — and that is the view the canvas's « Changes » tab
should show.

## 8 · What 6.1.0 still refuses

Re-checked from what was measured on 2026-10-04 — to be re-measured at the next Superset upgrade, not assumed:

| V3 asks | Still refused on the pinned image |
|---|---|
| an extension view/panel outside SQL Lab (§25, §49.1) | **yes** — hence §3's page. Upstream's pull request for dashboards and Explore was open, and adds calls, not places for a panel |
| a chat contribution (§14) | **yes** — it is in Superset's unreleased documentation only |
| an MCP tool (§38 item 14) | the decorator exists (`superset_core.mcp`); `fastmcp` is not in the image |
| a semantic-layer extension (§12, §25) | documented for 6.1.0, absent from the package the image ships |
| a custom telecom visualization (§17) | compiling Superset's frontend. `registry/visualizations.yaml` lists `TelecomQoEChart` as approved-but-not-installed, and a specification asking for it is refused |
| Superset's own screenshots (§31, §53.2) | no browser in Superset's image; the engine's Playwright does it |

None of these blocks the expansion. Each is a reason the workspace is a page and not a panel.

## 9 · Risks particular to this kit

| | Why it bites here | What answers it |
|---|---|---|
| two React roots on one page | §3's page loads Superset's whole application for its bar | the spike, before stage 1 |
| gunicorn kills a worker at 60 s | measured; the pool follows the threads for the same reason | every long operation is the engine's, asynchronous, with a job id (V3 §56 asks for this anyway) |
| the refresh replays *recorded* requests, on a schedule that is a Superset **tag** | a canvas that rewrites `position_json` and a draft that changes queries both invalidate what was recorded | re-record on build; leave the tag alone |
| the share scope is a release candidate's | `apache-superset-core 0.1.0rc3`, react 17.0.2, antd 5.27.6 | the compatibility matrix already names it as the first thing to look at on an upgrade; `check-adapter` and a browser session are what notice |
| an upgrade is 580 MB of image | measured; nothing about this expansion makes it smaller | keep `rehearse-upgrade.sh` in the loop; a canvas that writes layout needs its own round-trip assertion there |
| the kit calls nobody | the offline promise | §6's model decision, and nothing else in the workspace may reach outside |

## 10 · The plan

V3 §57 has seven phases written for a greenfield team. Re-cut for a kit where phases 1, 4 and 5 are largely
done, each stage ending in something that can be run and looked at — the repository's own rule.

| | Stage | Done when |
|---|---|---|
| **0** | **The host, proved.** The three spikes of §3; one picture of one chart from the engine, timed; `position_json` written by the canvas's shape and read back unchanged (a round trip, asserted) | **done, except the timing** (§11): the round trip is asserted in a test, and the mounting question was answered by removing it — no second React |
| **1** | **The workspace, read-only.** `/sharpsight/studio/<project>`: explorer, canvas drawn from the *committed* specification, inspector showing governance, validation drawer showing the last report. Nothing editable | **done** (§11) |
| **2** | **Layout becomes data.** The owner's decision taken; `layout` in `DashboardSpec`; the template reduced to a starting layout and constraints; Level 1 validation of a layout; drag, resize, undo/redo, autosaved draft; a commit that keeps it | **built, not yet run against a kit** (§11): a chart moved on the canvas must still be there after a commit, a build and a reload, and `rehearse-upgrade.sh` must still say every chart returns the same data |
| **3** | **The inspector, and the manual path.** The fields a specification carries, each with its test. Add a chart from a gallery without AI. Say plainly what Explore keeps and a commit sets back | **done** — the inspector's four tabs, and a gallery that places a governed metric on a free slot through the same proposal the copilot uses (V3 §48.3: « do not force users to use AI ») |
| **4** | **The copilot.** Proposals with diffs, Apply / Reject, chat history per project — on the deterministic reading first, exactly as the engine was built | **done** — `aibi/copilot.py`: add a view, take one off, rename, a top N, a grain, an order, a format, a width, a height, « what is … ». What it does not understand, it says. Not yet: streaming, cancel, clarifying questions |
| **5** | **The model.** Behind `llm.py`, once the owner has chosen where it runs. Clarifying questions; refusals for an uncertain metric | **waiting on a decision** (§13 question 3). The deterministic copilot already produces the `Proposal` a model will have to produce |
| **6** | **The release workspace.** The gates, the package and the promotion already exist — bring them into the workspace: changes, impact, review, approve-elsewhere, deploy, rollback | **partly done** — the drawer shows the versions, their gates and their packages, what a draft changes, and what the last run found. Promotion stays on the command line, on purpose |

Stages 0–4 are built. What is left of 5 and 6 is a decision and a short list, both in §11.

## 11 · The first vertical slice

V3 §58's prompt is the right one and it is this kit's own demo: *a CEM QoE executive dashboard — QoE Score,
Active Users, Degraded Users, a daily trend, a regional ranking, a distribution, a site map, with Date, Region
and RAT filters, on the approved CEM executive template.* The engine builds it today, through 22 steps, with
220 checks and a screenshot.

What the slice adds is the second half of the sentence: the dashboard comes up **in the workspace**, a person
drags the ranking under the trend, renames it, asks for a thirty-day trend, reads the diff, applies it, runs
validation, commits 1.0.0, and the package is the same `.ssbi` as before. Two of §58's steps stay refused on
purpose and should be said out loud rather than faked: the **site map** (a map of sites needs tiles from
outside, and this kit calls nobody — the geographic view is by region) and **UAT as a separate Superset**
(one Superset, one warehouse: promotion is the gate, the package and the record).

## 12 · What stays out, and why

Not timidity — each has a measured reason, and each is already written down in [`docs/ai-bi.md` §9](ai-bi.md):
real-time collaborative editing (V3 §54 itself says later), Keycloak and the portal, Airflow, separate UAT and
PROD Supersets, an MCP server (`fastmcp`), a semantic-layer extension, the custom visualization, RabbitMQ and
MinIO, warehouses other than PostgreSQL for governed data, a site-level map.

## 13 · What is the owner's to decide

Four questions. The first blocks stage 2; the others block stages 3, 5 and 6.

1. **Does a layout made by hand survive?** (§4.) The canvas requires yes. Then: is Superset's own dashboard
   editor closed on a project's dashboard, or read back as the draft's layout?
2. **May a developer query in SQL Lab at all?** Their account opens the page the panel is on and is given no
   database there. A workspace with a data preview makes the question concrete again.
3. **Where does a model run?** An API breaks the offline promise; a local model changes the bundle. Until this
   is answered, stage 4 stands on the deterministic planner — which is not a loss: it exercises every guard.
4. **How does a project standing in UAT or PROD get a next version**, in a kit with one Superset? Today
   neither a request nor a commit is accepted on it.

---

## 11 · What was built, 2026-10-19

One sitting, on this branch. Everything below was run; nothing was run against a Superset, because this
session has no Docker daemon — what that leaves owed is the last table.

### The engine

| | |
|---|---|
| `aibi/layout.py` | the layout in force (the specification's, or the template's), what a canvas may change and what it may not, and `from_positions` — reading a layout back out of what Superset holds |
| `aibi/models.py` | `DashboardSpec.layout`, in the template's own shape. Empty means « the template's », so every version stored before today restores exactly as it was |
| `aibi/dashboard_agent.py` | builds `position_json` from the layout in force; Level 1 validation of it |
| `aibi/picture.py` | the canvas's tiles: one picture of the dashboard in the engine's own browser, cut per slot |
| `aibi/main.py` | `/projects/{id}/studio`, the draft (`PUT`, `DELETE`, `/validate`, `/commit`), `/pictures`, `/picture/{slot}` — and `SUPERSET_MAY` widened to exactly those, plus the governed lists the explorer shows |
| the extension (`0.2.0`) | the same routes, as the gateway inside Superset: the person signed in, carried to the engine, which decides. Nothing that approves, promotes or restores — a test reads the source, and a second test asks the engine which routes that token opens |

### Inside Superset

`superset-addon/sharpsight_bi.py` serves `/sharpsight/studio/<project>/` from Superset's own page template,
and the Studio's two files as they are in the repository — **no build step, so the kit still needs no Node**.
A *Projects* entry joins the SharpSight menu (the projects page was reachable by typing its address only),
and each project there opens in the Studio.

### Proof

| | |
|---|---|
| `services/ai-bi/tests/` | 194 pass, of which 24 are new: the layout's rules against the real registry and the real templates, the round trip, and the draft — its token, its refusals, what the workspace is given |
| `python studio/preview.py --check` | the workspace driven in a browser against a stand-in engine: 13 promises, no console errors |
| `python studio/preview.py` | the same page to look at, and `--shot` to photograph it |

### The second sitting, the same day

| | |
|---|---|
| `aibi/copilot.py` | the deterministic copilot: a request read with the planner's own governed vocabulary, answered with a proposal — what it would change, what it touches, whether it would pass validation, and the specification as it would be. It changes nothing; applying it is an ordinary draft save |
| the gallery | the same proposal from a form: a free slot, a governed metric, a type the slot accepts. The manual path is not a prompt |
| `aibi/layout.py::materialise` | the rows as they are drawn — the layout a specification is given when a person first moves something |
| the engine's new routes | `/propose`, `/compose`, `/report` (the levels only a run can produce), and the dataset's lineage, each added to the gateway's allowlist |
| the workspace, rebuilt | five ES modules; a design system in one stylesheet; toasts; a modal; thirteen keyboard shortcuts with a card that lists them; four inspector tabs; five drawer tabs; skeletons, empty states, and a reason on every disabled control |
| `docs/adr/` | seventeen decision records — everything this architecture rests on, including the ones taken before the Studio |
| `docs/spec-v3-coverage.md` | the brief's sixty sections, each marked built, partly or not here, with the reason |

Proof after the second sitting: **212 engine tests** (18 more, for the copilot), and
`python studio/preview.py --check` driving the workspace in a browser against a stand-in engine — **thirty
promises**, no console error.

### The third sitting, 2026-10-20 — deploying it, and trying the whole thing

The owner asked the two questions that follow a workspace: *can a dashboard built here be deployed to the run
platform, with everything it needs?* and *can all of it be tested?*

| | |
|---|---|
| `aibi/deploy.py` | nine steps, each recorded: the package's checksum · the target's versions against the matrix · the governed connection created there under the name the package expects · the import · the **repoint** (an import keeps an existing URI — a run environment would otherwise read the development warehouse) · the audience and the row-level rules, which are not in an export · what arrived, read back · **every chart asked for its data**, because an import answers 200 for a dashboard that cannot draw · the record |
| what it refuses | a version nobody approved for that gate, a version whose validation did not pass, a package whose checksum is not the one that was validated, a package without Superset's own export, a target outside the compatibility matrix. Each in words a person can act on, and before anything is imported |
| `config.targets()` | a target is five variables in the engine's own environment (`AIBI_TARGET_<NAME>_URL`, `_USER`, `_PASSWORD`, `_DB_NAME`, `_DB_URI`), plus `_FROM_STUDIO=false` to make it the command line's alone, `_GATE=UAT` when one reviewer's approval is enough to go there (a target that does not say is held to PROD, whatever gate a request names) and `_PUBLIC_URL` where a person reaches it. Nothing is deployed anywhere that is not named there, and nothing there is written down in this repository |
| the engine, the extension, the CLI, the Studio | `GET /targets`, `GET /projects/{id}/deployments`, `POST /projects/{id}/versions/{v}/deploy` — on the gateway's allowlist, so the Studio may deploy and still cannot approve. `bash ai-bi.sh targets · deploy · deployments`. In the Studio: a **Deploy** button and a *Deployments* drawer tab showing the steps and what was read back |
| `docker-compose.run-target.yaml` | a **second Superset** on this machine as the « run » environment: its own metadata database (`runbi`), its own accounts, its own Redis database, its own URL — and nothing of the kit's in it. `bash ai-bi.sh run-target` |
| `scripts/e2e.sh` | the whole walk on a running kit, as eleven sections of promises: the machine · what is governed · a project from a request · the Studio's payload, draft, token refusal, validation and three roads to a proposal · a modification committed as the next version · the second person and the audit chain · the package and its checksum · the deployment and its read-back · the package as Superset's own importable export · what Superset's token may and may not open · both suites. `bash ai-bi.sh e2e`, `--only <n>` for one section |

Proof after the third sitting: **231 engine tests** (nineteen more: the deployment's refusals and its read-back, the
link a person can open, and the run-target overlay's handover to `config.targets`), and
`python studio/preview.py --check` — **sixty-nine promises**, no console error.

### What is owed before this can be called done

- **Run it — and now there is one command for it.** `bash ai-bi.sh e2e` walks everything below that a
  machine with Docker can answer: the generation, the Studio's draft and proposals, a commit, an approval,
  the package, the deployment to a second Superset and the read-back there. What it cannot answer is the
  three spikes of §3, which need eyes on the page.
- **The three spikes of §3** against a real Superset: the page under the bar with a workspace on it,
  the bar's own navigation with unsaved work, and `csrf_token()` in that template. Then one picture of one
  chart, timed, on real hardware.
- **The round trip on a real kit**: move a chart, commit, build, reload — and `rehearse-upgrade.sh` still
  green on real content.
- **The owner's first question is now answered in code** (a layout placed in the Studio is kept). The half
  that is still open: a layout moved in Superset's *own* dashboard editor is still named and undone.
  `from_positions` is the reading it would take to adopt it instead.
- Stage 3 onwards unchanged: the manual path for adding a chart, the copilot, the model, the release
  workspace.

---

**The shape of the answer, in one line:** the expansion is an editor for the specification, hosted on the page
Superset already lets us serve, drawing truthful pictures of charts Superset renders, talking to a toolbox that
exists — and its first real cost is that the template stops owning the layout.
