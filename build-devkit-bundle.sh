#!/usr/bin/env bash
# Build ONE tar that installs this Superset dev kit on another machine — including one with no internet.
#
#   bash tools/superset-devkit/build-devkit-bundle.sh
#   OUT=/mnt/usb bash tools/superset-devkit/build-devkit-bundle.sh
#
# It contains the built image, its two dependencies (PostgreSQL, Redis), the compose file, the configuration and
# an install.sh. It contains NO credential: the installed kit asks for its own on first start.
#
# It is deliberately NOT the platform's build-bundle.sh: no tag is required, nothing is checked against a commit,
# and nothing here can install or upgrade SharpSight.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${OUT:-$HERE/dist}"
VERSION="${VERSION:-$(date +%Y%m%d)}"
NAME="sharpsight-superset-devkit-$VERSION"
IMAGE="sharpsight-superset-devkit:1.0.0"
# nginx is the optional HTTPS entry (compose profile « tls »). It travels in every bundle: a target with no
# internet cannot fetch it on the day somebody turns the profile on.
DEPS=(postgres:16.4 redis:7-alpine nginx:1.27-alpine)

echo "═══ 1/4  building $IMAGE"
docker build --build-arg "WITH_JDBC=${DEVKIT_WITH_JDBC:-1}" -t "$IMAGE" "$HERE"

echo "═══ 2/4  collecting the images it needs"
for dep in "${DEPS[@]}"; do
  docker image inspect "$dep" >/dev/null 2>&1 || docker pull "$dep"
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
stage="$work/$NAME"
mkdir -p "$stage/images"

echo "═══ 3/4  saving images (the slow part)"
docker save "$IMAGE" "${DEPS[@]}" | gzip -1 > "$stage/images/devkit-images.tar.gz"
echo "  wrote $(du -h "$stage/images/devkit-images.tar.gz" | cut -f1)"

echo "═══ 4/4  sealing"
# -s, not -f: an EMPTY cem.env is not a secret, and saying it is teaches people to ignore the warning
[ "${INCLUDE_CEM:-0}" = "1" ] && [ -s "$HERE/cem.env" ] && INCLUDE_CEM_SECRET=yes || INCLUDE_CEM_SECRET=no
cp "$HERE/docker-compose.yaml" "$HERE/install.sh"    "$HERE/cem.env.example" "$HERE/import-into-devkit.sh" "$HERE/export-for-devkit.sh" "$stage/"
# THE KIT, AND NOTHING OF THE AI BI ENGINE — its settings and its documentation included. The engine is an overlay
# of this checkout and is not shipped (owner, 2026-10-05). Its section of .env.example travelled all the same,
# and install.sh adds to an upgraded .env every setting the bundle's .env.example knows: the first rehearsal of
# an upgrade wrote seven AIBI_ lines into a kit that has no engine. So the bundle's copy ends that section
# where the next one begins, and its README loses the rows that point at files which are not in the bundle.
awk '/^# ── SharpSight AI BI/ {skip=1; next} skip && /^# ── / {skip=0} !skip' "$HERE/.env.example" > "$stage/.env.example"
grep -v -i -E 'ai-bi|superset-addon/|superset-extension/' "$HERE/README.md" > "$stage/README.md"
if grep -q -i -E 'AIBI_|ai-bi' "$stage/.env.example" "$stage/README.md"; then
  echo "the bundle's .env.example or README still names the AI BI engine — its section moved or was renamed" >&2; exit 1
fi
cp -r "$HERE/initdb" "$stage/"
# the HTTPS entry's configuration: compose mounts this FILE, and Docker makes a directory of a file that is missing
mkdir -p "$stage/nginx" && cp "$HERE/nginx/default.conf.template" "$stage/nginx/"
mkdir -p "$stage/driver-jars" "$stage/exports" "$stage/krb5"

# INCLUDE_CEM=1 makes the bundle self-contained for the CEM: the Yacht driver, and cem.env if you have written
# one. The driver is a file, and travels quietly. cem.env is NOT: it holds an account and its password in
# clear, so a bundle carrying it must be handled like a password file, and the build says so out loud.
if [ "${INCLUDE_CEM:-0}" = "1" ]; then
  if compgen -G "$HERE/driver-jars/*.jar" >/dev/null; then
    cp -f "$HERE/driver-jars"/*.jar "$stage/driver-jars/"
    echo "  driver included: $(ls -1 "$HERE/driver-jars"/*.jar | xargs -n1 basename | tr '
' ' ')"
  else
    echo "  INCLUDE_CEM=1 but driver-jars/ holds no .jar — the kit will install without the driver." >&2
  fi
  if [ -s "$HERE/cem.env" ]; then
    cp -f "$HERE/cem.env" "$stage/cem.env"
    chmod 600 "$stage/cem.env"
    echo "  cem.env included — THIS TARBALL NOW CARRIES A PASSWORD. Treat it as one."
  else
    echo "  no cem.env here: the kit installs with cem.env.example, which names everything but the secrets."
  fi
fi
cat > "$stage/driver-jars/README.txt" <<'TXT'
Put the CEM's Yacht JDBC driver (.jar) here. It is mounted read-only at /app/driver-jars inside the container,
and named by CEM_JDBC_JARS in .env. It is NOT shipped in this bundle: it is the customer's file, not ours.
TXT
cat > "$stage/MANIFEST.txt" <<TXT
name=$NAME
built_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
image=$IMAGE
dependencies=${DEPS[*]}
superset=6.1.0 (same pinned digest as the SharpSight product image)
drivers=postgresql, mssql (pymssql AND pyodbc+FreeTDS), oracle (python-oracledb, thin), hive/spark (PyHive), JDBC bridge (JayDeBeApi + Java 17)
bi_engine=no — the SharpSight menu and the AI BI engine are not in this bundle
upgrade=bash install.sh in this folder finds the kit already installed, brings its settings, backs up, migrates, and counts what it kept
contains_secrets=${INCLUDE_CEM_SECRET:-no}
TXT

mkdir -p "$OUT"
tar -czf "$OUT/$NAME.tar.gz" -C "$work" "$NAME"
( cd "$OUT" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256" )

cat <<SUMMARY

  Dev kit ready:  $OUT/$NAME.tar.gz  ($(du -h "$OUT/$NAME.tar.gz" | cut -f1))
  Checksum:       $OUT/$NAME.tar.gz.sha256

  On the target machine (Docker required, internet not), standing in the folder that holds BOTH files
  (the checksum names the tarball without a path, so it is looked for in the current directory):
      sha256sum -c $NAME.tar.gz.sha256
      tar -xzf $NAME.tar.gz && cd $NAME
      bash install.sh

SUMMARY
