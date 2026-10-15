#!/usr/bin/env bash
# Install the SharpSight Superset dev kit on this machine. Docker is the only requirement; the internet is not.
#
#   bash install.sh            first install, an UPGRADE of the kit already on this machine, or a start after a stop
#   bash install.sh --from DIR upgrade, naming the folder the kit was installed from (when it cannot be found)
#   bash install.sh --stop     stop it, keep everything
#   bash install.sh --erase    stop it and delete ITS volumes (nothing else on this machine is touched)
#
# AN UPGRADE: unpack the new bundle in a NEW folder and run this. It finds the kit already installed, brings its
# settings here (.env — which holds the key your connections' passwords are encrypted with — cem.env, the driver,
# krb5, your exports), backs Superset's own database up, lets Superset migrate it, and then says what it kept:
# connections, dashboards, charts, datasets, saved queries, counted before and after. Nothing you made is touched.
#
# It installs nothing outside its own compose project: its containers, its network, its volumes. It cannot
# see, reach or modify a SharpSight platform running beside it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
# One kit per machine, under one name. The variable exists so that an upgrade can be REHEARSED beside a kit in use
# (a second project, on another port) — not to run two kits for real: they would both want port 3002.
PROJECT="${DEVKIT_PROJECT:-sharpsight-superset-devkit}"

compose() { docker compose -p "$PROJECT" "$@"; }

FROM=""
case "${1:-}" in
  --stop)  compose down; echo "stopped — your dashboards and extracts are kept"; exit 0 ;;
  --erase) compose down -v; echo "erased — only this dev kit's volumes were deleted"; exit 0 ;;
  --from)  FROM="${2:?usage: bash install.sh --from <the folder the kit was installed from>}" ;;
esac

command -v docker >/dev/null || { echo "Docker is not installed or not on PATH." >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is installed but not running." >&2; exit 1; }

# 0 · AN UPGRADE BRINGS ITS SETTINGS WITH IT.
#     A new bundle unpacks into a new folder, which has no .env — and the database on this machine wants the
#     password that is in the OLD folder's .env, while every connection you saved is encrypted with the key
#     beside it. This used to stop with « its .env is gone » and leave the copying to whoever was upgrading.
#     The kit's own containers say where it was installed from (compose records the folder on each of them),
#     so the settings are fetched from there. Copied, never moved: the old folder stays a way back.
if [ ! -f .env ] && docker volume inspect "${PROJECT}_devkit-db" >/dev/null 2>&1; then
  previous="$FROM"
  if [ -z "$previous" ]; then
    previous="$(docker ps -a --filter "label=com.docker.compose.project=$PROJECT" \
                  --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null \
                | grep -v '^$' | sort | uniq -c | sort -rn | sed -n '1s/^ *[0-9]* *//p')"
  fi
  # Docker Desktop records a Windows path; Git Bash wants its own spelling of it
  if [ -n "$previous" ] && command -v cygpath >/dev/null 2>&1; then previous="$(cygpath -u "$previous")"; fi
  if [ -n "$previous" ] && [ -f "$previous/.env" ] && [ "$(cd "$previous" && pwd)" != "$HERE" ]; then
    echo "upgrade — the kit on this machine was installed from $previous"
    cp -p "$previous/.env" .env && chmod 600 .env
    brought=".env"
    if [ -s "$previous/cem.env" ] && [ ! -s cem.env ]; then cp -p "$previous/cem.env" cem.env && chmod 600 cem.env; brought="$brought, cem.env"; fi
    for folder in driver-jars krb5 exports; do
      [ -d "$previous/$folder" ] || continue
      mkdir -p "$folder"
      n=0
      for file in "$previous/$folder"/* "$previous/$folder"/.[!.]*; do
        [ -e "$file" ] || continue
        [ -e "$folder/$(basename "$file")" ] && continue          # what this bundle ships is not overwritten
        cp -pr "$file" "$folder/" && n=$((n + 1))
      done
      [ "$n" = 0 ] || brought="$brought, $n file(s) of $folder/"
    done
    echo "  brought here: $brought   (copied — the old folder is left as it was)"
  elif [ -n "$FROM" ]; then
    echo "refusing: --from $FROM holds no .env — that is not the folder the kit was installed from." >&2
    exit 1
  fi
fi

# 1 · the images, if this is a bundle rather than a source checkout.
#     The IMAGE TAG NEVER CHANGES between versions, so "is it already there?" is the wrong question: unpacking a
#     newer bundle over an older kit would keep the old image and quietly run last week's code. The question is
#     whether THESE images were loaded, which the archive's own checksum answers; it is remembered beside them.
up_flags=()
if [ -f images/devkit-images.tar.gz ]; then
  want="$(sha256sum images/devkit-images.tar.gz | cut -d' ' -f1)"
  have="$(cat .images-loaded 2>/dev/null || true)"
  if [ "$want" != "$have" ] || ! docker image inspect sharpsight-superset-devkit:1.0.0 >/dev/null 2>&1; then
    echo "loading images (this takes a minute) …"
    gunzip -c images/devkit-images.tar.gz | docker load
    echo "$want" > .images-loaded
    # An upgrade must actually restart on the new image, not keep the containers made from the old one. It is
    # done at step 4 and NOT here, as it used to be: recreating here ran Superset's migrations before the
    # database had been backed up or counted.
    up_flags+=(--force-recreate)
  fi
  # A BUNDLE IS THE KIT AND NOTHING ELSE. The SharpSight menu and its AI BI engine are an overlay of the source
  # checkout (docker-compose.ai-bi.yaml) and are not shipped. A kit that ran them and is upgraded from a bundle
  # would keep the engine's containers running beside a Superset that no longer knows them; they are removed
  # with everything else this bundle does not define. What the engine MADE inside Superset — its dashboards and
  # its two connections — is content, and stays, like yours.
  up_flags+=(--remove-orphans)
fi

# 2 · the credentials, generated once, never shipped
if [ ! -f .env ]; then
  # A database volume from an EARLIER install outlives its .env. PostgreSQL only reads POSTGRES_PASSWORD when
  # it creates the data directory, so generating a fresh password here would leave every connection refused by
  # a database that still wants the old one — with nothing on screen to say why.
  if docker volume inspect "${PROJECT}_devkit-db" >/dev/null 2>&1; then
    cat >&2 <<'MSG'
refusing: this machine already holds a dev kit database, but its .env is gone.

The password PostgreSQL wants was in that file, and it cannot be guessed. An upgrade fetches it from the folder
the kit was installed from — and that folder could not be found, or holds no .env any more. Either:
  · name it:   bash install.sh --from <the folder the kit was installed from>
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

# 2a · AN OLDER .env MEETS A NEWER KIT. Two things, and neither may touch a value somebody chose:
#      · a setting this version finally HEARS. DEVKIT_DATA_CACHE_TTL=60 was in every .env the kit ever wrote, and
#        compose never passed it, so it meant nothing. It is passed now — and left alone, that inert 60 would
#        turn the one-hour cache of this version back into one minute on every upgraded kit. It becomes 3600
#        once, when the .env is older than settings version 2; set it to anything afterwards and it is yours.
#      · settings this version ADDS (.env.example has them, the old .env does not): appended with their
#        defaults, so that they are there to be read and changed. A line you commented out is not added back.
edit_env() { sed -E "$1" .env > .env.tmp && cat .env.tmp > .env && rm -f .env.tmp; }
level="$(sed -n 's/^DEVKIT_SETTINGS_VERSION=//p' .env | tail -1 | tr -d '[:space:]')"
# An .env from before this setting existed has no such line: that IS level 1. (The first version of this test
# left the variable empty in exactly that case, `[ "" -lt 2 ]` failed quietly, and the one upgrade it was written
# for was skipped — found by rehearsing an upgrade from the bundle of 3 October, 2026-10-05.)
case "$level" in ''|*[!0-9]*) level=1 ;; esac
if [ "$level" -lt 2 ]; then
  if grep -qE '^DEVKIT_DATA_CACHE_TTL=60[[:space:]]*$' .env; then
    edit_env 's/^DEVKIT_DATA_CACHE_TTL=60[[:space:]]*$/DEVKIT_DATA_CACHE_TTL=3600/'
    echo "settings — the chart cache goes from 60 s to one hour (the 60 in .env was never applied; change DEVKIT_DATA_CACHE_TTL to keep less)"
  fi
  if grep -qE '^DEVKIT_SETTINGS_VERSION=' .env; then edit_env 's/^DEVKIT_SETTINGS_VERSION=.*/DEVKIT_SETTINGS_VERSION=2/'
  else printf '\n# which of the kit'"'"'s settings changes this file has been through (install.sh)\nDEVKIT_SETTINGS_VERSION=2\n' >> .env; fi
  level=2
fi
#      · settings version 3 (2026-10-15): two workers was every .env's default, sized for a laptop. On a machine
#        with at least 4 CPUs and 8 GB of memory a DEVKIT_WORKERS=2 that is still the old default becomes 4; on a
#        smaller one it stays, and says so. Any other value was chosen and is not touched.
if [ "$level" -lt 3 ]; then
  cpus="$(nproc 2>/dev/null || echo 1)"
  mem_gb="$(awk '/^MemTotal:/ {print int($2/1048576)}' /proc/meminfo 2>/dev/null || echo 0)"
  if grep -qE '^DEVKIT_WORKERS=2[[:space:]]*$' .env; then
    if [ "${cpus:-1}" -ge 4 ] && [ "${mem_gb:-0}" -ge 8 ]; then
      edit_env 's/^DEVKIT_WORKERS=2[[:space:]]*$/DEVKIT_WORKERS=4/'
      echo "settings — Superset gets 4 workers instead of 2 (this machine: $cpus CPUs, ${mem_gb} GB)"
    else
      echo "settings — Superset keeps 2 workers: this machine has $cpus CPUs and ${mem_gb} GB (4 workers want 4 CPUs and 8 GB)"
    fi
  fi
  edit_env 's/^DEVKIT_SETTINGS_VERSION=.*/DEVKIT_SETTINGS_VERSION=3/'
fi
if [ -f .env.example ]; then
  added=""
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"
    case "$key" in "$line"|*[!A-Za-z0-9_]*|'') continue ;; esac
    grep -qE "^#?[[:space:]]*$key=" .env && continue
    [ -n "$added" ] || printf '\n# added by install.sh on %s: settings this version of the kit knows\n' "$(date +%Y-%m-%d)" >> .env
    printf '%s\n' "$line" >> .env
    added="$added $key"
  done < .env.example
  [ -z "$added" ] || echo "settings — added to .env with their defaults:$added"
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

# 3b · BEFORE A NEW VERSION TOUCHES IT: count what is there, and back it up.
#      Superset migrates its own database at every start (`superset db upgrade`, in devkit-init). On the day the
#      images changed, that is the moment something made here could be lost — so what is there is counted first
#      and counted again afterwards, and the whole of Superset's database is dumped where it can be put back.
#      Only then: an ordinary start changes no code, and a backup at every start would bury the one that matters.
metadata() { compose exec -T devkit-db psql -X -q -At -U devkit -d superset -c "$1" 2>/dev/null | tr -d '\r'; }
COUNTS="select (select count(*) from dbs)||' '||(select count(*) from dashboards)||' '||(select count(*) from slices)||' '||(select count(*) from tables)||' '||(select count(*) from saved_query)"
upgrading=0; kept_before=""; schema_before=""; backup=""
case " ${up_flags[*]:-} " in *" --force-recreate "*)
  kept_before="$(metadata "$COUNTS" | tail -1 || true)"
  if [ -n "$kept_before" ]; then                # a database Superset has already been in
    upgrading=1
    schema_before="$(metadata 'select version_num from alembic_version' | head -1 || true)"
    mkdir -p backups
    backup="backups/superset-metadata-$(date +%Y%m%d-%H%M%S).dump"
    if compose exec -T devkit-db pg_dump -U devkit -d superset -Fc > "$backup" 2>/dev/null && [ -s "$backup" ]; then
      echo "backup — Superset's own database as it stands, before the new version starts: $backup ($(du -h "$backup" | cut -f1))"
    else
      rm -f "$backup"
      echo "refusing: Superset's database could not be backed up, so the new version is not started on it." >&2
      echo "          (nothing was changed; the kit that was running is as it was)" >&2
      exit 1
    fi
  fi ;;
esac

# 4 · up
compose up -d ${up_flags[@]+"${up_flags[@]}"}
# what the sync service said about cem.env: it runs inside compose, so its line would otherwise sit in the logs
# where nobody looks — and « the CEM is not configured » is exactly the sentence someone needs to read.
compose logs --no-log-prefix devkit-cem-sync 2>/dev/null | grep -E "^cem\.env" | tail -1 || true
port="$(grep -E '^DEVKIT_PORT=' .env | cut -d= -f2)"; port="${port:-3002}"
bind="$(grep -E '^DEVKIT_BIND=' .env | cut -d= -f2)"; bind="${bind:-127.0.0.1}"

echo -n "waiting for Superset to answer"
answered=0
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then answered=1; break; fi
  echo -n "."; sleep 5
done
echo
if [ "$answered" != 1 ]; then
  echo "it did not answer in five minutes. What it says for itself:" >&2
  compose logs --tail 40 superset-devkit >&2
  [ -z "$backup" ] || echo "the backup taken before this start is $HERE/$backup" >&2
  exit 1
fi

# 5 · WHAT AN UPGRADE KEPT, COUNTED. « It still starts » is not the answer to « are my dashboards still there ».
if [ "$upgrading" = 1 ]; then
  kept_after="$(metadata "$COUNTS" | tail -1 || true)"
  schema_after="$(metadata 'select version_num from alembic_version' | head -1 || true)"
  echo "upgrade — what was there before this version started, and what is there now:"
  lost=0; i=0
  for name in connections dashboards charts datasets "saved queries"; do
    i=$((i + 1))
    b="$(printf '%s' "$kept_before" | cut -d' ' -f"$i")"; a="$(printf '%s' "$kept_after" | cut -d' ' -f"$i")"
    printf '    %-14s %5s  ->  %-5s %s\n' "$name" "${b:-?}" "${a:-?}" "$([ "${a:-0}" -lt "${b:-0}" ] 2>/dev/null && echo '  <-- FEWER THAN BEFORE')"
    [ "${a:-0}" -lt "${b:-0}" ] 2>/dev/null && lost=1
  done
  if [ "$schema_before" = "$schema_after" ]; then echo "    Superset's schema was already current (${schema_after:-unknown}): nothing to migrate"
  else echo "    Superset's schema was migrated: ${schema_before:-none} -> ${schema_after:-unknown}"; fi
  # every saved connection's password is encrypted with the key in .env: said by the start-up job, which reads them
  compose logs --no-log-prefix devkit-init 2>/dev/null | grep -E "^ *connections: " | tail -1 || true
  if [ "$lost" = 1 ] || [ -z "$kept_after" ]; then
    cat >&2 <<MSG

SOMETHING IS MISSING AFTER THE UPGRADE. Nothing was deleted by this script; Superset's database as it was is in
  $HERE/$backup
To put it back (the kit stays on the new version):
  docker compose -p $PROJECT exec -T devkit-db pg_restore -U devkit -d superset --clean --if-exists < "$backup"
  docker compose -p $PROJECT restart superset-devkit
MSG
    exit 1
  fi
  echo "    nothing is missing. The backup stays in $backup"
fi

echo "  ready — http://$bind:$port"
echo "    sign in, then SQL → SQL Lab. Two sources are already connected:"
echo "      · CEM live      the cluster, queried where it stands — nothing is copied"
echo "      · Dev kit store this kit's own PostgreSQL, where your extracts and CREATE TABLE AS land"
echo "    Three starting queries are in SQL → Requêtes enregistrées."
echo "    To add SQL Server or Oracle: Paramètres → Bases de données → + (a form, not a URI)."
