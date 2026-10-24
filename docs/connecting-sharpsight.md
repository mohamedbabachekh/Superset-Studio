# Connecting the kit to the SharpSight applications

The kit is a workbench, not a copy of the platform. It reaches SharpSight's world three ways, and they are
independent — you can use one, two or all three.

| | What it gives | What it costs |
|---|---|---|
| **The platform's dashboards and mart** | the Roaming Hub and Bassin de vie, with their data, in the kit | one archive to move, by hand |
| **The CEM, live** | SQL Lab queries the cluster where it stands | a JVM per worker; the cluster answers in its own time |
| **The CEM, landed** | an aggregate copied into the kit, instant to chart | you choose and refresh what is copied |

---

## 1 · The platform's dashboards, with their data

Two scripts, both in this repository.

**On the platform server** — read-only; it dumps and exports, it changes nothing:

```bash
bash export-for-devkit.sh          # copied from scripts/bundle/ → ~/sharpsight-devkit-export-<date>.tgz (+ .sha256)
```

It takes the schema `roaming_mart` of the `roaming` database (aggregates only: no subscriber number, nothing
under ten SIM cards) and **every** dashboard as Superset itself exports it. Exporting from the live server
rather than from the product's asset files matters: the dashboard's cross-filter scopes, its layout repairs and
its filter scopes are already applied there, and they travel with it.

It does **not** use `superset export-dashboards`: in 6.1 that command runs as a user called `admin`, which a
SharpSight Superset does not have — its users come from the platform. The script runs the same command object
as the first account holding the Admin role.

**On the machine running the kit**, from the kit's folder, with the kit up:

```bash
bash scripts/bundle/import-into-devkit.sh /path/to/sharpsight-devkit-export-<date>.tgz
```

It verifies the checksum, restores the mart into a database called **`roaming`**, writes this kit's password
into the connection, imports the dashboards, and sets the connection's URI again afterwards.

### Three things that must match, and why

| Thing | Value | Why it cannot be changed freely |
|---|---|---|
| the database | **`roaming`** | Superset 6 records a *catalog* per dataset, and for PostgreSQL the catalog is the database. The exported datasets say `catalog: roaming`. |
| the schema | **`roaming_mart`** | every chart is written against `roaming_mart.*` |
| the connection | **`cem_datamart`** | the datasets reference it by name and UUID |

Both applications read that one schema: the Roaming Hub from `cs_daily`, `alerts_daily`, `operator_scores_daily`
and friends, Bassin de vie from the `bv_*` tables in the same schema. One dump carries both.

### What a good import looks like

```
78 tables and views in roaming_mart
     connection(s) repointed: 1
Done.
```
then, in the kit: `roaming-outbound` with 115 charts and `bassin-de-vie` with 77.

A re-import matches on UUID, so it overwrites only those two dashboards. Anything you built yourself is
untouched.

### The archive never travels in the bundle

It holds the customer's data. The offline bundle carries images, drivers, scripts and branding — never this.
Move it yourself, and treat it as you would the mart.

---

## 2 · The CEM, live in SQL Lab

Fill `cem.env` (copy `cem.env.example`), restart, and add a connection whose SQLAlchemy URI is exactly:

```
yacht://cem
```

No credential goes into that form: [`yacht_dialect.py`](../superset/yacht_dialect.py) reads them from the mounted
`cem.env` at connect time, so **Superset's own database never stores them**. Then
`select * from dc_run.<table> limit 100` reads the cluster and stores nothing.

`CEM_SCHEMA=dc_run` says which schema SQL Lab opens on — the guess from the catalog name is wrong whenever the
two differ (`datacube_dc_run-SPARKSQL` holds `dc_run`).

What it costs: a JVM starts inside each Superset worker on first use (~200 MB), and every query opens its own
JDBC connection — safer than handing a pooled JDBC connection to the next person's query, but it pays the
cluster's authentication each time. `CEM_POOL=queue` turns pooling on.

---

## 3 · The CEM, landed

For anything you will look at more than once, do not fire twenty chart queries at the cluster:

```bash
docker compose -p sharpsight-superset-devkit exec superset-devkit \
  python /app/pythonpath/cem_to_mart.py "select day, country, sum(attempts) a from dc_run.… group by 1,2" \
  --table devmart.my_extract
```

The table is replaced whole in one transaction, so a chart never reads it half-written. Chart it through the
**Dev kit store** connection, schema `devmart`. This is the pattern the platform itself runs in production: a
batch fills a mart, and dashboards only ever read the mart.

---

## Where the kit and the platform differ on purpose

| | The platform's Superset | This kit |
|---|---|---|
| who you are | the SharpSight gateway authenticates and re-checks RBAC on every request; no login screen | an ordinary Superset login |
| what you may reach | one read-only connection; SQL Lab closed to readers | any connection you add; SQL Lab open |
| the dashboards | imported from files, not editable by readers | yours to draw |
| the origins | each application's origin serves only its own dashboards | no such confinement |

That last row is why a kit is never what a customer is given: it is a workbench, and an admin here can run any
SQL the connected account can run. Give it **read-only** database users.
