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
