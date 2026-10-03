#!/usr/bin/env bash
# Install the SharpSight Superset dev kit on this machine. Docker is the only requirement; the internet is not.
#
#   bash install.sh            first install, or start it again after a stop
#   bash install.sh --stop     stop it, keep everything
#   bash install.sh --erase    stop it and delete ITS volumes (nothing else on this machine is touched)
#
# It installs nothing outside its own compose project: its containers, its network, its two volumes. It cannot
# see, reach or modify a SharpSight platform running beside it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
PROJECT=sharpsight-superset-devkit

compose() { docker compose -p "$PROJECT" "$@"; }

case "${1:-}" in
  --stop)  compose down; echo "stopped — your dashboards and extracts are kept"; exit 0 ;;
  --erase) compose down -v; echo "erased — only this dev kit's volumes were deleted"; exit 0 ;;
esac

command -v docker >/dev/null || { echo "Docker is not installed or not on PATH." >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is installed but not running." >&2; exit 1; }

# 1 · the images, if this is a bundle rather than a source checkout.
#     The IMAGE TAG NEVER CHANGES between versions, so "is it already there?" is the wrong question: unpacking a
#     newer bundle over an older kit would keep the old image and quietly run last week's code. The question is
#     whether THESE images were loaded, which the archive's own checksum answers; it is remembered beside them.
if [ -f images/devkit-images.tar.gz ]; then
  want="$(sha256sum images/devkit-images.tar.gz | cut -d' ' -f1)"
  have="$(cat .images-loaded 2>/dev/null || true)"
  if [ "$want" != "$have" ] || ! docker image inspect sharpsight-superset-devkit:1.0.0 >/dev/null 2>&1; then
    echo "loading images (this takes a minute) …"
    gunzip -c images/devkit-images.tar.gz | docker load
    echo "$want" > .images-loaded
    # an upgrade must actually restart on the new image, not keep the containers made from the old one
    compose up -d --force-recreate >/dev/null 2>&1 || true
  fi
fi

# 2 · the credentials, generated once, never shipped
if [ ! -f .env ]; then
  # A database volume from an EARLIER install outlives its .env. PostgreSQL only reads POSTGRES_PASSWORD when
  # it creates the data directory, so generating a fresh password here would leave every connection refused by
  # a database that still wants the old one — with nothing on screen to say why.
  if docker volume inspect "${PROJECT}_devkit-db" >/dev/null 2>&1; then
    cat >&2 <<'MSG'
refusing: this machine already holds a dev kit database, but its .env is gone.

The password PostgreSQL wants was in that file, and it cannot be guessed. Either:
  · put the old .env back beside this script, or
  · start clean, losing the dashboards and extracts in it:   bash install.sh --erase
MSG
    exit 1
  fi
  echo "first run — writing .env with generated secrets"
  rand() { openssl rand -base64 "${1:-30}" 2>/dev/null | tr -d '\n=/+' | cut -c1-"${2:-32}"; }
  admin_pw="$(rand 24 20)"
  sed -e "s|^DEVKIT_SECRET_KEY=.*|DEVKIT_SECRET_KEY=$(rand 42 56)|" \
      -e "s|^DEVKIT_DB_PASSWORD=.*|DEVKIT_DB_PASSWORD=$(rand 24 24)|" \
      -e "s|^DEVKIT_ADMIN_PASSWORD=.*|DEVKIT_ADMIN_PASSWORD=$admin_pw|" \
      .env.example > .env
  chmod 600 .env
  echo
  echo "  ┌───────────────────────────────────────────────────────────────"
  echo "  │  sign in as: admin"
  echo "  │  password:   $admin_pw"
  echo "  │  (also in .env, which is readable only by you — change it in Superset if you prefer)"
  echo "  └───────────────────────────────────────────────────────────────"
  echo
fi

# 2b · the CEM's settings are a FILE handed to the container (compose `env_file`), never merged into .env:
#      .env is interpolated, so a password with a $ or a # would arrive mangled and the cluster would refuse it
#      with a 403 that looks like a wrong account. Compose needs the file to exist, so an empty one is written
#      when there is none; and a line that is not NAME=value is named here, because compose's own complaint
#      about it is unreadable (met on 2026-09-28: « unexpected character "#" in variable name »).
[ -f cem.env ] || { : > cem.env; chmod 600 cem.env; }
bad=0
while IFS= read -r line; do
  [ -n "${line//[[:space:]]/}" ] || continue
  case "$line" in '#'*) continue ;; esac
  case "${line%%=*}" in
    "$line"|*[!A-Za-z0-9_]*|[0-9]*|'') echo "  cem.env: this line is not a setting and is refused: ${line:0:60}" >&2; bad=1 ;;
  esac
done < cem.env
if [ "$bad" = 1 ]; then
  echo "refusing: fix the line(s) above in cem.env — every line must be NAME=value or a # comment." >&2
  exit 1
fi
# The copy into the container is done by the devkit-cem-sync service on EVERY start (docker-compose.yaml),
# so editing cem.env and restarting is enough — it used to happen only here, and an edit afterwards never
# reached the container. Docker would create a DIRECTORY where it expects this file, so all that is needed
# here is that the file exists, which the line near the top of this block already guarantees.
set_count="$(grep -cE '^[A-Za-z_][A-Za-z0-9_]*=.' cem.env || true)"
[ "$set_count" = 0 ] || echo "cem.env: $set_count setting(s) handed to the container as a file"

# 3 · the credentials in .env must be the ones the existing database was created with. They are not, whenever a
#     SECOND copy of this kit is installed on the same machine: the compose project name is fixed, so both copies
#     address the same volumes, while each has generated its own .env. Without this check the stack comes up and
#     dies inside Superset with a traceback ending in "password authentication failed for user devkit", which
#     names neither cause nor cure (met on 2026-09-27).
if docker volume inspect "${PROJECT}_devkit-db" >/dev/null 2>&1; then
  compose up -d devkit-db >/dev/null
  for _ in $(seq 1 30); do compose exec -T devkit-db pg_isready -U devkit -d devkit >/dev/null 2>&1 && break; sleep 2; done
  pw="$(grep -E '^DEVKIT_DB_PASSWORD=' .env | cut -d= -f2-)"
  # From ANOTHER container, by hostname, on purpose: inside the database's own container PostgreSQL trusts both
  # the unix socket and 127.0.0.1, so a test run there passes with any password at all — the first version of
  # this check did exactly that and waved the mismatch through (measured, 2026-09-27).
  if ! docker run --rm --network "${PROJECT}_default" -e PGPASSWORD="$pw" postgres:16.4        psql -h devkit-db -U devkit -d devkit -c 'select 1' >/dev/null 2>&1; then
    cat >&2 <<MSG
refusing: the database already on this machine does not accept the password in this .env.

It was created by another copy of this dev kit (they share one compose project, so they share its volumes),
or by an earlier .env. Either:
  · install from that copy instead, or copy its .env here, or
  · start clean, losing the dashboards and extracts in it:   bash install.sh --erase
MSG
    exit 1
  fi
fi

# 4 · up
compose up -d
# what the sync service said about cem.env: it runs inside compose, so its line would otherwise sit in the logs
# where nobody looks — and « the CEM is not configured » is exactly the sentence someone needs to read.
compose logs --no-log-prefix devkit-cem-sync 2>/dev/null | grep -E "^cem\.env" | tail -1 || true
port="$(grep -E '^DEVKIT_PORT=' .env | cut -d= -f2)"; port="${port:-3002}"
bind="$(grep -E '^DEVKIT_BIND=' .env | cut -d= -f2)"; bind="${bind:-127.0.0.1}"

echo -n "waiting for Superset to answer"
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    echo; echo "  ready — http://$bind:$port"
    echo "    sign in, then SQL → SQL Lab. Two sources are already connected:"
    echo "      · CEM live      the cluster, queried where it stands — nothing is copied"
    echo "      · Dev kit store this kit's own PostgreSQL, where your extracts and CREATE TABLE AS land"
    echo "    Three starting queries are in SQL → Requêtes enregistrées."
    echo "    To add SQL Server or Oracle: Paramètres → Bases de données → + (a form, not a URI)."
    exit 0
  fi
  echo -n "."; sleep 5
done
echo
echo "it did not answer in five minutes. What it says for itself:" >&2
compose logs --tail 40 superset-devkit >&2
exit 1
