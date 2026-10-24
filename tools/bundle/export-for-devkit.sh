#!/usr/bin/env bash
# Export the platform's mart and its Superset dashboards, to open them in the Superset dev kit
# (tools/superset-devkit). Run it ON THE PLATFORM SERVER, as a user who may run docker. Read-only: it dumps and
# exports, it changes nothing on the platform.
#
#   bash export-for-devkit.sh            → ~/sharpsight-devkit-export-<date>.tgz (+ .sha256)
#   (the file is copied to the platform server on its own; in the kit it is tools/bundle/export-for-devkit.sh)
#
# What the archive holds:
#   roaming_mart.dump   the schema roaming_mart of the « roaming » database (tables, views, data) — the mart both
#                       applications read. Aggregates only: no subscriber number, nothing under 10 SIM cards.
#   dashboards.zip      every dashboard of this Superset, as Superset exports it (charts, datasets, the connection
#                       — whose password Superset always masks; the import script puts the dev kit's own in).
#   manifest.txt        where and when it was taken.
set -euo pipefail

find_container() {   # the running container of a compose service, by label (project name differs per server)
  docker ps --filter "label=com.docker.compose.service=$1" --format '{{.Names}}' | head -1
}
DB=$(find_container superset-db)
SS=$(find_container superset)
[ -n "$DB" ] || { echo "no running superset-db container — is the platform up?" >&2; exit 1; }
[ -n "$SS" ] || { echo "no running superset container — is the platform up?" >&2; exit 1; }

STAMP=$(date +%Y%m%d-%H%M)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "1/3  the mart (schema roaming_mart of database roaming) from $DB"
docker exec "$DB" pg_dump -U superset -d roaming -n roaming_mart -Fc --no-owner --no-privileges \
  > "$WORK/roaming_mart.dump"
echo "     $(du -h "$WORK/roaming_mart.dump" | cut -f1)"

echo "2/3  every dashboard, exported by Superset in $SS"
# Not `superset export-dashboards`: in 6.1 that command runs as a user called « admin », which a SharpSight
# Superset does not have (its users come from the platform), and it fails on a missing user. Same command
# object, run as the first user holding the Admin role.
docker exec -i "$SS" python - >/dev/null 2>"$WORK/export.err" <<'PY' || { cat "$WORK/export.err" >&2; exit 1; }
from datetime import datetime
from zipfile import ZipFile

from superset.app import create_app

app = create_app()
with app.app_context():
    from flask import g
    from superset import db, security_manager
    from superset.commands.dashboard.export import ExportDashboardsCommand
    from superset.models.dashboard import Dashboard

    users = db.session.query(security_manager.user_model).order_by(security_manager.user_model.id).all()
    g.user = next(u for u in users if any(r.name == "Admin" for r in u.roles))
    ids = [i for (i,) in db.session.query(Dashboard.id).all()]
    root = "dashboard_export_" + datetime.now().strftime("%Y%m%dT%H%M%S")
    with ZipFile("/tmp/devkit-dashboards.zip", "w") as bundle:
        for name, content in ExportDashboardsCommand(ids).run():
            with bundle.open(root + "/" + name, "w") as fp:
                fp.write(content().encode())
PY
echo "     $(docker exec "$SS" python -c "import zipfile; n=zipfile.ZipFile('/tmp/devkit-dashboards.zip').namelist(); print(sum('/dashboards/' in x for x in n), 'dashboard(s),', sum('/charts/' in x for x in n), 'chart(s),', sum('/datasets/' in x for x in n), 'dataset(s)')")"
docker cp "$SS:/tmp/devkit-dashboards.zip" "$WORK/dashboards.zip" >/dev/null
docker exec "$SS" rm -f /tmp/devkit-dashboards.zip

{
  echo "taken_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "host=$(hostname)"
  echo "superset_db_container=$DB"
  echo "superset_container=$SS"
  echo "platform_version=$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.version"}}' "$SS" 2>/dev/null || true)"
} > "$WORK/manifest.txt"

OUT="$HOME/sharpsight-devkit-export-$STAMP.tgz"
echo "3/3  $OUT"
tar --force-local -czf "$OUT" -C "$WORK" roaming_mart.dump dashboards.zip manifest.txt
( cd "$(dirname "$OUT")" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" )
ls -lh "$OUT" "$OUT.sha256"
echo "Copy BOTH files to the dev machine, next to the dev kit, then run  bash tools/bundle/import-into-devkit.sh <archive>  from the kit's folder."
