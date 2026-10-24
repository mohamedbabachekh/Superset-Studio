# The CEM

The cluster is reached through Huawei's **Yacht JDBC** driver. Everything below is what the platform's own
gateway uses, so the kit and the platform speak to it the same way.

## The five facts

```ini
CEM_JDBC_DRIVER=com.huawei.ryoma.yacht.client.YachtDriver
CEM_JDBC_JARS=/app/driver-jars/yacht-jdbc-25.70.278.jar
CEM_JDBC_URL=jdbc:yacht://<host>:<port>/datacube_dc_run-SPARKSQL?timeout=3600
CEM_JDBC_USER=<service account>
CEM_JDBC_PASSWORD=<its password>
CEM_SCHEMA=dc_run
```

- **The driver class is not what a search engine suggests.** It is the class inside the jar, and a wrong one
  fails late — at the first connection, as `Class … is not found`.
- **The jar is the customer's file.** It is never committed and never shipped unless you build with
  `INCLUDE_CEM=1`. On a machine that already runs the platform's gateway it can be taken out of the
  `spark-jdbc-connector` image (`find / -name "*yacht*.jar"` inside it).
- **User and password are JDBC properties**, not parts of the URL: several drivers ignore them there.
- **`CEM_SCHEMA` is not the catalog.** The URL's catalog `datacube_dc_run-SPARKSQL` holds the schema `dc_run`.
- On the platform these same five live in `infra/compose/platform/.runtime/spark.env` under the names
  `JDBC_URL`, `DRIVER_CLASS`, `JDBC_USER`, `JDBC_PASSWORD`, `KRB_PRINCIPAL`.

**Kerberos**, if the cluster asks for a ticket: `CEM_KERBEROS=1`, `CEM_KRB_PRINCIPAL`, `CEM_KRB_KEYTAB`, and
`krb5.conf` + the keytab in `./kit/krb5/`. `cem_to_mart.py` then runs `kinit -kt` before the JVM opens the
connection, and reuses a ticket already in the cache.

## Prove it before blaming anything else

```bash
docker compose -p sharpsight-superset-devkit exec superset-devkit \
  python /app/pythonpath/cem_to_mart.py --check
```

It reads nothing. Two good lines mean the chain is whole: *the CEM answers over JDBC* and *the dev kit's
database answers*. A failure names itself — the setting that is empty, the jar that is not where the setting
says, the keytab that is not mounted, the driver class that is not in the jar, or the cluster's own refusal with
its reason.

## Two ways to use it

**Live** — add a connection with the URI `yacht://cem`. SQL Lab then queries the cluster and stores nothing.
Good for exploring; it pays the cluster's authentication on every query, and a JVM lives in each worker.

**Landed** — `cem_to_mart.py "<sql>" --table devmart.<name>` copies an aggregate into the kit's own PostgreSQL,
replacing the table whole in one transaction. Good for anything charted more than once, and the pattern the
platform runs in production.

Rules of thumb while exploring:

```bash
# what tables exist
cem_to_mart.py "show tables in dc_run" --table devmart.tables_dc_run
# what one of them looks like (always with a limit)
cem_to_mart.py "select * from dc_run.<table> limit 200" --table devmart.peek_<table>
# its columns, without rows
cem_to_mart.py "describe dc_run.<table>" --table devmart.cols_<table>
```

Aggregate **on the cluster** for anything you will chart: the helper refuses an extract over `--limit`
(500 000 rows by default) rather than dragging a table across the network.

## When it refuses

| What you see | What it means |
|---|---|
| `Name or service not known` | the JVM started and the driver loaded — only the hostname is wrong |
| `HTTP 403 … user name or password is incorrect, or the account is locked` | the cluster heard you and refused; check the account, not the plumbing |
| `Class … is not found` | wrong `CEM_JDBC_DRIVER`, or a jar that is not the Yacht driver |
| `the JDBC driver is not where CEM_JDBC_JARS says` | the jar is not in `./kit/driver-jars/` |
| `/run/cem/cem.env does not exist in this container` | `cem.env` was never handed over — restart the kit |

A password holding `$`, `#`, `/` or `!` is safe in `cem.env` (it is read as a file, byte for byte) and in
Superset's forms (it encodes them). It is **not** safe in a URI typed by hand, nor in `.env`.
