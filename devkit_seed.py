"""The kit arrives connected: the two sources and a few starting queries, created on first start.

Without this, a newcomer's first half hour is spent reading a README to copy a URI, find a password and tick
« Exposer dans SQL Lab » — three screens before a single row is seen. Everything here is known to the kit
already, so it does it itself:

  · « Dev kit store »  — this instance's own PostgreSQL, where cem_to_mart.py lands extracts and where
                         « CREATE TABLE AS » in SQL Lab writes; DML and CTAS allowed, it is a workbench.
  · « CEM live »       — the cluster, through the Yacht JDBC driver, created ONLY when cem.env names one.
                         Its URI is `yacht://cem`, which carries no credential: the dialect reads them from
                         the mounted file at connect time, so Superset's own database never stores them.
  · three saved queries — the catalogue of dc_run, a peek, and the shape of an aggregate to copy.

It is idempotent: run at every start, it updates what it made and leaves anything renamed or edited alone.
It never deletes, and it never touches a connection it did not create (they carry a marker in their extra).
"""
from __future__ import annotations

import json
import os
import sys

MARKER = {"devkit_seeded": True}
CEM_FILE = "/run/cem/cem.env"


def _mart_password() -> str:
    """The kit's own PostgreSQL password, read from the DSN the container already has."""
    for part in os.environ.get("DEVKIT_MART_DSN", "").split():
        if part.startswith("password="):
            return part.split("=", 1)[1]
    return ""


def _cem_configured() -> bool:
    try:
        with open(CEM_FILE, encoding="utf-8") as handle:
            return any(line.strip().startswith("CEM_JDBC_URL=") and line.strip() != "CEM_JDBC_URL="
                       for line in handle)
    except OSError:
        return False


SAVED_QUERIES = [
    ("Le catalogue de dc_run",
     "-- Toutes les tables du cluster, interrogées en direct.\n"
     "show tables in dc_run"),
    ("Regarder une table (200 lignes)",
     "-- Remplacez le nom de la table. Rien n'est copié : la requête part sur le cluster.\n"
     "select * from dc_run.interco_na_kpi_by_operator_daily\nlimit 200"),
    ("Un agrégat, la forme à copier",
     "-- Agrégez SUR LE CLUSTER, et ne ramenez que le résultat : c'est ce qu'un graphique doit lire.\n"
     "select direction,\n       COUNTRY_NAME,\n       sum(cast(ATTEMPTCOUNT as bigint)) as attempts\n"
     "from dc_run.interco_na_kpi_by_operator_daily\ngroup by 1, 2\norder by attempts desc\nlimit 100"),
]


def seed() -> int:
    from superset import db, security_manager
    from superset.models.core import Database
    from superset.models.sql_lab import SavedQuery

    made: list[str] = []

    def upsert(name: str, uri: str, **flags) -> Database | None:
        existing = db.session.query(Database).filter_by(database_name=name).one_or_none()
        if existing is not None:
            extra = {}
            try:
                extra = json.loads(existing.extra or "{}")
            except ValueError:
                pass
            if not extra.get("devkit_seeded"):
                return existing            # someone made their own by this name: leave it entirely alone
            target = existing
        else:
            target = Database(database_name=name)
            db.session.add(target)
            made.append(name)
        target.set_sqlalchemy_uri(uri)
        target.extra = json.dumps(MARKER)
        for key, value in flags.items():
            setattr(target, key, value)
        return target

    password = _mart_password()
    if password:
        upsert("Dev kit store", f"postgresql+psycopg2://devkit:{password}@devkit-db:5432/devkit",
               expose_in_sqllab=True, allow_dml=True, allow_ctas=True, allow_cvas=True,
               allow_file_upload=True)

    cem = None
    if _cem_configured():
        cem = upsert("CEM live", "yacht://cem", expose_in_sqllab=True, allow_dml=False,
                     allow_ctas=False, allow_cvas=False)
    db.session.commit()

    # the starting queries, on the CEM when there is one — they are what a newcomer should run first
    if cem is not None:
        admin = security_manager.find_user(username=os.environ.get("DEVKIT_ADMIN_USER", "admin"))
        for label, sql in SAVED_QUERIES:
            existing = db.session.query(SavedQuery).filter_by(label=label).one_or_none()
            if existing is not None:
                # Superset's list is filtered by OWNER, and a row with user_id but no created_by belongs to
                # nobody: the queries were there and the page showed none (measured 2026-10-01).
                if admin is not None and existing.created_by is None:
                    existing.created_by = admin
                    made.append(f"requête « {label} » (rendue visible)")
                continue
            db.session.add(SavedQuery(label=label, sql=sql, db_id=cem.id, schema="dc_run",
                                      description="Créée par le dev kit — modifiez-la librement.",
                                      user_id=admin.id if admin else None,
                                      created_by=admin, changed_by=admin))
            made.append(f"requête « {label} »")
        db.session.commit()

    if made:
        print("  seeded: " + ", ".join(made))
    else:
        print("  nothing to seed: the sources are already there"
              + ("" if _cem_configured() else " (cem.env names no CEM, so « CEM live » was not created)"))
    return 0


if __name__ == "__main__":
    from superset.app import create_app

    app = create_app()
    with app.app_context():
        sys.exit(seed())
