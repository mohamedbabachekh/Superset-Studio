# SharpSight Superset — dev kit

> **This is the dev kit's own repository.** It was lifted out of the SharpSight monorepo
> (`tools/superset-devkit`) on 2026-10-03 so it can be versioned, built and upgraded on its own. It needs
> nothing from that repository to build or run; what it needs to *know* about the platform is written down in
> [docs/](docs/). Git here is local only — there is no remote, and none is wanted.
>
> | | |
> |---|---|
> | [docs/architecture.md](docs/architecture.md) | the containers, the databases, what happens on a start |
> | [docs/connecting-sharpsight.md](docs/connecting-sharpsight.md) | the Roaming Hub and Bassin de vie, the CEM, what must match |
> | [docs/cem.md](docs/cem.md) | the cluster: the five settings, the two routes, what each refusal means |
> | [docs/building-a-bundle.md](docs/building-a-bundle.md) | building, verifying and shipping a deployable bundle |
> | [docs/ai-bi.md](docs/ai-bi.md) | the AI BI engine on top of the kit (optional): a request becomes a governed, validated, versioned dashboard |
> | [docs/lessons.md](docs/lessons.md) | every trap this kit has already paid for, with how it was measured |
> | [CHANGELOG.md](CHANGELOG.md) | what each bundle changed |

A complete Superset to **build dashboards in**, with the drivers your sources need and the SharpSight look, that
runs on any machine with Docker and **shares nothing with the SharpSight platform**.

You get: an ordinary Superset login · SQL Lab open · connections to **SQL Server**, **Oracle** (no Instant
Client), PostgreSQL and Hive/Spark · a helper that lands a **CEM** query where Superset can chart it · the
product's logos, colours and French interface, so what you draw is what the customer will see · an export that
drops straight into a product repository.

---

## 1 · Start it

**From this repository** (a machine that can build images):

```bash
bash install.sh
```

**On another machine**, including one with no internet — build a bundle here first:

```bash
bash build-devkit-bundle.sh                 # → dist/sharpsight-superset-devkit-<date>.tar.gz  (~1 GB)
```

then, over there — **from the folder the two files are in**, because the checksum file names the tarball
without a path and `sha256sum -c` looks for it in the current directory:

```bash
cd /path/to/the/two/files
sha256sum -c sharpsight-superset-devkit-<date>.tar.gz.sha256
tar -xzf sharpsight-superset-devkit-<date>.tar.gz && cd sharpsight-superset-devkit-<date>
bash install.sh
```

On a Linux machine reading the file from a Windows drive (`/mnt/c/...` under WSL), copy it into the Linux
filesystem first and unpack it there: `install.sh` writes `.env` readable only by you, and compose bind-mounts
three folders from where it stands — neither survives a `drvfs` mount intact, and everything is far slower.

```bash
cp /mnt/c/.../sharpsight-superset-devkit-<date>.tar.gz* ~/ && cd ~
sha256sum -c sharpsight-superset-devkit-<date>.tar.gz.sha256
```

The first run writes `.env` with **generated** secrets and prints the admin password once. Nothing is shipped
with a password in it.

| | |
|---|---|
| Address | `http://127.0.0.1:3002` (change `DEVKIT_PORT`; `DEVKIT_BIND=0.0.0.0` to expose it — then put TLS in front) |
| Sign in | the admin printed on first run, also in `.env` |
| Stop / start | `bash install.sh --stop` · `bash install.sh` |
| Erase it | `bash install.sh --erase` — deletes **only** this kit's two volumes |

It costs about **1.2 GB of memory**. On a laptop that already runs the SharpSight platform, run one or the other.

---

## 2 · What is already there on the first start

Nothing to configure before the first query:

| | |
|---|---|
| **A sign-in page of its own** | the SharpSight lockup, in French, and the navigation hidden until you are in |
| **CEM live** | the cluster through its JDBC driver — `yacht://cem`, credentials read from `cem.env` at connect time, so Superset's own database never stores them. Created only when `cem.env` names a CEM. |
| **Dev kit store** | this kit's PostgreSQL: where `cem_to_mart.py` lands extracts, where CSV uploads go, and where `CREATE TABLE AS` writes |
| **Three saved queries** | the catalogue of `dc_run`, a peek at one table, and the shape of an aggregate to copy — under *SQL → Requêtes enregistrées* |
| **No outbound beacon** | Apache's telemetry pixel is off in the page and blocked by a header for the bundle that adds it at runtime: nothing leaves the machine |

They are created by [devkit_seed.py](devkit_seed.py) on every start and are **idempotent**: it updates what it
made, never deletes, and leaves alone anything you rename or edit (it marks its own in the connection's extra).

## 3 · Connect another source

**Paramètres → Bases de données → + Base de données.** The first four buttons are **Microsoft SQL Server**,
**Oracle**, **PostgreSQL** and **Apache Hive** — each opens a form, not a URI:

| | |
|---|---|
| Hôte | the IP or hostname |
| Port | 1433 for SQL Server, 1521 for Oracle |
| Nom de la base de données | the database (SQL Server) · the **SID** (Oracle) |
| Nom d'utilisateur / Mot de passe | the account |
| Paramètres supplémentaires | `service_name=ORCLPDB1` for an Oracle named by its service |

**Oracle, the two ways SQL Developer asks.** Its URL carries the SID in the path and the service name in the
query string, and they are mutually exclusive. So: put the **SID** in « Nom de la base de données », or leave
that empty and add `service_name` under « Paramètres supplémentaires ». Both were built and read back:

```
SID           oracle+cx_oracle://svc:***@10.1.2.4:1521/ORCL
service name  oracle+cx_oracle://svc:***@10.1.2.4:1521/?service_name=ORCLPDB1
SQL Server    mssql+pymssql://svc:***@10.1.2.3:1433/KPI
```

**Neither engine offers that form in a stock Superset** — both were URI-only, and SQL Server was not even in the
list, because Superset probes each backend's *default* dialect and `mssql` defaults to **pyodbc**, which needs
unixODBC and Microsoft's ODBC driver. The kit registers **pymssql** as that default and gives both specs the
parameters form; [devkit_drivers.py](devkit_drivers.py) and [devkit_forms.py](devkit_forms.py) say exactly what
that changes. **Oracle needs nothing installed either**: Superset 6.1 ships SQLAlchemy 1.4, whose only Oracle
dialect is `oracle+cx_oracle` — the old driver that requires Oracle's Instant Client — so **python-oracledb**
(pure Python, thin mode) is registered under that name, as its own documentation prescribes.

If you prefer to paste a URI, the box is still there under « Autres » / *Other*:

| Source | URI |
|---|---|
| SQL Server | `mssql+pymssql://user:password@host:1433/database` |
| Oracle (SID) | `oracle+cx_oracle://user:password@host:1521/ORCL` |
| Oracle (service) | `oracle+cx_oracle://user:password@host:1521/?service_name=ORCLPDB1` |
| PostgreSQL | `postgresql+psycopg2://user:password@host:5432/database` |
| Hive / Spark Thrift | `hive://host:10000/default` |
| This kit's own store | `postgresql+psycopg2://devkit:<DEVKIT_DB_PASSWORD>@devkit-db:5432/devkit` |

**If SQL Server refuses pymssql, take the ODBC road.** pymssql is the simple driver and needs nothing, and it
is also the first to give up — an older server, a forced encryption, an Azure endpoint. The kit therefore also
carries **pyodbc with FreeTDS** (Debian's free ODBC driver: no licence click-through, no Microsoft repository
to reach, which an air-gapped machine could not reach anyway). Same form, then « Autres » / *Other* and:

```
mssql+pyodbc://user:password@host:1433/database?driver=FreeTDS&TDS_Version=7.4
```

`TDS_Version=7.4` covers SQL Server 2012 and later; drop to `7.3` for 2008. Verified in the image:
`pyodbc.drivers()` returns `['FreeTDS']`, and `mssql+pyodbc://…?driver=FreeTDS` resolves to `MSDialect_pyodbc`.

And if a source refuses every driver, there is always the CEM's own road below: land a query in the kit's
database and chart that.

### The CEM

Two ways, and you can use both at once.

**Live, in SQL Lab — `yacht://cem`.** Paramètres → Bases de données → + → *Autres*, paste `yacht://cem` as the
SQLAlchemy URI, tick *Exposer dans SQL Lab*, connect. The dialect in [yacht_dialect.py](yacht_dialect.py) takes
the host, account and driver from `cem.env` at connect time, so **no credential is stored in Superset's own
database**. Then `select … from dc_run.<table>` reads the cluster's rows and stores nothing anywhere. Its
schema panel speaks Spark SQL (`show databases`, `show tables in <schema>`, `describe`), and `CEM_SCHEMA` in
`cem.env` says which schema to open on — the guess from the catalog name is wrong whenever the two differ
(`datacube_dc_run-SPARKSQL` holds `dc_run`).

What it costs: a JVM starts inside each Superset worker on first use (~200 MB each), and every query opens its
own JDBC connection — safer than handing a pooled JDBC connection to the next person's query, but it pays the
cluster's authentication each time. `CEM_POOL=queue` in `cem.env` turns pooling on.

**Landed, for anything heavy — `cem_to_mart.py`.** A live dashboard firing twenty chart queries at the cluster
is not what a cluster is for. For anything you will look at more than once, pull an aggregate into the kit's own
PostgreSQL and chart that: it is instant, it survives the cluster being busy, and it is what the platform does
in production.

The rest of this section is that second way.

It is reached through a **Yacht JDBC** driver — a Java driver with **no SQLAlchemy dialect of its own**, which
is why the live route needed one written for it. The platform has the same constraint and answers it the same way: a batch
process reads the cluster and writes a mart, and Superset only ever reads the mart. `cem_to_mart.py` is that, in
miniature.

**What you need from the platform side** — the same five facts the gateway uses, which live in
`infra/compose/platform/.runtime/spark.env` on the platform host (keys `JDBC_URL`, `DRIVER_CLASS`, `JDBC_USER`,
`JDBC_PASSWORD`, `KRB_PRINCIPAL`), plus the driver jar itself, which is mounted there from
`local-secrets/jdbc/` and is the customer's file, not ours.

**All in one.** A bundle built with `INCLUDE_CEM=1` carries the **Yacht driver** and `cem.env.example`, which
already holds the driver class and the URL shape — so the only editing left on the server is three lines. Build
it that way here:

```bash
INCLUDE_CEM=1 bash build-devkit-bundle.sh
```

and, on the server, before `bash install.sh`:

```bash
cp cem.env.example cem.env && chmod 600 cem.env && nano cem.env    # URL host/port, user, password
```

`install.sh` merges `cem.env` into `.env` on the first run, so nothing is edited twice. If you also put your
filled `cem.env` beside the build script, `INCLUDE_CEM=1` will carry **that** too and the server needs no
editing at all — but the tarball then holds an account and its password in clear, and must be handled like a
password file: not on a shared drive, not attached to a ticket. The build says so out loud when it does it, and
writes `contains_secrets=yes` in the manifest.

**1 · The jar.** Already inside, when the bundle was built with `INCLUDE_CEM=1`. Otherwise copy it into
`./driver-jars/` beside `install.sh`; it is mounted read-only at `/app/driver-jars` and never baked into an image.

**2 · The settings**, in `.env`:

```ini
CEM_JDBC_URL=jdbc:yacht://<host>:<port>/datacube_dc_run-SPARKSQL?timeout=3600
CEM_JDBC_DRIVER=com.huawei.ryoma.yacht.client.YachtDriver
CEM_JDBC_JARS=/app/driver-jars/<the jar>.jar
CEM_JDBC_USER=<the service account>
CEM_JDBC_PASSWORD=<its password>
```

The user and the password go in as **JDBC properties**, not inside the URL — several drivers ignore them there.
If the cluster is **Kerberised**, put `krb5.conf` and the keytab in `./krb5/` (mounted at `/run/krb5`) and add:

```ini
CEM_KERBEROS=1
CEM_KRB_PRINCIPAL=ossuser@HADOOP.COM
CEM_KRB_KEYTAB=/run/krb5/user.keytab
```

`cem_to_mart.py` then runs `kinit -kt` before the JVM opens the connection, exactly as the platform's runner
does, and keeps a ticket that is already in the cache.

**A password with `$` or `#` in it.** Write it as it is — `cem.env` is mounted into the container read-only
and read from disk, so nothing interpolates it. Do **not** put CEM settings in `.env`: compose expands `$VAR`
and `${VAR}` there *and* in an `env_file`, and a password loses those pieces on the way in. Measured
2026-09-28: `p$aS#sw0rd${NOPE}end` in the file reached the environment as `p#sw0rded`, and the cluster then
refused an account that was never wrong — an HTTP 403 that reads like a typo.

**3 · Prove it, reading nothing:**

```bash
docker compose -p sharpsight-superset-devkit exec superset-devkit \
  python /app/pythonpath/cem_to_mart.py --check
```

It says which of the two ends answered, and names precisely what is wrong when one does not: the setting that is
empty, the jar that is not where `CEM_JDBC_JARS` says, the keytab that is not mounted, the driver class that is
not in the jar — and, when the cluster itself refuses, **what the cluster said**:

```
failed: the cluster refused with HTTP 403 and said: The user name or password is incorrect, or the account is locked.
```

(That reason travels inside the refusal's JSON body, past the 300 characters an error is usually cut at, which
is why a 403 used to arrive unexplained.) No URL, user or password is ever printed, including in an error.

**4 · Land a query where Superset can see it:**

```bash
docker compose -p sharpsight-superset-devkit exec superset-devkit \
  python /app/pythonpath/cem_to_mart.py "select day, country, sum(attempts) a from … group by 1,2" \
  --table devmart.my_extract
```

Aggregate on the cluster, not here: the helper refuses an extract over `--limit` (500 000 rows by default)
rather than pulling a table across the network. The target is replaced **whole, in one transaction**, so a chart
never reads a half-written extract.

**5 · Chart it** through the **Dev kit store** connection
(`postgresql+psycopg2://devkit:<DEVKIT_DB_PASSWORD>@devkit-db:5432/devkit`), schema `devmart`.

If the cluster also exposes a **Hive/Spark Thrift** port, skip all of this: connect Superset straight to
`hive://host:10000/default` and query it live.

---

## 4 · Bring the platform's dashboards in, with their data

The Roaming Hub and Bassin de vie run on the platform's mart. To work on them here — or to show them without
the platform — carry both across with the two scripts the kit ships:

**On the platform server** (read-only: it dumps and exports, it changes nothing):
```bash
bash export-for-devkit.sh          # → ~/sharpsight-devkit-export-<date>.tgz (+ .sha256)
```
It takes the schema `roaming_mart` of the `roaming` database — aggregates only, no subscriber number, nothing
under ten SIM cards — and every dashboard as Superset itself exports it, charts and datasets included.

**On the machine running the kit**, from the kit's folder, with the kit up:
```bash
bash import-into-devkit.sh /path/to/sharpsight-devkit-export-<date>.tgz
```
It checks the archive against its checksum, restores the mart into a database called **`roaming`** beside the
kit's own, writes this kit's password into the connection, and imports the dashboards.

**Why the database must be called `roaming`**: Superset 6 records a *catalog* on every dataset, and for
PostgreSQL the catalog is the database. The exported datasets say `catalog: roaming`, so Superset dials a
database of that name whatever the connection's URI says — restoring into `devkit` gave a dashboard that drew
nothing while every connection on screen looked correct, and only the log said why
(`FATAL: database "roaming" does not exist`). Keeping the platform's name means nothing in the export has to be
rewritten but the password.

Verified end to end on 2026-10-01: 78 tables and views restored, 192 charts and 38 datasets imported, the
Roaming Hub opening in the kit on its nine tabs with its real numbers and no chart in error.

The archive is **not** part of the bundle and never will be: it carries the customer's data, and it is for you
to move. Treat it as you would the mart itself.

## 5 · Build a dashboard, and take it to the product

1. **SQL Lab** → write the query → **Save as dataset** (or *Explore* straight from the result).
2. Chart it, drop the charts on a dashboard, add filters and cross-filters.
3. **Export**: on the dashboard list, ⋮ → *Export*. The `.zip` lands in `./exports/` (mounted into the
   container) and contains the dashboard, its charts, datasets and database reference as YAML.
4. To make it a **product** dashboard, the SharpSight way is dashboards-as-code: see
   `docs/guides/new-superset-app.md` **in the SharpSight repository**, which turns a drawing like this into a
   build script whose output is reproducible, reviewable and installable by the platform's offline bundle.

The kit sets the flags a dashboard author needs — cross-filters, drill to detail, drill by, Jinja in SQL,
query-cost estimation, full CSV export, versioned export, tags — and a **60-second** data cache, so a chart shows
the query you just changed. Raise `DEVKIT_DATA_CACHE_TTL` when you switch from drawing to demonstrating.

---

## 6 · What is inside, and what it is not

| Container | |
|---|---|
| `superset-devkit` | Superset 6.1.0 — the **same pinned digest** as the product image, so a dashboard meets the version it will be installed on |
| `devkit-db` | PostgreSQL 16.4 — two databases: `superset` (dashboards, users) and `devkit` (your extracts, in schema `devmart`) |
| `devkit-redis` | cache only, 96 MB, nothing persisted |
| `devkit-init` | one-shot: schema, admin account, `superset init` |

**It is not the product.** `services/superset` **in the SharpSight repository** builds what customers run: no login screen
(the SharpSight gateway authenticates and re-checks RBAC on every request), one read-only connection, SQL Lab
closed to readers, dashboards imported from files and not editable. This kit is the opposite of each of those,
on purpose, which is also why it must never be what a customer is given.

**It cannot touch a running platform.** Its own compose project (`sharpsight-superset-devkit`), its own network,
its own volumes, its own image name and its own port. It holds no credential of the platform's and no route to
it. Verified on 2026-09-27 with both running side by side: platform containers healthy, portal and Roaming Hub
origin answering 200, and nothing under `services/`, `infra/` or `database/` modified.

**Security, honestly stated.** This is a workbench: an admin can run any SQL the connected user can run, and
that is the point. So — give it **read-only** database users, keep `DEVKIT_BIND=127.0.0.1` unless you have put
TLS in front of it, and never point it at a production database you would not hand to a developer. Superset's
telemetry pixel is switched off (`SCARF_ANALYTICS = False`), CSRF and HTML sanitisation are on, and no credential
is baked into the image or the bundle.

---

## 7 · Files

| | |
|---|---|
| [Dockerfile](Dockerfile) | the image: Superset + the drivers + Java for the JDBC bridge + the brand |
| [superset_config.py](superset_config.py) | login, caches, SQL Lab limits, feature flags, the SharpSight theme |
| [devkit_drivers.py](devkit_drivers.py) | Oracle in thin mode on SQLAlchemy 1.4 |
| [cem_to_mart.py](cem_to_mart.py) | a CEM query landed in the kit's own database |
| [docker-compose.yaml](docker-compose.yaml) | the four containers, isolated from everything else |
| [install.sh](install.sh) | first run, start, stop, erase |
| [build-devkit-bundle.sh](build-devkit-bundle.sh) | one tar for a machine with no internet ([how](docs/building-a-bundle.md)) |
| [verify-bundle.sh](verify-bundle.sh) | checks a bundle before it leaves the machine; `INSTALL=1` rehearses the install |
| [.env.example](.env.example) | every setting, with the URI of every source |
| [cem.env.example](cem.env.example) | the CEM's settings, ready but for the host, the user and the password |
| [devkit_seed.py](devkit_seed.py) | the two connections and the starting queries, created on every start |
| [export-for-devkit.sh](export-for-devkit.sh) | run on the platform: its mart and its dashboards into one archive |
| [import-into-devkit.sh](import-into-devkit.sh) | run here: that archive into this kit, dashboards working |
| [yacht_dialect.py](yacht_dialect.py) | the dialect that lets SQL Lab query the CEM live |
| `templates/head_custom_extra.html` | the sign-in page's look, through Superset's own extension point |
| `brand/` | its own copy of the logos, so this folder builds alone |
| `driver-jars/` | where the customer's JDBC jar goes — never committed, never shipped |
| [ai-bi.sh](ai-bi.sh) · [docker-compose.ai-bi.yaml](docker-compose.ai-bi.yaml) | the AI BI engine: start, demo, approve, audit, discard, check-adapter, people, compatibility — an overlay, the kit runs without it ([what it is](docs/ai-bi.md)) |
| `services/ai-bi/` | the engine's service: API, tool layer, policy, validation, versions |
| `registry/` | the governed datasets, metrics, dashboard templates, design tokens, RBAC and policies the engine reads |
| `dbt/` | the dbt project behind the engine's datasets |
