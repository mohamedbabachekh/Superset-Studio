# SharpSight AI BI — the engine, on top of the kit

A proof of concept of the AI-assisted BI development engine: a request in plain words becomes a **governed,
validated, versioned** dashboard, and then waits for a person.

> Superset draws. dbt transforms. Git remembers. SharpSight decides what may be asked of each, checks what came
> back, and keeps the record. The AI is a developer's assistant inside those walls, not an administrator.

It is a **vertical slice**: one request goes all the way through, on real components. What is deliberately not
here yet is listed at the end, and nothing below claims otherwise.

**Superset is the interface** (chapter 3). The engine has no page of its own: what it produces is a Superset
dashboard, and what it knows — the gate each project stands at, the checks of every validation, the audit
trail, data quality, what a change would touch, releases, dbt's documentation — is shown in Superset too,
through a SharpSight menu in Superset's own top bar, tags and badges in Superset's own lists, and five ordinary
Superset dashboards. A dashboard is asked for and
approved on the command line.

---

## 1 · Start it, and run the brief's own request

```bash
bash install.sh        # the kit, if it is not installed yet
bash ai-bi.sh          # the engine: three one-shots and one service, added to the kit's compose project
bash ai-bi.sh demo
```

`demo` sends, as `dev.user`:

```
Create a CEM QoE dashboard using the CEM Executive template.

Use QoE Score, Active Users and Degraded Users.

Show daily trend, regional ranking, QoE distribution and geographic view.

Add Date, Region and RAT filters.
```

— the brief's own words (section 18) — and prints each of the twenty-two steps as it finishes. It ends on a
version `1.0.0` at the gate `READY_FOR_REVIEW`, with its git commit, its `.ssbi` package in `./exports/`, and a
dashboard at `http://127.0.0.1:3002/superset/dashboard/cem-qoe-overview/`.

Then, as two other people:

```bash
bash ai-bi.sh approve cem-qoe UAT                                  # lead.reviewer
bash ai-bi.sh approve cem-qoe PROD "checked in UAT with the CEM team"   # release.manager
bash ai-bi.sh audit cem-qoe                                        # everything that was done, and by whom
```

Two more, for housekeeping:

```bash
bash ai-bi.sh discard <project>          # a project still in DEV, or rejected: taken out of Superset and the store
bash ai-bi.sh check-adapter <project>    # every road of the Superset adapter, driven on a copy of its dashboard
bash ai-bi.sh person east.analyst        # a Superset account for a person of rbac.yaml; its password is shown once
bash ai-bi.sh people                     # every Superset account given the SharpSight roles rbac.yaml says, no others
bash ai-bi.sh compatibility              # the compatibility matrix, beside the versions actually running
bash ai-bi.sh validate cem-qoe            # what was edited in Superset since: taken, refused, set back
bash ai-bi.sh commit cem-qoe "weekly"    # take it: a new version, validated, awaiting review
bash ai-bi.sh where                      # where to look in Superset: the menu's six entries, and who sees which
# the same two, for a developer signed in to Superset: SQL ▸ SQL Lab, the SharpSight panel on the right
```

| | |
|---|---|
| The engine's API | `http://127.0.0.1:3003` — loopback only, always (`AIBI_PORT`) |
| Stop it | `bash ai-bi.sh --stop` — the kit keeps running |
| Its tests | `bash ai-bi.sh test` |
| Memory | under 0.1 GB idle, about 0.5 GB while a dashboard is being rendered (measured: 77 and 479 MiB) |
| Time | the request below takes 60 to 145 seconds, most of it dbt and three page loads (over three minutes once, right after an image build, on a machine short of memory) |

The CEM data in this kit is **synthetic** (`services/ai-bi/sql/bootstrap.sql`): sixteen regions, four sites each,
four technologies, ninety days ending yesterday, every value a pure function of site, technology and day. With
a real cluster the same table is what `cem_to_mart.py` lands, and only the dbt variable `cem_source_schema`
changes.

---

## 2 · What happens to a request

| # | Step | What actually happens here |
|---|---|---|
| 1 | Parse the requirement | the request is read against the governed vocabulary (`registry/lexicon.yaml`) into a `Requirement`: a template, metrics, filters, and views — trend, ranking, distribution, share (a pie), volume over time (an area), geographic view, detail. A template is what its phrase **ends** with: « the CEM Executive template » is `executive`, and the run says it read it so. What is not recognised is **reported**, never guessed |
| 2 | Find certified datasets | only what the requester may read; certified first |
| 3 | Find certified metrics | registered metrics only — a metric that is not in the registry is refused, not improvised |
| 4 | Identify missing data | each view is laid into a template slot and asked: which dataset serves it? Here the map needs a region **code**, and the certified mart has only the region's name |
| 5 | Determine transformations | a certified dimension (`cem.region`) has the code and shares the key → a join, as a **plan** |
| 6 | Generate the dbt model | SQL and tests rendered from the plan, checked by the policy engine, written on branch `ai/cem-qoe` |
| 7 | Compile | `dbt compile`, target `dev` |
| 8 | Run dbt tests | the tests of what the model is built **from**, before it is built |
| 9 | Run the model | `dbt run` into `bi_dev`, deferring every other model to production; then the model's own tests |
| 10 | Register the dataset | as `cem.cem_qoe__qoe_daily_region_iso` — generated, **not certified**, this project's own, with its lineage from dbt's artifacts |
| 11 | Generate the specification | the normalized document of the brief's section 23, carrying the version it is about to become |
| 12 | Validate the specification | template, slots, types, metrics, roles, RBAC, theme. Superset has not been called yet |
| 13 | Create Superset assets | datasets with their certified metrics saved on them, one chart per slot, the dashboard, its filters — and **who may open it**: the roles SharpSight grants, and each role's row-level rule |
| 14 | Retrieve the dashboard's data | each component's query, through Superset's own query API; each filter is probed |
| 15 | Validate the query results | data, SQL, BI, security, performance — the last with every query sent again by four people at once |
| 16 | Render a screenshot | a headless browser opens the dashboard at each of the template's viewports — and once more with every filter set |
| 17 | Visual QA | geometry, clipped text, empty or failed charts, holes in the page, the template's own widths and heights — and what the dashboard itself requested |
| 18 | Fix detected problems | bounded fixes on the specification (top N, a shorter title, a compact number, another approved type). The brief's request needs none; see « what was verified » |
| 19 | Re-render | and validate again. If it still fails, **the run fails**: no version is written |
| 20 | Store the final version | the project's folder, which is the `.ssbi` layout |
| 21 | Git commit | in `bi-projects`, tagged `cem-qoe/v1.0.0` |
| 22 | READY_FOR_REVIEW | the version is marked, and its `.ssbi` package is cut — once, by the tool `create_deployment_package` |

A run is a record (`GET /runs/<id>`): every step with its times, what it found, and why it was skipped.

**The quality gates** (the brief's section 31) run beside the steps. A project is in exactly one:

```
DRAFT → AI_GENERATED → VALIDATING → READY_FOR_REVIEW → APPROVED_FOR_UAT → UAT → APPROVED_FOR_PROD → PROD
                           └→ VALIDATION_FAILED                            └→ UAT_FAILED
```

| Gate | Reached when | Moved by |
|---|---|---|
| `DRAFT` | the request is received (steps 1–10: reading, discovery, the dbt model) | the requester |
| `AI_GENERATED` | the specification exists (step 11) | the engine |
| `VALIDATING` | steps 12–19 | the engine |
| `VALIDATION_FAILED` | anything in steps 12–19 stops the run, or the generated dbt model does not compile, build or pass its tests | the engine |
| `READY_FOR_REVIEW` | every check passed; the version is committed and its package cut | the engine |
| `APPROVED_FOR_UAT` → `UAT` | a reviewer approves; then the package is checked against the checksum recorded when it was cut | a person, then the system |
| `UAT_FAILED` | that check fails, or the version is rejected in UAT | the system, or a person |
| `APPROVED_FOR_PROD` → `PROD` | a second person approves; then the dashboard is published and every role is asked what it gets | a person, then the system |

A version is written only by a run that reached `READY_FOR_REVIEW` — there is still no half-valid *version* for
a reviewer to find. A run stopped earlier leaves its record on the **project**: `last_attempt` in
`GET /projects`, with the gate the last generation stopped at, the way there, and the error; the run keeps the
same in `quality_gate`. In the audit trail the engine's own moves are `gate:<name>` records, reaching review is
`version:ready_for_review`, and each approval and each promotion is a record of its own. A version keeps its
whole way (`history`: gate, who, when, why). Sent back from review a version is a `DRAFT` again; either way the
next thing that happens to the project is a new version.

---

## 3 · Superset is the interface

The engine has no page of its own. Its address answers the API the command line calls, and nothing a person
opens: `/studio/` is gone, and with it the asset library, composing by hand and everything that existed for a
screen of ours. A person works in **Superset** — its dashboards, Explore, SQL Lab — and what SharpSight knows is
shown there, by Superset's own means.

> Superset is the BI application. SharpSight adds to it; it does not stand beside it.

| In Superset | What a person sees | How it gets there |
|---|---|---|
| **Dashboards** (Superset's own list) | each project's dashboard, tagged with the environment and the gate it stands in and with its project — `env:UAT`, `gate:UAT`, `project:cem-qoe` — and Superset's « certified » badge once a person approved it for PROD | the REST API: tags, and the certification a promotion sets |
| **Charts** (Superset's own list) | a project's charts, tagged with their project and the domain of their data, and certified with their dashboard at PROD | the same |
| **Datasets**, **Explore** | governed datasets and metrics certified, with their owner; a generated dataset carries a warning instead. Tagged with their domain, and `sharpsight:certified` or `sharpsight:generated` | the REST API, when a dataset is registered (chapter 4) |
| **SharpSight ▸ BI Factory** | where every project stands: counts by gate; the projects with their version, gate, checks and package; the checks of each latest version by category, and those that did not pass; the way through the gates; what was asked | a Superset dashboard over the engine's records |
| **SharpSight ▸ Data Quality** | the governed datasets — owner, domain, certification, freshness target, how many dashboards use each; the dbt models behind them, what each is built from, every dbt test and how it ended; the data checks of each project's latest validation | the same |
| **SharpSight ▸ Impact Analysis** | what a change would touch: dashboards and charts per dataset and per metric; every use of each — dataset, model, metric, dimension, chart, dashboard, gate — to search; dbt's lineage upstream | the same |
| **SharpSight ▸ Releases** | every approval and rejection, by whom, with its comment; the package and commit of each version and the gate it stands at; the way through the release gates | the same |
| **SharpSight ▸ Audit trail** | every action, by whom, how it ended; refusals and errors counted; actions per day; whether the chain was intact when it was published | the same |
| **SharpSight ▸ Data models (dbt)** | dbt's own documentation site — models, columns, tests, the lineage graph — behind Superset's sign-in | one route added to Superset, serving the file `dbt docs generate` writes |

`bash ai-bi.sh where` prints the addresses.

**Asking and approving are on the command line** — `ai-bi.sh request "…"`, `ai-bi.sh approve <project> UAT|PROD`
(chapter 1). That is a choice, made on 2026-10-04: no screen of ours, not even one under Superset's address.

### Editing in Superset, then validate and commit

A chart is edited where charts are edited: Superset's own Explore. Nothing of ours stands in the way, and
nothing of ours is needed to do it. What SharpSight adds comes after — the edit is read back, in the
specification's words, and becomes a version only through the same road as a generated one.

```bash
bash ai-bi.sh validate cem-network-pulse
#   QoE Score  (kpi_1)
#       ✗ cannot be taken — an ad-hoc metric (« Best score ») — a chart shows a governed metric
#   QoE Score, daily trend  (trend_1)
#       time_grain: P1D → P1W
#   Top 5 regions by QoE  (ranking_1)
#       title: QoE Score by region → Top 5 regions by QoE
#       row_limit: None → 5
#       · a specification has no place for: show_legend  (a commit sets it back)
#   cannot be committed: Superset holds what cannot be a governed chart — …
bash ai-bi.sh commit cem-network-pulse "weekly trend, top five regions"
```

| For each chart of the project, `validate` says | |
|---|---|
| **what changed, in governed terms** | metric, dimension, kind of chart, top N, time grain, order, bins, title — as the specification would say it. A commit takes these |
| **what cannot be taken**, and why | an ad-hoc metric in place of a governed one, a visualization nobody approved, a dataset that is not the project's, several metrics on one chart, a time grain or a number of rows outside what is allowed. Nothing is approximated: while one chart is refused, nothing is committed |
| **what a specification has no place for** | a setting of Superset's the specification does not carry (a colour, a legend). It is named; a commit sets it back |
| **what was changed on the dashboard itself** | in Superset's dashboard editor: a chart added by hand, a layout that is no longer the template's, filters that are not the specification's. Named too, and in capitals that A COMMIT UNDOES THEM — the template's layout and the specification's filters are applied again |

`commit` takes what can be taken into the project's specification and sends it down the road of chapter 2 from
step 11 on — validation, the BI engine, the data, a screenshot, visual QA, a version, a git commit, a package,
`READY_FOR_REVIEW`. The specification is applied again on the way, so what Superset holds afterwards is the
governed form of what the person made. It needs `bi.generate`; it is refused on a project that stands in UAT
or PROD, as a regeneration is; and a reviewer cannot approve the edited dashboard before it — the fingerprint
no longer matches the version (chapter 4). `ai-bi.sh restore` remains the way back to the version as it was.

`GET /projects/<id>/changes` is what `validate` prints; `POST /projects/<id>/commit` starts the run.

**What a reviewer reads.** After a commit Superset and the specification agree again, so the difference
between two versions is written where the reviewer looks: *SharpSight ▸ Releases* has a table of what each
commit took — chart by chart, what was specified and what it is now, what was set back, what was undone on
the dashboard, by whom and with which comment (`bi_factory.version_changes`, from the audit record of the
commit).

### The menu, and dbt's documentation — `superset-addon/`

Two files, mounted into the kit's Superset by the engine's compose overlay. The kit's image and its
`superset/superset_config.py` are not edited: `superset-addon/superset_config_ai_bi.py` imports the kit's
configuration whole and extends one hook, and `SUPERSET_CONFIG_PATH` points at it. Without the overlay Superset
starts exactly as the kit ships it.

- **The menu** is six `appbuilder.add_link` calls — what Superset does for its own entries — under a
  « SharpSight » category.
- **A person sees the entries they may open, and no others.** The rule is SharpSight's: `rbac.yaml`, mounted
  read-only, read once at start. The four dashboards of the factory are for the roles that hold
  `bi.generate`, `bi.approve_uat` or `bi.approve_prod`; the audit trail for those that hold `audit.read`;
  dbt's documentation for a role granted at least one dataset, or one of the above. A role of `rbac.yaml` is the
  Superset role `sharpsight_<role>`; an administrator of Superset sees everything; someone who may open nothing
  sees no SharpSight menu at all.
- **dbt's documentation** is served at `/sharpsight/dbt-docs/`, and the route checks that same rule itself —
  hiding an entry is not what protects it. An account with no dataset is refused (403), as the engine refuses it
  a model's schema; an anonymous request is sent to Superset's sign-in. The `dbt-runner` writes the three files
  of the site (`index.html`, `manifest.json`, `catalog.json`) into a volume Superset mounts read-only.

### The engine's records, where Superset can draw them — `aibi/superset_view.py`

The records live in the engine's volume: a SQLite file, a JSON-lines audit trail, git. They are **copied**, whole
and in one transaction, into the schema `bi_factory` of the warehouse — `projects`, `gate_history`, `checks`,
`requests`, `audit` (the newest 5 000 records), `datasets`, `metrics`, `dbt_models`, `dbt_tests`, `usage`,
`deployments`, `published` — and five dashboards are made over them through the REST API. The registries and
dbt's artifacts are rows there too: that is what Data Quality and Impact Analysis are drawn from. Nothing reads
the copy back: drop the schema, delete a dashboard, take a tag off, and the next refresh puts it back.

It is refreshed when something happens: every action leaves an audit record, and the trail tells the view
(nothing is polled; a burst of records is one refresh). `GET /health` says when it was last done and how many
rows went — `shown_in_superset` — and why not, if the last attempt failed; it is tried again within a minute.
`ai-bi.sh` waits for the first one before it says « ready », so the menu never leads to a dashboard that is
not there yet.

**Who may read the copy.** The API asks `audit.read` for the trail; the copy must not be a way round that.

| | |
|---|---|
| the database | `bi_factory` is read by a role of its own, `bi_records_reader`. The role every BI chart reads through, `bi_reader`, is refused the schema |
| the connection | a second Superset connection, « SharpSight BI records (read-only) », **closed to SQL Lab** |
| the dashboards | granted — Superset's dashboard roles — to the BI-engine roles of those `rbac.yaml` gives the matching capability: *BI Factory*, *Data Quality*, *Impact Analysis* and *Releases* to those who generate or approve (`bi.generate`, `bi.approve_uat`, `bi.approve_prod`), *Audit trail* to those who hold `audit.read`. `ai-bi.sh people` gives a person's account those roles |
| the badge | these dashboards are **not certified**: in Superset's list that badge means « a person approved this for PROD ». They carry the tag `sharpsight:system` |
| the workspace | the rows of a project carry its `tenant`; four tables have none — `metrics`, `dbt_models`, `dbt_tests`, `published` — because the registries and dbt's project are one for everybody here. This kit has one workspace, so no row-level rule is set on these tables — with several, one rule per workspace is what is missing, and those four would need a tenant first |

**Tags, in Superset's own lists.** On a project's dashboard, three: `env:<DEV|UAT|PROD>`, `gate:<GATE>`,
`project:<id>` — kept exact as the gate moves, and a tag a person added is left alone. Three, and those, on
purpose: the list shows the first two tags of a dashboard in the order of the alphabet and counts the rest, and
with a domain tag as well the gate was the one hidden. The domain is said where it describes something: a
project's charts are tagged `project:<id>` and `domain:<domain>`, its datasets `domain:<domain>` and
`sharpsight:certified` or `sharpsight:generated`. A tag is not content: an export carries them (measured on
6.1), so `tags` is among the keys left out of the fingerprint a dashboard is validated under — otherwise
showing a gate would look like a change made after validation, and the next approval would be refused.

**Certified, at PROD.** A promotion to PROD certifies the dashboard and, with it, its charts: the same badge in
the chart list, set by the same person. Withdrawn together if the promotion is.

These five dashboards are plain Superset charts over a table — a count, a table, bars over time — written as
form data, without a template or a specification. They show records; they are not BI projects, and nothing of a
project's own is made this way.

### The extension — `superset-extension/`, `sharpsight-bi-0.1.0.supx`

A Superset extension of the kind Superset's own extension system loads: one bundle, a `.supx`. It is what the
revised brief asks for, in the one place Superset 6.1.0 has for it.

| It contributes | |
|---|---|
| **a panel** | « SharpSight », in SQL Lab's right sidebar: the project, the environment and gate it stands in, a link to its dashboard — and what the two buttons below answer |
| **two commands** | `sharpsight.bi.validate` and `sharpsight.bi.commit` — the *Validate* and *Commit* of the panel, registered with Superset so that anything else in it may run them |
| **a backend API** | `/extensions/sharpsight/bi/…`, inside Superset: `projects`, `projects/<id>/changes`, `projects/<id>/commit`, `runs/<id>` |

**It is a gateway, and nothing else.** The backend holds no state and decides nothing: each call goes to the
engine with the engine's token and the name of the person signed in to Superset, and the engine applies
`rbac.yaml` to that person. The name is read from Superset's session, on the server — never from the request.
An account that is nobody in `rbac.yaml` is told so; an analyst may validate and is refused a commit; a
developer's commit is the one of chapter 3's command line, run for them.

**The trust that was accepted, and where it stops** (2026-10-04). For this, Superset says who is signed in:
someone able to create a Superset account named like a developer can validate and commit as them. That was
accepted for these two actions and no other — and it is the **engine** that holds the line, not the
extension's source:

- Superset holds a token **of its own** (`AIBI_SUPERSET_GATEWAY_TOKEN`), not the one the command line uses.
  With it the engine answers four calls — the projects, a validation, a commit, a run — and refuses every
  other (403), whoever is named: approving, rejecting, restoring, the audit trail, a new request. So a fault in
  the extension, another extension, or Superset itself cannot approve with the only secret Superset has, and
  the rule that PROD needs a second person cannot be gone round from there. Approvals stay on the command line.
- A commit needs Superset's CSRF token. Flask-AppBuilder exempts an API from that protection unless told
  otherwise, and Superset's `@api` for extensions does not tell it: as first written, a POST carrying a
  developer's session cookie and no token started a commit. `csrf_exempt = False`; `check-adapter` sends the
  forged request and expects a refusal.

**Who reaches it.** The panel is in SQL Lab, so the accounts of those who generate (`bi.generate`) are given
Superset's own `sql_lab` role by `ai-bi.sh person` and `ai-bi.sh people`. It opens SQL Lab's page; no database
is granted with it.

**How it is built, recorded and loaded.**

- `superset-extension/build.py` makes the bundle: `manifest.json`, the backend's Python, the built panel. The
  zip is made so that the same sources give the same bytes on any machine — sorted names, one date, stored not
  deflated, one « made on » — and its `sha256` is written into `docker-compose.ai-bi.yaml`, not only beside the
  bundle: whoever could replace the bundle in its folder could replace a checksum lying there with it. A test
  rebuilds the bundle and compares.
- The panel is plain JavaScript built by webpack alone (module federation), with Node, on a developer's
  machine: `npm install && npm run build` in `frontend/`. The kit has no Node, so what was built
  (`frontend/dist/`, 12 KB) is in the repository — and `python superset-extension/build.py --check-frontend`
  builds it again elsewhere and says whether the two are the same. React and Ant Design are not in it: they are
  the page's own, so the panel wears Superset's theme.
- **Superset's loader checks nothing about a bundle** — no checksum, no signature. The kit's configuration
  layer does, before telling Superset where bundles are: a `.supx` that is not the one the compose file names, and Superset
  starts without extensions and says which. `ENABLE_EXTENSIONS` is on only when there is a recorded bundle.
- **The loader also fails quietly**: a backend that does not import is printed and skipped. So the engine asks
  Superset, at every refresh, whether `sharpsight.bi` is among what it loaded — `GET /health`,
  `shown_in_superset.extension` — `ai-bi.sh` says so at start when it is not, and `ai-bi.sh check-adapter`
  proves the API still answers.

### The Studio — `/sharpsight/studio/<project>/`

The visual builder of the brief's sections 46 to 60, added 2026-10-19. Superset 6.1.0 contributes a panel in
SQL Lab and nowhere else, so the Studio is **a page of Superset's own**: its page template, its application —
which draws the top bar — its session, its theme, and the workspace below. No fork, no second application, no
second React on the page (`studio/studio.js` is plain DOM).

What it edits is a **specification**, not a dashboard. That is what keeps the brief's first five rules: there
is no second renderer, no second chart editor, no second SQL Lab here.

| | |
|---|---|
| the canvas, two modes | **Compose** — the draft's rows, each tile a photograph of that chart — cut out of one picture of the real dashboard (`aibi/picture.py`), because Superset's own screenshot endpoints cannot work in this image. *Preview* opens it in a tab. **Live** — Superset's own standalone dashboard framed inside the workspace, with a box over each chart read from that page's own DOM: real charts, real data, no second renderer. A draft is not in it until it is committed, and the mode says so |
| what may be moved | the rows, in order; a chart's place on its row; a chart's width, taking from its neighbour, never below the template's minimum; a row's height. A chart may not leave its section — it has an owner and filters of its own — and with every template today being one row per section, that means it stays on its row |
| the inspector | what a specification carries for that chart (a title, what it is for, a top N, a time grain, an order, a number format) and its governance: the metric, its definition, its owner, whether it is certified; the dataset, its owner, its domain, its grain; the slot's purpose and what it accepts |
| the draft | the third state (§50.2): what a person is editing, kept with the project, saved a moment after each change, carrying a token so that two people with the page open cannot overwrite each other silently (§50.3) |
| validate | the engine's own Level 1 report on the draft, in the drawer — the layout, the slots, the visualizations, the metrics, the datasets, this person's clearance for each. It touches nothing in Superset |
| commit | the draft becomes a version **through the pipeline every other commit goes through**: the ten quality gates, Superset, visual QA, git, a package. Nothing here approves, and nothing here promotes |
| the library | the chart kinds the registry approves, grouped by what they are for, each saying which of this template's slots accept it and whether it is installed in this BI engine. One drags onto a row, or is clicked |
| the dashboard's own inspector | with nothing selected: its title, description and owner; its **filters** — the columns the template allows, on datasets it already uses, with how many it allows; its **security** — who may open the dashboard this becomes and the row-level rules that follow; and its layout |
| adding a chart | a gallery: a free slot of the template, a governed metric, a type the slot accepts. What comes back is a **proposal**, not a change — the registry chooses the dataset and refuses what cannot be drawn that way |
| the copilot | **deterministic** (`aibi/copilot.py`): it reads a request with the planner's own governed vocabulary and answers with a proposal — the changes, what they touch, whether validation would pass, and the specification as it would be. Add a view, take one off, rename, a top N, a time grain, an order, a number format, a width, a height, a row moved, « what is … ». What it does not understand, it says it does not understand. No language model has been chosen (`aibi/llm.py` is the seam); one plugs in by producing the same proposal |
| the drawer | Validation (Level 1, without touching Superset), Changes (what the draft changes about its version, and what Superset holds against what was validated), Last run (the levels only a run can produce — the data, the security probes, the render, the picture, the timings), Releases (every version, its gate, its package) |
| the keyboard | every move the mouse can make, and a card that lists them |

The layout a person places is **kept**: it is carried in the specification, in the template's own shape (rows
of slots with a width in twelfths), it is what the next build draws, and a commit no longer sets it back.
That is the first of the three decisions this chapter used to leave to the owner — the Studio takes it,
because a canvas whose work the next build undoes is a lie. What a template still owns is everything else:
which slots exist, what each accepts, what is mandatory, the minimum widths, the grid, the sections.

Looked at without Superset: `python studio/preview.py` (the same page, against a stand-in for the extension's
API) and `python studio/preview.py --check`, which drives it in a browser and holds it to thirteen promises.

### Where the brief's proof-of-concept list stands

Its section 38 lists fourteen things for one `sharpsight-bi.supx`. On Superset 6.1.0, with no fork:

| | The brief's item | Here |
|---|---|---|
| 1 | Project / environment context bar | **done, in the Studio** — project, environment, version, gate, draft state, and the commands beside them; and in the SQL Lab panel. On a dashboard and in Explore there is still no place for one |
| 2 | SharpSight menu entries | **done** — Superset's top bar, shown by `rbac.yaml` |
| 3 | Governance drawer | **in the Studio's inspector**, per chart: metric, definition, owner, certification, dataset, domain, grain. Elsewhere: certified badges and tags in Superset's lists; *Data Quality* |
| 4 | Asset Library panel | **in the Studio's explorer**: the project's outline, and the governed datasets and metrics this person may use. Adding a chart is still a request to the engine, not a drag from the list |
| 5 | AI chat panel | **done, deterministic** — the Studio's copilot answers with a proposal and changes nothing by itself. No language model was chosen; 6.1.0 has no chat contribution either, which is moot on a page of ours |
| 6 | Validate command | **done** — panel, command, command line |
| 7 | Commit command | **done** — panel, command, command line |
| 8 | dbt status panel | as a dashboard: *Data Quality* (models, every test); dbt's own documentation under the menu |
| 9 | Impact Analysis view | **done**, as a Superset dashboard |
| 10 | Release / deployment panel | as a dashboard: *Releases*. Approving is on the command line, on purpose |
| 11 | One custom telecom visualization | **not built**: compiled into Superset's frontend — a rebuild of it |
| 12 | SharpSight theme | **done** — SharpSight's colours lead; a client's are a brand |
| 13 | One backend extension API | **done** — `/extensions/sharpsight/bi/` |
| 14 | One SharpSight MCP tool | **not built**: Superset's MCP service lacks its dependency in this image |

### Sections

A template groups its rows into **sections** (`sections:` in the template); a specification may give each a
title, a description, an owner, filters of its own, and hide it.

| | |
|---|---|
| title | a header above the section's first row, on the Superset dashboard |
| description, owner | SharpSight's: the version's documentation |
| filters | a Superset filter scoped to the section's charts and no others — named `Region · Accountability` in the filter bar. Probed, and judged on the dashboard, like a filter of the whole dashboard |
| visibility | `hidden`: the section stays in the specification, its components kept aside (`parked`), and is not built. A section holding a mandatory slot cannot be hidden |
| layout | the template's rows — a dashboard cannot move a row from one section to another |
| permissions | **not offered.** Superset grants a dashboard, not a part of one; a narrower audience for one section could not be enforced, so it is not promised |

A specification that names no section is laid out as rows only, as before sections existed — which is why an
older version still restores as it was.

### What Superset 6.1.0 lets an extension do

The revised brief (« Superset-native », 2026-10-03) asks for a Superset extension package, `sharpsight-bi.supx`,
and — its section 45 — that every extension API be checked against the pinned version first. Read from the
kit's image and from Superset's 6.1.0 documentation on 2026-10-04:

| The brief asks for | In Superset 6.1.0 |
|---|---|
| a backend API contributed by an extension | yes — `superset_core.rest_api` (`@api`) |
| commands (Validate, Commit, …) | yes — `registerCommand` |
| panels: governance, asset library, dbt status, release | **in SQL Lab only** — `sqllab.leftSidebar`, `rightSidebar`, `panels`, `editor`, `statusBar`, `results`, `queryHistory` |
| a context bar, an outline, a governance drawer on dashboards, Explore, datasets | no extension point exists |
| SharpSight entries in a dashboard's own actions menu | no — an extension's menu items are SQL Lab's |
| an AI chat panel | no chat contribution (it is in Superset's unreleased documentation only) |
| an MCP tool | the decorator exists (`superset_core.mcp`); the MCP service's dependency, `fastmcp`, is not in the image |
| a semantic-layer extension | listed in the 6.1.0 documentation, absent from the package the image ships |
| a custom telecom visualization | compiled into Superset's frontend: a rebuild of it |

The package is `apache-superset-core 0.1.0rc3`, behind `ENABLE_EXTENSIONS`, off by default. Upstream, the pull
request that adds dashboards and Explore to the extension API was still open on 2026-10-04, and adds calls, not
places to put a panel. `registry/compatibility.yaml` records this beside the versions.

So, following the brief's own order (extension API, else a supported API, else do not build — a fork only when
essential), the decision taken on 2026-10-04 was **native first, then the extension** — and both are here:
Superset's own features everywhere, and a real `.supx` with a backend API, two commands and a panel in SQL
Lab. A thin fork was not chosen, so nothing is drawn on a dashboard or in Explore beyond what Superset itself
draws.

### What is used that is not the extension API

To be looked at again at every Superset upgrade. `ai-bi.sh check-adapter` and the browser session of chapter 8
are what would notice.

| | Where | What it relies on |
|---|---|---|
| a configuration layer over the kit's | `SUPERSET_CONFIG_PATH`, `superset-addon/superset_config_ai_bi.py` | Superset loading the file that variable names |
| the hook | `FLASK_APP_MUTATOR` | being called with the application, before Superset adds its own views |
| menu entries and their permissions | `appbuilder.add_link`, `sm.add_permissions_menu`, `sm.add_permission_role` | Flask-AppBuilder's public API; Superset building its AppBuilder with `update_perms=False` is why the permissions are created by hand |
| the entries placed last | `appbuilder.menu.menu`, reordered at the first request | Flask-AppBuilder keeping its menu as a list |
| who sees which entry | `cond=` on each link and on the category; `current_user.roles` | Flask-AppBuilder asking `cond` when it lists the menu; `registry/security/rbac.yaml` mounted read-only, and a role of it being the Superset role `sharpsight_<role>` |
| the route for dbt's documentation | a Flask blueprint, `/sharpsight/dbt-docs/` | Flask, and `flask_login.current_user` |
| tags on a dashboard | `POST`/`DELETE /api/v1/tag/3/<id>/` | object type 3 being a dashboard (`superset.tags.models.ObjectType`); the `TAGGING_SYSTEM` flag, which the kit sets |
| a dashboard's audience | `roles` on `PUT /api/v1/dashboard/<id>` | the `DASHBOARD_RBAC` flag, which the kit sets |
| Superset's own extension system | `ENABLE_EXTENSIONS`, `EXTENSIONS_PATH`, the layout of a `.supx` (`manifest.json`, `backend/src/…`, `frontend/dist/…`) | `apache-superset-core 0.1.0rc3`, a release candidate: the first thing to look at when Superset is upgraded |
| the permissions of the extension APIs | `sm.add_permissions_view`, at the first request (the add-on) | Superset registering its extensions API only when the flag is on — so the kit's `superset init`, which runs without it, never wrote them, and even an administrator was answered 403 |
| what the page gives an extension | `window.superset.views`, `.commands`, `.authentication`; the location `sqllab.rightSidebar` | read from the 6.1.0 frontend bundle, not from a package |
| React and Ant Design, shared | module federation's share scope: react 17.0.2, antd 5.27.6 | Superset's page offering them as singletons to an extension |
| the extension's API path | `/extensions/sharpsight/bi/…` — not under `/api/v1` | where `@api` puts an extension's endpoints in 6.1.0 |
| the kit's own, older than the engine | `superset/templates/head_custom_extra.html`, the brand CSS, the header that keeps images local | Superset's page template and class names |

---

## 4 · The pieces

### The registries — `registry/`

Files, read at start and **checked against each other**; a registry that does not hold together stops the
service.

| | |
|---|---|
| `datasets/` | owner, certification, source model, grain, freshness SLA — and a **classification on every column**. A column without one fails the load |
| `metrics/` | the governed metrics: expression, aggregation, dimensions, format, owner, documentation, certification, version, lineage — and a **security classification** of its own. A metric without one fails the load |
| `templates/` | `executive`, `telecom-kpi`, `operational`, `geographic`, `customer-experience`, `management`: layout, **sections**, slots and what each accepts, theme, typography, spacing, colours, KPI formatting, filters, responsive rules |
| `design/tokens.yaml` | the approved colours, schemes, type scale, spacing, component variants. **SharpSight's own colours lead**: `primary` is SharpSight blue and every template draws in the `sharpsight` series. A client's colours are a **brand** — an accent and a series (`brands:`) — that a dashboard wears when the request says so or its specification names it (`theme.brand`); it changes where a template says `accent: primary` and the series of the charts, and nothing of Superset's own page |
| `visualizations.yaml` | what the AI may select, and what each is in the BI engine |
| `security/rbac.yaml` | users, roles, dataset grants, row-level policies — a stand-in for the portal's RBAC. One declaration, enforced by the data gateway **and** given to Superset |
| `policies.yaml` | the limits: SQL, dbt, performance budgets (concurrency among them), the null rate a column may have, visual thresholds, and what may leave Superset as a file |
| `compatibility.yaml` | the compatibility matrix: the versions this service was built and run against (below) |
| `lexicon.yaml` | the vocabulary a request is read with |

Where a dataset physically lives is **not** in these files: it is read from dbt's `manifest.json`.

Two things worth knowing about the metrics, because they are what "governed" buys:

- `cem.qoe_score` is `SUM(qoe_score * active_users) / SUM(active_users)` — weighted, so it reads correctly at
  any level without re-weighting. A plain average would not.
- `cem.active_users` is a **daily mean** over the period, not a sum: the same subscriber is active on Monday
  and on Tuesday, and summing a month would print thirty times the customer base.

### dbt — `dbt/`

A real dbt Core project: sources → staging → intermediate → marts, a seed, macros, generic and singular tests.
The profile reads everything from the environment.

| Target | Schema | Role | Run by |
|---|---|---|---|
| `prod` | `bi` | `bi_builder` | the `dbt-runner` one-shot — what Airflow triggers in the target architecture |
| `dev` | `bi_dev` | `aibi_dbt_dev` | the AI service, on a project's own branch |

The AI never touches the governed project: it is mounted read-only, mirrored into a repository of the service's
own, and a generated model lives on `ai/<project>` and builds with `--defer` against the production artifacts.
PostgreSQL enforces it — `aibi_dbt_dev` can create in `bi_dev` and nowhere else. The files reach the real
project the way any model does: a person merges them.

A generated model is a **plan** first (`ModelPlan`: base, join, added columns), and the SQL is rendered from it.
It always carries three tests: its grain is still unique, the added column is never null, and it has exactly as
many rows as its base.

Artifacts consumed: `manifest.json`, `catalog.json`, `run_results.json` (and `semantic_manifest.json` when dbt
writes one). Lineage and model metadata from them are kept in SharpSight's store — `GET /datasets/<id>/lineage`.

### The tool layer — `aibi/tools.py`

The engine acts only through tools: a name, typed arguments, a policy check, an audit record. `GET /tools`
lists the 28 of them with JSON schemas. The brief's (its section 19) are all there under its own names —
`get_dataset`, `get_dataset_schema`, `get_dataset_sample`, `list_datasets`, `get_metric`, `list_metrics`,
`get_dimension`, `get_dashboard_template`, `execute_readonly_sql`, `validate_sql`, `generate_dbt_model`,
`compile_dbt`, `run_dbt_model`, `run_dbt_test`, `create_superset_dataset`, `create_superset_chart`,
`create_superset_dashboard`, `update_superset_dashboard`, `get_dashboard_data`, `validate_dashboard`,
`render_dashboard_screenshot`, `commit_project_version`, `create_deployment_package` — plus
`list_dashboard_templates`, `save_dashboard_spec`, `apply_superset_access`, `search_assets` and `get_asset_usage`.

`get_dimension` answers for a word of the business (« regional » is `region`): the column, the datasets it is
found in among those the caller may read, its type, role and classification there, and the metrics that may be
broken down by it. `create_deployment_package` asks the lifecycle for the latest version's package; it does not
build one for a version that did not reach review, and it does not build one twice.

A specification may leave a component's dataset out, as the brief's own example does: `save_dashboard_spec`
then takes it from the registry — a governed dataset before a generated one — and says which it took.

The tools that write to Superset take **a slot, or nothing**. What they create is derived from the project's
saved specification, and they refuse to run until one has been saved and found valid.

Looking something up in the registries needs `bi.read`; anything that runs a query, builds a model or touches
Superset needs `tools.use`. A reviewer has the first and not the second.

### Policy and the data gateway

```
AI → tools → policy engine → data gateway → a read-only role
```

SQL is read as a tree (sqlglot), not as text: one statement, a query, over **registered datasets the requester
may read**, in the allowed schemas, with no function that waits, reads files or reaches another server, and no
column classified above the requester's clearance. What runs is never what was written: row-level policies are
applied by rewriting each table into a filtered subquery, and the whole thing sits inside a row limit. The
database then enforces it again — `bi_reader` is read-only in the role itself, with a statement timeout.

### Who may open a dashboard, and which rows they see

SharpSight's RBAC is the only place this is decided; Superset is told, and then asked.

- **The audience** of a dashboard is every role that may read (`bi.read`) and is granted **all** of its
  datasets. Each becomes a role in Superset (`sharpsight_<role>`) and the dashboard is granted to exactly
  those. The roles carry no permission of their own: a person also holds Superset's base role, and gets the
  dashboard — once it is published — because they hold one of its roles.
- **A row-level policy** (`row_filters`) is written into Superset as a rule for that role on that dataset. It
  **follows a dataset into anything derived from it**: the generated map dataset inherits the mart's policy,
  or the regional analyst's map would show the whole country beside charts showing three regions. A derived
  dataset that no longer has the policy's column shows that role nothing.
- **A role without a policy does not lift another role's.** That is Superset's own rule, adopted by the data
  gateway so that the two layers return the same rows.
- **Then Superset is asked.** The engine keeps accounts of its own — `sharpsight-probe-<role>`, one with no
  role at all — given a password nobody keeps for as long as a question takes, and switched off again. During
  validation the account with no role must be refused the dashboard and its data. At promotion to PROD, once
  the dashboard is published, each role's account must open it, get its data, and get **exactly the rows the
  data gateway gives that role**; if not, the dashboard is withdrawn and nothing is recorded as PROD.

**A metric has a classification too.** A requester not cleared for it does not see it in `list_metrics`,
is refused it by `get_metric`, and a specification naming it fails validation. A dashboard is for the roles
granted every one of its datasets **and** cleared for every one of its metrics.

**What leaves as a file** is in `registry/policies.yaml` (`security.export`): a dashboard's *data* may be
exported by the roles it is granted to — the rows their policy gives them, counted against the data gateway's —
and by nobody else; its *definition* leaves through the `.ssbi` package only. Both are asked of Superset: of
the account with no role during validation, of every role at publication.

**People.** A role opens nothing until a person holds it, and who holds what is in `rbac.yaml`.
`bash ai-bi.sh people` carries that to Superset: every account whose name is a user there is given exactly that
user's SharpSight roles, and an account that is not loses any it holds — a role handed out by hand in Superset
is taken back. `bash ai-bi.sh person <user>` makes the account for a person who has none, with Superset's base
role, and shows its password once. Both need `access.manage` (the release manager), and both are a stand-in:
with Keycloak, identities arrive on their own and only the mapping of roles stays.

### The Superset adapter — `aibi/superset_adapter.py`

REST only: authentication, connections, datasets, charts, dashboards (create, update, copy, metadata), data,
export/import, embedded configuration, roles, row-level rules, accounts. **Superset's metadata database is
never touched.** The routes and schema fields were read out of the kit's own image, not recalled.

`bash ai-bi.sh check-adapter <project>` drives the roads a generation does not take — copy, export, embedded
configuration, delete, import — on a copy of a project's dashboard, and removes the copy. Run it after any
Superset upgrade: it says which of the adapter's assumptions still hold before a generation finds out.

What Superset 6.1 does not offer, and what the engine does instead:

| | |
|---|---|
| no dashboard version history, no restore | every version is a git commit of the specification and the export; restore re-applies a specification (`POST /projects/<id>/restore`) |
| no browser in the image, so its screenshot endpoints cannot work | the engine renders with its own headless browser |

Nothing above the adapter says "slice" or "form_data": `registry/visualizations.yaml` and two files
(`chart_agent.py`, `superset_adapter.py`) are what replacing Superset would rewrite. The registries, the
specifications, git and dbt would not notice.

### The compatibility matrix — `registry/compatibility.yaml`, `aibi/compatibility.py`

What this version of the service was built and run against — Superset, dbt-core, dbt-postgres, Python,
PostgreSQL, Playwright, sqlglot — and the connectors a governed dataset was actually built on (PostgreSQL;
Trino, Spark, Oracle, SQL Server and Hadoop are listed as not tested). Airflow is listed as not used: this kit
has no scheduler. The matrix is a statement; `GET /compatibility` (`bash ai-bi.sh compatibility`) reads what
is really running — Superset is asked its version, the warehouse its own — and says, component by component,
whether the statement covers it. A version outside the matrix refuses nothing: it is named in `/health`
(`untested_components`) and in the audit record of the service's start, for a person to decide about. After a
Superset upgrade: `bash ai-bi.sh check-adapter <project>`, then add the version to the file.

### Validation — `aibi/validation_agent.py`, `aibi/visual_qa_agent.py`

Every check is a measurement, recorded whether it passed or not. One failure keeps a version out of review.
The list is the brief's section 27, name for name.

| | |
|---|---|
| **Data** | schema (registered columns exist, typed as dbt's catalog says), **null rate** (none at all in the grain, the measures and the geography; up to 5 % in any other column), duplicates on the grain, row count, freshness against the SLA, declared ranges, **expected values**, metric validity (overall and per day) |
| **SQL** | syntax, allowed database, read-only, timeout, result size, forbidden operations — over every statement the run executed — plus **probes**: nine hostile statements the guard must refuse |
| **BI** | dataset exists, saved metrics match the registry's expression, each chart's query succeeds, the dashboard's own requests succeed, no empty mandatory chart. **Dependencies exist**: each component followed down — its chart on the dashboard, the chart's dataset in Superset on the read-only connection, the dbt model behind it — each filter's target column, and no chart on the dashboard that the specification does not name. **Filters twice**: each one through Superset's query API, and all of them on the dashboard itself — it is opened with the filters set, and every chart in scope must receive something else than it does unfiltered |
| **Security** | RBAC, row-level policies applied, a user with no grant is refused every dataset, tenant isolation (the same roles in another tenant are refused), the BI connection is read-only — and **in Superset itself**: the dashboard's roles and row-level rules are the ones SharpSight grants, and an account with no SharpSight role is refused the dashboard and its data. **Export permissions**: that account can take out neither the dashboard's definition nor its data as a file. **Direct URL access**: the dashboard's page, a chart, the chart's data, the chart in the editor, the dataset and SQL Lab, each asked for outright — signed in with no role, and not signed in at all |
| **Performance** | query duration, dashboard load duration, query count, result size, and **concurrency** — every chart's query, uncached, by four people in the same instant — against `registry/policies.yaml` |
| **Visual** | screenshot produced, every component on the page, no overlap, layout follows the template, nothing empty, no clipped text, no text under 9 px, labels have room. **Every section is announced**: each titled section the specification names has its header on the page. **Excessive whitespace**: the cards cover the grid and rows follow each other — a section's header between two rows is not a hole — (a failure), and a chart reaches across its card (a warning under 30 %). **Template violations**: each card has its slot's width in grid columns and its row's height, shows a chart its slot accepts, and the dashboard's stylesheet is the template's own. A clipped dashboard title is a warning |

The visual checks are measurements too: DOM geometry, pixel statistics of each chart's body, and the network
traffic of the real page. No model looks at the picture today — and one class of defect is therefore missed
knowingly: text a chart draws on a canvas (axis labels, values) has no DOM, so two axis labels printed into
each other pass. That is what a person, or a model reading the picture, is for.

### Versions and promotion — `aibi/lifecycle.py`

Every version records `project_id`, `version`, `git_commit`, `dbt_commit`, `superset_asset_version`,
`dataset_versions`, `metric_versions`, `template_version` — and `status`, the gate it is at, with the `history`
of every gate before it. `state` (DEV, UAT, PROD) is derived from the gate.

| | Who | Gate |
|---|---|---|
| `READY_FOR_REVIEW` (DEV) | the AI and the developer | a version exists only if validation passed. Its `.ssbi` package is cut here, once — a reviewer can download what they are about to approve |
| → `APPROVED_FOR_UAT` → `UAT` | a reviewer (`bi.approve_uat`), a person | validation passed **and** the dashboard in Superset is still the one that was validated: its content is fingerprinted, its roles and row-level rules are compared with SharpSight's, and anything changed by hand afterwards is refused. Then the package on disk is checked against the checksum recorded when it was cut; a file that changed sends the version to `UAT_FAILED` |
| → `APPROVED_FOR_PROD` → `PROD` | a release manager (`bi.approve_prod`), a **second** person | explicit confirmation and a comment; the package's checksum is verified again. The dashboard is published, certified and locked against editing — then every role's probe account is asked what it gets on screen, in a file it exports and by a direct address, and the publication is withdrawn (back to `UAT`) if Superset's answer is not SharpSight's |

The approval and the deployment are two records: `APPROVED_FOR_…` is the person's decision, written before
anything is done on the strength of it; `UAT` and `PROD` are the system's, written when its own checks passed.

A deployment to another Superset (`bash ai-bi.sh deploy`) carries the audience and the row-level rules
itself, since an export holds neither: the `sharpsight_<role>` roles are ensured there and granted the
dashboard, and each `sharpsight:<role>:<dataset>` rule is written onto the dataset the import created, with the
same names and adapter calls the kit uses at home, so a second deployment updates in place. A rule the policy
no longer has is withdrawn. Everything is then read back, and the deployment fails if the target refused it or
holds something else. Both go through one helper (`aibi/tools.py::apply_access`): every rule is written and read
back before the dashboard is granted, a grant that fails is put back to what it was, and only a rule the POLICY
no longer declares is deleted — never one another dashboard over the same dataset still needs. A version whose
datasets or metrics the registry no longer knows is refused before any access is applied.

The `.ssbi` package is the project's folder as committed — `manifest.yaml`, `dbt/`, `semantic/`, `datasets/`,
`metrics/`, `superset/`, `dashboards/`, `charts/`, `security/`, `tests/`, `documentation/` — zipped, with the
commit and a checksum per file in its manifest. It is copied to `./exports/`. It is **immutable**: a package
that has gone missing or no longer matches its checksum is not rebuilt — the checksum is what the later gates
hold it to — and the way on is a new version.

The fingerprint (`superset_asset_version`) is a hash of the dashboard's **content**: every chart named by
its place on the dashboard, every dataset by its table — no engine id, no uuid, no file name, none of the marks a
promotion sets, and « empty » spelled one way. So a chart deleted and re-created is the same dashboard; a title
changed, or a filter taken off a chart, is not.

A regeneration that fails after it has changed Superset does not leave the reviewed dashboard altered: the
latest version's specification is re-applied from git, and the run's error says so.

**This kit has one Superset and one PostgreSQL.** A promotion here is the gate, the package, the record and the
mark on the dashboard — not a copy onto other machines. The package is what an installer on a real UAT or PROD
would be given.

### Audit — `aibi/audit.py`

One JSON line per action — the fields of the brief's section 32: user, project, request id, AI model, the
prompt (on the `request` record, which every later record refers to by its request id), datasets accessed,
**metrics accessed**, SQL generated, dbt files generated (with hashes), Superset assets created or modified,
validation results, **visual QA results** (each render, and what the visual checks failed or warned), the
**git commit** the action made or acted on, deployment, timestamp — and the actor (AI, human, system). An
automatic correction is a record of its own (`fix`), as is each quality gate (`gate:<name>`) and the package
(`package`). Each line carries the hash of the one before, so a line edited or removed afterwards breaks the chain
and `GET /audit/verify` says where. Secrets are scrubbed; rows of data never enter it.

---

## 5 · Who holds which credential

| | Held by | May |
|---|---|---|
| the database owner | `aibi-db-init`, for the seconds it runs | create roles and schemas |
| `bi_builder` | `dbt-runner` only | build the governed models in `bi` |
| `aibi_dbt_dev` | the AI service | read `bi`; create in `bi_dev` (drafts) and in `bi_factory` (the copy of its own records that Superset draws) only |
| `bi_reader` | the AI service's data gateway, and Superset's BI connection | read `bi` and `bi_dev`; write nothing |
| `bi_records_reader` | the AI service, which hands it to Superset's second connection — closed to SQL Lab | read `bi_factory`; nothing governed; write nothing |
| the Superset account `sharpsight-ai` | the AI service | Admin **in this DEV Superset** — it creates the BI connection, the roles and the row-level rules |
| the Superset accounts `sharpsight-probe-*` | nobody: a random password is set for the seconds a probe lasts, then the account is switched off | Superset's base role, and at most one `sharpsight_<role>` |
| a person's Superset account (`ai-bi.sh person`) | that person: the password is shown once to whoever created it, and is neither stored nor logged | Superset's base role and the roles `rbac.yaml` gives them; for those who generate, Superset's `sql_lab` role too — SQL Lab's page, where the SharpSight panel is, and no database |
| `AIBI_API_TOKEN` | whoever calls the API: the command line (in the platform, the portal) | present a user's identity, for anything that user may do |
| `AIBI_SUPERSET_GATEWAY_TOKEN` | Superset, for its extension's backend | present the identity of the person signed in to Superset — for the projects, a validation, a commit and a run. Refused by the engine for everything else |

No credential reaches a model: a model's whole interface is the tool layer. The service that would talk to one
holds nothing that can change a governed table.

---

## 6 · The API

`Authorization: Bearer $AIBI_API_TOKEN` and `X-SharpSight-User: <user>` on every call but `/health`.

| | |
|---|---|
| `POST /projects/generate` `{prompt}` | start a generation → a run id |
| `GET /runs/<id>` | the run, step by step, and the quality gate it left its project at |
| `GET /projects` · `/projects/<id>` · `/projects/<id>/versions/<v>` | projects (`status`, and `last_attempt` — also for a project whose only run failed) and versions (`status`, `history`) |
| `GET …/versions/<v>/spec` · `/validation` · `/screenshot?viewport=desktop` · `/package` | what a version is |
| `POST /projects/<id>/approve` `{target, comment, confirm}` · `/reject` · `/restore` · `/discard` | review, and taking a draft away |
| `GET /projects/<id>/changes` · `POST /projects/<id>/commit` `{comment}` | what Superset holds against the specification, chart by chart; taking what was edited there into a new version (`bi.generate`) |
| `GET /datasets` · `/datasets/<id>` · `/datasets/<id>/lineage` · `/metrics` · `/dimensions/<word>` · `/templates` | the registries |
| `GET /compatibility` | the matrix, beside what is running |
| `GET /me` · `/security/overview` | who is calling; users, roles and permissions for those who administer or audit them |
| `GET /tools` · `POST /tools/<name>` | the tool layer, as a model would use it |
| `POST /people/sync` · `/people/<user>` | accounts in Superset given their roles; an account made (`access.manage`) |
| `GET /audit` · `/audit/verify` | the trail (`audit.read`) |

---

## 7 · Add a dataset, a metric, a template

All three are a YAML file in `registry/` and a restart of the service.

- **A dataset** — its dbt model must exist in `dbt/`; give every column a `classification`, name the grain, and
  set `role` on what can be a time axis (`time`), a breakdown (`dimension`), a number (`measure`) or a map key
  (`geo_region`, with `geo: {standard, country}`). A `label` is what people read in a table header or a filter.
- **A metric** — an expression over a dataset's columns, the datasets and columns it is valid on (`lineage`),
  what it may be broken down by (`dimensions`, enforced), whether its parts add up to its whole (`additive`:
  a pie is refused for a score or a rate), who may be shown it (`classification`, required), and the words
  people use for it (`synonyms`): that last line is how a request finds it.
- **A template** — rows of slots on a twelve-column grid, what each slot is for and accepts, names from
  `design/tokens.yaml` (a name that is not a token fails the load), and `sections`: every row in exactly one,
  in the layout's order.
- **A domain** — an entry in `domains.yaml` naming the datasets and metrics filed under it; an asset is in one
  domain at most. **A chart template** — an entry in `chart_templates.yaml` naming an approved visualization.

---

## 8 · What was verified, and how

On 2026-10-03, on this kit (Superset 6.1.0, PostgreSQL 16.4, dbt-core 1.12.5), beside a running SharpSight
platform that was not touched:

| | |
|---|---|
| The brief's request, from nothing | in the brief's own words (« the CEM Executive template »): all 22 steps; 220 checks, none failed, none warning; version `1.0.0`, `READY_FOR_REVIEW` by way of `DRAFT → AI_GENERATED → VALIDATING`, committed, tagged and packaged, its three sections announced by their headers. Run from an erased engine several times over the day |
| The generated model | built in `bi_dev` on branch `ai/cem-qoe`: 5 760 rows, the same count as its base, its six tests passing |
| The picture | looked at by a person, at 1440 and 1180 px, and with the three filters set — which is how four of the entries in [lessons.md](lessons.md) were found |
| The review path | fifteen cases: no token, an unknown user, a user with no grant, the developer and the AI's own identity trying to approve, PROD before UAT, a chart edited by hand in Superset after validation (refused as drift), restore from git (drift gone), UAT, PROD without confirmation, PROD — and a regeneration refused once in PROD |
| The package | cut at generation; the eleven entries of the brief, the commit and 32 checksums in its manifest, no secret in any file. Downloaded by a reviewer before approving. Altered on disk by one byte after generation, on a second project: approved for UAT, and stopped at `UAT_FAILED` naming the checksum; a second approval refused; the project discarded, and its package gone from `exports/` |
| The audit trail | every tool call and every statement; the chain verifies; no secret and no row of data in it. For the brief's request: 119 records, 37 naming the metrics they read, 10 naming a git commit, 3 carrying visual QA results; an approval and the promotion that follows it are two records |
| The fix loop | **not triggered by the brief's request.** Triggered on its own by the `telecom-kpi` template, whose ranking slot gives sixteen regions 16 px each: cut to « Top 14 », re-rendered, passed. Also exercised once under a threshold raised on purpose. The other three fixes are unit-tested only |
| All six templates, every chart type | `executive`, `operational`, `telecom-kpi`, `geographic` (its own generated model, in its own project), `customer-experience` and `management` (203 checks; a weekly trend, a share by RAT as a pie, a ranking, a detail table), each from a request, each looked at. Big number, line, area, bar, histogram, pie, table and map have all been drawn |
| A run that cannot pass | forced with a label threshold no chart can meet: one fix tried, the run **failed**, no version was written, and the dashboard — which the attempt had cut to three charts — was put back as `1.0.0`, still matching its validated fingerprint. Forced again on a project with no version yet (text under 40 px fails): the project is listed at `VALIDATION_FAILED` with the way there and the error, no version, three `gate:` records in the trail; the same request under the real policy then reaches `READY_FOR_REVIEW` |
| Access, in Superset | an account with no SharpSight role: 404 on the dashboard, 403 on both datasets, 404 or 403 on every direct address and every export; not signed in: the sign-in page, or 401. Four roles: the dashboard and all 16 regions, on screen and in an exported file. The regional analyst: 3 regions on the mart **and** on the generated dataset — on screen, in the file, and from the data gateway. No role reaches a chart, a dataset or SQL Lab on its own, and none can export the dashboard's definition. Roles removed by hand, then a row-level rule rewritten to `1 = 1`: promotion refused both times, naming the difference |
| The adapter's other roads | copy, export, embedded configuration, delete, import: all pass on a copy, which is removed. The first run found the copy had no layout, and that an import is not identical to its export until « {} » counts as empty |
| Discard | a DEV project taken away with its generated dataset; one standing in UAT refused; a reviewer refused |
| A person, not a probe | `east.analyst`, created by the command, signs in: the PROD dashboard and nothing else; three regions in every chart, on the map, and in the region filter's own choices; SQL Lab refused. A role given to `admin` by hand in Superset was taken back by `people` |
| The brief's specification | section 23's example given to `save_dashboard_spec` as written — `version: "1.0"`, `template: executive-telecom-v1`, no dataset on any component: read, its four datasets found in the registry, and one finding, the map by a column called `site` that this registry does not have. Without that component it is valid |
| **In Superset**, in a real browser | As an administrator: the SharpSight menu in Superset's top bar, with its six entries; the five dashboards over the records draw every chart — ten, eight, four, seven and six — with none in error, and the counts *BI Factory* shows are the engine's own answer; Superset's own dashboard list shows a project's environment and gate as tags, and « certified » on the PROD dashboard only; its seven charts carry their project and domain, and are certified with it; dbt's documentation opens when signed in (« dbt Docs », with the project's tree), and an anonymous request is sent to the sign-in. Then as five kinds of people, with an account made for the question and switched off after it: with no SharpSight role, or with a role granted no dataset, no SharpSight menu, every dashboard refused, and the documentation refused (403); an analyst sees the documentation's entry and nothing else; a developer sees the four dashboards of the factory and the documentation, and is refused the audit trail; an auditor sees the trail and the documentation, and is refused the factory's. The records' connection is closed to SQL Lab. Asked directly, the database refuses `bi_reader` the schema, and lets `bi_records_reader` read it, read nothing governed, and write nothing. The engine's own address answers 404 for `/studio/`, `/builder/…` and `/dbt-docs/`. No request left the machine: Superset's telemetry pixel is attempted and blocked by the kit's own header |
| **Edited in Superset**, validated, committed | A second dashboard is generated (operational template, five charts) and nobody touches it: `validate` finds every chart as specified and nothing to commit. Three charts are then edited the way Explore saves them — through Superset's own API: the trend by week; the ranking cut to five, renamed, with a legend; a KPI given an ad-hoc metric — and, as the dashboard editor saves it, one chart made narrower. A commit is refused, naming the KPI; the reviewer is refused too, the fingerprint having changed. `validate` says the rest in the specification's words: `time_grain: P1D → P1W`, `row_limit: None → 5`, the new title, the legend as something a specification has no place for, and the layout as no longer the template's — which a commit undoes. The KPI's governed metric is put back in Superset; a commit by an analyst is refused (`bi.generate`); the developer's says what it takes, sets back and undoes, goes through steps 11 to 22 — 162 checks, none failed — and leaves version 1.1.0 at `READY_FOR_REVIEW`, by way of `DRAFT → VALIDATING`: its specification says week and top five, Superset's ranking has five rows and no legend, the layout is the template's again, and `validate` finds all five charts as specified. The reviewer reads, on Releases, what the commit took, set back and undone, and approves 1.1.0 for UAT; a further commit is refused, the project being in UAT; its dashboard is tagged `env:UAT`, `gate:UAT` |
| **Whose colours** | A dashboard asked for « in inwi's colours », on the Management template, is specified with that brand: its charts are drawn in the `inwi` series and its stylesheet carries the magenta accent. The one beside it, asked for without a word about colours, is drawn in the `sharpsight` series, and its stylesheet carries no magenta. Measured in the browser: the accent above a KPI card is the brand's magenta on the first and SharpSight blue on the second, and Superset's own page — its buttons, its links, the sign-in button — is SharpSight blue for both |
| **The extension**, its API | Superset lists `sharpsight.bi 0.1.0` among its extensions, loaded from the `.supx` whose checksum was recorded. Not signed in: 401. An account that is nobody in `rbac.yaml`: refused by the engine, by name. As an analyst: the projects, a validation, and a commit refused (`bi.generate`). As a developer: the same, and a commit that reaches the engine (refused there, nothing having been edited). A project name that is a path: not routed. `approve`, `restore`, `audit`: no such endpoint. The accounts are made for the question and taken away after it. `check-adapter` finds the extension loaded and its API answering. A commit sent with a developer's session cookie and no CSRF token is refused (400); with the token the page gives, it reaches the engine. And the engine, asked directly with the token Superset holds: the projects and a validation, 200; approve, restore, the audit trail, a new request, the people sync, 403 — whoever is named |
| **The SharpSight panel**, in a browser | In SQL Lab, on the right. An account SharpSight does not know reads « … is not a known SharpSight user ». As a developer: the projects are offered; one is chosen and shows its environment and gate; a chart of it having been edited in Superset — the trend, by month — *Validate* says so in the specification's words and that a commit would take it; *Commit*, with a sentence, runs to « Version 1.1.0 — READY_FOR_REVIEW », which the engine's own record confirms: created by that developer, 203 checks, none failed. The panel has three buttons — Refresh, Validate, Commit — and none that approves. No error on the page |
| What reading the output, the pictures and a second reader caught | a tag changed the fingerprint a dashboard is validated under — every tagged dashboard would have been refused at its next gate as « changed after validation »; menu entries added by the hook had no permission, so nobody saw them; with four tags on a dashboard, Superset's list showed two and hid the gate. And, found by an audit of the branch from another session rather than by the tests: the copy of the records could be read through the connection every chart uses, SQL Lab included, while the auditors `rbac.yaml` names could not open the trail's dashboard; the two system dashboards wore the badge that means « approved for PROD »; the menu led to dashboards that were not made yet for some minutes after a start; and dbt's documentation was open to every signed-in account, one with no dataset included. And in the extension, by the same reader: its commit endpoint took a request with no CSRF token, and Superset held the engine's only token, so that « it approves nothing » was true of its source and not of the engine. All fixed |
| The engine's rules | 170 tests, no database needed: `bash ai-bi.sh test` |

## 9 · What is not here yet

| | State |
|---|---|
| **A language model** | **Left open on purpose** (2026-10-03). The planner is deterministic; `aibi/llm.py` is the seam, and a model plugs in by producing the same `Requirement` structure. Everything downstream is unchanged, which also means the deterministic run already exercises every guard a model will meet |
| **The extension, what it does not do** | it approves nothing, on purpose. It has no « ask for a dashboard » box: no language model was chosen, and a request is made on the command line. Its panel is in SQL Lab because Superset 6.1.0 takes a panel nowhere else. Its words are English under a French interface. The bundle is checked against a recorded checksum — not signed. The panel is built with Node on a developer's machine and its build is committed: the kit cannot rebuild it (`build.py --check-frontend` says, where there is Node, whether the build in the repository is what the sources give). No MCP tool, no chat, no semantic-layer extension, no custom visualization (chapter 3's list) |
| **Exact versions under one tag** | the kit's image is still `sharpsight-superset-devkit:1.0.0` though its theme changed on 2026-10-04; the engine's went from 0.1.0 to 0.2.0 only at the end of that day's work. A version's package now says what it was built with (`built_with` in its manifest: this service's version, Superset's) |
| **A context bar, an outline, a governance drawer on dashboards and in Explore** | they are in the Studio, which is a page of its own (chapter 3). ON a dashboard and IN Explore there is still no extension point, and a fork was not chosen: what stands there is Superset's own — tags, badges, the SharpSight menu, five dashboards |
| **What a commit does not take from Superset** | only what a specification carries, of the charts it has a place for: kind, metric, dimension, dataset, top N, order, time grain, bins, title — and, since the Studio, the layout, which a specification now carries and a commit keeps. The rest of what Explore offers — number formats, colours, a legend, a chart's own filters, ECharts options — is named per chart and set back. On the dashboard itself, a chart added by hand, a row moved in Superset's dashboard editor, a filter changed there are named, and undone: the layout is the template's and is applied again at every build. So Superset's dashboard editor is open, and what is done in it does not last. And a dashboard built from nothing in Superset is not adopted as a project: a project starts with a request |
| **Two decisions that are the owner's**, put to them, not taken here | (the first of three is now taken: a layout placed IN THE STUDIO is kept, carried in the specification — what is still open is the layout a person moves in Superset's OWN dashboard editor, which is still named and undone; `aibi/layout.py::from_positions` is the reading it would need to be adopted instead.) How a project that stands in UAT or PROD gets a next version in a kit that has one Superset — today neither a request nor a commit is accepted on it; and whether a developer may QUERY in SQL Lab — their account opens its page, where the panel is, and is given no database there: granting the governed schemas would need Superset's row-level rules applied in SQL Lab too |
| **The records' dashboards, per workspace** | this kit has one workspace, so no row-level rule is set on `bi_factory`; every row carries its tenant for the day there are several |
| **Branding, the parts not done** | SharpSight's colours lead the interface and the dashboards (2026-10-04). Not done: the five system dashboards have English titles under a French menu; the application is still named « SharpSight Superset — atelier », where the brief says « SharpSight BI Studio powered by Apache Superset »; a client's brand changes a dashboard's accent and series, not Superset's page around it (6.1 can give one dashboard a theme of its own — not used); the brand values are written in three places kept alike by hand (`superset_config.py`, `registry/design/tokens.yaml`, the sign-in page's template) |
| **The portal** (`sharp-sight-web`, `sharp-sight-api`) and **Keycloak** | not built. The API is what the portal would call; identity arrives as a header behind a shared token; RBAC is a file, and people reach Superset through a command an administrator runs |
| **Airflow** | not running, and listed as not used in the compatibility matrix. The `dbt-runner` one-shot builds the governed models at each start |
| **Separate UAT and PROD environments** | one Superset, one warehouse: promotion is the gate, the package and the record |
| **`TelecomQoEChart`** (the custom visualization) | not built. It is listed in `registry/visualizations.yaml` as approved-but-not-installed, and a specification asking for it is refused. Building it means compiling Superset's frontend with the plugin |
| **RabbitMQ, MinIO, a reverse proxy** | not needed by the slice; generations run one at a time in-process, packages are files |
| **An MCP server** | the tool layer has the shape (names, schemas, one audited entry point); nothing serves it over MCP yet. Superset 6.1.0 carries an MCP service whose dependency (`fastmcp`) is not in its image |
| **The offline bundle** | `build-devkit-bundle.sh` does not carry the engine's image yet |
| **A site-level map** | the geographic view is by region: a map of sites needs tiles from outside, and this kit calls nobody |
| **Choosing a theme from a request** | a request may name a client's colours (« in inwi's colours ») and gets that brand's accent and series; nothing else of a theme is chosen from a request: every dashboard takes its template's typography, spacing and sequential scheme |
| **Other warehouses** | the engine speaks PostgreSQL only (dbt profile, SQL policy dialect, data gateway), and the compatibility matrix says so: the other connectors of the brief are listed as not tested. The kit's Superset has drivers for SQL Server, Oracle and Hive; the engine does not use them |
| **A section a person can collapse on the dashboard itself** | sections are titled and filtered in Superset; collapsing one is not offered: Superset has no collapsible part of a dashboard short of tabs |
| **Spark** as a transformation engine, **Version retrieval** from Superset | transformations are dbt on PostgreSQL. Superset keeps no versions of a dashboard: SharpSight does, in git, and `restore` re-applies one |
| **Other transformations** | a generated model is one shape: a governed base joined to a governed dimension to add columns. No aggregation to another grain, no filter, no derived column |
