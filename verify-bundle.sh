#!/usr/bin/env bash
# Check a bundle before it is shipped — and say what is in it, in words.
#
#   bash verify-bundle.sh dist/sharpsight-superset-devkit-20261003.tar.gz
#   INSTALL=1 bash verify-bundle.sh dist/…tar.gz      also installs it in a scratch folder and signs in
#
# What it asserts, and why each one has cost a round trip at least once:
#   · the checksum file matches, and names THIS tarball (each bundle verifies against its own, never another's)
#   · the archive carries every file install.sh needs, and the images archive is not truncated
#   · the manifest's `contains_secrets` agrees with what is actually inside
#   · no .env, no cem.env (unless deliberately included), no mart dump, nothing a customer owns
#   · every shell script inside is LF: a CRLF install.sh does not execute on Linux at all
# With INSTALL=1 it goes further: unpacks into a temporary folder, installs, waits for Superset to answer and
# signs in with the generated admin — the only proof that matters.
set -euo pipefail

ARCHIVE="${1:?usage: bash verify-bundle.sh <bundle.tar.gz>}"
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"
NAME="$(basename "$ARCHIVE" .tar.gz)"
PROJECT=sharpsight-superset-devkit
fail=0
ok()  { echo "  PASS  $1"; }
no()  { echo "  FAIL  $1"; fail=1; }

echo "== $NAME"

# ── the checksum, from the folder that holds it ───────────────────────────────────────────────────────────────
if [ -f "$ARCHIVE.sha256" ]; then
  ( cd "$(dirname "$ARCHIVE")" && sha256sum -c "$(basename "$ARCHIVE").sha256" >/dev/null 2>&1 ) \
    && ok "checksum matches  ($(awk '{print substr($1,1,16)}' "$ARCHIVE.sha256")…)" \
    || no "checksum does NOT match — the copy is damaged, or it is a different build under the same name"
  grep -q "$(basename "$ARCHIVE")" "$ARCHIVE.sha256" \
    && ok "the checksum file names this tarball" \
    || no "the .sha256 names another file — each bundle verifies against its own"
else
  no "no $ARCHIVE.sha256 beside it — ship both files, always"
fi

# ── what is inside ────────────────────────────────────────────────────────────────────────────────────────────
listing="$(tar -tzf "$ARCHIVE")"
for needed in install.sh docker-compose.yaml .env.example cem.env.example README.md MANIFEST.txt \
              images/devkit-images.tar.gz initdb/10-devmart.sql import-into-devkit.sh export-for-devkit.sh \
              nginx/default.conf.template; do
  printf '%s\n' "$listing" | grep -q "^$NAME/$needed$" \
    && ok "carries $needed" || no "MISSING $needed — install.sh would fail on the target"
done

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
tar -xzf "$ARCHIVE" -C "$work" --exclude="*/images/*"
R="$work/$NAME"

size="$(tar -tvzf "$ARCHIVE" | awk '/images\/devkit-images.tar.gz/ {print $3}')"
[ "${size:-0}" -gt 400000000 ] \
  && ok "the images archive is whole ($((size / 1024 / 1024)) MB)" \
  || no "the images archive is only ${size:-0} bytes — the save was interrupted"

# ── secrets ───────────────────────────────────────────────────────────────────────────────────────────────────
declared="$(sed -n 's/^contains_secrets=//p' "$R/MANIFEST.txt")"
has_cem=no; printf '%s\n' "$listing" | grep -q "^$NAME/cem.env$" && has_cem=yes
[ "$declared" = "$has_cem" ] \
  && ok "the manifest tells the truth about secrets (contains_secrets=$declared)" \
  || no "the manifest says contains_secrets=$declared but cem.env present=$has_cem"
[ "$has_cem" = yes ] && echo "  NOTE  this tarball CARRIES AN ACCOUNT AND ITS PASSWORD — handle it like a password file"
printf '%s\n' "$listing" | grep -qE "^$NAME/\.env$" \
  && no ".env is inside — that is this kit's own passwords; never ship it" \
  || ok "no .env inside"
printf '%s\n' "$listing" | grep -qE "\.(dump|tgz)$" \
  && no "a dump or archive is inside — customer data does not travel in a bundle" \
  || ok "no mart dump inside"

# ── the kit, and nothing else ─────────────────────────────────────────────────────────────────────────────────
# The SharpSight menu and its AI BI engine are an overlay of the source checkout. A bundle that is deployed must
# not carry them (owner, 2026-10-05) — not their compose file, not their code, not their image — and « it never
# has » is a habit of the build script, not a guarantee. This is the guarantee.
engine_parts="$(printf '%s\n' "$listing" | grep -E "^$NAME/(docker-compose\.ai-bi\.yaml|ai-bi\.sh|superset-addon/|superset-extension/|registry/|dbt/|services/)" | head -3 || true)"
[ -z "$engine_parts" ] \
  && ok "no SharpSight menu, no AI BI engine: none of the overlay's files is inside" \
  || no "the AI BI engine's files are inside ($(printf '%s' "$engine_parts" | tr '\n' ' ')) — this bundle is the kit only"
if grep -qiE 'sharpsight-ai-bi|superset_config_ai_bi|sharpsight_bi' "$R/docker-compose.yaml" "$R/install.sh" 2>/dev/null; then
  no "docker-compose.yaml or install.sh inside names the AI BI engine — a deployed kit must start without it"
else
  ok "the compose file and the installer inside start Superset without the engine"
fi
grep -qiE 'AIBI_|ai-bi' "$R/.env.example" "$R/README.md" 2>/dev/null \
  && no "the .env.example or the README inside still names the AI BI engine — install.sh would write its settings into a deployed .env" \
  || ok "neither the settings template nor the README inside mentions the engine"
grep -qiE '^(image|dependencies)=.*ai-bi' "$R/MANIFEST.txt" \
  && no "the manifest lists the engine's image" \
  || ok "the manifest lists no engine image ($(sed -n 's/^bi_engine=//p' "$R/MANIFEST.txt" | head -1 | sed 's/^$/bi_engine line absent/'))"

# ── line endings ──────────────────────────────────────────────────────────────────────────────────────────────
# One grep, not a shell per file: the per-file form forks hundreds of times and Git Bash gives up on it.
# `|| true` is not decoration: grep finding NOTHING exits 1, and under `set -e` with `pipefail` that ends the
# script — silently, right where the good news was (met while writing this, 2026-10-03).
crlf="$( { grep -rlU --include='*.sh' "$(printf '\r')" "$R" 2>/dev/null || true; } | wc -l)"
[ "$crlf" = 0 ] \
  && ok "every shell script inside is LF" \
  || no "$crlf script(s) carry CRLF — bash on the target answers « \\r: command not found »"

echo "  —  manifest:"; sed 's/^/     /' "$R/MANIFEST.txt"

# ── the only proof that matters ───────────────────────────────────────────────────────────────────────────────
if [ "${INSTALL:-0}" = "1" ]; then
  echo "== installing it in a scratch folder (this removes the kit currently on this machine)"
  docker ps -a --filter "label=com.docker.compose.project=$PROJECT" -q | xargs -r docker rm -f >/dev/null
  docker volume ls --filter "label=com.docker.compose.project=$PROJECT" -q | xargs -r docker volume rm >/dev/null
  scratch="$work/install"; mkdir -p "$scratch"
  tar -xzf "$ARCHIVE" -C "$scratch"
  ( cd "$scratch/$NAME" && bash install.sh ) | sed 's/^/     /'
  port="$(sed -n 's/^DEVKIT_PORT=//p' "$scratch/$NAME/.env")"; port="${port:-3002}"
  user="$(sed -n 's/^DEVKIT_ADMIN_USER=//p' "$scratch/$NAME/.env")"; user="${user:-admin}"
  pass="$(sed -n 's/^DEVKIT_ADMIN_PASSWORD=//p' "$scratch/$NAME/.env")"
  token="$(curl -s -X POST "http://127.0.0.1:$port/api/v1/security/login" -H 'Content-Type: application/json' \
            -d "{\"username\":\"$user\",\"password\":\"$pass\",\"provider\":\"db\",\"refresh\":true}" \
            | sed -n 's/.*"access_token": *"\([^"]*\)".*/\1/p')"
  [ -n "$token" ] && ok "it installs and signs in on port $port" || no "installed, but the sign-in failed"
  ( cd "$scratch/$NAME" && bash install.sh --stop >/dev/null 2>&1 ) || true
fi

echo
[ "$fail" = 0 ] && echo "== the bundle is fit to ship" || echo "== DO NOT SHIP THIS BUNDLE"
exit "$fail"
