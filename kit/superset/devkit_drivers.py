"""The two drivers Superset would otherwise not find: Oracle without Instant Client, SQL Server without ODBC.

MEASURED 2026-09-27, inside the built image: Superset 6.1 runs **SQLAlchemy 1.4.54**, and the `oracle+oracledb`
dialect only exists from SQLAlchemy 2.0 — `NoSuchModuleError: Can't load plugin: sqlalchemy.dialects:oracle.oracledb`.
SQLAlchemy 1.4 has one Oracle dialect, `oracle+cx_oracle`, and cx_Oracle is the old driver that needs Oracle's
Instant Client: a licensed ~100 MB download, native libraries, LD_LIBRARY_PATH — on every machine that runs this
kit, and impossible on an air-gapped one that cannot fetch it.

python-oracledb (the maintained successor) is pure Python in THIN mode and is API-compatible with cx_Oracle; its
own documentation names this alias as the way to use it under a framework that asks for cx_Oracle. So the module
is registered under that name, which makes `oracle+cx_oracle://` work with nothing installed but this wheel.

`oracle+oracledb://` is registered too, pointing at the same dialect, so a URI copied from newer documentation
does not fail with a plugin error.

SQL SERVER, AND WHY IT WAS NOT EVEN OFFERED. Superset lists an engine only if the **default** dialect of that
backend can import its DBAPI (`get_available_engine_specs`: `sqlalchemy.dialects.registry.load("mssql")`, then
`dialect.dbapi()`). SQLAlchemy's default for `mssql` is **pyodbc**, which needs unixODBC and Microsoft's ODBC
driver — neither is in this image — so the import failed, the backend was skipped, and « Microsoft SQL Server »
appeared nowhere in the connection dialog even though pymssql was installed and `mssql+pymssql://` worked
perfectly if you typed it by hand (measured 2026-09-27). So pymssql is registered as the default dialect for
that backend: the probe then succeeds, the engine is offered, and a URI written without a driver still means
the driver we ship.

Nothing here touches any other driver: PostgreSQL and Hive have working defaults in 1.4 already.
"""
from __future__ import annotations

import sys


def install() -> list[str]:
    """Make Oracle reachable in thin mode. Returns what was arranged, for the log."""
    done: list[str] = []
    try:
        import oracledb
    except ImportError:                       # a build made without the Oracle wheel: say nothing, break nothing
        return done

    if "cx_Oracle" not in sys.modules:
        # SQLAlchemy 1.4's dialect reads this attribute as cx_Oracle's version and REFUSES anything under 5.2:
        # « cx_Oracle version 5.2 and above are supported ». python-oracledb DOES define it — as its own
        # version, 2.5.1 — so it must be OVERWRITTEN with the 8.3 the dialect expects, which is what
        # python-oracledb's documentation prescribes for this alias. The first version of this line kept the
        # driver's own value when it had one (`getattr(oracledb, "version", "8.3.0")`), so every Oracle
        # connection failed with that message, whatever the connection string (met on 2026-10-05). The driver's
        # real version stays readable as oracledb.__version__.
        oracledb.version = "8.3.0"
        sys.modules["cx_Oracle"] = oracledb
        done.append("oracle+cx_oracle (python-oracledb, thin mode — no Instant Client)")

    try:
        from sqlalchemy.dialects import registry
        registry.register("oracle.oracledb", "sqlalchemy.dialects.oracle.cx_oracle", "OracleDialect_cx_oracle")
        done.append("oracle+oracledb (alias of the same dialect)")
    except Exception:                          # a SQLAlchemy that already has it: nothing to do
        pass

    # The CEM, live in SQL Lab: a dialect over its JDBC driver (yacht_dialect.py says what it can and cannot do).
    try:
        from sqlalchemy.dialects import registry
        registry.register("yacht", "yacht_dialect", "YachtDialect")
        registry.register("yacht.jaydebeapi", "yacht_dialect", "YachtDialect")
        done.append("yacht:// queries the CEM live, through its JDBC driver")
    except Exception:
        pass

    try:
        import pymssql  # noqa: F401
    except ImportError:
        return done
    try:
        from sqlalchemy.dialects import registry
        registry.register("mssql", "sqlalchemy.dialects.mssql.pymssql", "MSDialect_pymssql")
        done.append("mssql defaults to pymssql (no ODBC driver manager needed, and the engine is offered at all)")
    except Exception:
        pass
    return done
