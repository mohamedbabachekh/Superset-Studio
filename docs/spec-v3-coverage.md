# The V3 brief, section by section — what is built, and what is not

`SharpSight_BI_Studio_AI_Visual_Builder_Spec_V3.md`, all sixty sections, against this repository on
2026-10-19. Three verdicts and nothing in between:

| | |
|---|---|
| **built** | it is here, it runs, and something tests it |
| **partly** | some of it is here; the rest is named below with what is missing |
| **not here** | it is not built. Every one says **why**, and whether the reason is a decision, a measurement, or work not yet done |

Nothing in this document was run against a live Superset in the session that wrote it (no Docker daemon); what
that leaves owed is [`superset-studio.md` §11](superset-studio.md).

---

## §0–§4 · The Superset-native revision

| | | |
|---|---|---|
| §0 | Superset is the BI application; SharpSight extends it | **built** — [ADR 0001](adr/0001-superset-is-the-bi-application.md) |
| §0.1–5 | Do not rebuild Explore, SQL Lab, the dashboard builder, a chart engine, a renderer | **built** — none of the five exists here, by construction ([ADR 0006](adr/0006-the-canvas-edits-the-specification.md)) |
| §0.6 | No large fork | **built** — the fork is empty ([ADR 0002](adr/0002-native-first-then-the-extension-no-fork.md)) |
| §1–2 | Two surfaces: Superset-native BI Studio, and the enterprise portal | **partly** — the BI surface is built; the portal (`sharp-sight-web`) is not, and the API it would call is what the engine already exposes |
| §3 | Build as a family of Superset extensions | **partly** — one `.supx` (a backend API, two commands, a SQL Lab panel). The workspace is a page, because 6.1.0 contributes nowhere else ([ADR 0005](adr/0005-the-studio-is-a-page-not-a-panel.md)) |
| §4 | The native UI map, with SharpSight around it | **built** — Superset's own pages, plus the SharpSight menu, five record dashboards, and the Studio |

## §5–§12 · The dashboard, the assets, the semantics

| | | |
|---|---|---|
| §5 | Superset's dashboard builder is the canvas | **partly** — Superset's builder stays open and is the runtime; the Studio's canvas edits the specification instead of the dashboard, which is §49.3's option 2 ([ADR 0006](adr/0006-the-canvas-edits-the-specification.md)) |
| §6 | Project context injected into the UI | **built** — the Studio's command bar; tags on the dashboard; the SQL Lab panel. Not inside Explore: no extension point |
| §7 | Asset Library | **partly** — the Studio's explorer lists the project's outline, the governed datasets and the metrics. A chart is still added by placing a governed metric on a free slot, not by dragging a shared asset |
| §8 | Outline | **built** — the explorer's outline, by section |
| §9 | Local vs shared assets | **partly** — certification and ownership are shown everywhere; « local to this project » exists for generated datasets (`dataset.project`), and is not a scope a person chooses |
| §10 | Native Explore stays the chart editor | **built** — and what Explore changes beyond a specification is named and set back on commit |
| §11 | SQL Lab stays the SQL IDE | **built** — with the SharpSight panel in it. Monaco is not swapped in: 6.1.0's editor contribution was not needed |
| §12 | Semantic layer in Explore | **partly** — the registry is the semantic layer and governs everything the engine builds; Superset's own semantic-layer extension module is **absent from the 6.1.0 package**, so governed metrics are mapped into datasets instead. No DAX, no Power Query, no second modeling engine — as asked |

## §13–§18 · dbt, AI, visualizations, code

| | | |
|---|---|---|
| §13 | dbt inside the Superset experience | **partly** — a *Data Quality* dashboard (models, every test), dbt's own documentation under the menu, lineage in the Studio's inspector. « Run dbt » from a button is not offered: a model is built by the pipeline, on a branch, not on demand from a page |
| §14 | AI assistant inside the interface | **built, deterministic** — the Studio's copilot ([ADR 0012](adr/0012-the-copilot-is-deterministic-and-proposes.md)). Superset's own chat contribution does not exist in 6.1.0, which is moot on a page of ours |
| §15 | The AI workflow | **partly** — discovery, plan, specification, validation, preview, commit are all built. « dbt if required » exists in the pipeline but not from the copilot: it proposes only what a specification can carry |
| §16 | AI fills a template's slots | **built** — a template's slots are what both the planner and the copilot fill |
| §17 | Custom telecom visualizations | **not here** — a visualization plugin is compiled into Superset's frontend; this kit does not rebuild it. `registry/visualizations.yaml` lists `TelecomQoEChart` as approved-but-not-installed, and a specification asking for it is refused rather than silently redrawn |
| §18 | Advanced JS/CSS, safely | **built** — the AI never writes CSS: a template names design tokens, and the generator turns a token into a rule scoped to one dashboard. No `<script>`, no Python, no shell from a prompt |

## §19–§25 · Governance, git, review, release, the extension API

| | | |
|---|---|---|
| §19 | Governance drawer | **built** — the chart's inspector (metric, definition, owner, certification, dataset, domain, grain, section) and the dashboard's *Security* tab (audience, classifications, row-level rules, what is not certified) |
| §20 | Version control and git in the menus | **partly** — validate and commit are commands in SQL Lab and in the Studio; git holds every version's specification, and `restore` re-applies one. Superset's own dashboard menu takes no SharpSight entry: no extension point |
| §21 | BI review inside Superset | **partly** — the Studio's « Changes » tab shows what a draft changes about its version, and what Superset holds against what was validated. Approving is deliberately elsewhere ([ADR 0010](adr/0010-a-commit-goes-through-the-pipeline.md)) |
| §22 | Release and deployment controls | **partly** — the « Releases » tab lists every version, its gate and its package. Promotion is the command line, on purpose |
| §23 | The Superset API boundary | **built** — REST only, never the metadata database ([ADR 0004](adr/0004-superset-only-through-its-rest-api.md)) |
| §24 | A suite of extensions (core/ai/data/devops/viz) | **partly** — one `sharpsight-bi.supx`, as §24 itself allows for V0 |
| §25 | Views, commands, menus, editors, chat, backend API, MCP, semantic layer | **partly** — commands and a backend API are contributed; views are a page; menus are Flask-AppBuilder's; **no chat, no MCP tool, no semantic-layer module** on the pinned version (`fastmcp` is not in the image) |

## §26–§37 · Theme, fork policy, backend, data, AI factory, QA, environments

| | | |
|---|---|---|
| §26 | Token-based theme | **built** — SharpSight's colours lead the interface and the dashboards; a client's brand is an accent and a series. The tokens still live in three files kept alike by hand |
| §27 | Fork policy, extension-first | **built** — the decision tree was followed and recorded ([ADR 0002](adr/0002-native-first-then-the-extension-no-fork.md)) |
| §28 | SharpSight as an external control plane | **partly** — the engine is that control plane; Keycloak and the portal are not built, and identity arrives as a header behind a shared token |
| §29 | Sources → dbt → registry → Superset | **partly** — built on PostgreSQL ([ADR 0016](adr/0016-postgresql-only-for-governed-data.md)); Airflow is not used, and a one-shot builds the governed models at each start |
| §30 | The AI BI factory, Superset-native | **built, deterministic** — the twenty-two steps, the registries, the gates |
| §31 | AI testing and visual QA | **built** — 220 checks, the dashboard rendered in a browser, a screenshot read back, the queries the real charts made. Never bypassing Superset's permissions: the probes are accounts holding one role or none |
| §32 | Separate DEV/UAT/PROD Supersets | **not here** — one Superset and one warehouse; promotion is a gate, a package and a record ([ADR 0017](adr/0017-one-superset-so-promotion-is-a-gate.md)) |
| §33 | The `.ssbi` package | **built** — cut from the committed version, with its manifest, its checksums and what it was built with |
| §34 | On-prem deployment | **partly** — Docker Compose, the kit plus the overlay. No Keycloak, RabbitMQ, MinIO or git server: the slice needs none of them |
| §35 | Versioning strategy | **built** — `registry/compatibility.yaml`, with what is actually running put beside it, and `/health` saying when something is untested |
| §36 | Security | **partly** — Superset RBAC, SharpSight RBAC, RLS, read-only AI SQL, timeouts and limits, extension checksums, audit. **Keycloak SSO is not built**; the extension bundle is checksummed, not signed |
| §37 | Performance | **built** — the workspace loads one payload and then the lists; the tiles are one page load; long work is the engine's, with a run id |

## §38–§45 · The POC, its acceptance, the backlog

| | | |
|---|---|---|
| §38 | The fourteen-item POC package | **12 of 14** — the scoreboard is [`ai-bi.md` §3](ai-bi.md). Missing: a custom telecom visualization (§17), an MCP tool (`fastmcp` absent) |
| §39 | POC acceptance — native BI | **built** — a developer opens Superset, sees the project context, uses SQL Lab, Explore and the dashboard builder, and takes governed datasets and metrics from the Studio |
| §39 | — governance | **built** — certification, owner, domain, freshness and dbt status are visible; impact analysis is a dashboard |
| §39 | — AI | **partly** — the chat is there, discovery and certified metrics are used, a chart is created and validated. A chart *preview before saving* is the proposal and its validation, not a rendered preview; dbt generation is the pipeline's, not the copilot's |
| §39 | — lifecycle | **built** — validate, commit, review, DEV → UAT, PROD needing a second person, every deployment recorded |
| §39 | — technical | **built** — no metadata-database writes, a real `.supx` through the approved mechanism, Superset independently upgradeable within the matrix |
| §40–41 | The phase backlog and the coding rules | **built** — the twelve rules are ADRs 0001, 0004, 0006, 0010–0012 and this file |
| §42–45 | Final architecture, the reference baseline | **built** — pinned version, extension APIs checked against the image before use, a compatibility matrix rather than `latest` |

## §46–§60 · The AI Visual Builder

| | | |
|---|---|---|
| §46 | The product decision, the feasibility table | **built** — the staged reading is [`superset-studio.md`](superset-studio.md) |
| §47.1 | Three panes and a drawer | **built** |
| §47.2 | The command bar: environment, save state, undo/redo, preview, validate, commit, export, deploy | **built, less deploy** — including **Export .ssbi**, the immutable package of the latest version. Deploying is the command line's, because PROD needs a second person ([ADR 0010](adr/0010-a-commit-goes-through-the-pipeline.md)). Draft / version / package / deployment are never conflated |
| §47.3 | The project and asset explorer | **built** — outline, free slots, parked charts, a **component library** of the approved chart kinds (each saying where it may go and whether it is installed), governed datasets and metrics, search. A kind **drags onto a row**, which opens the gallery on that section's free slot: what is placed is still a proposal, because a chart without a governed metric is not a chart |
| §47.4 | The canvas | **built, less one** — grid, drag to another row, resize, reorder rows, row heights, selection, loading and empty states, keyboard equivalents for every mouse move, undo/redo, autosave, « add here » on a free slot, **zoom**, **the viewport widths the template declares**, and **preview with real data**: a *Live* mode that frames Superset's own standalone dashboard inside the workspace and draws the Studio's chrome over it ([ADR 0022](adr/0022-two-canvases-compose-and-live.md)). Not built: duplicate. Alignment guides are moot: a twelfth is a twelfth, and every drop snaps |
| §47.1 | Small screens: collapse the panes into drawers rather than squeezing the canvas | **built** — under 1240px the side panes are drawers over the canvas, closed to begin with |
| §47.5 | The contextual inspector | **built** — for a chart: Content (with the dataset, metric and dimension as controls that ask for a proposal), Chart (the kinds the slot accepts, as a row of icons), Governance, Data & dbt. For the dashboard (nothing selected): Dashboard, **Filters** (the columns the template allows, on datasets it already uses), **Security** (who may open it, and the row-level rules that follow), Layout. Style is deliberately thin: colour and legend have no place in a specification |
| §47.6 | The AI copilot | **built, deterministic** — a proposal with its changes, what it touches, its validation, Apply / Reject, history per project, suggestions drawn from the project. **Not built**: streaming, cancel, clarifying questions, « explain this change » beyond the diff |
| §47.7 | The validation drawer | **partly** — Validation, Changes, Last run (the levels only a run can produce), Releases. **No data preview** ([ADR 0013](adr/0013-no-data-preview-in-the-studio.md)); logs and timings are inside the last run's report rather than tabs of their own |
| §48.1 | Create a dashboard from a prompt | **built** — on the command line (`ai-bi.sh request`), twenty-two steps. Not from the Studio: a project starts with a request |
| §48.2 | Modify an existing dashboard with AI | **built** — select, ask, read the diff, apply or reject, validate, commit |
| §48.3 | Manual chart creation | **built** — the gallery: a free slot, a governed metric, a type the slot accepts, and a proposal |
| §48.4 | Deploy to production | **partly** — commit, review, package, approval by a second person, the record and the rollback version are built; importing into another environment is the deployment service's, which this kit does not run |
| §49 | The logical architecture and the responsibilities | **built** |
| §50.1 | The canonical draft specification | **built**, with one deliberate difference: the layout is rows and widths, not `x, y, w, h`, because that is what Superset stores ([ADR 0007](adr/0007-the-layout-belongs-to-the-specification.md)) |
| §50.2 | Three state types | **built** — draft, Superset assets, release manifest |
| §50.3 | Concurrency | **partly** — optimistic token, refusal with what to do, reload. « Compare » and « create branch » are not offered |
| §51.1 | The AI pipeline | **built** |
| §51.2 | Typed, scoped tools | **built** — `aibi/tools.py`, one audited entry point, a policy check on each |
| §51.3 | AI policy | **built** — DEV only, no self-approval, no metric or RLS change, read-only SQL, audited calls, a person applies every change |
| §52 | dbt and semantics in the builder | **partly** — the model, its lineage and its tests are shown; « request a transformation » from the page is not offered |
| §53 | Validation levels 1–6 | **built** — Level 1 in the Studio without touching Superset; 2 to 6 in the pipeline, shown in « Last run » |
| §54 | Collaboration | **partly** — ownership, roles, review, version history, changed assets, audit. **No comments** on a dashboard or a component, and no real-time editing (§54 itself says later) |
| — | *Beyond the brief*: a command palette (⌘K), a dark theme, a design system written down ([`design-system.md`](design-system.md)), a navigation rail ([ADR 0023](adr/0023-a-rail-of-short-cuts-inside-the-studio.md)), the areas Design / Data / Filters / Interactions, and device widths | **built** — from the design direction of 2026-10-19 and 2026-10-20 |
| §55 | The `.ssbi` package and promotion | **built** — with environment mapping left to the deployment service |
| §56 | API and service contracts | **partly** — the engine's API covers projects, drafts, proposals, validation, commits, packages, deployments-as-gates. The route names are the engine's own, not the brief's `/api/bi/...` |
| §57 | The implementation plan, phases 0–6 | **phases 0–4 built** — see the roadmap in [`superset-studio.md` §10](superset-studio.md). Phase 5 (a model) waits on a decision; phase 6 (comments, real-time) is not started |
| §58 | The first vertical slice | **13 of 15 steps** — the two refused are named: a site map needs tiles from outside, and UAT is not a second Superset |
| §59 | Risks | **built** — each mitigation is in place: pinned version, round-trip test, certified registry, proposals, import validation, no production code execution, security probes, progressive disclosure, stage gates |
| §60 | The final product decision | **built** — Visual Builder, AI Copilot and Release Workspace on one governed project model |

---

## What is missing, in one list

Everything above that is **not here**, with its reason. Nothing here is forgotten; each is a decision, a
measurement, or work with a known shape.

**Decided against, for a reason that would have to change first**

1. **A second Superset for UAT and PROD** — one kit, one engine ([ADR 0017](adr/0017-one-superset-so-promotion-is-a-gate.md)).
2. **A data preview in the Studio** — a developer's Superset account has no database in SQL Lab ([ADR 0013](adr/0013-no-data-preview-in-the-studio.md)).
3. **Promotion from the page** — PROD needs a second person, and that cannot be enforced if a page can do it.
4. **Free `x, y` coordinates on the canvas** — Superset stores none ([ADR 0007](adr/0007-the-layout-belongs-to-the-specification.md)).
5. **A fork of Superset** — the brief's own last resort, not reached ([ADR 0002](adr/0002-native-first-then-the-extension-no-fork.md)).

**Impossible on the pinned version — measured, not assumed**

6. **A chat, a view, a panel outside SQL Lab, a semantic-layer extension** — `apache-superset-core 0.1.0rc3`
   contributes to SQL Lab only. The Studio exists *because* of this.
7. **An MCP tool** — the decorator exists; `fastmcp` is not in the image.
8. **A custom telecom visualization** — compiling Superset's frontend. Listed as approved-but-not-installed,
   and refused rather than silently redrawn.
9. **Superset's own screenshots** — no browser in its image; the engine brought one.

**Not built yet, and nothing stands in the way**

10. **A language model** — `aibi/llm.py` is the seam; the deterministic copilot already exercises every guard
    one will meet. What is open is *where a model runs*, for a kit that calls nobody.
11. **Keycloak SSO and the portal** — identity arrives as a header behind a shared token.
12. **Airflow** — a one-shot builds the governed models at each start.
13. **The engine's image in the offline bundle** — `build-devkit-bundle.sh` does not carry it.
14. **Comments and real-time collaboration** — §54 says later, and this agrees.
15. **Warehouses other than PostgreSQL** — the matrix says `not_tested`, which is a statement about what was
    run, not a limitation of the design.
16. **What is left of the canvas** — duplicating a chart, « compare » and « branch » on a conflicting
    draft, streaming and clarifying questions in the copilot. (Small screens, the command palette, the dark
    theme, the written design system, the component library with drag onto the canvas, zoom and the
    template's viewport widths were all done on 2026-10-19.)
17. **A site-level map** — map tiles come from outside, and this kit calls nobody.
18. **One source for the brand values** — they are in three files kept alike by hand.

**Owed before any of this is called finished:** none of it has been run against a live Superset in the session
that built it. [`superset-studio.md` §11](superset-studio.md) is the list.
