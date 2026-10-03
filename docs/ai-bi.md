# SharpSight AI BI — the engine, on top of the kit

A proof of concept of the AI-assisted BI development engine: a request in plain words becomes a **governed,
validated, versioned** dashboard, and then waits for a person.

> Superset draws. dbt transforms. Git remembers. SharpSight decides what may be asked of each, checks what came
> back, and keeps the record. The AI is a developer's assistant inside those walls, not an administrator.

It is a **vertical slice**: one request goes all the way through, on real components. What is deliberately not
here yet is listed at the end, and nothing below claims otherwise.

---

## 1 · Start it, and run the brief's own request

```bash
bash install.sh        # the kit, if it is not installed yet
bash ai-bi.sh          # the engine: three one-shots and one service, added to the kit's compose project
bash ai-bi.sh demo
```

`demo` sends, as `dev.user`:

```
Create a CEM QoE dashboard using the executive template.

Use QoE Score, Active Users and Degraded Users.
Show daily trend, regional ranking, QoE distribution and geographic view.
Add date, region and RAT filters.
```

and prints each of the twenty-two steps as it finishes. It ends on a version `1.0.0`, in `DEV`,
`READY_FOR_REVIEW`, with its git commit, and a dashboard at `http://127.0.0.1:3002/superset/dashboard/cem-qoe-overview/`.

Then, as two other people:

```bash
bash ai-bi.sh approve cem-qoe UAT                                  # lead.reviewer
bash ai-bi.sh approve cem-qoe PROD "checked in UAT with the CEM team"   # release.manager
bash ai-bi.sh audit cem-qoe                                        # everything that was done, and by whom
```

| | |
|---|---|
| The engine's API | `http://127.0.0.1:3003` — loopback only, always (`AIBI_PORT`) |
| Stop it | `bash ai-bi.sh --stop` — the kit keeps running |
| Its tests | `bash ai-bi.sh test` |
| Memory | under 0.1 GB idle, about 0.5 GB while a dashboard is being rendered (measured: 77 and 479 MiB) |
| Time | the request below takes 70 to 80 seconds, most of it dbt and three page loads (over three minutes once, right after an image build, on a machine short of memory) |

The CEM data in this kit is **synthetic** (`services/ai-bi/sql/bootstrap.sql`): sixteen regions, four sites each,
four technologies, ninety days ending yesterday, every value a pure function of site, technology and day. With
a real cluster the same table is what `cem_to_mart.py` lands, and only the dbt variable `cem_source_schema`
changes.

---

## 2 · What happens to a request

| # | Step | What actually happens here |
|---|---|---|
| 1 | Parse the requirement | the request is read against the governed vocabulary (`registry/lexicon.yaml`) into a `Requirement`. What is not recognised is **reported**, never guessed |
| 2 | Find certified datasets | only what the requester may read; certified first |
| 3 | Find certified metrics | registered metrics only — a metric that is not in the registry is refused, not improvised |
| 4 | Identify missing data | each view is laid into a template slot and asked: which dataset serves it? Here the map needs a region **code**, and the certified mart has only the region's name |
| 5 | Determine transformations | a certified dimension (`cem.region`) has the code and shares the key → a join, as a **plan** |
| 6 | Generate the dbt model | SQL and tests rendered from the plan, checked by the policy engine, written on branch `ai/cem-qoe` |
| 7 | Compile | `dbt compile`, target `dev` |
| 8 | Run dbt tests | the tests of what the model is built **from**, before it is built |
| 9 | Run the model | `dbt run` into `bi_dev`, deferring every other model to production; then the model's own tests |
| 10 | Register the dataset | as `cem.cem_qoe__qoe_daily_region_iso` — generated, **not certified**, this project's own, with its lineage from dbt's artifacts |
| 11 | Generate the specification | the normalized document of the brief's section 6 |
| 12 | Validate the specification | template, slots, types, metrics, roles, RBAC, theme. Superset has not been called yet |
| 13 | Create Superset assets | datasets with their certified metrics saved on them, one chart per slot, the dashboard, its filters |
| 14 | Retrieve the dashboard's data | each component's query, through Superset's own query API; each filter is probed |
| 15 | Validate the query results | data, SQL, BI, security, performance |
| 16 | Render a screenshot | a headless browser opens the dashboard at each of the template's viewports — and once more with every filter set |
| 17 | Visual QA | geometry, clipped text, empty or failed charts — and what the dashboard itself requested |
| 18 | Fix detected problems | bounded fixes on the specification (top N, a shorter title, a compact number, another approved type). The brief's request needs none; see « what was verified » |
| 19 | Re-render | and validate again. If it still fails, **the run fails**: no version is written |
| 20 | Store the final version | the project's folder, which is the `.ssbi` layout |
| 21 | Git commit | in `bi-projects`, tagged `cem-qoe/v1.0.0` |
| 22 | READY_FOR_REVIEW | |

A run is a record (`GET /runs/<id>`): every step with its times, what it found, and why it was skipped.

---

## 3 · The pieces

### The registries — `registry/`

Files, read at start and **checked against each other**; a registry that does not hold together stops the
service.

| | |
|---|---|
| `datasets/` | owner, certification, source model, grain, freshness SLA — and a **classification on every column**. A column without one fails the load |
| `metrics/` | the governed metrics: expression, aggregation, dimensions, format, owner, documentation, certification, version, lineage |
| `templates/` | `executive`, `telecom-kpi`, `operational`, `geographic`, `customer-experience`: layout, slots and what each accepts, theme, typography, spacing, colours, KPI formatting, filters, responsive rules |
| `design/tokens.yaml` | the approved colours, schemes, type scale, spacing, component variants |
| `visualizations.yaml` | what the AI may select, and what each is in the BI engine |
| `security/rbac.yaml` | users, roles, dataset grants, row-level policies — a stand-in for the portal's RBAC |
| `policies.yaml` | the limits: SQL, dbt, performance budgets, visual thresholds |
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
lists them with JSON schemas. The brief's twenty are there (`get_dataset`, `get_dataset_schema`,
`get_dataset_sample`, `get_metric`, `list_metrics`, `get_dashboard_template`, `execute_readonly_sql`,
`validate_sql`, `generate_dbt_model`, `compile_dbt`, `run_dbt_model`, `run_dbt_test`,
`create_superset_dataset`, `create_superset_chart`, `create_superset_dashboard`, `update_superset_dashboard`,
`get_dashboard_data`, `validate_dashboard`, `render_dashboard_screenshot`, `commit_project_version`), plus
`list_datasets`, `list_dashboard_templates` and `save_dashboard_spec`.

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

### The Superset adapter — `aibi/superset_adapter.py`

REST only: authentication, connections, datasets, charts, dashboards (create, update, copy, metadata), data,
export/import, embedded configuration. **Superset's metadata database is never touched.** The routes and schema
fields were read out of the kit's own image, not recalled.

What Superset 6.1 does not offer, and what the engine does instead:

| | |
|---|---|
| no dashboard version history, no restore | every version is a git commit of the specification and the export; restore re-applies a specification (`POST /projects/<id>/restore`) |
| no browser in the image, so its screenshot endpoints cannot work | the engine renders with its own headless browser |

Nothing above the adapter says "slice" or "form_data": `registry/visualizations.yaml` and two files
(`chart_agent.py`, `superset_adapter.py`) are what replacing Superset would rewrite. The registries, the
specifications, git and dbt would not notice.

### Validation — `aibi/validation_agent.py`, `aibi/visual_qa_agent.py`

Every check is a measurement, recorded whether it passed or not. One failure keeps a version out of review.

| | |
|---|---|
| **Data** | schema (registered columns exist, typed as dbt's catalog says), nulls, duplicates on the grain, freshness against the SLA, row count, declared ranges, accepted values, metric validity (overall and per day) |
| **SQL** | syntax, allowed database, read-only, timeout, result size, forbidden operations — over every statement the run executed — plus **probes**: nine hostile statements the guard must refuse |
| **BI** | dataset exists, saved metrics match the registry's expression, each chart's query succeeds, the dashboard's own requests succeed, no empty mandatory chart. **Filters twice**: each one through Superset's query API, and all of them on the dashboard itself — it is opened with the filters set, and every chart in scope must receive something else than it does unfiltered |
| **Security** | RBAC, row-level policies applied, a user with no grant is refused every dataset, tenant isolation (the same roles in another tenant are refused), the BI connection is read-only |
| **Performance** | query duration, dashboard load duration, query count, result size — against `registry/policies.yaml` |
| **Visual** | screenshot produced, every component on the page, no overlap, layout follows the template, nothing empty, no clipped text, no text under 9 px, labels have room; a clipped dashboard title is a warning |

The visual checks are measurements too: DOM geometry, pixel statistics of each chart's body, and the network
traffic of the real page. No model looks at the picture today — and one class of defect is therefore missed
knowingly: text a chart draws on a canvas (axis labels, values) has no DOM, so two axis labels printed into
each other pass. That is what a person, or a model reading the picture, is for.

### Versions and promotion — `aibi/lifecycle.py`

Every version records `project_id`, `version`, `git_commit`, `dbt_commit`, `superset_asset_version`,
`dataset_versions`, `metric_versions`, `template_version`.

| | Who | Gate |
|---|---|---|
| `DEV` | the AI and the developer | a version exists only if validation passed |
| → `UAT` | a reviewer (`bi.approve_uat`), a person | validation passed **and** the dashboard in Superset is still the one that was validated: its export is fingerprinted, and an edit made by hand afterwards is refused. The `.ssbi` package is built here |
| → `PROD` | a release manager (`bi.approve_prod`), a **second** person | explicit confirmation and a comment; the package's checksum is verified again. The dashboard is published, certified and locked against editing in Superset |

The `.ssbi` package is the project's folder as committed — `manifest.yaml`, `dbt/`, `semantic/`, `datasets/`,
`metrics/`, `superset/`, `dashboards/`, `charts/`, `security/`, `tests/`, `documentation/` — zipped, with the
commit and a checksum per file in its manifest. It is copied to `./exports/`.

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

One JSON line per action: user, actor (AI, human, system), AI model, request id, datasets accessed, SQL
generated, dbt files generated (with hashes), Superset assets modified, validation results, deployment,
timestamp. Each line carries the hash of the one before, so a line edited or removed afterwards breaks the chain
and `GET /audit/verify` says where. Secrets are scrubbed; rows of data never enter it.

---

## 4 · Who holds which credential

| | Held by | May |
|---|---|---|
| the database owner | `aibi-db-init`, for the seconds it runs | create roles and schemas |
| `bi_builder` | `dbt-runner` only | build the governed models in `bi` |
| `aibi_dbt_dev` | the AI service | read `bi`; create in `bi_dev` only |
| `bi_reader` | the AI service's data gateway, and Superset's BI connection | read `bi` and `bi_dev`; write nothing |
| the Superset account `sharpsight-ai` | the AI service | Admin **in this DEV Superset** — it must create the BI connection |
| `AIBI_API_TOKEN` | whoever calls the API (the portal; here, the CLI) | present a user's identity |

No credential reaches a model: a model's whole interface is the tool layer. The service that would talk to one
holds nothing that can change a governed table.

---

## 5 · The API

`Authorization: Bearer $AIBI_API_TOKEN` and `X-SharpSight-User: <user>` on every call but `/health`.

| | |
|---|---|
| `POST /projects/generate` `{prompt}` | start a generation → a run id |
| `GET /runs/<id>` | the run, step by step |
| `GET /projects` · `/projects/<id>` · `/projects/<id>/versions/<v>` | projects and versions |
| `GET …/versions/<v>/spec` · `/validation` · `/screenshot?viewport=desktop` · `/package` | what a version is |
| `POST /projects/<id>/approve` `{target, comment, confirm}` · `/reject` · `/restore` | review |
| `GET /datasets` · `/datasets/<id>` · `/datasets/<id>/lineage` · `/metrics` · `/templates` | the registries |
| `GET /tools` · `POST /tools/<name>` | the tool layer, as a model would use it |
| `GET /audit` · `/audit/verify` | the trail (`audit.read`) |

---

## 6 · Add a dataset, a metric, a template

All three are a YAML file in `registry/` and a restart of the service.

- **A dataset** — its dbt model must exist in `dbt/`; give every column a `classification`, name the grain, and
  set `role` on what can be a time axis (`time`), a breakdown (`dimension`), a number (`measure`) or a map key
  (`geo_region`, with `geo: {standard, country}`).
- **A metric** — an expression over a dataset's columns, the datasets and columns it is valid on (`lineage`),
  and the words people use for it (`synonyms`): that last line is how a request finds it.
- **A template** — rows of slots on a twelve-column grid, what each slot is for and accepts, and names from
  `design/tokens.yaml`. A name that is not a token fails the load.

---

## 7 · What was verified, and how

On 2026-10-03, on this kit (Superset 6.1.0, PostgreSQL 16.4, dbt-core 1.12.5), beside a running SharpSight
platform that was not touched:

| | |
|---|---|
| The brief's request, from nothing | all 22 steps; 179 checks, none failed, none warning; version `1.0.0`, `READY_FOR_REVIEW`, committed and tagged. Run from an erased engine four times over the day |
| The generated model | built in `bi_dev` on branch `ai/cem-qoe`: 5 760 rows, the same count as its base, its six tests passing |
| The picture | looked at by a person, at 1440 and 1180 px, and with the three filters set — which is how four of the entries in [lessons.md](lessons.md) were found |
| The review path | fifteen cases: no token, an unknown user, a user with no grant, the developer and the AI's own identity trying to approve, PROD before UAT, a chart edited by hand in Superset after validation (refused as drift), restore from git (drift gone), UAT, PROD without confirmation, PROD — and a regeneration refused once in PROD |
| The package | the eleven entries of the brief, the commit and 32 checksums in its manifest, no secret in any file |
| The audit trail | every tool call and every statement; the chain verifies; no secret and no row of data in it |
| The fix loop | **not triggered by the brief's request.** Exercised once on the live stack with the label threshold raised from 16 to 24 px: the ranking failed on both viewports, was cut to « Top 12 », re-rendered, and passed. The other three fixes are unit-tested only |
| A second template | `operational`, no transformation needed (steps 6–10 skipped), version `1.1.0` on regeneration |
| A run that cannot pass | forced with a label threshold no chart can meet: one fix tried, the run **failed**, no version was written, and the dashboard — which the attempt had cut to three charts — was put back as `1.0.0`, still matching its validated fingerprint |
| The engine's rules | 83 tests, no database needed: `bash ai-bi.sh test` |

## 8 · What is not here yet

| | State |
|---|---|
| **A language model** | **Left open on purpose** (2026-10-03). The planner is deterministic; `aibi/llm.py` is the seam, and a model plugs in by producing the same `Requirement` structure. Everything downstream is unchanged, which also means the deterministic run already exercises every guard a model will meet |
| **The portal** (`sharp-sight-web`, `sharp-sight-api`) and **Keycloak** | not built. The API is what the portal would call; identity arrives as a header behind a shared token; RBAC is a file |
| **Airflow** | not running. The `dbt-runner` one-shot builds the governed models at each start |
| **Separate UAT and PROD environments** | one Superset, one warehouse: promotion is the gate, the package and the record |
| **`TelecomQoEChart`** (the custom visualization) | not built. It is listed in `registry/visualizations.yaml` as approved-but-not-installed, and a specification asking for it is refused. Building it means compiling Superset's frontend with the plugin |
| **RabbitMQ, MinIO, a reverse proxy** | not needed by the slice; generations run one at a time in-process, packages are files |
| **An MCP server** | the tool layer has the shape (names, schemas, one audited entry point); nothing serves it over MCP yet |
| **The offline bundle** | `build-devkit-bundle.sh` does not carry the engine's image yet |
| **A site-level map** | the geographic view is by region: a map of sites needs tiles from outside, and this kit calls nobody |
