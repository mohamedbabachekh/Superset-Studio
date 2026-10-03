# Changelog

Versions are the date a bundle was built (`sharpsight-superset-devkit-<version>.tar.gz`). Everything below was
built and verified on a running kit before it was written down.

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
