#!/usr/bin/env bash
# The SharpSight AI BI engine, on top of the dev kit.
#
#   bash ai-bi.sh              start the kit and the engine (first use: writes the engine's secrets into .env)
#   bash ai-bi.sh demo         the brief's request, end to end, as dev.user
#   bash ai-bi.sh request "Create a … dashboard using the … template. …"      (generate is the same command)
#   bash ai-bi.sh validate <project>                       what was edited in Superset since: taken, refused, set back
#   bash ai-bi.sh commit <project> ["what changed"]        take it: a new version, validated, awaiting review
#   bash ai-bi.sh approve <project> UAT                    as lead.reviewer
#   bash ai-bi.sh approve <project> PROD "what was checked"   as release.manager — a second person, on purpose
#   bash ai-bi.sh audit [project]                          the audit trail, and whether its chain is intact
#   bash ai-bi.sh discard <project>                        a project still in DEV, taken away
#   bash ai-bi.sh people                                   Superset accounts given the roles rbac.yaml says
#   bash ai-bi.sh person <rbac user>                       a Superset account for someone who has none yet
#   bash ai-bi.sh check-adapter <project>                  every road of the Superset adapter, on a copy
#   bash ai-bi.sh compatibility                            the compatibility matrix, beside what is running
#   bash ai-bi.sh where                                    where to look: everything is shown in Superset
#   bash ai-bi.sh test                                     the engine's own tests, inside its container
#   bash ai-bi.sh --stop       stop the engine, leave the kit running
#   bash ai-bi.sh --erase      stop the engine and delete ITS two volumes (the kit's are untouched)
#
# SUPERSET IS THE INTERFACE. The engine has no page of its own: what it produces and what it knows are in
# Superset — its dashboards, a SharpSight menu in the top bar, the gate of each project as a tag in the
# dashboard list. This script is where a dashboard is asked for and approved.
#
# It adds containers to the kit's compose project and nothing anywhere else. Like the kit, it cannot see or
# reach a SharpSight platform running on the same machine.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
PROJECT=sharpsight-superset-devkit
ENGINE=(aibi-db-init dbt-runner aibi-superset-init sharpsight-ai-bi)

compose() { docker compose -p "$PROJECT" -f docker-compose.yaml -f docker-compose.ai-bi.yaml "$@"; }
# A container path inside `sh -c`, so Git Bash cannot rewrite it on the way (docs/lessons.md).
cli() { compose exec -T sharpsight-ai-bi sh -c 'python -m aibi.cli "$@"' cli "$@"; }

case "${1:-}" in
  --stop)
    compose stop "${ENGINE[@]}" >/dev/null 2>&1 || true
    compose rm -f "${ENGINE[@]}" >/dev/null 2>&1 || true
    echo "the engine is stopped — the kit, its dashboards and the engine's own history are kept"; exit 0 ;;
  --erase)
    compose stop "${ENGINE[@]}" >/dev/null 2>&1 || true
    compose rm -f "${ENGINE[@]}" >/dev/null 2>&1 || true
    docker volume rm "${PROJECT}_aibi-state" "${PROJECT}_aibi-dbt-prod" >/dev/null 2>&1 || true
    echo "erased — the engine's two volumes only (its records and dbt's production artifacts)."
    echo "Still there, in the kit's own Superset and database: the dashboards it created with their gate tags,"
    echo "the two SharpSight dashboards, the schemas bi / bi_dev / bi_factory, and the volume holding dbt's"
    echo "documentation (Superset has it mounted; the next build rewrites it). The next start empties bi_factory:"
    echo "the two dashboards then show an engine that knows no project."; exit 0 ;;
  demo)     shift; cli demo "$@"; exit $? ;;
  request|generate) shift; cli generate "$@"; exit $? ;;
  validate) cli validate "${2:?usage: ai-bi.sh validate <project>}"; exit $? ;;
  commit)   cli commit "${2:?usage: ai-bi.sh commit <project> [what was changed, in a sentence]}" --comment "${3:-}"; exit $? ;;
  approve)
    project="${2:?usage: ai-bi.sh approve <project> UAT|PROD [comment]}"; target="${3:?UAT or PROD}"
    if [ "$target" = "PROD" ]; then
      cli approve "$project" PROD --user release.manager --confirm --comment "${4:?production needs a comment: what was checked in UAT}"
    else
      cli approve "$project" UAT --user lead.reviewer --comment "${4:-reviewed}"
    fi; exit $? ;;
  audit)    if [ -n "${2:-}" ]; then cli audit --user lead.reviewer --project "$2"; else cli audit --user lead.reviewer; fi; exit $? ;;
  discard)  cli discard "${2:?usage: ai-bi.sh discard <project>}"; exit $? ;;
  people)   cli people --user release.manager; exit $? ;;
  compatibility) cli compatibility; exit $? ;;
  where)
    bind="$(grep -E '^DEVKIT_BIND=' .env 2>/dev/null | cut -d= -f2 || true)"
    bi_port="$(grep -E '^DEVKIT_PORT=' .env 2>/dev/null | cut -d= -f2 || true)"
    bi="http://${bind:-127.0.0.1}:${bi_port:-3002}"
    echo "Everything is in Superset:  $bi   (sign in as the kit's administrator)"
    echo "  Dashboards, Charts    each project's are tagged (env:UAT, gate:UAT, project:… ), and « certified » at PROD"
    echo "  SharpSight ▸ BI Factory        projects, gates, the checks of each validation      $bi/superset/dashboard/sharpsight-bi-factory/"
    echo "  SharpSight ▸ Data Quality      governed datasets, dbt models and their tests       $bi/superset/dashboard/sharpsight-data-quality/"
    echo "  SharpSight ▸ Impact Analysis   what a change to a dataset or a metric would touch  $bi/superset/dashboard/sharpsight-impact-analysis/"
    echo "  SharpSight ▸ Releases          what was approved, by whom, and what shipped        $bi/superset/dashboard/sharpsight-releases/"
    echo "  SharpSight ▸ Audit trail       every action, by whom, how it ended                 $bi/superset/dashboard/sharpsight-audit-trail/"
    echo "  SharpSight ▸ Data models       dbt's own documentation and lineage                 $bi/sharpsight/dbt-docs/"
    echo "A person sees the entries they may open (registry/security/rbac.yaml); an administrator sees all six."
    echo "  SQL ▸ SQL Lab                  the SharpSight panel, on the right: project, gate, Validate, Commit   $bi/sqllab/"
    echo "                                 (for an account named like a user of rbac.yaml:  bash ai-bi.sh person dev.user)"
    echo "Asking and approving are here:  bash ai-bi.sh request \"…\"   ·   bash ai-bi.sh approve <project> UAT|PROD"
    echo "A chart is edited in Superset's own Explore; then:  bash ai-bi.sh validate <project>   ·   bash ai-bi.sh commit <project>"
    exit 0 ;;
  person)   cli person "${2:?usage: ai-bi.sh person <a user of registry/security/rbac.yaml>}" --user release.manager; exit $? ;;
  check-adapter)
    compose exec -T sharpsight-ai-bi sh -c 'python -m aibi.adapter_check "$@"' check "${2:?usage: ai-bi.sh check-adapter <project>}"; exit $? ;;
  test)     compose exec -T sharpsight-ai-bi sh -c 'cd /app && python -m pytest'; exit $? ;;
  ""|start) ;;
  *) echo "unknown: $1 — see the top of this file" >&2; exit 2 ;;
esac

command -v docker >/dev/null || { echo "Docker is not installed or not on PATH." >&2; exit 1; }
[ -f .env ] || { echo "the kit is not installed yet: run  bash install.sh  first." >&2; exit 1; }

# The engine's own secrets, added to the kit's .env ONCE. Hexadecimal on purpose: .env is interpolated by
# compose, and a `$` in a value arrives cut (docs/lessons.md). Nothing is printed.
rand() { openssl rand -hex 24; }
added=0
for name in AIBI_API_TOKEN AIBI_BUILDER_PASSWORD AIBI_DBT_DEV_PASSWORD AIBI_READER_PASSWORD \
            AIBI_RECORDS_READER_PASSWORD AIBI_SUPERSET_PASSWORD; do
  grep -qE "^${name}=.+" .env && continue
  if grep -qE "^${name}=" .env; then
    # the line is there, empty (a .env made from today's .env.example): fill it where it stands
    sed -e "s|^${name}=.*|${name}=$(rand)|" .env > .env.aibi && cat .env.aibi > .env && rm -f .env.aibi
  else
    # a .env written before the engine existed: add the lines at the end
    [ "$added" = 0 ] && printf '\n# ── SharpSight AI BI (written by ai-bi.sh) ──\n' >> .env
    printf '%s=%s\n' "$name" "$(rand)" >> .env
  fi
  added=1
done
[ "$added" = 0 ] || echo "wrote the engine's secrets into .env"
[ -f cem.env ] || { : > cem.env; chmod 600 cem.env; }

echo "starting the kit and the engine (the first build downloads a browser: allow a few minutes) …"
# Build the ENGINE's image only. `up --build` would rebuild the kit's own image too, and a rebuilt image —
# identical or not — makes compose recreate a Superset that was running perfectly well (seen 2026-10-03).
compose build dbt-runner sharpsight-ai-bi
compose up -d
port="$(grep -E '^AIBI_PORT=' .env | cut -d= -f2 || true)"; port="${port:-3003}"

echo -n "waiting for the engine to answer"
for _ in $(seq 1 90); do
  # ready means: the engine answers AND what Superset's SharpSight menu leads to has been made. The menu is
  # there as soon as Superset starts; its two dashboards are made by the engine a little later.
  if curl -fsS "http://127.0.0.1:$port/health" 2>/dev/null | grep -q '"dashboards_made"'; then
    echo; echo "  ready — the engine answers (http://127.0.0.1:$port/health). It has no page: look in Superset."
    # Superset's extension loader fails quietly: it starts without a bundle it could not load, or one whose
    # checksum is not the recorded one. The engine asks Superset what it loaded; said here if it is not there.
    if curl -fsS "http://127.0.0.1:$port/health" 2>/dev/null | grep -q '"extension": *null'; then
      echo "  BUT Superset did not load sharpsight-bi.supx: SQL Lab has no SharpSight panel. Its log says why:" >&2
      echo "    docker logs sharpsight-superset-devkit-superset-devkit-1 2>&1 | grep -i 'extension\\|sharpsight'" >&2
    fi
    echo "    next:  bash ai-bi.sh demo        then:  bash ai-bi.sh where"
    exit 0
  fi
  echo -n "."; sleep 5
done
echo
echo "it did not answer. What the one-shots and the service say:" >&2
compose logs --tail 25 aibi-db-init dbt-runner aibi-superset-init sharpsight-ai-bi >&2
exit 1
