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

**…and the alias must LIE about its version, unconditionally.** SQLAlchemy 1.4 reads `cx_Oracle.version` and
refuses anything under 5.2. python-oracledb has a `version` of its own — 2.5.1 — so a shim that only supplies
one « when it is missing » (`getattr(oracledb, "version", "8.3.0")`) supplies nothing, and EVERY Oracle
connection fails with « cx_Oracle version 5.2 and above are supported », whatever the string. It read like a
wrong connection string and cost three of them (2026-10-05). The entry above said « Oracle then works » after
the dialect LOADED; nobody had created an engine. → `oracledb.version = "8.3.0"`, always. Prove a driver by
`sa.create_engine(uri)` at least, and by « test connection » to a dead port: the answer must be a NETWORK
error (`DPY-6005 … Connection refused`), not a refusal that never left the process.

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

**A « slow dataset » can be a database with one CPU.** Roaming Outbound's four KPI tables took 6–8 s each on
the page, and the log said so chart by chart, so the dataset (`kpi_by_ref_day`, a view over views) was blamed.
Asked ALONE, with the very filter the page sends, the query answers in 0.2 s. A tab sends its queries
together, the query is CPU work, and `devkit-db` was limited to one CPU: four at once took 1.1–1.6 s each in
the database, ten at once 2.7 s — more on a busy laptop. JIT and parallel workers made no difference; an index
could not help (the base tables are keyed by day already, and the filter cannot use the key through the
view). → the limit is four (`DEVKIT_DB_CPUS`); the four tables together now finish in 0.75 s through Superset.
Before calling a query slow, time it alone AND several at once, and read the container's `cpu.max`. What is
left if a server needs more: the view stored as a table indexed by day answers in 5–9 ms (218 MB, eleven
seconds to refresh) — measured, not applied, because it is the product's mart.

**A setting in `.env` reaches a container only if compose hands it over.** `DEVKIT_DATA_CACHE_TTL` was
documented in `.env.example` and read by `superset_config.py` — and absent from the service's `environment`, so
the value anyone wrote was read by nobody and the configuration's own default always applied. Same for the
metadata cache and for the query timeout, which reached gunicorn and not Superset. Nothing failed, which is why
it lasted. → all four are in `x-devkit-env`. When adding a setting, check it INSIDE the container
(`docker exec … printenv`), or in what it changes (`cache_timeout` in a chart's answer).

**Port 8443 is the platform's on a developer's laptop.** The optional HTTPS entry was first published there,
and its first start failed: « Bind for 0.0.0.0:8443 failed: port is already allocated » — the SharpSight
platform's own edge. → 3443, beside 3002 and 3003. The plain port never moved.

**An upgrade is proved by upgrading.** The installer's upgrade path was written, read twice and looked right.
Rehearsed — the bundle of 3 October installed as a second compose project (`DEVKIT_PROJECT`, another port), a
connection and a dashboard made in it, then the new bundle from a new folder — it kept everything AND showed
two faults nothing else would have: an empty variable made `[ "" -lt 2 ]` fail quietly, so the one settings
change the upgrade existed for was skipped; and the bundle's `.env.example` still carried the AI BI engine's
section, so « add the settings this version knows » wrote seven `AIBI_` lines into a kit with no engine.
→ `rehearse-upgrade.sh <old bundle> <new bundle>` is the test for any change to `install.sh`: old bundle, real content, new bundle, then
ask for the content THROUGH Superset (a query through the saved connection proves its password still decrypts;
a row count does not).

**Measure what the page sends, not what a probe finds convenient.** A check that asked each chart without the
dashboard's « first item by default » filter measured the same four tables at 3–4 s: the query over all 78
days, which no reader ever sends. The request the page really made is in the `logs` table (`json`, the
`filters` of `form_data`); copy it. And sign in through `/login/`: the legacy map endpoint
(`/superset/explore_json/`) answers 401 to an API token.

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

### Access, and the roads a generation does not take (same day, second pass)

**A dashboard's roles decide nothing until it is published.** With `DASHBOARD_RBAC`, a draft is its owner's
alone; published, it is open to whoever holds one of its roles — and those roles need no permission of their
own, only a name. A chart's data is then given to a role holder when the request is made in the dashboard's
name (`form_data.dashboardId` and `slice_id`). Measured with accounts holding Superset's base role and exactly
one other: with no dashboard role, 404 on the dashboard and 403 on its data; with one, 200 on both.

**A row-level rule applies to whoever holds its role, whatever else they hold**, and rules sharing a
`group_key` are OR-ed. → the data gateway was changed to say the same: an unrestricted role no longer lifts a
restricted one. Two layers that disagree about which rows a person sees are worse than either rule.

**A policy on a table is not a policy on what is built from it.** The generated map dataset is the mart plus a
column, in another schema, under another dataset id: Superset would have shown the regional analyst three
regions on every chart and the whole country on the map. → a policy follows a dataset into everything
`derived_from` it, and both rules are written.

**`POST /dashboard/<id>/copy/` takes the copy's layout from `positions` INSIDE the metadata it is sent.** Sent
the original's `json_metadata` as stored, the copy has its charts and no layout. → the adapter puts the
original's `position_json` there. Found the first time the copy was run at all, by `ai-bi.sh check-adapter`.

**An import is not identical to its export.** The copy carries `default_filters: '{}'`; after export and import
it is gone. « {} » as TEXT is one more way the engine writes « nothing ». → it counts as empty in the
fingerprint, with null, `''`, `[]` and `{}`.

**The embedded configuration is accepted with `EMBEDDED_SUPERSET` off.** The flag gates the embedded page, not
the record: `POST /dashboard/<id>/embedded` answers and can be read back.

**With an ellipsis, ONE pixel of overflow costs the last letters.** « CEM experience Customer Experien… »
needed 372 px and had 371. A check that tolerated a pixel passed it.

**At 16 px per category the chart engine drops every other label, silently.** Sixteen regions in a slot that
gave them 16.1 px each: eight names drawn, no error, no scrollbar. At 19.1 px all sixteen. → the threshold is
18, and the ranking is then cut to what fits (« Top 14 »).

**A table's header is the raw column name** unless the dataset's column has a verbose name — `rat`, on an
executive dashboard. Setting it means sending back EVERY column with its id: a column left out of the list is
one Superset deletes.

**A pie of a rate draws, and means nothing.** Sixteen regional rates shown as « shares » of their own sum. No
engine refuses it. → a metric says whether its parts add up (`additive`), and a share-of-total chart is refused
for one that does not.

**The dashboard title is measured in one font weight and drawn in another.** Superset sizes the title field
from a hidden element in the theme's « strong » weight and draws the title itself in the normal one. With this
kit's theme (strong = 500) the two widths differ by a pixel for some titles, and one pixel is enough for the
ellipsis. Not a font-loading race: a cold and a warm load measure the same. → the engine's dashboards carry
`.dynamic-title-input { font-weight: inherit }`, so the drawn text is the measured text (352 needed / 351 given
became 351 / 351). The same line in `templates/head_custom_extra.html` would cure every dashboard in the kit,
hand-made ones included; that is the kit's stylesheet and was left alone.

**A pie's label on one line loses its share for long names; on two it does not.** `label_type: template` with
`{name}\n{percent}`. And `show_total` prints the total across the slices of a small ring — the total belongs in
a KPI card beside it.

**A share-of-total chart must draw every part.** A pie limited to its top twelve shows shares of something
that is not the whole. → no row limit on a pie; thin slices keep their place and lose their label.

**Superset states its version at `/version`** — `{"version_string": "6.1.0", …}`, no session needed. Nothing
under `/api/v1/` does. That is what the compatibility matrix is checked against.

**Dashboard RBAC gives a role the dashboard — and nothing it is made of.** Measured on 2026-10-03, with an
account holding Gamma and one `sharpsight_<role>` granted the dashboard: the dashboard and its charts' data
through it, 200; the same chart asked for by itself (`/api/v1/chart/<id>`, its `/data/`, `/api/v1/explore/`),
the dataset, SQL Lab: 404 or 403. Which is the property wanted, and is now a check rather than a belief.

**« Download as CSV » goes through the same road as the chart, row-level rules included.** The regional
analyst's file has three regions; the analyst's, sixteen. The count is compared with the data gateway's.

**A role that may not export a dashboard gets a 500, not a 403.** Gamma holds `can_export on Dashboard`; the
export then fails on the datasets the role has no direct access to. Nothing is handed over — but a check that
waited for a clean refusal would have called it a failure of the test. → the check is « was the definition
served », not « was it refused politely ».

**Any signed-in account gets 200 on `/explore/?slice_id=…`.** It is the page's empty shell; the chart inside it
is refused by `/api/v1/explore/`. → direct-address checks ask the API, not the shell.

**The audit scrubber took « pass » for a password.** Its pattern for secret key names matched `pass` and
`passed`, so every validation summary in the trail read `"passed": "[redacted]"` and each category's count of
passed checks with it — the one field that says whether the validation passed, withheld from the record of
the validation. Nobody had read a record that far; it showed when the visual QA results were added to the
trail and printed. → `password`, `passwd`, `passphrase` and `pwd` are secrets whatever they hold; a bare
`pass` only when it holds text.

**A fixed label threshold leaves a slice nobody can name.** A share by technology: 4G 60 %, 5G 18 %, 3G 18 %
— and a fourth slice with no label, because 2G was 3.99 % and labels stopped at 4 %. The threshold had been
chosen on sixteen regions, where thin slices crowd; with no legend, an unlabelled slice of four is anonymous.
Every check passed: only the picture showed it. → the engine asks how many parts the dimension has; up to
eight, every slice is labelled.

**A ranking and a table beside it came out with the same title**, « Active Users by region » twice. → a detail
table is « Active Users, detail by region ».

**Two things were both called `Status`.** A check's verdict (pass, warn, fail, skipped) and a version's gate.
With postponed annotations pydantic resolved each class against whichever was bound when the class was built,
so it worked — by accident, and until somebody moved a class. → the gates are `Gate`.

**A page measured in numbers before a threshold was written.** Cards cover 95 % of the grid; rows are 16 px
apart; a card is its row's height in grid units × 8 px exactly, and its width within 0.1 of a column of the
template's. The narrowest drawing is the country map: half its card, because Morocco is tall. → 85 % cover,
32 px, 0.35 of a column, and a warning (not a failure) under 30 % of a card.

**A screen that finishes loading late must not take over.** BI Studio's builder kept its state in one place.
Opening a project, then going elsewhere before it had loaded, let the late answer overwrite that state — and
the builder drawn next was wired to the other project: a chart dropped on a slot was « accepted » by a
read-only project nobody was looking at, and nothing happened on screen. No error anywhere. It was found only
because a scripted session did what a person does, switching user and page in quick succession. → a load
checks that it is still the one that was asked for before it touches anything; a build reports to the builder
it was started from, or to none.

**… and an action that finishes late must not redraw another screen.** The same session, a run later: a
reviewer approves for UAT — several seconds of comparing Superset with what was validated — and goes to
another screen meanwhile. When the approval answered, the page drew « the current screen » again, which was
now a builder: reloaded from the service, over whatever had been composed and not saved. → an action redraws
only the screen it was started from; a builder with unsaved changes is given back as it is, never loaded
again over itself; and the browser asks before the page is left with a dashboard half composed.

**A dashboard's own charts are assets too — and so need the same question asked.** Shared charts were shown
only to those cleared for their metric; local charts, added later to the same list, were shown to everyone
in the workspace. A test with a user cleared for nothing caught it the day it was written.

**A draft must not be the specification a promotion is judged against.** The project's stored specification
is what the access checks and the probes at promotion compute from. Saving a Studio draft into it would have
made a reviewer's approval compare Superset with something that was never built. → a draft is kept beside the
built specification, and becomes it only by a build that passed.

**Superset's dashboard layout has a HEADER component, and it is enough for a section.** `type: HEADER` with a
text, as a child of the grid, above a row. Measured on a render: 64 px high, and the gap between two rows grows
from 16 px to 96 px with one between them. → the whitespace check takes a header's height out, and counts the
spacing it brings; otherwise every titled section is « a hole ».

**Two text nodes side by side in a CSS grid are ONE grid item.** A two-column list written as `label, value,
label, value` with plain text collapsed into a single cell. → every cell is an element.

**A filter can be scoped to part of a dashboard**: a native filter's `scope.excluded` and `chartsInScope`. That
is what a section's own filter is; Superset names it in the filter bar like any other, so the section's title
goes into the filter's name.

**Superset can be framed by another page of the same host as it is configured here** — no `X-Frame-Options`,
and its session cookie (SameSite=Lax) is sent because `127.0.0.1:3003` and `127.0.0.1:3002` are the same
*site*: the port does not count. A Studio opened as `localhost:3003` framing Superset at `127.0.0.1:3002`
would be two sites, and by that same rule the cookie would not go to the frame (reasoned, not tried here).
→ the Studio builds Superset's address on the host it was itself opened on.

**A frame cannot be moved, only kept.** Re-attaching an `<iframe>` reloads it. A page that redraws itself by
replacing its content reloads Superset at every click. → the builder has fixed regions, each redrawn on its
own, and the frame is created once.

**A frame of another origin takes no drop and tells nothing of a click.** So the place where a slot is picked
and an asset is dropped cannot be Superset's picture of the dashboard: it is the structure tree beside it.

**A live dashboard is never « network idle ».** A test that waits for the network to go quiet waits for ever
once Superset is in the page. → wait for what was asked — the Studio's own « updating » mark to go.

**`dbt docs generate` writes a whole site, in one file.** `target/index.html` reads `manifest.json` and
`catalog.json` from beside it: published with them, it is dbt's own documentation and lineage graph, with
nothing to build. Its `<body>` has no height of its own (the page is laid out absolutely), which a test reads
as « hidden »; and it asks for two images named `{{ getIcon(…) }}` before its templates are filled — two 404s
that are dbt's, not the kit's.

**Reading a chart back needs the same vocabulary in both directions.** A chart's settings are built from a
specification's component; a chart edited in Explore is read back into one. Where the two do not meet — a
title compared as a field rather than as what people read — an untouched chart reads as changed. The test
that holds it: every kind of chart, built and read back, is itself.

## Superset as the interface (2026-10-04)

BI Studio, which the lessons about framing Superset were learned on, was taken out: Superset is the interface,
and SharpSight adds to it. What that taught:

**« On top of Superset » meant inside it.** Twice a request to build on Superset was read as building something
of ours around it — first screens of our own, then a frame with Superset in the middle. Both were built, shown,
and refused. → before building a screen, show where it will appear, in text, and count the new screens: the
answer wanted was zero.

**An export carries a dashboard's tags.** In Superset 6.1 the dashboard's file in an export has a `tags` key.
A gate shown as a tag therefore changed the fingerprint the dashboard was validated under, and the next
approval would have refused it as « changed after validation ». → `tags` is a governance mark, left out of the
fingerprint like certification. Found by tagging one dashboard and comparing before and after, before any
gate was tagged.

**A menu entry added by a hook has no permission.** Superset builds its AppBuilder with `update_perms=False`:
permissions are written by `superset init`, which runs with the kit's configuration and has never heard of an
entry a later layer adds. The entry was registered and nobody saw it — the menu API simply left it out. →
create the permission where the entry is added. And Superset's role sync then treats an entry it does not know
as everyone's: grant it the same way from the first start, and guard what the entry leads to.

**`FLASK_APP_MUTATOR` runs before Superset adds its own views.** Entries added there come first in the top bar.
→ moved to the end at the first request.

**`SUPERSET_CONFIG_PATH` layers a configuration without editing one.** The file it names is executed in place
of `superset_config`, which stays importable: the layer does `from superset_config import *` and extends one
hook. The kit's image and file are untouched, and without the overlay nothing is different.

**A copy made to be shown can go round the policy that guards the original.** The audit trail asks for
`audit.read` at the API. Its copy, put where Superset can chart it, was readable by the role every chart uses —
through SQL Lab — while the auditors themselves could not open the dashboard. → a role and a connection of its
own, closed to SQL Lab, and the dashboard granted to the roles that hold the capability. Asked of Superset as
each kind of person, and of the database as each role.

**A badge means one thing.** Superset's « certified » was used to mark the two system dashboards as the
engine's. In the same list it means « a person approved this for PROD ». → a tag for the first, the badge for
the second only.

**A volume another container mounts cannot be removed.** Once Superset mounted the volume dbt's artifacts are
published in, `--erase` could no longer delete it, and said nothing. → the documentation has a volume of its
own, rewritten at every build; the artifacts keep theirs, which nothing else mounts.

**What an extension system offers is read from the pinned image, not from the newest documentation.** The
brief listed panels on dashboards, a chat, a semantic layer. The image (`apache-superset-core 0.1.0rc3`) has
contribution points in SQL Lab only; the chat is in the unreleased documentation; the semantic-layer module
the 6.1.0 documentation lists is not in the package. → listed side by side in docs/ai-bi.md before a line of an
extension is written.

**Superset's dashboard list shows the first two tags of a dashboard, by the alphabet, and counts the rest.**
With four tags — domain, env, gate, project — the list read « domain:cem  env:PROD  +2… »: the gate, the one
thing the tags were for, was the one hidden. Seen in the picture of the list, not in the API, which returned
all four. → three tags on a dashboard, chosen so that the environment and the gate come first; the domain goes
on the charts and the datasets, where it describes something.

**Hiding a menu entry protects nothing.** dbt's documentation was gated by the permission to see its entry —
which Superset's own role sync gives to every standard role. Every signed-in account could read every model's
SQL, including one the engine refuses a model's schema. → the route asks the rule itself (a role granted a
dataset), and the entry is shown by that same rule: one rule, read from rbac.yaml, used twice. Found by a
second reader of the commit, an hour after it was made.

**The reading of an edited chart was deleted with the screen that called it.** When the Studio went, so did
`read_back` — its only caller was the Studio's « take into the specification » button. It never needed a
page: it compares a Superset chart with a specification. A day later it was the heart of « Superset is where a
person edits »: restored from history, behind two commands. → when a screen is removed, ask of each thing
behind it whether it served the screen or the person; the first goes, the second gets another way in.

**Two statements of one rule need a test that holds them equal.** Who sees which entry of the SharpSight menu
is said in Superset's process (the add-on) and who is granted the dashboard is said in the engine's: two
files, two containers. Nothing failed — and nothing would have, the day one was edited alone: an entry that
opens nothing, or a dashboard nobody is shown. → the engine's tests load the add-on's rule from its file and
compare, dashboard by dashboard. Asked for by a second reader.

**A commit that sets things back must say which, before.** The first `commit` named what it would undo chart
by chart, and only said « a layout, a filter… » when it had nothing to take. With one chart change to take, a
row moved in the dashboard editor and a chart added by hand were undone in silence: the template's layout is
applied again at every build. Found by reading the code path a second reader pointed at, not by the scenario,
which had edited charts only. → what differs on the dashboard itself is listed in `validate`, in the commit's
answer and in its audit record; and the scenario now moves something on the dashboard.

**« Colour-vision-safe » is a number or it is nothing.** The kit described its client series as validated.
Measured — each colour passed through Machado's deuteranopia and protanopia matrices, distances in L*a*b* —
its neighbours are well apart and two colours three places from each other are 3 apart: the same to that eye.
The first candidate for SharpSight's own series had a violet 5 from its blue. → the series was chosen by the
measure, the numbers are written beside it, and the word « validated » is gone from the description.

## A Superset extension (2026-10-04)

**Superset's extensions API is itself behind the flag — and so are its permissions.** `ExtensionsRestApi` is
registered only when `ENABLE_EXTENSIONS` is on. The kit's `superset init` runs without it, so the permissions
of that API were never written: the bundle loaded, and `GET /api/v1/extensions/` answered 403 to an
administrator — which is the call every browser makes to find out there is an extension. → written by the
configuration layer that turns the flag on, at the first request, for the views it adds.

**Superset's loader checks nothing, and fails quietly.** No checksum and no signature on a bundle; a backend
that does not import is printed and skipped, and Superset starts without it. → the checksum is recorded when
the bundle is built and verified before Superset is told where bundles are; and the engine asks Superset what
it loaded, every refresh, and says so in /health.

**A zip is not the same zip on two machines.** Built on Windows and rebuilt on Linux from the same files, the
bundle differed twice: the two zlib builds do not deflate alike, and each entry's header records the system
it was made on. A checksum over it meant nothing until both were pinned. → stored, not deflated; one date;
« made on Unix » said everywhere. Found by the test that rebuilds the bundle in the engine's container.

**`dist/` was already ignored.** The kit's `.gitignore` leaves out every folder named `dist` — bundles are
built there. The extension's built panel is in a folder of that name: it would have stayed out of the
repository, and the bundle with its recorded checksum could not have been rebuilt by anyone. Seen in
`git status`, which did not list it. → one exception, said beside the rule; and the files marked as bytes.

**A panel is only as reachable as the page it is on.** The one place 6.1.0 takes a panel is SQL Lab, and this
kit gives SQL Lab to administrators only — who are not people of rbac.yaml, so the engine refuses them. The
panel worked and nobody it was for could open it. → those who generate are given Superset's `sql_lab` role:
the page, no database.

**Acting through Superset means trusting who Superset says is signed in.** An extension's backend runs inside
Superset; to ask the engine anything it must hold the engine's token and name the person. Whoever can create a
Superset account can then be anyone the engine knows. → asked of the owner before it was built, with what it
costs; accepted for validate and commit; and nothing that approves is behind it — which a test reads the
extension's source to hold.

**A property of the source is not a property of the system.** « The extension approves nothing » was true: it
has no such endpoint, and a test reads its source to say so. But Superset held the engine's one token, which
the engine accepts for anything — so the rule that PROD needs a second person rested on the absence of forty
lines in a file inside Superset. Another extension, a fault, Superset itself, could have asked. → Superset
has a token of its own, and the engine answers it for four calls and refuses the rest; the test now asks the
engine's own routes which ones that token opens. Found by a second reader, the same hour it was committed.

**Flask-AppBuilder exempts an API from CSRF protection unless told otherwise.** Superset's own APIs are told
otherwise by a mixin; the `@api` it gives an extension is not. The panel sent the token and nothing checked it:
a POST with a developer's session cookie and no token started a commit — measured. → `csrf_exempt = False`, a
test that the source says so, and the forged request sent by `check-adapter` every time.
