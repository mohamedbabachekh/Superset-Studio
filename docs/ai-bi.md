# SharpSight AI BI — the engine, on top of the kit

A proof of concept of the AI-assisted BI development engine: a request in plain words becomes a **governed,
validated, versioned** dashboard, and then waits for a person.

> Superset draws. dbt transforms. Git remembers. SharpSight decides what may be asked of each, checks what came
> back, and keeps the record. The AI is a developer's assistant inside those walls, not an administrator.

It is a **vertical slice**: one request goes all the way through, on real components. What is deliberately not
here yet is listed at the end, and nothing below claims otherwise.

Beside the request in plain words there is **BI Studio** (chapter 3): one place that wraps the tools rather
than replacing them. Its middle panel **is Superset** — the live dashboard, and Superset's own Explore when a
chart is edited; dbt's own documentation and lineage are framed in it too. Around them stands what is
SharpSight's: the asset library, sections, governance, review, and the gates.

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
bash ai-bi.sh studio                     # where BI Studio is (http://127.0.0.1:3003/studio/), and how to sign in
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

## 3 · BI Studio

`http://127.0.0.1:3003/studio/` — static files served by the engine itself: no build step, no script from
anywhere else, no new container. Sign in with a user of `registry/security/rbac.yaml` and the API token
(`AIBI_API_TOKEN` in `.env`); in the platform this is the portal's job, and Keycloak's. The frames use the
browser's Superset session, and the Studio looks for Superset on the host name it was itself opened on: sign
in to Superset under that same name (`127.0.0.1` is the one this was verified on).

> Assets are reusable objects. Dashboards are compositions of those objects.

**It wraps Superset; it does not rebuild it.** Nothing in the Studio draws a chart. The middle of the builder
is a frame showing Superset's own page — the project's live dashboard, and Superset's Explore for a chart — and
the SQL Lab screen frames Superset's SQL Lab. What is SharpSight's is around the frame: which assets exist and
where they are used, how a dashboard is structured, whether it is in order, and whether what Superset shows has
been validated. The page holds no rule of its own: every change goes to the service.

### The builder

| | |
|---|---|
| **Middle — Superset** | the project's dashboard as Superset draws it, live: real charts, Superset's own filter bar, the section headers. « Edit in Superset » on a chart turns the frame to Superset's **Explore** for that chart. `⇤⇥` gives the frame the whole width |
| **Left, above — the dashboard's structure** | sections, and in each the template's slots, the filled ones and the empty ones. This is where a slot is picked and where an asset is dropped — the frame is Superset's and takes neither. A metric dropped on a slot makes a chart of the slot's first accepted type; a shared chart is referenced; a dimension changes what a chart is broken down by; a chart template changes its kind; a dimension on a section, or on the filter bar, is a filter |
| **Left, below — the asset library** | tabs for metrics, datasets, charts and templates; folders that are logical **domains** (`registry/domains.yaml`), not schemas; favourites; a search box; *All / Shared / Local*. Each row shows whether it is certified and how many dashboards use it |
| **Right — the inspector** | contextual. A chart: what it is (kind, metric, dimension, dataset), its description, and *Edit in Superset*. A section: title, description, owner, visibility, filters, layout. The dashboard: metadata, template, colour scheme, global filters, who it is for, responsive behaviour |
| **Above the frame** | the dashboard's filters; and what SharpSight has to say about what Superset shows — see below |
| **Bottom** | `✓ Validation · n assets · n warnings`, what Superset currently shows, then *Build* and *Deploy* |
| **Modes** | *Dashboard* (Superset) · *Data*: the dashboard's datasets with every column's classification, a preview of rows, its metrics' expressions, what each dataset is derived from · *Governance*: the checklist below, who the dashboard is for and which rows each role sees, and every failing check |

**A change is applied to Superset at once** (`POST /builder/projects/<id>/apply`). If the specification is
valid, the engine makes it real in the project's DEV dashboard — through the same tools a generation uses — and
the frame shows the result. If it is not valid yet (a mandatory slot still empty), it is kept as a draft,
nothing is sent to Superset, and the Studio says what is missing.

**What a person edits in Explore is read back** (`GET …/superset`, `POST …/adopt`). After a save in Explore
the Studio lists, chart by chart:

- what **changed, in governed terms** — the metric, the dimension, the kind of chart, top N, time grain, order,
  bins, title. *Take into the specification* makes it part of the dashboard, and applies the specification
  again so that Superset holds its governed form;
- what **cannot be taken**, and why — an ad-hoc metric in place of a governed one, a visualization nobody
  approved, a dataset that is not registered, several metrics on one chart. It is refused, never approximated;
- what **a specification has no place for** — a colour, a legend switched on. It is named, and a build sets it
  back. *Put back as specified* does so at once.

A shared chart edited in Explore and taken becomes a local copy: it is no longer the chart it referenced.

**Build validates what Superset shows.** Applying writes no version. *Build* takes the specification through
the same steps as a generated dashboard — 12 to 22: validation, data, the picture, visual QA, a version, a
commit, a package — and the same gates, `DRAFT → VALIDATING → READY_FOR_REVIEW` (never `AI_GENERATED`: a
person composed it). Steps 2 to 5 are skipped, saying why. Once a version exists, the Studio says whether
Superset still shows it: a change after validation is announced (« Superset no longer shows version 1.0.0: a
reviewer would be refused »), a reviewer **is** refused until it is built again, and *Put 1.0.0 back* undoes
it. A project whose version has been handed on (`UAT`, `PROD`) opens read-only, the frame still live.

**Signing in to Superset.** The frame is Superset's own page, so it uses the browser's Superset session: if
it asks, sign in there. A draft dashboard is seen by a Superset administrator (in this kit: `admin`); the roles
a dashboard is granted to see it once it is published. The Studio holds no Superset credential.

### dbt, in the same place

*Develop → Data Models (dbt)*. Above: what SharpSight keeps of dbt's models — layer, where each is built, its
tests and how they ended, what it is built from, which governed dataset it is the source of — the governed
project's, and the ones the engine generated on a branch (`GET /dbt/models`). Below, framed: **dbt's own
documentation site**, with its lineage graph, exactly as the production build wrote it (`dbt docs generate`;
published with the other artifacts and served at `/dbt-docs/`). A run's dbt steps — compile, test, run — are
under *Deliver → Pipelines*.

### Shared and local

Every asset has a scope. **Shared**: the registries' datasets and metrics, the templates, and the charts and
sections of the library (`aibi/library.py`). **Local**: a dashboard's own charts, and a dataset generated for
one project.

- *Share this chart* turns a local chart into one definition in the library; the dashboard then **references**
  it (`asset: <id>` in the specification), and so can any other. What the library says is written into the
  specification when it is saved, so a version records exactly what was built.
- A reference must stay what it names: a component that carries `asset` and differs from the shared chart
  fails validation. *Stop referencing* makes it a local copy.
- **A shared asset in use is not changed unseen**: the change is refused with its impact — which dashboards,
  which charts, whether any is in UAT or PROD — and goes through only when sent again with
  `acknowledge_impact`. One in use is not deleted. A change un-certifies: what was certified was the asset as
  it stood. Certifying needs `assets.certify`, and someone other than the author.
- A shared section is an ordered list of shared charts with a title; dropped on a section of a dashboard, its
  charts go into the first free slots that accept them, and what found no slot is said.
- The engine does the same: a generated dashboard that needs a chart the library already defines references
  it (and the run says so). A correction the fix loop makes to one dashboard's chart drops the reference.
- Shared assets are kept in the control plane's store and, as files, in the `bi-projects` repository under
  `_library/` — each change a commit.

One definition, referenced — in SharpSight. In Superset each dashboard still has its own chart object,
generated from that one definition: Superset's charts belong to the lifecycle of the dashboard they are on.

### Sections

A template groups its rows into **sections** (`sections:` in the template); a dashboard may give each a title,
a description, an owner, filters of its own, and hide it.

| | |
|---|---|
| title | a header above the section's first row, on the Superset dashboard |
| description, owner | SharpSight's: the Studio, the outline, the version's documentation, the governance checklist |
| filters | a Superset filter scoped to the section's charts and no others — named `Region · Accountability` in the filter bar. Probed, and judged on the dashboard, like a filter of the whole dashboard |
| visibility | `hidden`: the section stays in the specification, its components kept aside (`parked`), and is not built. A section holding a mandatory slot cannot be hidden |
| collapse / expand | how a person looks at it in the Studio; Superset has no collapsible part of a dashboard |
| layout | the template's rows — a dashboard cannot move a row from one section to another |
| permissions | **not offered.** Superset grants a dashboard, not a part of one; a narrower audience for one section could not be enforced, so it is not promised |

A specification that names no section is laid out as rows only, as before sections existed — which is why an
older version still restores as it was.

### Chart templates

`registry/chart_templates.yaml`: KPI, Time series, Top N, Distribution, Geographic, Detail table — each an
approved visualization and what is still to choose. Dragged onto a slot, the inspector asks for the metric,
the dimension and the style, then *Use in this slot* or *Create as shared*. **Variance** and **Target vs
actual** are listed and cannot be drawn yet — the first needs a comparison period no approved visualization
draws, the second a target the metric registry does not hold — and the Studio says so instead of hiding them.

### Where is this used, and what would a change touch

Every asset has a page (`GET /assets/<kind>/<id>`): what it is, where it is used — dashboards, charts,
metrics, shared sections, clients — and its **impact**: `none`, `low`, `medium` (something handed on to UAT),
`high` (something in PROD). The same answer is what refuses an unseen change to a shared asset.

### Governance, while developing

`✓ / ⚠ / ✗`, for the working specification: datasets certified · metrics certified · owner defined ·
description complete (the dashboard, its sections, its charts) · no broken dependencies · security configured
(who it is for, the row-level rules) · dataset refresh SLA · shared assets certified · **no duplicated asset**
(a local chart that repeats a shared one) · the last build's validation.

### The rest of the navigation

| | Screens | Over |
|---|---|---|
| **Develop** | Projects · Dashboards · Data Assets · Data Models (dbt) · Metrics · Templates · SQL / Data Lab | the projects; the asset catalogue; dbt's models and its documentation, framed; a governed query through the read-only gateway (`validate_sql`, `execute_readonly_sql`) and, framed, Superset's own SQL Lab |
| **Govern** | Tests · Certification · Code Review · Impact Analysis · Data Catalog | each project's last validation report; what is certified; versions waiting for a person, with approve and reject; impact; search by word, type, domain, status, owner, used-by |
| **Deliver** | Releases · Pipelines · Environments · Deployments | every version with its gates and its package; every run, step by step; what stands in DEV, UAT and PROD; approvals and promotions in order |
| **Operate** | Monitoring · Data Quality · Performance · Audit Logs | health and the compatibility matrix; the data and the performance checks of each last build; the audit trail and whether its chain is intact |
| **Clients** | Organizations · Workspaces · Users · Permissions | `rbac.yaml`, read-only, for those who administer or audit access |

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
| `design/tokens.yaml` | the approved colours, schemes, type scale, spacing, component variants |
| `visualizations.yaml` | what the AI may select, and what each is in the BI engine |
| `security/rbac.yaml` | users, roles, dataset grants, row-level policies — a stand-in for the portal's RBAC. One declaration, enforced by the data gateway **and** given to Superset |
| `policies.yaml` | the limits: SQL, dbt, performance budgets (concurrency among them), the null rate a column may have, visual thresholds, and what may leave Superset as a file |
| `compatibility.yaml` | the compatibility matrix: the versions this service was built and run against (below) |
| `domains.yaml` | the asset library's folders: logical domains, and which datasets and metrics are filed under each |
| `chart_templates.yaml` | the eight ways a chart is started in BI Studio, two of which cannot be drawn yet |
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

`search_assets` and `get_asset_usage` give the engine the library a person has in BI Studio: what there is,
where it is used, and what a change would touch.

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
| `aibi_dbt_dev` | the AI service | read `bi`; create in `bi_dev` only |
| `bi_reader` | the AI service's data gateway, and Superset's BI connection | read `bi` and `bi_dev`; write nothing |
| the Superset account `sharpsight-ai` | the AI service | Admin **in this DEV Superset** — it creates the BI connection, the roles and the row-level rules |
| the Superset accounts `sharpsight-probe-*` | nobody: a random password is set for the seconds a probe lasts, then the account is switched off | Superset's base role, and at most one `sharpsight_<role>` |
| a person's Superset account (`ai-bi.sh person`) | that person: the password is shown once to whoever created it, and is neither stored nor logged | Superset's base role and the roles `rbac.yaml` gives them |
| `AIBI_API_TOKEN` | whoever calls the API (the portal; here, the CLI) | present a user's identity |

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
| `GET /datasets` · `/datasets/<id>` · `/datasets/<id>/lineage` · `/metrics` · `/dimensions/<word>` · `/templates` | the registries |
| `GET /compatibility` | the matrix, beside what is running |
| `GET /assets` · `/assets/tree` · `/assets/search?q=…&kind=&domain=&status=&owner=&used_by=` | the asset library: cards, the tree by domain, search |
| `GET /assets/<kind>/<id>` · `/impact` · `PUT`/`DELETE …/favorite` | one asset, where it is used, what a change would touch; a person's favourites |
| `POST /assets/chart` · `/assets/section` · `DELETE /assets/<kind>/<id>` · `POST …/certify` | shared charts and sections (`assets.write`, `assets.certify`) |
| `GET /chart-templates` · `POST /chart-templates/create` | create from template |
| `POST /builder/inspect` `{spec}` | everything BI Studio shows of a working specification: validation, canvas, outline, governance, audience |
| `POST /builder/projects` · `GET /builder/projects/<id>` · `POST …/apply` · `POST …/build` | a project started by hand; its working specification; a change applied to Superset when it is valid; the build |
| `GET /builder/projects/<id>/superset` · `POST …/adopt` · `POST …/put-back` | what Superset holds against what was specified; taking what was edited in Explore; putting Superset back to the specification, or to the latest version |
| `GET /dbt/models` · `/dbt-docs/` | dbt's models as SharpSight keeps them; dbt's own documentation site |
| `GET /projects/<id>/outline` · `/governance` · `/definition` | the dashboard as a composition (the brief's section 15) |
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
| **BI Studio**, as two people in a real browser | The session asks the **frame** what it shows. `dev.user` starts a dashboard on the Management template: with one KPI it is a draft and the frame says nothing is in Superset yet; the trend, started from a chart template, makes it valid, and Superset draws it — two charts counted *inside the frame*. The rest is dragged from the library, picked, or « used in slot »; a ranking is shared; a filter for the dashboard and one for a single section; a section given a description and an owner: seven charts under three section headers, in the frame. A chart is opened in Superset's Explore inside the Studio; four charts are then edited the way Explore saves them, and read back: a time grain and a top 5 with a new title are taken (the shared ranking becomes a local copy), an ad-hoc metric is refused by name, a colour setting is named as having no place in a specification, and a donut switched off is put back. Built: 204 checks, none failed, `DRAFT → VALIDATING → READY_FOR_REVIEW`, and the Studio says Superset shows version 1.0.0 as validated. A title changed afterwards: the Studio announces it, the reviewer is refused (409, drift), *Put 1.0.0 back* restores it. `lead.reviewer` approves for UAT from Code Review and leaves at once: the builder she went to is left alone, and opens read-only with the frame still live. A second dashboard **references** the shared chart, hides a section (Superset then draws two charts), shares it, changes template, refills it, is built; the engine, asked for a regional ranking, references the same chart. Impact, a refused change, a refused deletion, certification by someone else. The dbt screen lists four governed models and one generated, with dbt's documentation framed (« dbt Docs »); the SQL Lab frame is Superset's. Every other screen of the navigation opens: the developer is refused the audit trail and the Clients screens, which the reviewer and the release manager see filled. No request left the machine: Superset's telemetry pixel is attempted by its full pages and blocked by the kit's own header, as the kit already measured |
| What the session, the pictures and the tests caught | a builder still loading when the person went elsewhere later overwrote the open builder's state — a drop then silently went nowhere; an approval that finished after the reviewer had moved on redrew whatever screen was open; a dashboard's own charts were listed to a user not cleared for their metric; a shared chart read as « title changed » the moment it was created; the word « null » printed under a list. All fixed, and the session now behaves like an impatient person on purpose |
| The engine's rules | 169 tests, no database needed: `bash ai-bi.sh test` |

## 9 · What is not here yet

| | State |
|---|---|
| **A language model** | **Left open on purpose** (2026-10-03). The planner is deterministic; `aibi/llm.py` is the seam, and a model plugs in by producing the same `Requirement` structure. Everything downstream is unchanged, which also means the deterministic run already exercises every guard a model will meet |
| **BI Studio**, the parts that are not there | The frame uses the browser's own Superset session: a person who is not a Superset administrator sees a dashboard there only once it is published — showing drafts to developers without making them administrators needs Superset's guest-token embedding (`EMBEDDED_SUPERSET`), which this kit keeps off. Only **charts** are read back from Superset: a layout changed in Superset's dashboard editor, or a chart created there, is not taken — it shows as drift and a build sets it back. A headline number's compact format is not read back. Charts are placed in a template's slots: no free positioning. **Per-section permissions** are not offered (Superset cannot enforce them). **Variance** and **Target vs actual** cannot be drawn. A dashboard has filters, not **parameters**. A shared chart is one definition in SharpSight and one chart object *per dashboard* in Superset. Organizations, users and permissions are shown, not edited. « Pipelines » lists the engine's runs: data pipelines are dbt's, and Airflow's in the platform |
| **Signing in to BI Studio** | a user name and the gateway token, kept for the browser session. It is the kit's stand-in for the portal's sign-in; nothing about it belongs in production |
| **The portal** (`sharp-sight-web`, `sharp-sight-api`) and **Keycloak** | not built. The API is what the portal would call; identity arrives as a header behind a shared token; RBAC is a file, and people reach Superset through a command an administrator runs |
| **Airflow** | not running, not framed, and listed as not used in the compatibility matrix. The `dbt-runner` one-shot builds the governed models at each start |
| **Separate UAT and PROD environments** | one Superset, one warehouse: promotion is the gate, the package and the record |
| **`TelecomQoEChart`** (the custom visualization) | not built. It is listed in `registry/visualizations.yaml` as approved-but-not-installed, and a specification asking for it is refused. Building it means compiling Superset's frontend with the plugin |
| **RabbitMQ, MinIO, a reverse proxy** | not needed by the slice; generations run one at a time in-process, packages are files |
| **An MCP server** | the tool layer has the shape (names, schemas, one audited entry point) and the asset library is among its tools; nothing serves it over MCP yet |
| **The offline bundle** | `build-devkit-bundle.sh` does not carry the engine's image yet |
| **A site-level map** | the geographic view is by region: a map of sites needs tiles from outside, and this kit calls nobody |
| **Choosing a theme from a request** | a specification may name an approved colour scheme, and the validator holds it to the tokens — but nothing in the planner ever sets one: every dashboard takes its template's |
| **Other warehouses** | the engine speaks PostgreSQL only (dbt profile, SQL policy dialect, data gateway), and the compatibility matrix says so: the other connectors of the brief are listed as not tested. The kit's Superset has drivers for SQL Server, Oracle and Hive; the engine does not use them |
| **A section a person can collapse on the dashboard itself** | sections are titled and filtered in Superset; collapsing one is the Studio's, since Superset has no collapsible part of a dashboard short of tabs |
| **Spark** as a transformation engine, **Version retrieval** from Superset | transformations are dbt on PostgreSQL. Superset keeps no versions of a dashboard: SharpSight does, in git, and `restore` re-applies one |
| **Other transformations** | a generated model is one shape: a governed base joined to a governed dimension to add columns. No aggregation to another grain, no filter, no derived column |
