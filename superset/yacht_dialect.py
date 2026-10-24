"""A SQLAlchemy dialect over the CEM's JDBC driver, so SQL Lab queries the cluster LIVE.

WHY THIS EXISTS. Superset speaks SQLAlchemy; the CEM speaks JDBC through Huawei's Yacht driver, and no
SQLAlchemy dialect for it exists. Until now the only route was to land a query's result in the kit's own
PostgreSQL and chart that (cem_to_mart.py) — correct, and how the platform runs in production, but it means
nothing of `dc_run` is visible until someone has copied it. The owner asked for the other thing: to open SQL
Lab, pick a schema, and read the cluster's own rows without storing them anywhere.

JayDeBeApi is a DB-API 2.0 module over JDBC, so the missing piece is only the dialect — the part that tells
SQLAlchemy how to open a connection, and tells Superset what schemas, tables and columns exist. That is what
this file is. Nothing is written: every statement it issues is a read.

    yacht://cem                       everything from cem.env (the file the kit already mounts)
    yacht://?url=jdbc:...&class=...&jars=/path.jar&user=u&password=p      an explicit JDBC source

The first form is the one to type into Superset: **no credential is stored in Superset's metadata database**,
because the dialect reads them from the mounted file at connect time.

WHAT TO KNOW BEFORE RELYING ON IT
  · A JVM starts inside each Superset worker on first use (~200 MB). Two workers, two JVMs.
  · Connections are not pooled by default: every query opens and closes one, which costs the cluster's
    authentication each time but cannot leave a JDBC connection wedged in a worker that Superset then reuses
    for another user. CEM_POOL=queue in cem.env turns pooling on.
  · Introspection speaks Spark SQL: `show databases`, `show tables in <schema>`, `describe <schema>.<table>`.
  · There is no transaction: commit and rollback are no-ops, because the driver has nothing to do with them
    and SQLAlchemy calls rollback on every connection it returns to the pool.
"""
from __future__ import annotations

import os
import re
import threading

from sqlalchemy import pool
from sqlalchemy import types as sqltypes
from sqlalchemy.engine import default, reflection
from sqlalchemy.sql import compiler

CEM_FILE = "/run/cem/cem.env"
# Held only while a worker's FIRST connection starts the Java VM (YachtDialect.connect says why).
_JVM_START = threading.Lock()

# Spark's type names, as `describe` prints them, to what SQLAlchemy shows in Superset.
_TYPES = [
    (re.compile(r"^(string|varchar|char|binary)"), sqltypes.String),
    (re.compile(r"^(tinyint|smallint|int|integer)"), sqltypes.Integer),
    (re.compile(r"^bigint"), sqltypes.BigInteger),
    (re.compile(r"^(double|float|real)"), sqltypes.Float),
    (re.compile(r"^(decimal|numeric)"), sqltypes.Numeric),
    (re.compile(r"^boolean"), sqltypes.Boolean),
    (re.compile(r"^timestamp"), sqltypes.DateTime),
    (re.compile(r"^date"), sqltypes.Date),
]


def _settings() -> dict[str, str]:
    """cem.env as it is on disk — never through the environment, which compose interpolates."""
    values: dict[str, str] = {}
    try:
        with open(CEM_FILE, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if key.replace("_", "").isalnum():
                    values[key] = value
    except OSError:
        pass
    return values


class YachtIdentifierPreparer(compiler.IdentifierPreparer):
    """Spark quotes an identifier with BACKTICKS, and a double quote means a string.

    SQLAlchemy's default preparer writes `SELECT "ACTDAY", "Segment" FROM …`. Spark SQL reads those as string
    literals, so the cluster returns the column NAMES as the value of every cell, 602 identical rows — which is
    exactly what the owner saw (2026-09-30). Nothing errors: the query is valid, it just answers a different
    question. Hive and Spark dialects elsewhere do the same as this.
    """

    reserved_words = compiler.RESERVED_WORDS.copy()
    reserved_words.update(["select", "from", "where", "group", "order", "by", "table", "partition"])

    def __init__(self, dialect):
        super().__init__(dialect, initial_quote="`", final_quote="`", escape_quote="`")


class YachtCompiler(compiler.SQLCompiler):
    """LIMIT as a number, not as a parameter.

    SQLAlchemy renders `LIMIT ?` and binds the value; Spark's JDBC does not accept a parameter there, and the
    failure would come back as a parse error on a query nobody wrote by hand. The count is an integer we hold,
    so it is written into the statement. OFFSET is not emitted: Spark only gained it recently and the driver
    in front of it is older than that.
    """

    def limit_clause(self, select, **kw):
        text = ""
        if select._limit_clause is not None:
            text += " LIMIT " + str(int(select._limit))
        if select._offset_clause is not None and select._offset:
            raise NotImplementedError("the CEM's SQL has no OFFSET; page with a WHERE on a key instead")
        return text


class YachtDialect(default.DefaultDialect):
    name = "yacht"
    driver = "jaydebeapi"
    preparer = YachtIdentifierPreparer
    statement_compiler = YachtCompiler
    supports_statement_cache = False
    supports_native_boolean = True
    supports_sane_rowcount = False
    supports_sane_multi_rowcount = False
    description_encoding = None
    max_identifier_length = 255
    # Every query opens its own connection. A JDBC connection carried between Superset's threads and handed to
    # the next user's query is a worse problem than the cost of authenticating again; CEM_POOL=queue overrides.
    poolclass = pool.NullPool

    @classmethod
    def dbapi(cls):                                   # SQLAlchemy 1.4 asks for the DB-API module here
        import jaydebeapi

        return jaydebeapi

    @classmethod
    def import_dbapi(cls):                            # SQLAlchemy 2.0 asks for it under this name
        return cls.dbapi()

    def create_connect_args(self, url):
        conf = _settings()
        query = dict(url.query)
        jdbc_url = query.get("url") or conf.get("CEM_JDBC_URL", "")
        driver = query.get("class") or conf.get("CEM_JDBC_DRIVER", "")
        jars = query.get("jars") or conf.get("CEM_JDBC_JARS", "")
        user = query.get("user") or url.username or conf.get("CEM_JDBC_USER", "")
        password = query.get("password") or url.password or conf.get("CEM_JDBC_PASSWORD", "")
        if not jdbc_url or not driver:
            # Say what was FOUND, not only what is missing: the same sentence used to appear whether the file
            # was absent, unreadable, empty, or simply not re-copied after an edit — four different fixes
            # behind one message (owner, 2026-09-28).
            try:
                size = os.path.getsize(CEM_FILE)
                seen = (f"{CEM_FILE} holds {size} byte(s) and {len(conf)} setting(s)"
                        f"{': ' + ', '.join(sorted(conf)) if conf else ''}")
            except FileNotFoundError:
                seen = f"{CEM_FILE} does not exist in this container"
            except OSError as exc:
                seen = f"{CEM_FILE} cannot be read here ({exc.__class__.__name__})"
            raise RuntimeError(
                f"the CEM's JDBC url and driver class are not set — {seen}. Fill cem.env beside install.sh "
                f"(CEM_JDBC_URL, CEM_JDBC_DRIVER, CEM_JDBC_JARS) and restart the kit, which hands it over; "
                f"or put them in the URI as `yacht://?url=...&class=...&jars=...`")
        paths = [j for j in jars.split(":") if j]
        missing = [j for j in paths if not os.path.exists(j)]
        if missing:
            raise RuntimeError(f"the JDBC driver is not where it is named: {', '.join(missing)}")
        props = {k: v for k, v in (("user", user), ("password", password)) if v}
        return (driver, jdbc_url), {"driver_args": props, "jars": paths}

    # ── ONE thread starts the JVM; the others wait for it ─────────────────────────────────────────────────────
    def connect(self, *cargs, **cparams):
        """Open a connection, letting exactly one thread per worker start the Java VM.

        JayDeBeApi starts the JVM on the first connection: `if not jpype.isJVMStarted(): jpype.startJVM(...)`,
        with no lock. A Superset worker serves twenty threads, and a dashboard sends its charts together — so
        on the first dashboard opened after a start, several threads found « not started » at once and each
        called startJVM. The JVM loads JPype's native library once per classloader, and the second caller got
        « UnsatisfiedLinkError: Native Library …_jpype…so already loaded in another classloader ». Worse, the
        worker never recovered: the half-started JVM still answers « not started », so EVERY later connection
        tried again and failed the same way until Superset was restarted (production, 2026-10-06: dashboard
        qoe-reseau-5g, every traceback inside jpype.startJVM at 09:18:31, and still at 09:19:11).

        So the first connection of a worker is made under a lock. It is JayDeBeApi's own start, with the
        arguments it has always used; the threads behind it wait a few seconds, once, and then find the JVM
        started and connect without touching the lock again.
        """
        import jpype

        if not jpype.isJVMStarted():
            with _JVM_START:
                if not jpype.isJVMStarted():
                    return self.dbapi.connect(*cargs, **cparams)
        return self.dbapi.connect(*cargs, **cparams)

    # ── there is no transaction on the cluster ────────────────────────────────────────────────────────────────
    def do_rollback(self, dbapi_connection):
        pass                                          # SQLAlchemy rolls back every returned connection

    def do_commit(self, dbapi_connection):
        pass

    def do_begin(self, dbapi_connection):
        pass

    # ── what Superset shows in its panels ─────────────────────────────────────────────────────────────────────
    def _rows(self, connection, statement):
        cursor = connection.connection.cursor()
        try:
            cursor.execute(statement)
            return cursor.fetchall()
        finally:
            try:
                cursor.close()
            except Exception:                          # a driver that dislikes a second close must not end a listing
                pass

    @reflection.cache
    def get_schema_names(self, connection, **kw):
        return [str(row[0]) for row in self._rows(connection, "show databases")]

    def _default_schema(self, url_database: str | None = None) -> str:
        conf = _settings()
        named = conf.get("CEM_SCHEMA", "").strip()
        if named:
            return named
        # the catalog in the JDBC url often names it: jdbc:yacht://host:port/datacube_dc_run-SPARKSQL
        catalog = conf.get("CEM_JDBC_URL", "").rstrip("/").split("/")[-1].split("?")[0]
        match = re.search(r"([A-Za-z0-9_]+?)-?SPARKSQL$", catalog)
        return (match.group(1) if match else catalog) or "default"

    def initialize(self, connection):
        # SQLAlchemy ASSIGNS default_schema_name during initialize, so this cannot be a property — it raised
        # « can't set attribute » on the first connection (2026-09-28). It is set here instead.
        super().initialize(connection)
        self.default_schema_name = self._default_schema()

    def _schema(self, schema):
        return schema or self._default_schema()

    @reflection.cache
    def get_table_names(self, connection, schema=None, **kw):
        rows = self._rows(connection, f"show tables in {self._schema(schema)}")
        # Spark returns database, tableName, isTemporary
        return [str(row[1] if len(row) > 1 else row[0]) for row in rows]

    @reflection.cache
    def get_view_names(self, connection, schema=None, **kw):
        return []

    @reflection.cache
    def get_columns(self, connection, table_name, schema=None, **kw):
        rows = self._rows(connection, f"describe {self._schema(schema)}.{table_name}")
        columns = []
        for row in rows:
            name = str(row[0]).strip()
            # `describe` ends with a partition block whose first cell is blank or a header
            if not name or name.startswith("#"):
                break
            kind = str(row[1]).strip().lower() if len(row) > 1 else "string"
            sql_type = sqltypes.String
            for pattern, mapped in _TYPES:
                if pattern.match(kind):
                    sql_type = mapped
                    break
            columns.append({"name": name, "type": sql_type(), "nullable": True, "default": None,
                            "comment": (str(row[2]).strip() if len(row) > 2 and row[2] else None)})
        return columns

    @reflection.cache
    def get_pk_constraint(self, connection, table_name, schema=None, **kw):
        return {"constrained_columns": [], "name": None}

    @reflection.cache
    def get_foreign_keys(self, connection, table_name, schema=None, **kw):
        return []

    @reflection.cache
    def get_indexes(self, connection, table_name, schema=None, **kw):
        return []

    def has_table(self, connection, table_name, schema=None, **kw):
        try:
            self._rows(connection, f"describe {self._schema(schema)}.{table_name}")
            return True
        except Exception:
            return False

    def _get_server_version_info(self, connection):
        return None

    def do_ping(self, dbapi_connection):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("select 1")
            cursor.fetchall()
            return True
        finally:
            try:
                cursor.close()
            except Exception:
                pass
