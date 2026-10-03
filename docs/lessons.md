# What this kit knows, and how it learned it

Every entry below was **measured**, not assumed — usually because something looked right and behaved wrong.
They are here so the next person does not pay for them twice. Each says what happened, what it cost, and where
the answer lives in the code.

---

## Superset's drivers

**Superset 6.1 ships SQLAlchemy 1.4, where `oracle+oracledb` does not exist.**
It arrives in SQLAlchemy 2.0. The only Oracle dialect in 1.4 is `oracle+cx_oracle`, and cx_Oracle needs Oracle's
Instant Client — a licensed ~100 MB download with native libraries, impossible on an air-gapped machine.
→ `devkit_drivers.py` registers **python-oracledb** (pure Python, thin mode) under the name `cx_Oracle`, which
its own documentation prescribes, and aliases `oracle+oracledb` to the same dialect. Oracle then works with
nothing installed but the wheel. *Thin mode needs Oracle Database 12.1 or newer; an 11g server would need the
thick client.*

**Superset offers an engine only if that backend's DEFAULT dialect can import its driver.**
`get_available_engine_specs()` calls `sqlalchemy.dialects.registry.load("mssql")` and then `dialect.dbapi()`.
SQLAlchemy's default for `mssql` is **pyodbc**, which needs unixODBC and Microsoft's ODBC driver. Neither was in
the image, so **Microsoft SQL Server appeared nowhere** in the connection dialog — while `mssql+pymssql://`
worked perfectly if you typed it by hand.
→ `devkit_drivers.py` registers **pymssql** as that backend's default dialect.

**The « host, port, user, password » form exists only for a spec carrying `BasicParametersMixin`.**
PostgreSQL has it; `MssqlEngineSpec` and `OracleEngineSpec` do not, so both were URI-only.
→ `devkit_forms.py` gives both the mixin, without forking: it adds a base to the classes Superset already
loaded, so the registry and every `issubclass` check see the same objects.

**Oracle names a database two mutually exclusive ways**, and Superset's builder handles only one.
SQLAlchemy's Oracle URL carries the **SID in the path** and the **service name in the query string**
(`?service_name=…`). `BasicParametersMixin.build_sqlalchemy_uri` reads `parameters["database"]` with no guard,
so a form left empty for a service-name connection raised `KeyError`.
→ `devkit_forms.py` gives Oracle a schema where the SID is optional and a builder that tolerates its absence.

**A second road to SQL Server.** pymssql is the simple driver and the first to give up — an old server, a forced
encryption, an Azure endpoint. The image also carries **pyodbc with FreeTDS** (Debian's driver: no licence
click-through, no Microsoft repository to reach) and the `/etc/odbcinst.ini` entry that names it, without which
`?driver=FreeTDS` fails with "Can't open lib". Use
`mssql+pyodbc://user:password@host:1433/db?driver=FreeTDS&TDS_Version=7.4`.

---

## The CEM, through its JDBC driver

**The Yacht driver has no SQLAlchemy dialect**, so Superset cannot dial it — which is why `yacht_dialect.py`
exists. JayDeBeApi is a DB-API 2.0 module, so only the dialect was missing: how to open a connection, and what
schemas, tables and columns exist.

**Spark quotes identifiers with backticks, and a double-quoted word is a STRING.**
SQLAlchemy's default preparer writes `SELECT "ACTDAY", "Segment" …`; Spark answered with the literal text
`ACTDAY` in every row — 602 identical rows, no error anywhere. The tell: lower-case columns (`direction`, `cc`)
showed real values, because SQLAlchemy quotes only what must be quoted.
→ `YachtIdentifierPreparer` sets the quote characters to `` ` ``.

**`LIMIT` must be a number, not a bound parameter.** Spark's JDBC takes no parameter there, and the failure
comes back as a parse error on a query nobody typed. → `YachtCompiler.limit_clause` writes the integer.

**A JDBC driver hands back Java objects.** `show databases` returns `java.lang.String` for every cell and
psycopg2 refused the whole insert with « can't adapt type 'java.lang.String' » — *after* the query had run on
the cluster. Worse, **JPype registers a boxed `java.lang.Integer` as a Python `int`** and a `java.lang.Boolean`
as a `bool`, so an "already native?" shortcut placed first lets Java objects through untouched (the test caught
it only when it started demanding exact types rather than `isinstance`).
→ `cem_to_mart.py::_native` decides by the Java class name, before any native shortcut.

**The cluster's refusal carries its reason past the usual truncation.** Yacht answers HTTP 403 with a JSON body
whose `data` block holds the message; the first 300 characters are column metadata, so a plain truncation showed
everything except why. → `cem_to_mart.py::_readable` parses it: *"the cluster refused with HTTP 403 and said:
The user name or password is incorrect, or the account is locked."*

---

## Secrets and files

**docker compose interpolates values — in `.env` AND in an `env_file`.**
`p$aS#sw0rd${NOPE}end` written in the file arrived in the container as `p#sw0rded`: `$aS` and `${NOPE}` were
expanded to nothing. A perfectly good account is then refused with an HTTP 403 that reads like a typo. Compose
also **prints the fragments it did not recognise** (« The "aS" variable is not set ») — pieces of a password on
the console and in the logs.
→ `cem.env` is **mounted as a file** and read from disk by `cem_to_mart.py` and `yacht_dialect.py`; no CEM
setting passes through compose.

**A bind-mounted `0600` file is unreadable inside the container.** The file is owned by the person who wrote it;
the container runs as `superset` (uid 1000); a bind mount keeps the host's ownership. On a real Linux host that
is `PermissionError`; Docker Desktop fakes permissions and shows nothing.
→ `devkit-cem-sync` (a one-shot compose service running as root) copies it into a **volume** on every start, so
the host file keeps its `0600` and no world-readable copy is made beside it. It runs on *every* start, because
copying it only in `install.sh` meant editing `cem.env` and restarting never reached the container.

**Say when there is nothing to hand over.** An empty `cem.env` was created silently once and cost an afternoon:
the dialect kept saying its settings were not set while the file sat right there. The sync service now says
*"cem.env is empty or absent — the CEM is NOT configured"*, and the dialect's refusal names what it found
(absent / unreadable / empty / N settings).

---

## The interface

**Superset 6.1 draws its sign-in page in React.** Replacing Flask-AppBuilder's `login_db.html` does nothing —
the stock page keeps coming back. The supported way in is **`head_custom_extra.html`**, which `superset/spa.html`
includes on every page.

Two things the CSS had to learn, both by measurement:
- the login fields carry an **`id` and no `name`**, so a `body:has(input[name="username"])` scope left the whole
  stylesheet inert;
- the labels' words live in a **child `<span>`** with its own font size: zeroing the label changes nothing, and
  hiding the span removes the box the replacement text was attached to. It keeps its place and loses its size.

**The telemetry beacon is in the frontend bundle.** `SCARF_ANALYTICS = False` removes the pixel from the page
Superset renders, but `8085.*.entry.js` adds one at runtime. → a narrow
`Content-Security-Policy: img-src 'self' data: blob:` blocks it; measured as *attempted, blocked by csp, nothing
completed*.

---

## Serving

**The image runs `gunicorn --timeout 60`.** Any request longer than a minute gets its worker killed — a slow CEM
query is enough, and with two workers the interface then hangs. → `GUNICORN_TIMEOUT` follows
`DEVKIT_SQLLAB_TIMEOUT`.

**The metadata pool must follow the threads.** The image runs 20 threads per worker; SQLAlchemy's default pool
is 5 with 10 of overflow, so a burst (SQL Lab's own bootstrap is one) queues behind the pool rather than the
database. The platform met this in production. → `SQLALCHEMY_ENGINE_OPTIONS` sizes the pool from
`SERVER_THREADS_AMOUNT`.

**A hung SQL Lab is usually its tab state, not the server.** Symptom: skeletons for ever while the server is
idle and every request returns 200. Look for `GET /api/v1/dataset/<id>` returning **404** right after an
Explore round trip — the editor is hydrating a tab that points at a *query* id as if it were a dataset. Two
tabs both marked `active` do the same. Cure:
`delete from table_schema; delete from tab_state;` in the `superset` database (tabs only — dashboards, charts,
datasets and saved queries are untouched).

---

## Carrying the platform's dashboards

**Superset 6 records a CATALOG on every dataset, and for PostgreSQL the catalog IS the database.**
The exported datasets say `catalog: roaming`, so Superset dials a database of that name whatever the
connection's URI says. Restoring the mart into `devkit` gave a dashboard that drew nothing, no error on screen,
and only the log saying `FATAL: database "roaming" does not exist`.
→ `import-into-devkit.sh` restores into a database called **`roaming`**, beside the kit's own.

**`import-dashboards` keeps an existing connection's URI.** It matches a database by UUID and keeps the one it
already has — so a kit that had imported before kept pointing at the platform's `superset-db`, which does not
exist on another network, while the archive on disk was perfectly correct.
→ the import runs **`set-database-uri` afterwards** as well: idempotent on a fresh kit, a repair on an old one.

**`set-database-uri` CREATES a connection when the name is unknown.** A placeholder name left in a command line
produced a junk connection called `NAME_FROM_THE_LISTING`. Pass the exact name, and check the result.

---

## Working on Windows

**Git Bash rewrites an argument that looks like a path** before Docker sees it: `-p /tmp/x.zip` reaches the
container as `C:/Users/.../Temp/x.zip`. Turning conversion off globally (`MSYS_NO_PATHCONV=1`) breaks the other
direction — the *host* paths the same script passes. → container paths go inside a `sh -c '…'` string.

**`docker compose cp` lands a file as root** while the container runs as `superset`, so a tidy-up `rm` inside
the container may be refused. It must not fail the operation around it.

---

## The AI BI engine, and what Superset's API really does

All of these were met on 2026-10-03 while running the engine ([ai-bi.md](ai-bi.md)) against this kit's Superset
6.1. Each has a test in `services/ai-bi/tests/` so it cannot quietly come back.

**A query-level `time_range` REPLACES the range written in a `TEMPORAL_RANGE` filter clause.**
That is how a dashboard's time filter reaches a chart: the chart carries its own clause on the time column
saying « No filter », and the dashboard sends `time_range`. So a query built with the clause
`{"op": "TEMPORAL_RANGE", "val": "Last week"}` *and* `"time_range": "No filter"` returns everything — the SQL
has no `WHERE` at all. The engine's own filter probe was built that way and reported a working filter as broken.
→ `chart_agent.query_context` sends the chart's clause always, and `time_range` only when the dashboard would.

**A colour scheme set on the DASHBOARD turns the country map categorical.**
Superset pushes the dashboard's scheme into every chart, and the map reads its presence as « colour by
category »: sixteen regions in six unrelated colours that say nothing about the metric, with no error.
→ no dashboard-level scheme; each chart carries the template's scheme itself, and the map its sequential one.

**Horizontal bars are drawn from the bottom up.** Sorted descending for display, a ranking shows its best
region at the bottom. → sorted ascending on screen (`x_axis_sort_asc`), while the rows *fetched* are still the
largest (`order_desc`, `row_limit`): two different settings.

**The Morocco map shipped in 6.1 is the older sixteen-region division** (`MA-01` … `MA-16`: « Grand Casablanca »,
« Souss - Massa - Draâ »…), not today's twelve regions. A reference table using the current codes joins
perfectly and draws an empty map. → `dbt/seeds/dim_region.csv` was written from the image's own geometry.

**Superset's image has no browser, so its screenshot endpoints cannot work here** — and there is no dashboard
version history or restore in the API at all. → the engine renders with its own headless browser, and keeps
versions itself, in git.

**A chart that is not on screen is not drawn** (`DASHBOARD_VIRTUALIZATION` is on by default). A screenshot taken
through an ordinary window reports everything below the fold as empty. → the engine's browser window is 2600 px
tall, and the picture is cut to where the dashboard ends.

**The histogram has no margin control for its y-axis title**, and draws it over the tick labels. → no y-axis
title on that chart.

**A KPI card under 128 px draws its caption at 8 px.** The number shrinks with the card, the caption with the
number. → every template's KPI row is 16 grid units, and visual QA fails any text under 9 px.

**The dashboard's own title can lose its last letters.** Superset sizes the title field from the text and comes
up a few pixels short for some titles (« CEM QoE regional Operatio… », 284 px needed, 281 given). Nothing in a
specification can cure it. → reported as a warning, with both numbers.

**What a measurement cannot see.** Chart text drawn on a canvas — axis labels, values — has no DOM, so two
axis labels printed into each other (« Wed 29 August ») pass every check. That is the one class of defect the
visual QA knowingly misses; it is what reading the picture, by a person or a model, is for.

**A browser signed in through the sign-in page can be signed out by that page.** The page fires requests of
its own before anyone is signed in (its event log, the language pack). When one is answered *after* the sign-in,
its `Set-Cookie` replaces the fresh session with an anonymous one, and the next request is redirected to
`/login/`. Seen once in some twenty-five renders: the engine waited ninety seconds for charts on a page that was
a login form, and Superset's access log showed `GET /superset/dashboard/7/ → 302`.
→ the engine signs in without a browser — the form's `csrf_token`, one `POST /login/`, a check on `/api/v1/me/`
— and hands the session cookie to the browser, which never loads the sign-in page.

**An export names its files and its references after ids the engine assigned.** `charts/QoE_Score_218.yaml`,
`chartId`, `chartsInScope: [218, …]`, `datasource: 45__table`. Delete a chart and create it again and every one
of them changes while the dashboard is identical. Comparing exports byte for byte to detect a hand edit
therefore refuses any restored dashboard. → the engine compares content: each chart named by its place on the
dashboard, each dataset by its table (`lifecycle.canonical_assets`), and a re-created chart keeps its uuid.

**Refreshing a dataset rewrites it even when nothing changed** — every column's `expression: null` becomes
`expression: ''` — and in 6.1 it also logs, for each date column, a failed connection to a database named after
the *schema* (`FATAL: database "bi" does not exist`, from `datetime_format_detector`): the catalog confusion
again, harmless but alarming. → a dataset is refreshed only when the registry has a column the engine lacks.

**`docker compose up --build` rebuilds every image in the project**, and a rebuilt image — identical or not —
makes compose recreate a container that was running perfectly well. Starting the engine that way restarted the
kit's own Superset. → `ai-bi.sh` builds the engine's image only, then `up -d`.

**dbt.** 1.10 announces at every run that it is no longer patched; the engine pins 1.12. A generic test's
inputs go under `arguments:` (`require_generic_test_arguments_property`), or every test warns. And `dbt docs
generate` rewrites `run_results.json` with its own compile — the build's results must be copied aside first.
