#!/usr/bin/env bash
# Load an export-for-devkit.sh archive into the Superset dev kit, so its dashboards open on the mart's data.
# Run it on the DEV machine, FROM THE DEV KIT'S FOLDER (where its install.sh and .env are), with the kit running
# (bash install.sh).
#
#   bash scripts/bundle/import-into-devkit.sh /path/to/sharpsight-devkit-export-<date>.tgz
#
# What it does:
#   1. checks the archive against its .sha256;
#   2. restores the schema roaming_mart into the kit's OWN database (devkit), replacing a previous copy;
#   3. points the dashboards' connection at that database — the kit's password is written into the connection at
#      import time, read from this kit's .env: the archive never carries one;
#   4. imports the dashboards into the kit's Superset (overwriting earlier imports of the same dashboards).
set -euo pipefail

ARCHIVE="${1:?usage: bash scripts/bundle/import-into-devkit.sh <sharpsight-devkit-export-…tgz>}"
P=sharpsight-superset-devkit
[ -f install.sh ] && [ -f .env ] || { echo "run this from the dev kit's folder (install.sh and .env)" >&2; exit 1; }
DEVKIT_DB_PASSWORD=$(grep -E '^DEVKIT_DB_PASSWORD=' .env | head -1 | cut -d= -f2-)
ADMIN=$(grep -E '^DEVKIT_ADMIN_USER=' .env | head -1 | cut -d= -f2-); ADMIN=${ADMIN:-admin}
[ -n "$DEVKIT_DB_PASSWORD" ] || { echo "DEVKIT_DB_PASSWORD is not in .env" >&2; exit 1; }
docker compose -p "$P" ps --status running --services 2>/dev/null | grep -qx superset-devkit \
  || { echo "the dev kit is not running — start it with: bash install.sh" >&2; exit 1; }

if [ -f "$ARCHIVE.sha256" ]; then
  ( cd "$(dirname "$ARCHIVE")" && sha256sum -c "$(basename "$ARCHIVE").sha256" )
else
  echo "warning: no $ARCHIVE.sha256 beside the archive — integrity not checked" >&2
fi
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
tar --force-local -xzf "$ARCHIVE" -C "$WORK"
cat "$WORK/manifest.txt"

echo "1/3  restore roaming_mart into a database called 'roaming', beside the kit's own"
# THE DATABASE'S NAME IS NOT A DETAIL. Superset 6 records a CATALOG on every dataset, and for PostgreSQL the
# catalog IS the database: the exported datasets carry `catalog: roaming`, so Superset connects to a database
# of that name whatever the connection's URI says. Restoring into `devkit` gave 38 datasets pointing at a
# database that did not exist, a dashboard that drew nothing, and « FATAL: database "roaming" does not exist »
# in the log while every connection on screen looked right (met 2026-10-01). So the kit keeps the platform's
# name, and nothing in the export has to be rewritten but the password.
docker compose -p "$P" exec -T devkit-db psql -v ON_ERROR_STOP=1 -q -U devkit -d postgres -tAc \
  "select 1 from pg_database where datname='roaming'" | grep -q 1 \
  || docker compose -p "$P" exec -T devkit-db psql -v ON_ERROR_STOP=1 -q -U devkit -d postgres \
       -c 'create database roaming owner devkit'
docker compose -p "$P" exec -T devkit-db psql -v ON_ERROR_STOP=1 -q -U devkit -d roaming \
  -c 'drop schema if exists roaming_mart cascade'
# an earlier version of this script restored into `devkit`; leave nothing behind to wonder about
docker compose -p "$P" exec -T devkit-db psql -q -U devkit -d devkit \
  -c 'drop schema if exists roaming_mart cascade' >/dev/null 2>&1 || true
docker compose -p "$P" exec -T devkit-db pg_restore -U devkit -d roaming --no-owner --no-privileges --exit-on-error \
  < "$WORK/roaming_mart.dump"
docker compose -p "$P" exec -T devkit-db psql -q -U devkit -d roaming -tAc \
  "select count(*) || ' tables and views in roaming_mart' from information_schema.tables where table_schema='roaming_mart'"

echo "2/3  point the dashboards' connection at the kit's database"
docker compose -p "$P" cp "$WORK/dashboards.zip" superset-devkit:/tmp/devkit-dashboards.zip
docker compose -p "$P" exec -T -e DEVKIT_DB_PASSWORD="$DEVKIT_DB_PASSWORD" superset-devkit python - <<'PY'
import os, re, zipfile
src, dst = "/tmp/devkit-dashboards.zip", "/tmp/devkit-dashboards-local.zip"
# the database the datasets name in their catalog, not the kit's own
uri = "postgresql+psycopg2://devkit:%s@devkit-db:5432/roaming" % os.environ["DEVKIT_DB_PASSWORD"]
n = 0
with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        data = zin.read(item.filename)
        if re.search(r"/databases/[^/]+\.yaml$", item.filename):
            text = data.decode("utf-8")
            text, k = re.subn(r"(?m)^sqlalchemy_uri:.*$", "sqlalchemy_uri: " + uri, text)
            n += k
            data = text.encode("utf-8")
        zout.writestr(item, data)
print("     connection(s) repointed:", n)
PY

echo "3/3  import the dashboards into the kit's Superset (as $ADMIN)"
# The container's paths go inside a `sh -c` string, never as arguments: Git Bash rewrites an argument that
# looks like a path, so `-p /tmp/x.zip` reached Superset as `C:/Users/.../Temp/x.zip` and it opened nothing
# (met 2026-10-01). Turning conversion off for the whole script breaks the other direction — the HOST paths
# this script also passes to docker — so the guard belongs here, where only container paths appear.
docker compose -p "$P" exec -T superset-devkit sh -c \
  "superset import-dashboards -p /tmp/devkit-dashboards-local.zip -u '$ADMIN'"
# AND SET THE URI AGAIN, AFTER THE IMPORT. Rewriting it inside the archive is not enough: Superset matches a
# database by UUID and KEEPS the one it already has, URI included — so a kit that had imported these dashboards
# before (or had made that connection by hand) kept pointing at the platform's `superset-db`, which does not
# exist on this network, and every chart failed with « could not translate host name "superset-db" » while the
# archive on disk was perfectly correct (owner's server, 2026-10-02). The platform's own importer learned the
# same lesson. This is idempotent: on a fresh kit it sets what the import just set.
docker compose -p "$P" exec -T -e DEVKIT_DB_PASSWORD="$DEVKIT_DB_PASSWORD" superset-devkit sh -c   'superset set-database-uri -d "cem_datamart" -u "postgresql+psycopg2://devkit:$DEVKIT_DB_PASSWORD@devkit-db:5432/roaming"'   || echo "  note: no connection called cem_datamart to repoint (the export may name it otherwise)" >&2

# `docker compose cp` lands the file as root while the container runs as superset, so this tidy-up may be
# refused — it is a temporary file inside a container, and failing the whole import over it would be absurd.
docker compose -p "$P" exec -T superset-devkit sh -c \
  'rm -f /tmp/devkit-dashboards.zip /tmp/devkit-dashboards-local.zip 2>/dev/null' || true
echo "Done. Open the kit, Tableaux de bord: the imported dashboards read the kit's copy of roaming_mart."
