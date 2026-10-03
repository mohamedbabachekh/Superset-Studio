# Changelog

Versions are the date a bundle was built (`sharpsight-superset-devkit-<version>.tar.gz`). Everything below was
built and verified on a running kit before it was written down.

## Not in a bundle yet — the AI BI engine (2026-10-03)

An optional overlay; the kit itself is unchanged and starts without it. See [docs/ai-bi.md](docs/ai-bi.md).

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
- **BI Studio** (the updated brief's sections 1 to 15), served by the engine at `/studio/`. An asset library
  filed under logical domains, with favourites and search; charts and sections **shared** — defined once,
  referenced by the dashboards that use them, not changed without their impact having been seen; dashboards as
  compositions of **sections**, each with a title on the Superset dashboard, an owner, filters of its own, and
  the option to hide it; an outline; chart templates; a contextual inspector; Dashboard, Data and Governance
  modes; and the navigation's other screens over what the engine already knew. A person composes a
  specification on a template's slots and builds it through the same steps and gates as a generated one.
  `ai-bi.sh studio` says where it is.
- **The Studio wraps Superset and dbt instead of standing beside them.** The middle of the builder is Superset
  itself, framed: the live dashboard, and Superset's Explore to edit a chart. A change is applied to Superset
  as soon as it is valid; what a person saves in Explore is read back into the specification — or refused,
  saying why (an ad-hoc metric, an unapproved visualization) — and a build validates what Superset shows.
  dbt's own documentation site is published by the production build and framed under SharpSight's table of
  models and tests; Superset's SQL Lab is framed in the Data Lab.
- **Which language model drives it is left open.** The planner is deterministic today; `aibi/llm.py` is the seam.
- Verified on a running kit, from an erased engine: the request (four times), fifteen review cases, a forced
  failure that wrote no version and put the reviewed dashboard back, the fix loop once under a stricter policy.
  Not built: the portal, Keycloak, Airflow, separate UAT/PROD environments, the `TelecomQoEChart` plugin, and
  the offline bundle does not carry the engine's image.

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
