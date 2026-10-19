# The SharpSight Superset dev kit — a handover

**For a session that will rebuild this kit and add to it.** It assumes nothing: what the repository is, what
every file does, how to run it, what has already been measured and must not be re-broken, what is unfinished,
and where the freedom is. Written 2026-10-19 against commit `c69bdd1`.

Everything here was built and verified on a running kit. Where a statement comes from a measurement, it says so
— those are the expensive ones.

---

## 1 · What this is

A **self-contained Apache Superset 6.1.0 workbench**, shipped as one offline tarball, that a person installs
with `bash install.sh` on any machine with Docker. It exists because the SharpSight platform's own Superset is
not a place to work: there, readers have no login (a gateway authenticates), SQL Lab is closed, dashboards are
imported from files and cannot be edited. This kit is the opposite of each of those, deliberately.

It has two layers.

| Layer | What it is | In a bundle? |
|---|---|---|
| **The kit** | Superset + the drivers, the branding, the seeded connections, the scheduled refresh, the installer, the bundle builder | **yes** — this is what ships |
| **The AI BI engine** (`services/ai-bi`, `docker-compose.ai-bi.yaml`) | an optional overlay: a governed pipeline that turns a written request into a reviewed dashboard, with registries, dbt, policies, audit and a Superset extension | **not yet** — `build-devkit-bundle.sh` does not carry its image |

The kit starts without the engine, and the engine never edits the kit's `docker-compose.yaml`.

**Origin.** It lived in the SharpSight monorepo at `tools/superset-devkit` until 2026-10-03, when it was lifted
out so it could be versioned and shipped on its own. **That monorepo copy is stale.** Git here is **local only
— there is no remote and none is wanted.**

---

## 2 · Run it from zero

```bash
bash install.sh                 # first run: generates .env, prints the admin password ONCE
# → http://127.0.0.1:3002   (and https://127.0.0.1:3443 through its own proxy)
bash install.sh --stop          # keeps everything
bash install.sh --erase         # deletes ONLY this kit's volumes
```

On first start it already holds: **Dev kit store** (its own PostgreSQL), **CEM live** (the cluster, if
`cem.env` names one), three saved queries, a French interface, and a sign-in page of its own.

The engine, when you want it:

```bash
bash ai-bi.sh demo              # a written request → a dashboard awaiting review, through 22 steps
bash ai-bi.sh where             # where to look in Superset for what the engine knows
bash ai-bi.sh test              # its test suite
```

Full instructions: [`README.md`](README.md) for the kit, [`docs/ai-bi.md`](docs/ai-bi.md) for the engine.

---

## 3 · The map

~130 tracked files, ~20 000 lines. Nothing is vestigial; this is what each piece is for.

### The kit itself (repository root)

| File | Lines | Its job |
|---|---|---|
| `Dockerfile` | 79 | the image: Superset on the **same pinned digest as the product**, plus psycopg2, pymssql, python-oracledb, PyHive, JayDeBeApi + JPype + Java 17, pyodbc + FreeTDS, the French catalogue compiled from the base image's `.po` files, this kit's modules, templates and brand |
| `docker-compose.yaml` | 324 | eight services (§5) |
| `install.sh` | 317 | first install, start, stop, erase, and **`--from <old folder>`** to upgrade in place carrying `.env`/`cem.env`; it remembers the checksum of the images archive it loaded, so an upgrade actually loads the new image |
| `superset_config.py` | 237 | login, caches, SQL Lab limits, feature flags, the SharpSight theme, the pool that follows the threads, the CSP, and the `FLASK_APP_MUTATOR` that installs the rest |
| `devkit_drivers.py` | 78 | makes Oracle and SQL Server *exist* at all (§6) |
| `devkit_forms.py` | 79 | gives both the « host, port, user, password » dialog |
| `yacht_dialect.py` | 297 | a SQLAlchemy dialect over the CEM's JDBC driver, so SQL Lab queries the cluster live |
| `cem_to_mart.py` | 294 | lands a CEM query in the kit's own database (the production pattern, in miniature) |
| `devkit_seed.py` | 149 | creates the two connections and three saved queries on every start, idempotently |
| `devkit_refresh.py` | 583 | the scheduled refresh: replays recorded dashboard requests so an open hits cache |
| `sharpsight_translations.py` | 115 | compiles the French `.mo`/`.json` the base image does not ship |
| `templates/head_custom_extra.html` | 93 | the sign-in page's look, through Superset's own extension point |
| `brand/` | 6 files | logos, the loader, the chrome CSS |
| `initdb/10-devmart.sql` | 10 | the `superset` database and the `devmart` schema, on the database's first start |
| `nginx/default.conf.template` | 49 | the HTTPS/HTTP-2 proxy in front of Superset |
| `.env.example` / `cem.env.example` | 141 / 37 | every setting, with what it costs; the CEM's, with everything that is not secret already filled |

### Shipping and proving

| File | Lines | Its job |
|---|---|---|
| `build-devkit-bundle.sh` | 109 | one tar: the image, PostgreSQL, Redis, the scripts, the docs, a manifest. `INCLUDE_CEM=1` adds the Yacht driver and (if non-empty) `cem.env` — and says out loud that the tarball then holds a password |
| `verify-bundle.sh` | 126 | 16 checks before a bundle leaves the machine; `INSTALL=1` rehearses the install and signs in |
| `rehearse-upgrade.sh` | 176 | rehearses an upgrade **on a copy of a real kit's database** — the only way an upgrade is allowed to be called safe |
| `rehearse_real_content.py`, `rehearse_upgrade_content.py`, `rehearse_dashboards.mjs` | 157/133/47 | what the rehearsal asserts: every connection answers, every chart returns **the same data** before and after |
| `export-for-devkit.sh` | 76 | **on the platform server**: dumps the mart and exports every dashboard as Superset itself exports it |
| `import-into-devkit.sh` | 94 | **here**: restores that archive, repoints the connection, imports the dashboards |

### The AI BI engine (`services/ai-bi/`, ~10 000 lines + 2 400 of tests)

| Area | Files | What it holds |
|---|---|---|
| entry | `main.py` (610), `cli.py` (262), `ai-bi.sh` (143) | the API and the command surface: `demo`, `request`, `validate`, `commit`, `approve`, `discard`, `person`, `people`, `where`, `check-adapter`, `compatibility`, `audit`, `test` |
| the run | `pipeline.py` (521), `planner.py` (177), `lifecycle.py` (702) | a request → a plan → a specification → Superset; ten quality gates; the `.ssbi` package |
| agents | `dashboard_agent.py` (569), `chart_agent.py` (331), `dataset_agent.py`, `semantic_agent.py`, `sql_agent.py`, `dbt_agent.py` | each step of the generation |
| Superset | `superset_adapter.py` (388), `superset_view.py` (616), `adapter_check.py` (147) | everything that talks to Superset's REST API, and the check that drives the roads a generation does not take |
| governance | `policy_engine.py` (348), `registry.py` (251), `audit.py` (149), `people.py` (127) | policies, the registries, the audit record, accounts and roles |
| validation | `validation_agent.py` (477), `visual_qa_agent.py` (527) | 220 checks, and a screenshot read back |
| the seam | `llm.py` (43) | **where a language model plugs in.** The planner is deterministic today and produces the same `Requirement` structure a model would |
| tests | `tests/` (12 files, 2 400 lines) | run with `bash ai-bi.sh test` |

`registry/` (19 files) is the governed vocabulary: datasets, metrics, six templates, policies, RBAC, design
tokens, the lexicon, the compatibility matrix, the approved visualizations. `dbt/` (16 files) is a real dbt
project — staging, intermediate, marts, seeds, macros, tests — with a branch for AI-generated models.
`superset-extension/` (14 files) is `sharpsight-bi.supx`: a panel in SQL Lab, its backend API, and a build whose
checksum is recorded and verified before Superset is allowed to load it. `superset-addon/` is the configuration
layer the overlay mounts — and, since 2026-10-19, the two pages SharpSight serves inside Superset: the
projects, and the **Studio** (`studio/`, ~900 lines of plain JavaScript and CSS, no build step), the visual
builder of the revised brief's sections 46 to 60.

### The documents

| | Lines | Read it when |
|---|---|---|
| [`README.md`](README.md) | 429 | you are using the kit |
| [`docs/architecture.md`](docs/architecture.md) | 91 | you need the containers, the databases, what happens on a start |
| [`docs/lessons.md`](docs/lessons.md) | **594** | **before changing anything** — every trap already paid for, each with its measurement |
| [`docs/building-a-bundle.md`](docs/building-a-bundle.md) | 155 | you are shipping |
| [`docs/connecting-sharpsight.md`](docs/connecting-sharpsight.md) | 119 | you need the platform's dashboards and mart |
| [`docs/cem.md`](docs/cem.md) | 77 | you are pointing it at the cluster |
| [`docs/ai-bi.md`](docs/ai-bi.md) | **747** | you are touching the engine; §9 is everything not built |
| [`docs/superset-studio.md`](docs/superset-studio.md) | 319 | the AI Visual Builder of the V3 brief, read against this kit — and, §11, what of it is built |
| [`CHANGELOG.md`](CHANGELOG.md) | 270 | what each bundle changed, 20260927 → 20261016 |
| `SharpSight_BI_Studio_Spec_Updated.md` | — | the owner's brief the engine implements (sections 18–32 are the factory) |

---

## 4 · What runs

```
  you ──3002/3443──►  superset-devkit (Superset, 2 workers × 20 threads)
                          │           │              │
                    devkit-db    devkit-redis   /run/cem/cem.env (volume, 0400)
                 PostgreSQL 16.4     cache
                 ├─ superset   metadata: dashboards, users
                 ├─ devkit     your extracts (schema devmart), uploads, CTAS
                 └─ roaming    the platform's mart, when imported (schema roaming_mart)
```

| Service | Role |
|---|---|
| `devkit-db` | three databases, above. **Four CPUs**: the "slow dataset" was this container capped at one |
| `devkit-redis` | cache, 1 GB — dashboards open on cached data |
| `devkit-init` | one-shot: `db upgrade`, the admin, `superset init`, then the seeding |
| `devkit-cem-sync` | one-shot, **every start**: copies `cem.env` into a volume as root, and says what it handed over |
| `superset-devkit` | the application |
| `devkit-refresh` | replays each dashboard's recorded requests on the schedule its Superset **tag** names |
| `devkit-cert`, `devkit-proxy` | a self-signed certificate and the HTTPS/HTTP-2 front |

Volumes: `devkit-db`, `devkit-home`, `devkit-cem`, `devkit-certs`. `install.sh --erase` deletes only these.

The overlay adds: `sharpsight-ai-bi` (the engine), `dbt-runner` (builds the governed models at each start),
`aibi-db-init`, `aibi-superset-init`, and volumes `aibi-state`, `aibi-dbt-prod`, `aibi-dbt-docs`.

---

## 5 · The non-negotiables

A rewrite is free to change almost everything **except** these. Each one cost a debugging session; all are in
[`docs/lessons.md`](docs/lessons.md) with the measurement.

1. **Superset 6.1 ships SQLAlchemy 1.4.** `oracle+oracledb` does not exist there. python-oracledb is registered
   *as* `cx_Oracle` (thin mode, no Instant Client).
2. **Superset offers an engine only if that backend's DEFAULT dialect imports its driver.** `mssql` defaults to
   pyodbc; without registering pymssql as the default, SQL Server appears **nowhere** in the UI while
   `mssql+pymssql://` works by hand.
3. **Spark quotes with backticks; `"X"` is a string literal.** Get this wrong and every cell of a chart returns
   its own column name — valid SQL, no error, wrong question. `LIMIT` must be a literal too.
4. **Superset 6 records a CATALOG per dataset, and for PostgreSQL the catalog IS the database.** The platform's
   datasets say `catalog: roaming`, so the mart must be restored into a database called `roaming`.
5. **`import-dashboards` keeps an existing connection's URI.** Always `set-database-uri` afterwards, or an
   upgraded kit silently points at the platform's `superset-db`.
6. **docker compose interpolates `.env` AND `env_file`.** A password with `$` or `#` arrives cut, and compose
   prints the fragments. CEM settings travel as a **mounted file**, read from disk.
7. **A bind-mounted `0600` file is unreadable inside the container** (uid 1000, host ownership). Hence the
   root-filled volume. Docker Desktop fakes permissions and hides this.
8. **The sign-in page is React.** Flask-AppBuilder's template is never rendered; `head_custom_extra.html` is the
   way in. The fields carry an `id` and no `name`; the label text is in a child `<span>`.
9. **gunicorn's own `--timeout` is 60 s** in this image and kills the worker serving a longer request; the
   metadata pool must follow the thread count.
10. **Every text file is LF** (`.gitattributes`). A CRLF `install.sh` does not execute on Linux at all.
11. **One JVM, started once.** In the CEM dialect, one thread starts the Java VM and the others wait.

---

## 6 · Build, verify, ship, upgrade

```bash
bash build-devkit-bundle.sh                        # → dist/sharpsight-superset-devkit-<date>.tar.gz
INCLUDE_CEM=1 VERSION=20261019b bash build-devkit-bundle.sh
bash verify-bundle.sh dist/<bundle>.tar.gz         # 16 checks
INSTALL=1 bash verify-bundle.sh dist/<bundle>.tar.gz   # … and install it in a scratch folder
bash rehearse-upgrade.sh <old bundle> <new bundle>     # on a COPY of a real kit's database
```

Rules that have each cost an hour: **never build twice under the same name** (bump `VERSION`); **carry `.env`
across on an upgrade** or the database refuses its own volume; **`cd` to the folder before `sha256sum -c`**;
**`loading images` must appear on an upgrade**, or the old image is still running; **keep the previous bundle**
until the new one is installed.

Latest shipped: **20261016**, verified 24/24, upgrade rehearsed from 20261006 on real content — 4/4 connections
and **246/246 charts returning the same data** before and after.

---

## 7 · How it meets SharpSight

Three independent roads, all in [`docs/connecting-sharpsight.md`](docs/connecting-sharpsight.md):

- **The platform's dashboards with their data** — `export-for-devkit.sh` there, `import-into-devkit.sh` here.
  Three things must match: the database **`roaming`**, the schema **`roaming_mart`**, the connection
  **`cem_datamart`**. The export archive **never travels in a bundle**: it holds customer data.
- **The CEM live** — a connection whose URI is `yacht://cem`; credentials are read from the mounted file at
  connect time, so Superset's own database never stores them.
- **The CEM landed** — `cem_to_mart.py`, for anything charted more than once.

---

## 8 · Where it stands, and what is owed

**Verified** (dates in `CHANGELOG.md`): the kit installs, upgrades and ships; both platform dashboards import
and render (115 and 77 charts); the CEM answers on both roads; the engine's demo runs end to end with 220
checks, a screenshot, a git commit and a review status.

**Not done** — the full list with reasons is [`docs/ai-bi.md` §9](docs/ai-bi.md); the ones that matter to a
rebuild:

- **No language model.** `aibi/llm.py` is the seam; the deterministic planner already exercises every guard one
  will meet.
- **The engine is not in the offline bundle.** `build-devkit-bundle.sh` does not carry its image.
- **The image tag never moves** (`sharpsight-superset-devkit:1.0.0`) though its contents change — `install.sh`
  works around it by remembering the images archive's checksum. A rebuild should give the image a real version.
- **Superset 6.1.0 takes an extension panel in SQL Lab only** — no dashboard, no Explore, no chat. Read from the
  image before anything was built on it.
- **Brand values live in three places** kept alike by hand (`superset_config.py`,
  `registry/design/tokens.yaml`, the sign-in template). One source would be better.
- **Three decisions are the owner's**, not ours: whether a hand-made layout in Superset should survive a commit;
  how a project in UAT/PROD gets a next version in a kit with one Superset; whether a developer may query in SQL
  Lab at all.

---

## 9 · How work is done here

- **Git is local. No remote, ever.** Commit messages say what changed and why, in prose.
- **Verify by running**, never by reading. Every claim in these documents came from a command whose output was
  read. A screenshot is looked at, not assumed.
- **Secrets never enter the repository**: `.env`, `cem.env`, the Yacht jar and any mart dump are ignored. A
  bundle built with `INCLUDE_CEM=1` and a filled `cem.env` carries a password, says so, and records
  `contains_secrets=yes` in its manifest.
- **Document the trap with its measurement.** `docs/lessons.md` is the reason this kit is not re-debugged every
  month; keep adding to it.
- **A comment says *why*, not *what*.** The code here explains the constraint it is obeying.

---

## 10 · If you are remaking it

**Keep**: the eleven constraints of §5 (they are facts about Superset 6.1, not design choices); the shape of
`install.sh` (one command, generated secrets, an upgrade that keeps data); `verify-bundle.sh` and
`rehearse-upgrade.sh` (nothing else makes an upgrade safe to promise); the registries and the governed pipeline
if the engine stays; and `docs/lessons.md`, which is the most valuable file in the repository.

**Fair to change**: the image tag and versioning; the branding's three sources; how the engine ships (its image
belongs in the bundle); the refresh's design; the choice of proxy; whether the engine is an overlay at all.

**Where a rewrite will get hurt**: anything that assumes Superset's API or UI is stable — the login page, the
extension points, the catalog on datasets, how an import treats an existing connection. Check each against the
running image before relying on it, exactly as the notes in §5 were checked. And the bundle: 580 MB of image
means an upgrade is never a quick thing to test, so keep the rehearsal.

**Start by reading, in this order**: `README.md` → `docs/architecture.md` → `docs/lessons.md` → (if the engine
matters) `docs/ai-bi.md`. Then run `bash install.sh` and look at what comes up.
