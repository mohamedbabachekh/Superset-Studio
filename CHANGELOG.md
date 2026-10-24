# Changelog

Versions are the date a bundle was built (`sharpsight-superset-devkit-<version>.tar.gz`). Everything below was
built and verified on a running kit before it was written down.

## Not in a bundle yet — the repository grouped by role (2026-10-24)

The layout below (`superset/`, `scripts/bundle/`…) was rejected: a folder per kind of file still left eleven
folders at the root, and nothing said which of them was Superset, which the engine and which what a person
sees. The checkout is now grouped by ROLE, each a single folder, every move a `git mv`. The root keeps what a
person runs and what Docker and the installer look for there — the compose files and the `Dockerfile` stay, so
every documented `docker compose …` command works as written.

- **`kit/`** — Superset itself: `kit/superset/` (its modules and `templates/`), `kit/nginx/`, `kit/krb5/`,
  `kit/initdb/`, `kit/driver-jars/`. The image still copies Superset's modules FLAT into `/app/pythonpath`.
- **`engine/`** — the AI BI engine, everything that was `services/ai-bi/`; `services/` is gone. Its compose
  build context is `./engine`.
- **`studio/`** — everything shown inside Superset: `studio/web/` (the Studio's page, was `studio/`),
  `studio/addon/` (was `superset-addon/`), `studio/extension/` (was `superset-extension/`). The extension's
  bundle was rebuilt because a docstring in it names the new path; its checksum in `docker-compose.ai-bi.yaml`
  is the rebuilt one.
- **`governance/`** — `governance/registry/` (was `registry/`) and `governance/dbt/` (was `dbt/`, with
  `cem_to_mart.py`). The engine mounts them at the same relative path under `/app` (`/app/governance/…`), and
  `AIBI_REGISTRY_DIR` / `AIBI_DBT_PROJECT_DIR` default there, so a test finds a file the same way in the
  container and in a checkout.
- **`tools/`** — was `scripts/`: `tools/e2e.sh`, `tools/bundle/`, `tools/rehearse/`. Each finds the root from
  its own location.
- **`docs/brand/`** — was `brand/`.
- **A bundle** carries `kit/initdb/`, `kit/nginx/`, `kit/driver-jars/`, `kit/krb5/` and `tools/bundle/` at the
  same paths as the checkout; `verify-bundle.sh` asks for them there and still refuses the overlay under its old
  names and its new ones. `install.sh --upgrade` looks for the driver and krb5 files of the old install under
  `kit/` and, for a kit installed before this change, at its root.

## Not in a bundle yet — the repository's layout (2026-10-24)

The root held thirty files; it now holds what a person opens first and the files Docker and the installer
look for there. Every move was a `git mv`, so each file keeps its history. Nothing an installed kit does
changes: the image is the same image, built from the same sources at new paths.

- **`superset/`** — `superset_config.py`, `devkit_drivers.py`, `devkit_forms.py`, `devkit_seed.py`,
  `devkit_refresh.py`, `yacht_dialect.py`, `sharpsight_translations.py` and `templates/head_custom_extra.html`.
  The Dockerfile still copies them FLAT into `/app/pythonpath`, so they import each other by bare name as
  before, and every `/app/pythonpath/<file>.py` command in the compose files and the docs is unchanged.
- **`dbt/cem_to_mart.py`** — beside the dbt project whose source it lands; in the image it is still
  `/app/pythonpath/cem_to_mart.py`.
- **`scripts/bundle/`** — `build-devkit-bundle.sh`, `verify-bundle.sh`, `export-for-devkit.sh`,
  `import-into-devkit.sh`. The build finds the root two folders up. A bundle now carries the two archive scripts
  at `scripts/bundle/` too, so `bash scripts/bundle/import-into-devkit.sh <archive>` is the same command in a
  checkout and in an installed folder; `verify-bundle.sh` asks for them there.
- **`scripts/rehearse/`** — `rehearse-upgrade.sh` and the three files it runs, which it finds beside itself.
- The root keeps `README.md`, `HANDOVER.md`, `CHANGELOG.md`, `install.sh`, `ai-bi.sh`, the `Dockerfile`, the
  compose files and the two `.example` settings files. The commands in the README, the handover and `docs/`
  name the new paths; the entries below keep the names they were written with.

## Not in a bundle yet — SharpSight Studio (2026-10-19)

The visual builder of the brief's sections 46 to 60, as a page of Superset's own — not an extension panel:
6.1.0 contributes to SQL Lab and nowhere else. The engine is 0.3.0, the extension 0.2.0. Read
[docs/superset-studio.md](docs/superset-studio.md) for what was built and what is owed, and
[docs/ai-bi.md](docs/ai-bi.md) §3 for what it does.

- **A workspace, at `/sharpsight/studio/<project>/`.** Superset's own top bar, session and theme; the project,
  its environment, its version and its gate; an explorer with the project's outline and the governed datasets
  and metrics; a canvas; an inspector; a validation drawer. No build step and no second React on the page: the
  Studio is plain DOM, served as it is in the repository, so the kit still needs no Node.
- **The layout stops belonging to the template.** A specification may carry its own — in the template's shape,
  which is Superset's: rows of charts with a width in twelfths and a height in grid units. A chart moved or
  resized on the canvas is what the next build draws, and a commit keeps it. A specification that carries none
  is laid out by its template, so every version stored before today restores exactly as it was.
- **What a canvas may do is a rule, not a hope**: the rows reorder, a chart moves on its row, widths take from
  a neighbour and never go below the template's minimum, a row changes height — and a chart does not leave the
  section it is accountable to. Each is checked in the engine before Superset is called, and said in words a
  person can act on.
- **A draft is the third state**: what a person is editing, kept with the project, saved a moment after each
  change, with a token so two people with the page open cannot overwrite each other silently. It changes
  nothing in Superset by existing, and it becomes a version through the pipeline every other commit goes
  through — ten quality gates, visual QA, git, a package.
- **A tile is a photograph of the real chart**, cut out of one picture of the real dashboard taken in the
  engine's own browser; *Preview* opens Superset's own dashboard for the live thing. There is no second chart
  renderer anywhere, which is the brief's own first rule.
- **A copilot that invents nothing.** It reads a request with the planner's own governed vocabulary and
  answers with a **proposal**: the changes, what they touch, whether validation would pass, and the
  specification as it would be — never a change. Add a view, take one off, rename, a top N, a time grain, an
  order, a number format, a width, a height, a row moved, « what is … ». What it does not understand, it says
  it does not understand, and proposes nothing. No language model has been chosen for this kit, which calls
  nobody; one plugs into `aibi/llm.py` and produces the same proposal.
- **A chart is added without a prompt too** (the brief's « do not force users to use AI »): a gallery of the
  template's free slots and the governed metrics, answered by the same proposal.
- **A workspace with a keyboard.** Thirteen shortcuts, a card that lists them, four inspector tabs
  (content, chart, governance, data and dbt), five drawer tabs (validation, what a draft changes, what the
  last run found, the releases, what this workspace is), toasts that say why something was refused,
  skeletons while it loads, and a reason on every control that is disabled.
- **Looked at without Superset**: `python studio/preview.py` serves the workspace against a stand-in engine,
  and `--check` drives it in a browser and holds it to thirteen promises. The engine's own rules are held by
  24 new tests against the real registry (194 in all).
- **A design system, written down** ([docs/design-system.md](docs/design-system.md)): one source for the
  values (`registry/design/tokens.yaml`), six component groups, ten rules, and where the workspace departs
  from the references it was drawn against. Every colour in the stylesheet is a token, which is what made the
  rest possible: **a dark theme** (light is the default, dark is one button, kept per person), **a command
  palette** at ⌘K listing everything the workspace can do and every chart it holds, and **side panes that
  become drawers** under 1240px instead of squeezing the canvas into a third of a laptop.
- **The style pass.** Softer corners and shadows, more air, buttons that read as actions; the canvas is a
  white surface whose grain appears only while something is being placed; the dashboard is shown as a reader
  will meet it — its title, its description, and the filters it carries as the chips they become; and the
  person signed in is in the bar. What a chart looks like is still Superset's, and the stand-in charts
  `studio/preview.py` draws (SVG, invented numbers) now look like what Superset draws, because a canvas of
  flat rectangles says nothing about whether the workspace around it reads well.
- **It is navigated like a workspace.** A rail on the left — SharpSight's own pages, the ones this person
  may open (built server-side from the same rule the menu obeys), and Superset's own — and four areas under
  the breadcrumb: **Design** (the canvas), **Data** (what the dashboard reads, with its lineage), **Filters**
  (the columns the template allows, with its limit) and **Interactions** (what a specification carries about
  scope, and what is Superset's own). The canvas gained device widths; the inspector gained a row of the
  chart kinds its slot accepts.
- **The inspector's controls propose.** Dataset, metric, dimension and kind are dropdowns, as a visual
  builder has them — and changing one asks the engine for a **proposal** with its validation rather than
  editing the draft, because those four are the registry's to allow. One road in, checked once.
- **The canvas has two modes.** *Compose* is the draft — tiles that can be dragged, resized and reordered.
  *Live* frames **Superset's own standalone dashboard inside the workspace**, with the Studio's chrome drawn
  over it: a box per chart, read from that page's own DOM by the same chart id the engine's visual QA reads,
  following its scrolling, clicking through to the inspector. Real charts, real data, and still no second
  renderer — the one thing the brief forbids outright. A draft is not in what Superset holds until it is
  committed, and Live says so rather than letting it look applied.
- **It looks like an editor now.** The library is a shelf of cards, each with an icon drawn for that kind of
  chart; the canvas has the grain of a drawing surface; a tile under the pointer names itself, and a selected
  one is dashed, carries eight handles and wears its slot's label; and while a chart is dragged or resized,
  the twelve columns it will snap to are drawn across the canvas, and only then.
- **A component library, and the dashboard's own inspector.** The explorer now offers every chart kind the
  registry approves, grouped by what it is for, each saying which of this template's slots accept it and
  whether it is installed in this BI engine — what is approved but not installed is shown, not hidden. A kind
  drags onto a row and opens the gallery on that section's free slot. And with nothing selected, the
  inspector is the dashboard's: its title and owner, its **filters** (the columns the template allows, on
  datasets it already uses, with the limit it allows), its **security** (who may open the dashboard this
  becomes, and the row-level rules that follow it), and its layout. The canvas gained **zoom** and **the
  viewport widths the template declares**.
- **Export .ssbi from the bar** — the immutable package of the latest version, through the same gateway with
  the same identity. Reading a package is not promoting it: PROD still needs a second person.
- **Twenty-one architecture decision records** ([docs/adr/](docs/adr/)) — everything this architecture rests
  on, including the decisions taken before the Studio and the platform patterns underneath it (a modular
  monolith with named domains, a backend for the frontend, an adapter at every boundary, a control plane and
  an execution plane) — and the brief read section by section, each marked built, partly or not here, with
  its reason ([docs/spec-v3-coverage.md](docs/spec-v3-coverage.md)).
- **A version is deployed to another Superset, and read back there.** `aibi/deploy.py`: the `.ssbi` package
  that was validated — Superset's own export of the dashboard, taken when the version was committed — is
  imported into a target, its governed connection **repointed** at that environment's warehouse (an import
  keeps an existing URI, which is how a run environment quietly reads the development one), the audience and
  the row-level rules SharpSight decided are granted, and then every chart is **asked for its data**, because
  an import answers 200 for a dashboard whose charts cannot draw. Nine steps, each recorded, each shown in the
  Studio's *Deployments* tab under the Deploy button, with what came back: « every chart arrived », « the
  audience is the one SharpSight decided », and one line per chart. It deploys and it does not approve: a
  version reaches a target already approved for that gate by somebody other than whoever asked for it, and an
  unvalidated version, a changed package or a Superset outside the compatibility matrix is refused before
  anything is imported ([ADR 0024](docs/adr/0024-a-deployment-carries-the-package-and-is-read-back.md)).
  `bash ai-bi.sh targets` · `deploy <project> <version> --to <target> --gate UAT` · `deployments <project>`.
  A target is five variables in the engine's own environment and is written down nowhere in this repository.
  Asking for one opens a **sheet**, not a browser's confirm box: what is about to happen, numbered in the
  order it happens, and a line for what you checked, which goes into the record.
- **A run environment to try it on, on one machine.** `docker-compose.run-target.yaml` starts a **second
  Superset** — its own metadata database, its own accounts, its own URL, and nothing of the kit's in it: no
  SharpSight menu, no Studio, no seeded connection, so a deployment has to bring everything it needs.
  `bash ai-bi.sh run-target` (and `--stop` / `--erase`).
- **The whole solution, tried end to end.** `bash ai-bi.sh e2e` (`scripts/e2e.sh`) walks it on a running kit
  and says, promise by promise, what held: the governed products are listed · a request becomes a project,
  a run and a first version · the Studio draws from one call, keeps a layout as a draft, refuses a save
  against an older one, validates without touching Superset, and answers all three roads (words, the
  gallery's form, the inspector's controls) with proposals that leave the draft alone · a title and a layout
  changed in the Studio become the next version through the pipeline, with the older one still there · a
  reviewer approves UAT, whoever asked cannot approve PROD, and the audit chain is intact record by record ·
  the version exports as one file carrying Superset's own export, and its checksum is the one recorded · the
  approved version deploys, every step done, every chart read back, and the link it leaves behind answers ·
  Superset's token opens the Studio's routes and nothing of the audit trail or the approvals · and both
  suites run. One broken promise does not hide the twenty behind it, and the exit code is the tally.
- **Four screens, drawn from the owner's own layouts** ([ADR 0025](docs/adr/0025-four-screens-on-one-page.md)).
  One page still — the project being edited is the same on all four — switched from the rail and by `1` `2`
  `3` `4`:
  **① Dashboard Builder** — a left pane of components grouped as a builder groups them (charts, KPIs, the
  governed columns that may become filters, the template's rows, and what a specification does not carry,
  said rather than hidden), beside Templates and Outline; the canvas; and **Chart Properties** on the right:
  Data, Style, Interactions, Governance.
  **② AI Copilot** — the conversation given room, the plan it answers with, and beside it the dashboard that
  plan would make, with what is new marked; *Apply* is the tab row's own button and is still an ordinary
  draft save.
  **③ Data Modeling & Assets** — the governed models by domain, the **columns** of the one being read (name,
  type, role, classification, and whether you may read it), dbt's own lineage graph as it was built, the
  tests it declares, the metrics valid on it, and its documentation. No row of the data anywhere, and the
  page says why ([ADR 0013](docs/adr/0013-no-data-preview-in-the-studio.md)).
  **④ Release & Deployment** — Version History, Validation, Environments and Releases beside a **release
  pipeline** (DEV → UAT → PROD) that shows where each version stands. The pipeline is a picture, not a set
  of buttons: *« + New Release »* is the commit, *« Run Validation »* is the validation, and promoting still
  needs a second person on the command line.
  The bar splits in two — identity, search and the quiet icons on top, the screen's tabs and actions below —
  and the drawer is gone, its six tabs now the release screen's four. Nothing that could be done before was
  dropped; what left the bar is on the screen it belongs to, or one keystroke away in ⌘K.
- **Typing works properly now**: a redraw puts focus and the caret back where they were, so the search box
  in the bar can actually be typed in.
- Proof: 231 engine tests (2 skipped), and `python studio/preview.py --check` driving the workspace in a
  browser against a stand-in engine — **eighty-two promises**, no console error, including that no row of
  data appears on any of the four screens.
- Not run against a kit yet: this was built where no Superset could be started. What that leaves owed is in
  [docs/superset-studio.md](docs/superset-studio.md) §11 — and `bash ai-bi.sh e2e` is now the one command
  that answers it on a machine that has one.

## Not in a bundle yet — the AI BI engine (2026-10-03)

An optional overlay: the kit starts without it, and its `docker-compose.yaml` is not edited. The engine is 0.3.0. One thing of the kit's own did change with it, on 2026-10-04: its theme and sign-in page are SharpSight blue, in the same image tag. See [docs/ai-bi.md](docs/ai-bi.md).

- **A request becomes a dashboard awaiting review.** `bash ai-bi.sh demo` takes the POC's own sentence through
  twenty-two steps: certified datasets and metrics found, the one missing column (the map's region code)
  supplied by a generated dbt model on a branch of its own, a dashboard specification validated before Superset
  is called, seven charts and three filters created through the REST API, 220 checks, a screenshot, a git commit,
  `READY_FOR_REVIEW`. Then `approve … UAT` and `approve … PROD`, by two other people.
- **What is governed stays governed**: registries in `registry/`, a real dbt project in `dbt/`, three database
  roles (the service that would talk to a model can write to one schema of drafts and nothing else), every action
  a tool with a policy check and an audit record, an `.ssbi` package built from the commit that was validated.
- **Who may open a dashboard is SharpSight's decision, enforced in Superset.** The roles granted every dataset
  of a dashboard become its roles there; a row-level policy becomes a rule, and follows a dataset into what is
  derived from it. Superset is then asked, by probe accounts holding one role or none, and a promotion to PROD
  is withdrawn if its answer is not SharpSight's. Roles or rules changed by hand stop a promotion.
- `ai-bi.sh check-adapter` drives copy, export, embedded configuration, delete and import on a copy — the roads
  a generation does not take, and the first thing to run after a Superset upgrade. `ai-bi.sh discard` takes a
  draft project away.
- **People.** `ai-bi.sh person <user>` makes a Superset account for a person of `rbac.yaml`; `ai-bi.sh people`
  gives every account the SharpSight roles it should hold and takes back the rest.
- A request can ask for a **share** (a pie, for a metric whose parts add up) and a **volume over time** (an
  area, drawn from zero). The dashboard title is no longer cut by a pixel: it is drawn in the weight Superset
  measures it in.
- **The updated brief's factory (its sections 18 to 32).** The ten **quality gates** replace DEV/UAT/PROD plus
  a review status: a version carries its gate and the way there, and a run that fails validation leaves
  `VALIDATION_FAILED` on its project. The `.ssbi` package is cut when a version becomes reviewable and checked
  again at each gate; one that changed stops at `UAT_FAILED`. Two tools (`get_dimension`,
  `create_deployment_package`), a sixth template (`management`), a **classification on every metric**, a
  `version` in the specification, « the CEM Executive template » read as the brief writes it. Validation gains
  null rate, dependencies, export permissions, direct URL access, concurrency, excessive whitespace and
  template violations; the audit record gains metrics accessed, visual QA results and the git commit; and
  `ai-bi.sh compatibility` puts the compatibility matrix beside what is running. Found on the way, by reading
  the output and looking at the picture: the audit trail had been writing `"passed": "[redacted]"` (the word
  was taken for a password), and a pie of four parts left one unnamed.
- **Superset is the interface** (the brief's « Superset-native » revision). A BI Studio of our own was built —
  first with its own screens, then as a frame around Superset — and taken out again: the engine has no page.
  What it knows is shown by Superset itself:
  - a **SharpSight menu** in Superset's top bar, where a person sees the entries they may open and no others
    (`rbac.yaml` decides): **BI Factory**, **Data Quality**, **Impact Analysis**, **Releases** and **Audit
    trail** — five ordinary Superset dashboards over a copy of the engine's records, each granted to the roles
    that hold the matching capability — and **dbt's own documentation**, behind Superset's sign-in;
  - **tags in Superset's own lists**: a project's dashboard says its environment, its gate and its project;
    its charts their project and domain; its datasets their domain and whether they are certified;
  - the **certified badge**, set on a dashboard and its charts when a person approves them for PROD.

  `ai-bi.sh where` says where to look; `ai-bi.sh request` is `generate` under the word the menu uses. The kit's
  Superset image and configuration are not edited: a configuration layer and three mounts, in the overlay.
- **A chart is edited in Superset, and the edit becomes a version.** `ai-bi.sh validate <project>` reads what
  a person changed in Superset's own Explore back in the specification's words — what changed (metric,
  dimension, kind, top N, time grain, order, title), what cannot be a governed chart and why (an ad-hoc metric,
  an unapproved visualization), and what a specification has no place for. `ai-bi.sh commit <project>` takes it:
  the same validation, visual QA, version, git commit, package and review as a generated dashboard. Until then
  a reviewer is refused, as before.
- **What a commit undoes is named first, and a reviewer reads what a version changed.** `validate` and `commit`
  now also say what was changed on the dashboard itself in Superset's editor — a chart added by hand, a layout
  that is no longer the template's, filters that are not the specification's — because a commit applies the
  specification again and undoes them. *Releases* has a table of what each commit took, set back and undid.
- **SharpSight's own colours lead.** The interface is SharpSight blue, the sign-in page with it, and a
  dashboard is drawn in a `sharpsight` series (measured for colour-vision deficiency; the numbers are beside
  it in `superset_config.py`). A client's colours are a **brand** a dashboard wears when the request says so —
  « in inwi's colours » — an accent and a series, nothing of Superset's own page. Templates go to 1.2.
- **`sharpsight-bi.supx`: a Superset extension.** Loaded by Superset's own extension system — a SharpSight
  panel in SQL Lab (project, environment, gate, *Validate*, *Commit*), two commands, and the backend API they
  call, which asks the engine for the person signed in to Superset. It approves nothing, and cannot: Superset
  holds a token of its own that the engine accepts for those calls only — UAT and PROD stay on the command line. The bundle is built reproducibly, its checksum recorded and verified before Superset is
  allowed to load it; the engine reports whether it was loaded. Developers' accounts are given Superset's
  `sql_lab` role, so the panel is somewhere they can go.
- **Where the brief's fourteen proof-of-concept items stand** is a table in `docs/ai-bi.md`: six done, five
  delivered as Superset dashboards, lists or in the SQL Lab panel rather than as the panels the brief draws,
  three not built (AI chat, a custom
  visualization, an MCP tool) — each with the reason.
- **Sections stay.** A template groups its rows into sections, each announced by a header on the Superset
  dashboard, with filters of its own; a section can be hidden.
- **What Superset 6.1.0's extension system can carry was read from the image** before anything was built on
  it: panels in SQL Lab only, no place on a dashboard or in Explore, no chat. It is recorded in the
  compatibility matrix and in `docs/ai-bi.md`; the extension package comes after the native step.
- **Which language model drives it is left open.** The planner is deterministic today; `aibi/llm.py` is the seam.
- Verified on a running kit, from an erased engine: the request (four times), fifteen review cases, a forced
  failure that wrote no version and put the reviewed dashboard back, the fix loop once under a stricter policy.
  Not built: the portal, Keycloak, Airflow, separate UAT/PROD environments, the `TelecomQoEChart` plugin, and
  the offline bundle does not carry the engine's image.

## 20261016

What 20261015b does, made safe to install over existing work. Bundle verified 24/24.

- **Upgrade rehearsed on REAL content**, from 20261006 (the version deployed on the server): a copy of the
  laptop kit's whole database server (Superset's metadata, the `roaming` mart, the kit's store), with its key.
  Before and after, through Superset: 4 of 4 connections answer, 246 of 246 charts return **the same data**
  (rows fingerprinted), 10 of 10 dashboards answer. In a browser, all 10 dashboards draw the same 72 charts on
  their first tab, with none in error. Counted by the installer, nothing fewer: 4 connections, 10 dashboards,
  246 charts, 53 datasets, 10 users, 12 roles, 680 tags, 2 row rules. The scheduled refresh signed in.
- Rehearsed again from a kit that never had the refresh account: devkit-init created it (users 1 → 2), and the
  refresh signed in and read every dashboard's schedule.
- **Nothing in the refresh account's set-up can stop Superset from starting.** If it fails, `devkit-init`
  prints a warning and Superset starts all the same, without the refresh.
- The installer now also counts users, roles, tags and row-level-security rules before and after an upgrade.
- `rehearse-upgrade.sh` takes `REAL_CONTENT_FROM=<kit project> REAL_CONTENT_ENV=<its .env>` to rehearse on a
  copy of a real kit, and opens every dashboard in a browser before and after (`rehearse_dashboards.mjs`).
  Charts saved without a query (most imported ones) are drawn only by a browser; the API half asks their dataset.

## 20261015b

- **Each dashboard has its own refresh schedule, set in Superset as a tag**: `refresh:06:30` (every day at
  06:30, one tag per time), `refresh:30m` / `refresh:2h`, `refresh:off`. Untagged dashboards follow `.env`.
- **Between two refreshes the database is not asked.** 20261015 re-queried an entry before it expired, so a
  dataset with a 900 s timeout of its own hit the cluster every ~13 minutes. Now the refresh stretches the cached
  entry's life to the next scheduled refresh (seen: 37 550 s for a 06:30 schedule at 20:19).
- `--status` (each dashboard: schedule, charts kept warm, age of the data, next refresh) and `--now <id|all>`.
- Only requests sent by a **dashboard** are recorded (not a chart being edited in Explore). Each request is
  attached to its dashboard through the address or, for filters, the form data: 11 of 11 on Roaming Outbound,
  where only 5 carried the dashboard in the address.
- Measured in a headless browser on the laptop, Roaming Outbound (6 filters + 5 charts): last chart's data at
  20.1 s with an empty cache; with the refresh, 11 of 11 requests from the cache in 0.3–0.7 s each, the page
  complete at 6.8 s (Superset draws the filters before it asks for the charts).

## 20261015

- Upgrade rehearsed from 20261006 with `rehearse-upgrade.sh`: every check passes, 2/1/1/1/1 before and after,
  nothing to migrate; settings version 3 gave the rehearsal 4 workers (14 CPUs, 15 GB) and added the new settings.
  Bundle verified 24/24.

- **Dashboards open on cached data: a scheduled refresh.** A new container, `devkit-refresh`, writes down every
  chart request a dashboard sends (the body as the page sends it, so that a replay fills the same cache entry)
  and asks it again every `DEVKIT_REFRESH_EVERY_MIN` minutes (60), or at the times of `DEVKIT_REFRESH_AT`.
  Every entry is also refreshed before it expires, and after a restart the whole list is asked again. It signs in
  with its own account, `devkit-refresh`, whose password is derived from `DEVKIT_SECRET_KEY` and written
  nowhere. Checked on the laptop's kit: six legacy charts and one `chart/data` chart recorded. Their cache
  entries were deleted, then refreshed at the next pass, then read by a new visit **from the cache, under the
  same keys**. Redis was restarted empty: the 7 requests were put back and refreshed in 4 s.
- **The cache is 1 GB, not 96 MB** (`DEVKIT_REDIS_MAXMEMORY`). A CEM result can weigh megabytes, and a full
  cache throws out its oldest entries: one dashboard's charts pushed out another's.
- **More room, as settings**: Superset 4 CPUs / 4 GB (`DEVKIT_CPUS`, `DEVKIT_MEMORY`, was 2 / 1 GB), its
  database 1 GB (`DEVKIT_DB_MEMORY`), and 4 workers by default. On an upgrade (settings version 3), a `.env`
  still on the old default of 2 workers gets 4 if the machine has at least 4 CPUs and 8 GB; otherwise it keeps 2,
  and the installer says why.
- Found on the way: the product's imported datasets carry a cache timeout of **900 s** of their own, which
  overrides the kit's hour. The refresh keeps such entries alive at their own pace; empty the field to follow
  the kit.

## 20261006

- Upgrade rehearsed from 20261005b with `rehearse-upgrade.sh`: every check passes, 2/1/1/1/1 before and after,
  nothing to migrate, no settings change (the `.env` was already at settings version 2).

- **A CEM dashboard opened first thing no longer breaks the worker.** Several charts starting the Java VM at
  once failed with « Native Library … already loaded in another classloader », and every CEM query in that
  worker then failed until Superset was restarted (seen on the deployed kit, 20261005b). `yacht_dialect.py`
  now lets one thread make a worker's first connection while the others wait. Checked with a stand-in for the
  JVM, in the kit's container: twenty threads at once, one start, twenty connections (twenty starts without
  the lock). **Not checked with a real JVM and a real cluster** — that is the deployed kit's to show.

## 20261005b

- **An upgrade keeps what you made, and says so.** Unpack the bundle in a NEW folder and run `bash install.sh`:
  it finds the kit already on the machine (compose records the folder on each container), brings its `.env` —
  the key every saved connection is encrypted with — `cem.env`, the driver, krb5 and the exports, backs
  Superset's own database up into `backups/`, lets Superset migrate it, and counts connections, dashboards,
  charts, datasets and saved queries before and after; fewer after is an error that names the backup and the
  command to put it back. The start-up job says whether every connection is still readable with this kit's
  key. `--from <folder>` when the old folder cannot be found. Until now a new folder stopped at « its .env is
  gone », and the installer recreated the containers — running the migrations — before anything was backed up.
- **An older `.env` is brought up to date once** (`DEVKIT_SETTINGS_VERSION`): the inert `DEVKIT_DATA_CACHE_TTL=60`
  becomes 3600, a value somebody chose is left alone, and the settings this version adds are appended with
  their defaults.
- **No SharpSight menu and no AI BI engine in a bundle — guaranteed, not habitual.** The bundle's
  `.env.example` and README no longer carry the engine's section, `verify-bundle.sh` fails a bundle that holds
  any of the overlay's files, settings or image, and an upgrade from a bundle removes the engine's containers
  from a kit that ran them. What the engine made inside Superset (its dashboards, its two connections) is
  content and stays.
- **Rehearsed, end to end**, as a second compose project beside the kit in use: the bundle of 3 October
  installed by its own installer; a connection with a saved password, a dataset, a chart, a dashboard and a
  saved query made in it; then this bundle from a new folder, nothing copied by hand. 2 connections, 1
  dashboard, 1 chart, 1 dataset, 1 saved query before and after; a query THROUGH the saved connection still
  answers (165); cache 3600 s inside Superset; the port unchanged; Oracle reaches the network; a second
  `bash install.sh` takes no second backup. The first rehearsal found two faults of this very change — the
  cache stayed at 60 s, and seven `AIBI_` settings were written into the upgraded `.env` — both fixed and the
  whole rehearsal run again. **Not rehearsed:** an upgrade of a kit that runs the engine, and one across a
  Superset version change (both bundles are 6.1.0, so « nothing to migrate » is what was seen).

## 20261005

- **Oracle connects.** Every Oracle connection failed with « cx_Oracle version 5.2 and above are supported »,
  whatever the connection string: `devkit_drivers.py` registered python-oracledb under the name `cx_Oracle` but
  kept the driver's own version number (2.5.1), which SQLAlchemy 1.4 reads as cx_Oracle's and refuses. It is
  now stated as the 8.3 the dialect expects. Checked on the running kit: « test connection » to a port where
  nothing listens answers `DPY-6005 … Connection refused` — the driver reaches the network. Not checked: a real
  Oracle server, none being reachable.
- **The kit's database may use four CPUs, not one** (`DEVKIT_DB_CPUS`). A dashboard tab sends its chart queries
  together and a query over the mart's views is CPU work: Roaming Outbound's four KPI tables took 6–8 s each
  on the page while the same query answers in 0.2 s alone. Measured through Superset, uncached, asked as the
  page asks: 0.25 s each alone, the four together done in 0.75 s. A limit, not a reservation: no memory cost.
- **A chart's result is kept one hour, and the setting is finally heard.** `DEVKIT_DATA_CACHE_TTL` was in
  `.env.example` and read by the configuration, and compose never passed it — nor the metadata cache's, nor the
  query timeout, which reached gunicorn alone. All four are passed now. The default goes from 60 s to 3600 s:
  the cache key is the query, so an edited chart is never served stale; only new data behind the same query
  waits, and « Force refresh » asks again. Checked: asked again, a chart comes back from the cache in 0.07 s,
  « kept for 3600 s ».
- **A worker is replaced after 10 000 requests** (`DEVKIT_WORKER_MAX_REQUESTS`, with jitter), so memory gathered
  over days is given back. A precaution: nothing measured asked for it.
- **Optional: HTTPS with HTTP/2 in front of Superset** — `COMPOSE_PROFILES=tls` and `DEVKIT_BEHIND_PROXY=true`.
  nginx on `https://localhost:3443` beside the plain port, **which does not move: 3002**. A self-signed
  certificate is made on first start and kept in a volume; `DEVKIT_TLS_DIR` takes your own. The nginx image
  travels in every bundle. 3443 and not 8443: the SharpSight platform's own edge publishes 8443 on the laptops
  this kit is for, and the first start failed on it. Checked: a browser is offered `h2`, a sign-in and a chart
  go through it, responses are compressed. **Not measured: what it gains on a dashboard** — that needs a
  browser's network panel.
- Checked on the rebuilt kit, as its admin, before and after these changes: sign-in, French interface, the five
  engines offered, SQL Lab on every connection, all 246 charts of the 10 dashboards (none failed, median
  0.13–0.17 s), the engine and its seven menu pages.

## 20261002

- **The import sets the connection's URI again after importing.** `import-dashboards` matches a database by
  UUID and keeps the one it already has, so a kit that had imported before kept pointing at the platform's
  `superset-db` — a host that does not exist on another network — while the archive on disk was correct.
  Idempotent on a fresh kit, a repair on an old one.

## 20261001b

- **The platform's dashboards, with their data.** `export-for-devkit.sh` (on the platform, read-only) and
  `import-into-devkit.sh` (here) are tested, fixed and shipped in the bundle. The mart restores into a database
  called `roaming`, because Superset 6 records a *catalog* per dataset and for PostgreSQL the catalog is the
  database — restoring into `devkit` gave a dashboard that drew nothing and said nothing.
- Container paths inside `sh -c` so Git Bash cannot rewrite them; the container-side tidy-up can no longer fail
  the import.
- Verified: 78 tables and views restored, 192 charts and 38 datasets imported, the Roaming Hub on its nine tabs
  with real values and no chart in error.

## 20261001

- **The kit arrives connected.** `devkit_seed.py` creates *Dev kit store* and *CEM live* on first start, plus
  three saved queries; idempotent, and it never touches what you rename.
- **Its own sign-in page**: the SharpSight lockup, French, navigation hidden until you are in, and a line saying
  what is already connected — through `head_custom_extra.html`, because Superset 6.1 draws that page in React
  and Flask-AppBuilder's template is never rendered.
- **No outbound beacon**: the telemetry pixel the frontend bundle adds at runtime is blocked by a narrow CSP.
- gunicorn's timeout follows the query timeout (the image's own default kills a worker at 60 s), and the
  metadata pool follows the thread count.

## 20260930

- **SQL Lab can query the CEM live** — `yacht://cem`, a SQLAlchemy dialect over the Yacht JDBC driver, with no
  credential stored in Superset's database.
- Identifiers quoted with backticks and `LIMIT` written as a number, because Spark reads a double-quoted word as
  a string and takes no bound parameter there. Before that, every cell of a chart held its own column name.
- `cem.env` is copied into the container on **every** start and says what it handed over, or that the CEM is not
  configured.

## 20260928

- **The CEM travels with the bundle**: the Yacht driver and a `cem.env.example` holding everything that is not
  secret. `cem.env` reaches the container through a volume, read as a file — compose interpolates `.env` *and*
  `env_file`, and a password with `$` or `#` arrived cut.
- **A second road to SQL Server**: pyodbc with FreeTDS beside pymssql.
- Both engines are added by a **form** rather than a URI, and SQL Server is offered at all (Superset lists an
  engine only if that backend's default dialect imports — `mssql` defaults to pyodbc).
- The port moved to **3002**.

## 20260927

- First bundle: Superset 6.1 on the product's pinned digest, SQL Lab open, drivers for PostgreSQL, SQL Server,
  Oracle (python-oracledb in thin mode, no Instant Client) and Hive, the SharpSight brand, French interface, and
  an offline installer.
