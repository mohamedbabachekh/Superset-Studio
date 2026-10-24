"""Land a CEM query in the dev kit's own database, so Superset can chart it.

WHY THIS EXISTS. The CEM cluster is reached through a Yacht JDBC driver — a Java driver with no SQLAlchemy
dialect, which means Superset cannot dial it the way it dials PostgreSQL or Oracle. The platform solves this the
same way in production: a batch process reads the cluster and writes a mart, and Superset only ever reads the
mart (roaming_pull.py). This is that pattern, small enough to run by hand while you build a dashboard.

    python cem_to_mart.py "select ... from ..." --table devmart.my_extract
    python cem_to_mart.py --file query.sql --table devmart.my_extract --limit 200000
    python cem_to_mart.py --check                 the driver, the JVM and both connections, nothing read

The table is replaced WHOLE inside one transaction: Superset sees the previous extract or the new one, never a
half-written one. Column types are whatever pandas inferred, which is what a dashboard draft needs.

CREDENTIALS COME FROM THE ENVIRONMENT AND ARE NEVER PRINTED — not in the log line, not in an error. Set them in
.env (see .env.example):

    CEM_JDBC_URL        jdbc:yacht://<host>:<port>/<catalog>            (kept out of every message)
    CEM_JDBC_DRIVER     the driver class, e.g. com.huawei.yacht.jdbc.Driver
    CEM_JDBC_JARS       /app/driver-jars/<the jar>.jar   (colon-separated for several)
    CEM_JDBC_USER       passed as a JDBC PROPERTY, not in the URL — most drivers ignore it there
    CEM_JDBC_PASSWORD   idem
    CEM_KERBEROS        1 when the cluster is Kerberised; then also:
    CEM_KRB_PRINCIPAL     e.g. ossuser@HADOOP.COM
    CEM_KRB_KEYTAB        e.g. /run/krb5/user.keytab   (mount it; /etc/krb5.conf too)
    CEM_JVM_ARGS        extra JVM flags, space-separated (e.g. -Dsun.security.krb5.debug=true)
    DEVKIT_MART_DSN     host=devkit-db port=5432 dbname=devkit user=devkit password=…

The same five facts the platform's own gateway uses, under their own names: JDBC_URL, DRIVER_CLASS, JDBC_USER,
JDBC_PASSWORD, KRB_PRINCIPAL (infra/compose/platform/.runtime/spark.env on the platform host).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

SAFE_TABLE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")


def _readable(exc: BaseException) -> str:
    """What the cluster actually said.

    The Yacht gateway refuses with an HTTP code and a JSON body whose « data » block holds the reason — and the
    first 300 characters of that body are the column metadata, so a plain truncation showed everything except
    why (met on 2026-09-28: a 403 whose reason was never visible). The body is parsed when it is there, and
    the code is kept either way. Nothing here prints the URL, the user or the password.
    """
    import json
    import re

    text = str(exc)
    code = re.search(r"HttpsCode:(\d+)", text)
    said = []
    for match in re.finditer(r"\{.*\}", text, re.S):
        try:
            body = json.loads(match.group(0))
        except ValueError:
            continue
        rows = body.get("data") or []
        for row in rows if isinstance(rows, list) else []:
            said.extend(str(cell) for cell in (row if isinstance(row, list) else [row]))
    if code and said:
        return f"the cluster refused with HTTP {code.group(1)} and said: " + " | ".join(said[:4])
    if code:
        return (f"the cluster refused with HTTP {code.group(1)}. It sent no readable reason; "
                f"the raw answer starts: {text[:200]}")
    return text[:400]


CEM_FILE = "/run/cem/cem.env"


def _from_file() -> dict[str, str]:
    """cem.env as it is on disk — the only copy no one has interpolated.

    Docker compose expands `$VAR` and `${VAR}` in .env AND in an env_file, so a password holding either
    reaches the container silently shortened (measured 2026-09-28). The file is mounted read-only and read
    here directly; surrounding quotes, which people add exactly because of characters like these, are removed.
    """
    values: dict[str, str] = {}
    try:
        with open(CEM_FILE, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if not key.replace("_", "").isalnum():
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key] = value
    except FileNotFoundError:
        pass
    except OSError as exc:
        # Never die here: this runs at import, so a file this process cannot read used to end the program with
        # a traceback before it could name the problem (PermissionError on a 0600 file owned by someone else,
        # met 2026-09-28). The settings then come from the environment, and the risk of that is stated.
        print(f"  note: {CEM_FILE} could not be read ({exc.__class__.__name__}); falling back to the "
              f"environment, where docker compose may have expanded a $ or a #", flush=True)
    return values


_FILE_VALUES = _from_file()


def _env(name: str) -> str:
    value = _FILE_VALUES.get(name, "").strip() or os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is not set — put it in cem.env (see cem.env.example)")
    return value


def _kinit() -> None:
    """A Kerberised cluster wants a ticket in the JVM's cache before the connection is opened.

    Same shape as the platform's runner: if a ticket is already there, keep it; otherwise kinit with the keytab.
    The principal is printed (it names a service account, not a secret); the keytab's contents never are.
    """
    if (_FILE_VALUES.get("CEM_KERBEROS") or os.environ.get("CEM_KERBEROS", "0")) != "1":
        return
    import subprocess

    try:
        if "Default principal" in subprocess.run(["klist"], capture_output=True, text=True).stdout:
            print("  a Kerberos ticket is already in the cache")
            return
    except FileNotFoundError:
        raise SystemExit("CEM_KERBEROS=1 but this image has no kinit — rebuild it with WITH_JDBC=1")
    principal, keytab = _env("CEM_KRB_PRINCIPAL"), _env("CEM_KRB_KEYTAB")
    if not os.path.exists(keytab):
        raise SystemExit(f"the keytab is not at {keytab} — mount it into the container (see .env.example)")
    done = subprocess.run(["kinit", "-kt", keytab, principal], capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"kinit failed for {principal}: {done.stderr.strip()[:200]}")
    print(f"  kinit ok for {principal}")


def _jdbc_connect():
    """Start one JVM with the driver on its class path, and open a connection. Secrets stay in variables."""
    import jaydebeapi
    import jpype

    url, driver, jars = _env("CEM_JDBC_URL"), _env("CEM_JDBC_DRIVER"), _env("CEM_JDBC_JARS")
    paths = [j for j in jars.split(":") if j]
    missing = [j for j in paths if not os.path.exists(j)]
    if missing:
        raise SystemExit(f"the JDBC driver is not where CEM_JDBC_JARS says: {', '.join(missing)}\n"
                         f"put the jar in tools/superset-devkit/driver-jars/ — it is mounted at /app/driver-jars")
    _kinit()
    if not jpype.isJVMStarted():
        flags = [f"-Djava.class.path={os.pathsep.join(paths)}", *(_FILE_VALUES.get("CEM_JVM_ARGS") or os.environ.get("CEM_JVM_ARGS", "")).split()]
        jpype.startJVM(jpype.getDefaultJVMPath(), *flags)
    # user and password go in as PROPERTIES: a JDBC driver is free to ignore them in the URL, and several do.
    props = {k: v for k, v in (("user", _FILE_VALUES.get("CEM_JDBC_USER") or os.environ.get("CEM_JDBC_USER", "")),
                               ("password", _FILE_VALUES.get("CEM_JDBC_PASSWORD") or os.environ.get("CEM_JDBC_PASSWORD", ""))) if v}
    return jaydebeapi.connect(driver, url, props, paths)


def _native(value):
    """A Python value for a cell the JDBC driver handed back as a Java object.

    JayDeBeApi converts the common types, but not every driver's: a `show databases` through Yacht returns
    `java.lang.String` objects, and psycopg2 then refuses the whole insert with « can't adapt type
    'java.lang.String' » — after the query has already run (met 2026-09-28). The Java class decides the
    conversion, because guessing from the text would turn a string column of digits into numbers.
    """
    import datetime
    import decimal

    if value is None:
        return None
    # The Java classes are asked about FIRST. A boxed java.lang.Integer answers True to isinstance(x, int) and a
    # java.lang.Boolean to isinstance(x, bool) — JPype registers them — so an "already native?" shortcut placed
    # ahead of this let them through untouched, and psycopg2 then met a Java object anyway (test, 2026-09-28).
    name = f"{type(value).__module__}.{type(value).__name__}"
    if name.startswith(("java.", "jpype", "_jpype")):
        if "Boolean" in name:
            return bool(value)
        if any(k in name for k in ("Integer", "Long", "Short", "Byte", "BigInteger")):
            return int(value)
        if any(k in name for k in ("Double", "Float", "BigDecimal")):
            return float(value)
        return str(value)                  # String, Char, Date, Timestamp, and anything unexpected
    if isinstance(value, (str, int, float, bool, bytes)):
        return value
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time, decimal.Decimal)):
        return value
    return str(value)


def _write(frame, table: str) -> int:
    """Replace `table` with this frame, in one transaction."""
    import pandas as pd  # noqa: F401  (imported for the caller's type, and to fail early if absent)
    import psycopg2
    from psycopg2 import sql as psql

    if not SAFE_TABLE.match(table):
        raise SystemExit(f"--table must be `name` or `schema.name`, lower case: {table!r}")
    schema, _, name = table.rpartition(".")
    schema = schema or "devmart"
    columns = [re.sub(r"[^a-z0-9_]", "_", str(c).strip().lower()) or f"col{i}"
               for i, c in enumerate(frame.columns)]
    types = []
    for c in frame.columns:
        kind = str(frame[c].dtype)
        types.append("double precision" if kind.startswith("float")
                     else "bigint" if kind.startswith(("int", "uint"))
                     else "timestamp" if kind.startswith("datetime")
                     else "boolean" if kind == "bool" else "text")
    conn = psycopg2.connect(_env("DEVKIT_MART_DSN"))
    try:
        with conn, conn.cursor() as cur:
            cur.execute(psql.SQL("create schema if not exists {}").format(psql.Identifier(schema)))
            cur.execute(psql.SQL("drop table if exists {}.{}").format(psql.Identifier(schema), psql.Identifier(name)))
            cur.execute(psql.SQL("create table {}.{} ({})").format(
                psql.Identifier(schema), psql.Identifier(name),
                psql.SQL(", ").join(psql.SQL("{} {}").format(psql.Identifier(c), psql.SQL(t))
                                    for c, t in zip(columns, types))))
            if len(frame):
                from psycopg2.extras import execute_values
                rows = [tuple(None if v != v else v for v in row) for row in frame.itertuples(index=False, name=None)]
                execute_values(cur, psql.SQL("insert into {}.{} ({}) values %s").format(
                    psql.Identifier(schema), psql.Identifier(name),
                    psql.SQL(", ").join(map(psql.Identifier, columns))).as_string(cur), rows, page_size=1000)
        return len(frame)
    finally:
        conn.close()


def check() -> int:
    """Everything but the reading: the jar is there, the JVM starts, both ends answer."""
    import psycopg2
    conn = _jdbc_connect()
    cur = conn.cursor()
    cur.execute("select 1")
    cur.fetchall()
    conn.close()
    print("  the CEM answers over JDBC")
    pg = psycopg2.connect(_env("DEVKIT_MART_DSN"))
    with pg, pg.cursor() as c:
        c.execute("select current_user, current_database()")
        who, db = c.fetchone()
    pg.close()
    print(f"  the dev kit's database answers as {who} on {db}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", help="the SQL to run on the cluster")
    ap.add_argument("--file", help="read the SQL from a file instead")
    ap.add_argument("--table", help="where to land it, e.g. devmart.my_extract")
    ap.add_argument("--limit", type=int, default=500_000, help="rows the extract may not exceed (default 500 000)")
    ap.add_argument("--check", action="store_true", help="prove the connections and read nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    sql = args.query or (open(args.file, encoding="utf-8").read() if args.file else "")
    if not sql.strip() or not args.table:
        ap.error("give a query (or --file) and --table")

    import pandas as pd

    t0 = time.time()
    conn = _jdbc_connect()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        names = [d[0] for d in cur.description]
        rows = cur.fetchmany(args.limit + 1)
    finally:
        conn.close()
    if len(rows) > args.limit:
        raise SystemExit(f"the query returned more than --limit ({args.limit}) rows; aggregate it on the cluster")
    frame = pd.DataFrame([[_native(cell) for cell in row] for row in rows], columns=names)
    written = _write(frame, args.table)
    print(f"  {written} row(s) → {args.table} in {time.time() - t0:.1f}s — add the connection "
          f"postgresql+psycopg2://…@devkit-db:5432/devkit in Superset and chart it")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:                      # never let a driver print a URL that carries a password
        sys.exit(f"failed: {type(exc).__name__}: {_readable(exc)}")
