# Changelog

Versions are the date a bundle was built (`sharpsight-superset-devkit-<version>.tar.gz`). Everything below was
built and verified on a running kit before it was written down.

## Not in a bundle yet — the AI BI engine (2026-10-03)

An optional overlay: the kit starts without it, and its `docker-compose.yaml` is not edited. The engine is 0.2.0. One thing of the kit's own did change with it, on 2026-10-04: its theme and sign-in page are SharpSight blue, in the same image tag. See [docs/ai-bi.md](docs/ai-bi.md).

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
