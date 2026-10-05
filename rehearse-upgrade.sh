#!/usr/bin/env bash
# REHEARSE AN UPGRADE of the dev kit — the test for any change to install.sh, and for every bundle before it ships.
#
#   bash rehearse-upgrade.sh dist/sharpsight-superset-devkit-<old>.tar.gz dist/sharpsight-superset-devkit-<new>.tar.gz
#   KEEP=1 bash rehearse-upgrade.sh …      leave the scratch project up at the end, to look at it
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
OLD_BUNDLE="${1:?usage: bash rehearse-upgrade.sh <old bundle.tar.gz> <new bundle.tar.gz>}"
NEW_BUNDLE="${2:?usage: bash rehearse-upgrade.sh <old bundle.tar.gz> <new bundle.tar.gz>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
abs() { echo "$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"; }
OLD_BUNDLE="$(abs "$OLD_BUNDLE")"; NEW_BUNDLE="$(abs "$NEW_BUNDLE")"
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
DEVKIT_PROJECT="$P" bash install.sh > "$W/old-install.log" 2>&1 || { echo "  the OLD installer failed:"; grep -v -i 'password' "$W/old-install.log" | tail -12 | cut -c1-160; exit 1; }
grep -v -i 'password' "$W/old-install.log" | grep -E 'first run|ready —' | cut -c1-120
echo "  its settings: $(grep -E '^DEVKIT_(PORT|DATA_CACHE_TTL|SETTINGS_VERSION)=' .env | tr '\n' ' ')"

echo "== 2. a developer's work in it =="
PYTHONIOENCODING=utf-8 python "$HERE/rehearse_upgrade_content.py" make "$(native "$W/old")" "http://127.0.0.1:$PORT" || exit 1

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
PYTHONIOENCODING=utf-8 python "$HERE/rehearse_upgrade_content.py" check "$(native "$W/new")" "http://127.0.0.1:$PORT" || fail=1
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
