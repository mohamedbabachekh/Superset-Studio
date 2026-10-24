"""SQL Server and Oracle asked for the way SQL Developer asks: host, port, user, password — not a URI.

Superset draws that form only for an engine whose spec carries `BasicParametersMixin`. PostgreSQL has it;
**MssqlEngineSpec and OracleEngineSpec do not** (read in the image, 6.1.0), which is why those two offer nothing
but the « SQLAlchemy URI » box — a string a DBA has to assemble by hand, with the password in the middle of it.

So the two specs are given the mixin here. Nothing is forked: the classes Superset already loaded gain a base,
so whatever holds a reference to them — the registry, `issubclass` checks, the API that lists what you may
connect to — sees the same objects with the form attached.

ORACLE, AND THE TWO WAYS ITS DBAs NAME A DATABASE. SQLAlchemy's Oracle URL carries the SID in the path and the
service name in the query string, and they are mutually exclusive (read in sqlalchemy/dialects/oracle/cx_oracle.py):

    SID           oracle+cx_oracle://user:password@host:1521/ORCL
    service name  oracle+cx_oracle://user:password@host:1521/?service_name=ORCLPDB1

Both are reachable from the form: put the SID in « Database name », or leave it empty and add
`service_name` = <name> under « Additional parameters ». `database` is therefore NOT required here, which is the
one thing the stock schema gets wrong for Oracle.

This file changes nothing for the SharpSight product: it lives in the dev kit's image only.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def install() -> list[str]:
    """Give both specs the parameters form. Returns what was arranged, for the log."""
    from marshmallow import fields
    from superset.db_engine_specs.base import BasicParametersMixin, BasicParametersSchema
    from superset.db_engine_specs.mssql import MssqlEngineSpec
    from superset.db_engine_specs.oracle import OracleEngineSpec

    class OracleParametersSchema(BasicParametersSchema):
        # The only difference from the stock schema, and the reason Oracle needs its own: a database named by
        # its SERVICE has nothing to put here, and `required=True` would refuse the form before it was sent.
        database = fields.String(
            required=False, allow_none=True,
            metadata={"description": "SID — for a service, leave this empty and add "
                                     "service_name under Additional parameters"})

    @classmethod
    def build_oracle_uri(cls, parameters, encrypted_extra=None):
        """Same builder, but a connection named by its SERVICE has no SID to put in the path.

        The stock one reads `parameters["database"]` directly and dies with KeyError on a form that left it
        empty — which is exactly the service-name case this schema allows (measured 2026-09-27).
        """
        filled = {**parameters}
        filled.setdefault("database", None)
        return BasicParametersMixin.build_sqlalchemy_uri.__func__(cls, filled, encrypted_extra)

    done: list[str] = []
    for spec, driver, schema, placeholder in (
        (MssqlEngineSpec, "pymssql", BasicParametersSchema(),
         "mssql+pymssql://user:password@host:1433/database"),
        (OracleEngineSpec, "cx_oracle", OracleParametersSchema(),
         "oracle+cx_oracle://user:password@host:1521/?service_name=ORCLPDB1"),
    ):
        if issubclass(spec, BasicParametersMixin):
            continue                                   # a later Superset ships it: leave theirs alone
        try:
            spec.__bases__ = (BasicParametersMixin, *spec.__bases__)
        except TypeError:                              # layout refused: copy what the form needs instead
            for name, value in BasicParametersMixin.__dict__.items():
                if not name.startswith("__") and name not in spec.__dict__:
                    setattr(spec, name, value)
        spec.default_driver = driver
        spec.parameters_schema = schema
        spec.sqlalchemy_uri_placeholder = placeholder
        if spec is OracleEngineSpec:
            spec.build_sqlalchemy_uri = build_oracle_uri
        done.append(f"{spec.engine} (+{driver}) can be added by host, port, user and password")
    for line in done:
        logger.info("devkit: %s", line)
    return done
