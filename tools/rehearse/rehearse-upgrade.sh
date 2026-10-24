#!/usr/bin/env bash
# REHEARSE AN UPGRADE of the dev kit — the test for any change to install.sh, and for every bundle before it ships.
#
#   bash tools/rehearse/rehearse-upgrade.sh dist/sharpsight-superset-devkit-<old>.tar.gz dist/sharpsight-superset-devkit-<new>.tar.gz
#   KEEP=1 bash tools/rehearse/rehearse-upgrade.sh …      leave the scratch project up at the end, to look at it
#   REAL_CONTENT_FROM=sharpsight-superset-devkit REAL_CONTENT_ENV=.env bash tools/rehearse/rehearse-upgrade.sh …
#       rehearse on a COPY OF A REAL KIT instead of a made-up one: its connections, datasets, charts, dashboards,
#       users, tags and extracts (every database of its server), with its secret key so its saved passwords still
#       decrypt. Every connection, dashboard and chart is asked before and after; with REHEARSAL_BROWSER_IMAGE (a
#       Playwright image, default sharpsight/pbix-export-worker:v1.15.84 when present) every dashboard is also
#       OPENED in a browser before and after. The copy and the key live in the scratch folder and go with it.
#
# What it does, beside the kit in use and without touching its containers, volumes or folder:
#   1. installs the OLD bundle with ITS OWN installer, as a second compose project on another port;
#   2. makes in it what a developer makes: a connection with a saved password, a dataset, a chart, a dashboard,
#      a saved query;
#   3. unpacks the NEW bundle in a NEW folder and runs `bash install.sh` — nothing copied by hand;
#   4. asks for all of it again THROUGH Superset: a query through the saved connection (its password must still
#      decrypt), the dashboard at its address, the chart's figure, the saved query; and that the bundle brought
#      no SharpSight menu and no AI BI engine;
#   5. removes the scratch project, its volumes and its folders.
#
# WHY IT EXISTS (2026-10-05). The upgrade path of install.sh read correctly and was wrong twice: the one settings
# change it existed for was skipped by an empty variable, and seven of the engine's settings were written into a
# kit that has no engine. Both were found here, by upgrading, and by nothing else.
#
# IT SHARES ONE THING WITH THE REAL KIT: the image tag. Both bundles load sharpsight-superset-devkit:1.0.0, so
# when this ends the tag is the NEW bundle's image, and the kit in use is recreated on it at its next start.
# Stop the real kit first on a small machine: a second Superset needs about 700 MB.
set -uo pipefail
OLD_BUNDLE="${1:?usage: bash tools/rehearse/rehearse-upgrade.sh <old bundle.tar.gz> <new bundle.tar.gz>}"
NEW_BUNDLE="${2:?usage: bash tools/rehearse/rehearse-upgrade.sh <old bundle.tar.gz> <new bundle.tar.gz>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
abs() { echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; }
OLD_BUNDLE="$(abs "$OLD_BUNDLE")"; NEW_BUNDLE="$(abs "$NEW_BUNDLE")"
[ -z "${REAL_CONTENT_ENV:-}" ] || REAL_CONTENT_ENV="$(abs "$REAL_CONTENT_ENV")"   # read after a cd
P=devkit-upgrade-rehearsal
PORT="${REHEARSAL_PORT:-3102}"
W="$(mktemp -d)"
native() { command -v cygpath >/dev/null 2>&1 && cygpath -m "$1" || printf '%s' "$1"; }   # a path python.exe reads

fail=0
clean() {
  [ "${KEEP:-0}" = "1" ] && { echo "KEEP=1 — the scratch kit is left up on http://127.0.0.1:$PORT, in $W"; return; }
  docker compose -p "$P" down -v --remove-orphans >/dev/null 2>&1
  docker ps -a --filter "label=com.docker.compose.project=$P" -q | xargs -r docker rm -f >/dev/null 2>&1
  docker volume ls --filter "label=com.docker.compose.project=$P" -q | xargs -r docker volume rm >/dev/null 2>&1
  rm -rf "$W"
}
trap clean EXIT

if docker ps -a --filter "label=com.docker.compose.project=$P" -q | grep -q .; then
  echo "refusing: a scratch project « $P » is already on this machine (an earlier rehearsal kept with KEEP=1?)." >&2
  echo "          remove it first:  docker compose -p $P down -v" >&2
  trap - EXIT; rm -rf "$W"; exit 3
fi

echo "== 1. the old bundle, installed by its own installer =="
cd "$W" && tar -xzf "$OLD_BUNDLE" && mv "$(basename "$OLD_BUNDLE" .tar.gz)" old && cd old || exit 1
# two words of it are changed, and only these: the project it installs as, and the port it listens on
sed -i -E "s/^PROJECT=.*/PROJECT=\"\${DEVKIT_PROJECT:-$P}\"/" install.sh
sed -i "s/^DEVKIT_PORT=.*/DEVKIT_PORT=$PORT/" .env.example
REAL="${REAL_CONTENT_FROM:-}"
if [ -n "$REAL" ]; then
  # the real kit's two secrets, so that what its database holds is readable here: the key that encrypts the saved
  # connection passwords, and the database password those connections use to reach the kit's own server
  src_env="${REAL_CONTENT_ENV:?REAL_CONTENT_ENV: the .env of the kit named by REAL_CONTENT_FROM}"
  key="$(sed -n 's/^DEVKIT_SECRET_KEY=//p' "$src_env" | tail -1)"; dbpw="$(sed -n 's/^DEVKIT_DB_PASSWORD=//p' "$src_env" | tail -1)"
  [ -n "$key" ] && [ -n "$dbpw" ] || { echo "  $src_env has no DEVKIT_SECRET_KEY or DEVKIT_DB_PASSWORD"; exit 1; }
  admin_pw="$(head -c 18 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  awk -v k="$key" -v d="$dbpw" -v a="$admin_pw" '
    /^DEVKIT_SECRET_KEY=/ {print "DEVKIT_SECRET_KEY=" k; next}
    /^DEVKIT_DB_PASSWORD=/ {print "DEVKIT_DB_PASSWORD=" d; next}
    /^DEVKIT_ADMIN_PASSWORD=/ {print "DEVKIT_ADMIN_PASSWORD=" a; next} {print}' .env.example > .env
  chmod 600 .env
fi
DEVKIT_PROJECT="$P" bash install.sh > "$W/old-install.log" 2>&1 || { echo "  the OLD installer failed:"; grep -v -i 'password' "$W/old-install.log" | tail -12 | cut -c1-160; exit 1; }
grep -v -i 'password' "$W/old-install.log" | grep -E 'first run|ready —' | cut -c1-120
echo "  its settings: $(grep -E '^DEVKIT_(PORT|DATA_CACHE_TTL|SETTINGS_VERSION)=' .env | tr '\n' ' ')"

BROWSER_IMAGE="${REHEARSAL_BROWSER_IMAGE:-sharpsight/pbix-export-worker:v1.15.84}"
docker image inspect "$BROWSER_IMAGE" >/dev/null 2>&1 || BROWSER_IMAGE=""
admin_of() { sed -n 's/^DEVKIT_ADMIN_USER=//p' "$1/.env" | tail -1; }
pw_of()    { sed -n 's/^DEVKIT_ADMIN_PASSWORD=//p' "$1/.env" | tail -1; }
# what every connection, dashboard and chart answers — through the API, and (with a browser image) on screen.
# The browser is capped: an uncapped container has taken Docker Desktop down on this kind of machine.
snapshot() {   # <folder> <label>
  DEVKIT_REHEARSAL_PW="$(pw_of "$1")" PYTHONIOENCODING=utf-8 \
    python "$HERE/rehearse_real_content.py" snapshot "http://127.0.0.1:$PORT" "$(admin_of "$1")" "$(native "$W/$2-api.json")" || return 1
  [ -n "$BROWSER_IMAGE" ] || { echo "  (no browser image: the dashboards are asked through the API only)"; return 0; }
  cp "$HERE/rehearse_dashboards.mjs" "$W/"
  MSYS_NO_PATHCONV=1 docker run --rm --name "$P-browser" --memory 1200m --cpus 2 --pids-limit 512 \
    --network "${P}_default" -e DEVKIT_REHEARSAL_PW="$(pw_of "$1")" -v "$(native "$W"):/work" --entrypoint node \
    "$BROWSER_IMAGE" /work/rehearse_dashboards.mjs http://superset-devkit:8088 "$(admin_of "$1")" "/work/$2-screen.json" \
    | sed 's/^/  /' | cut -c1-170
}

if [ -n "$REAL" ]; then
  echo "== 2. a copy of the real kit « $REAL » in it: every database of its server =="
  docker compose -p "$P" stop superset-devkit >/dev/null 2>&1
  srcdb() { docker compose -p "$REAL" exec -T devkit-db "$@"; }
  dstdb() { docker compose -p "$P" exec -T devkit-db "$@"; }
  # its roles (the read-only accounts its connections use), without the kit's own, which exists here already
  srcdb pg_dumpall -U devkit --roles-only 2>/dev/null | grep -vE 'ROLE (devkit|postgres)( |;)' | dstdb psql -q -U devkit -d postgres >/dev/null 2>&1
  for db in $(srcdb psql -At -U devkit -d postgres -c "select datname from pg_database where not datistemplate and datname <> 'postgres'" | tr -d '\r'); do
    dstdb psql -q -U devkit -d postgres -c "create database \"$db\"" >/dev/null 2>&1 || true
    srcdb pg_dump -U devkit -d "$db" -Fc 2>/dev/null > "$W/$db.dump"
    dstdb pg_restore -U devkit -d "$db" --clean --if-exists < "$W/$db.dump" >/dev/null 2>&1
    echo "  copied database « $db » ($(du -h "$W/$db.dump" | cut -f1))"
  done
  # a kit older than the scheduled refresh has no refresh account: the copy of a newer one does, so it goes, and
  # the upgrade has to create it as it will on a real older kit
  dstdb psql -q -U devkit -d superset -c "delete from ab_user_role where user_id in (select id from ab_user where username = 'devkit-refresh'); delete from ab_user where username = 'devkit-refresh'" >/dev/null 2>&1 \
    || echo "  (the copy's refresh account could not be removed: the upgrade will find it already there)"
  docker compose -p "$P" up -d superset-devkit >/dev/null 2>&1
  # the copy brought the real kit's admin and its password: this folder's admin is given this folder's password
  docker compose -p "$P" exec -T superset-devkit superset fab reset-password --username "$(admin_of "$W/old")" --password "$(pw_of "$W/old")" >/dev/null 2>&1
  for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break; sleep 5; done
  echo "  before the upgrade, asked through Superset:"
  snapshot "$W/old" before || { echo "  FAIL  the old kit could not be asked"; exit 1; }
else
echo "== 2. a developer's work in it =="
PYTHONIOENCODING=utf-8 python "$HERE/rehearse_upgrade_content.py" make "$(native "$W/old")" "http://127.0.0.1:$PORT" || exit 1
fi

echo "== 3. the new bundle, unpacked in a NEW folder: bash install.sh, and nothing else =="
cd "$W" && tar -xzf "$NEW_BUNDLE" && mv "$(basename "$NEW_BUNDLE" .tar.gz)" new && cd new || exit 1
DEVKIT_PROJECT="$P" bash install.sh > "$W/new-install.log" 2>&1; rc=$?
grep -v -i 'password:' "$W/new-install.log" | grep -E '^(upgrade|settings|backup|refusing|SOMETHING)|brought here|->|schema|connections:|nothing is missing|ready —|integer expression|rror' | cut -c1-190
[ $rc -eq 0 ] || { echo "  FAIL  the new installer exited $rc"; exit 1; }
grep -q 'integer expression\|command not found\|syntax error' "$W/new-install.log" && { echo "  FAIL  the installer printed a shell error (above)"; fail=1; }
grep -q '^upgrade — the kit on this machine was installed from' "$W/new-install.log" \
  && echo "  PASS  it found the kit already installed, by itself" || { echo "  FAIL  it did not recognise an upgrade"; fail=1; }
ls backups/*.dump >/dev/null 2>&1 && echo "  PASS  it backed Superset's database up first ($(ls backups | head -1))" || { echo "  FAIL  no backup was taken"; fail=1; }
grep -q -i 'AIBI' .env && { echo "  FAIL  the engine's settings were written into the upgraded .env"; fail=1; } || echo "  PASS  no engine setting in the upgraded .env"

echo "== 4. is the developer's work still there, asked through Superset? =="
if [ -n "$REAL" ]; then
  snapshot "$W/new" after || { echo "  FAIL  the new kit could not be asked"; fail=1; }
  PYTHONIOENCODING=utf-8 python "$HERE/rehearse_real_content.py" compare "$(native "$W/before-api.json")" "$(native "$W/after-api.json")" || fail=1
  if [ -n "$BROWSER_IMAGE" ]; then
    PYTHONIOENCODING=utf-8 python "$HERE/rehearse_real_content.py" compare-screen "$(native "$W/before-screen.json")" "$(native "$W/after-screen.json")" || fail=1
  fi
  docker compose -p "$P" logs --no-log-prefix devkit-init 2>/dev/null | grep -E "^ *connections: " | tail -1 | sed 's/^ */  /'
else
PYTHONIOENCODING=utf-8 python "$HERE/rehearse_upgrade_content.py" check "$(native "$W/new")" "http://127.0.0.1:$PORT" || fail=1
fi
# the scheduled refresh: started, and signed in with the account the upgrade gave it (a kit older than 20261015
# has none — devkit-init creates it, and nothing in that step may keep Superset from starting)
docker compose -p "$P" logs --no-log-prefix devkit-init 2>/dev/null | grep -q "WARNING: the scheduled refresh" \
  && { echo "  FAIL  devkit-init could not set the refresh's account"; fail=1; }
for _ in $(seq 1 24); do
  docker compose -p "$P" logs --no-log-prefix devkit-refresh 2>/dev/null | grep -qE "chart request|waiting for Superset|cannot sign in" && break
  docker compose -p "$P" exec -T devkit-redis redis-cli exists devkit_refresh:dashboards 2>/dev/null | grep -q 1 && break
  sleep 5
done
if docker compose -p "$P" logs --no-log-prefix devkit-refresh 2>/dev/null | grep -q "cannot sign in"; then
  echo "  FAIL  the scheduled refresh cannot sign in"; fail=1
elif docker compose -p "$P" exec -T devkit-redis redis-cli exists devkit_refresh:dashboards 2>/dev/null | grep -q 1; then
  echo "  PASS  the scheduled refresh signed in with its own account and read every dashboard's schedule"
else
  echo "  FAIL  the scheduled refresh has not done a pass in two minutes"; fail=1
fi
inside="$(docker compose -p "$P" exec -T superset-devkit sh -c 'printf "%s" "$DEVKIT_DATA_CACHE_TTL"' 2>/dev/null)"
want="$(sed -n 's/^DEVKIT_DATA_CACHE_TTL=//p' .env | tail -1)"
[ -n "$inside" ] && [ "$inside" = "$want" ] && echo "  PASS  the chart cache Superset runs with is the one in .env ($inside s)" \
  || { echo "  FAIL  .env says a cache of « $want » s and Superset runs with « $inside »"; fail=1; }
[ "$(sed -n 's/^DEVKIT_PORT=//p' .env | tail -1)" = "$PORT" ] && echo "  PASS  the port did not move ($PORT)" || { echo "  FAIL  the port changed"; fail=1; }

echo "== 5. a plain restart afterwards takes no second backup =="
DEVKIT_PROJECT="$P" bash install.sh > "$W/again.log" 2>&1
[ "$(ls backups | wc -l)" = "1" ] && echo "  PASS  one backup, still" || { echo "  FAIL  $(ls backups | wc -l) backups after a plain restart"; fail=1; }

echo
[ "$fail" = 0 ] && echo "== the upgrade keeps what a developer made ==" || echo "== THE UPGRADE IS NOT SAFE TO SHIP =="
exit "$fail"
