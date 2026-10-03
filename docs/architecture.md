# How the kit is put together

Four containers, two volumes, one published port. Nothing it runs can see or touch a SharpSight platform on the
same machine: its own compose project, its own network, its own image name.

```
                    ┌─────────────────────────────────────────────┐
  you  ──3002──►    │ superset-devkit   Superset 6.1.0            │
                    │   · its own login (AUTH_DB)                 │
                    │   · SQL Lab open, drivers for MSSQL/Oracle/ │
                    │     PostgreSQL/Hive, and yacht:// for the   │
                    │     CEM through its JDBC driver             │
                    └───┬──────────────┬──────────────┬───────────┘
                        │              │              │
                 devkit-db        devkit-redis   /run/cem/cem.env
            PostgreSQL 16.4         cache          (volume, 0400)
            ├─ superset   metadata: dashboards, users
            ├─ devkit     your extracts (schema devmart), CSV uploads, CTAS
            └─ roaming    the platform's mart, when you import it (schema roaming_mart)
```

| Container | Role | Lives after a stop? |
|---|---|---|
| `superset-devkit` | the application, 2 workers × 20 threads | — |
| `devkit-db` | PostgreSQL: three databases (above) | yes, volume `devkit-db` |
| `devkit-redis` | cache only, 96 MB, nothing persisted | no |
| `devkit-init` | one-shot: `db upgrade`, admin account, `superset init`, seeding | exits 0 |
| `devkit-cem-sync` | one-shot: copies `cem.env` into a volume the app can read | exits 0 |

Volumes: `devkit-db` (everything you keep), `devkit-home` (Superset's home), `devkit-cem` (the CEM's settings).
`install.sh --erase` deletes only these three.

## What happens on a start

1. `devkit-db` comes up; on its very first start `initdb/10-devmart.sql` creates the `superset` database and
   the `devmart` schema.
2. `devkit-cem-sync` copies `cem.env` into the `devkit-cem` volume as root — the only way to read a `0600` file
   owned by you — and says how many settings it handed over, or that the CEM is not configured.
3. `devkit-init` runs `superset db upgrade`, creates the admin from `.env`, runs `superset init`, then
   [`devkit_seed.py`](../devkit_seed.py).
4. `superset-devkit` starts, loads [`superset_config.py`](../superset_config.py), which installs the drivers
   ([`devkit_drivers.py`](../devkit_drivers.py)), the connection forms ([`devkit_forms.py`](../devkit_forms.py)),
   the sign-in page's look (`templates/head_custom_extra.html`) and a narrow CSP.

## What the seeding creates

[`devkit_seed.py`](../devkit_seed.py), on every start, idempotent:

- **Dev kit store** — `devkit` database, where extracts and `CREATE TABLE AS` land;
- **CEM live** — `yacht://cem`, only when `cem.env` names a CEM; no credential in Superset's database;
- three saved queries: the catalogue of `dc_run`, a peek, the shape of an aggregate.

It marks what it made in the connection's `extra`, updates only that, deletes nothing, and leaves alone anything
you rename or edit.

## The settings that matter

All in `.env` (see `.env.example`); the ones people actually change:

| | |
|---|---|
| `DEVKIT_PORT`, `DEVKIT_BIND` | where it listens — loopback by default; `0.0.0.0` exposes it, and then put TLS in front |
| `DEVKIT_WORKERS`, `DEVKIT_THREADS` | one worker per core on a server; each worker that touches the CEM starts a JVM (~200 MB) |
| `DEVKIT_SQLLAB_TIMEOUT` | how long a query may run **and** how long gunicorn lets a request take (the image's own default is 60 s, which a slow cluster exceeds) |
| `DEVKIT_DATA_CACHE_TTL` | 60 s while building; raise it when you start demonstrating |
| `DEVKIT_LOCALE`, `DEVKIT_DARK` | French by default, light theme only |

The CEM's own settings live in `cem.env`, never in `.env` — compose interpolates `.env`, and a password holding
`$` or `#` would arrive cut (see [lessons](lessons.md)).

## The image

`Dockerfile`, from the same pinned Superset digest the product uses, so a dashboard drawn here meets the version
it will be installed on. On top: psycopg2, pymssql, python-oracledb, PyHive, JayDeBeApi + JPype, pyodbc with
FreeTDS and its `odbcinst.ini`, Java 17 and `krb5-user` for the JDBC bridge, the French catalogue compiled from
the `.po` files the base image ships, this kit's Python modules, its templates and its copy of the brand.

## Shipping it

```bash
bash build-devkit-bundle.sh                 # → dist/sharpsight-superset-devkit-<version>.tar.gz
INCLUDE_CEM=1 bash build-devkit-bundle.sh   # … and the Yacht driver, and cem.env if you have written one
```

The bundle carries the image, PostgreSQL and Redis, the compose file, the scripts and the documentation — it
installs with `bash install.sh` on a machine that has Docker and no internet. `install.sh` remembers the
checksum of the images archive it loaded, so unpacking a newer bundle over an older kit actually loads the new
image instead of keeping the old one.

It never carries: `.env`, `cem.env` (unless you deliberately build with one), a mart dump, or anything a
customer owns.
