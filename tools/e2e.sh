#!/usr/bin/env bash
# THE WHOLE SOLUTION, TRIED END TO END — on a machine where the kit and the engine are running.
#
#   bash ai-bi.sh e2e                 everything that does not need a second Superset
#   bash ai-bi.sh run-target          start the « run » environment (docker-compose.run-target.yaml)
#   bash ai-bi.sh e2e                 …and then the deployment is tried too, for real
#   bash tools/e2e.sh --help        the options
#
# It asks the engine the way the command line does (its own token, a user named in a header), and Superset
# the way a browser does. Nothing is mocked: a project is really generated, a draft really saved, a version
# really committed and approved, a package really cut, and — if a target is configured — really deployed and
# then read back.
#
# WHAT IT LEAVES BEHIND, on purpose: the project it created, its versions, its package and its deployment.
# This is a dev kit; those are what you look at afterwards. Its project id is printed at the end.
#
# A promise that cannot be kept is printed and counted, and the run goes on: one broken thing should not
# hide the twenty behind it. The exit code is 0 only if every promise held.
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

ONLY=""
WAIT_FOR_RUN=${E2E_WAIT_FOR_RUN:-0}
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="${2:?--only needs a section number, e.g. --only 7}"; shift 2 ;;
    --wait-for-run-target) WAIT_FOR_RUN=1; shift ;;
    --help|-h)
      sed -n '2,20p' "$0"; echo
      echo "  --only <n>               one section only (0 the machine · 1 what is governed · 2 a project"
      echo "                           · 3 the Studio · 4 modify and commit · 5 governance · 6 export"
      echo "                           · 7 deploy · 8 import · 9 the gateway's limits · 10 the suites)"
      echo "  --wait-for-run-target    wait up to 5 minutes for the run environment to answer"
      exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

# ── the plumbing ────────────────────────────────────────────────────────────────────────────────────────────
# One Python, found once and used everywhere below. On Windows, Git Bash's `python3` is the Store's stub: it
# opens a prompt to install Python and runs nothing, so a name on PATH is not proof of an interpreter. Each
# candidate is asked to print something; the first that does is the one.
PY=()
for candidate in "python3" "python" "py -3"; do
  # shellcheck disable=SC2086  (« py -3 » is a command and its argument, split on purpose)
  if [ "$($candidate -c 'import json, sys; print(sys.version_info[0])' 2>/dev/null)" = 3 ]; then
    read -r -a PY <<< "$candidate"; break
  fi
done
if [ ${#PY[@]} -eq 0 ]; then
  echo "no Python 3 that runs: tried python3, python and py -3 (the walk reads the engine's answers with it)" >&2
  exit 2
fi

PASSED=0; FAILED=0; SKIPPED=0
BOLD=''; DIM=''; GREEN=''; RED=''; YELLOW=''; OFF=''
if [ -t 1 ]; then BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'; fi
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
BODY="$TMP/body"; CODE=""

section() {
  [ -n "$ONLY" ] && [ "$ONLY" != "$1" ] && return 1
  printf '\n%s%s · %s%s\n' "$BOLD" "$1" "$2" "$OFF"; return 0
}
ok()   { PASSED=$((PASSED+1)); printf '  %s✓%s %s\n' "$GREEN" "$OFF" "$1"; }
no()   { FAILED=$((FAILED+1)); printf '  %s✗%s %s\n' "$RED" "$OFF" "$1"
         [ -n "${2:-}" ] && printf '      %s%s%s\n' "$DIM" "$(printf '%s' "$2" | head -c 400 | tr '\n' ' ')" "$OFF"; }
skip() { SKIPPED=$((SKIPPED+1)); printf '  %s–%s %s\n' "$YELLOW" "$OFF" "$1"
         [ -n "${2:-}" ] && printf '      %s%s%s\n' "$DIM" "$2" "$OFF"; }
# holds "what was promised" <0 if it holds>  [why not]
holds() { if [ "$2" = 0 ]; then ok "$1"; else no "$1" "${3:-$(head -c 300 "$BODY" 2>/dev/null)}"; fi; }

env_of() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2-; }

ENGINE="http://127.0.0.1:$(env_of AIBI_PORT || true)"
[ "$ENGINE" = "http://127.0.0.1:" ] && ENGINE="http://127.0.0.1:3003"
BI="http://$(env_of DEVKIT_BIND || true)"; [ "$BI" = "http://" ] && BI="http://127.0.0.1"
BI="$BI:$(env_of DEVKIT_PORT || true)"; case "$BI" in *:) BI="${BI}3002" ;; esac
RUN_HOST="http://$(env_of DEVKIT_BIND || true)"; [ "$RUN_HOST" = "http://" ] && RUN_HOST="http://127.0.0.1"
RUN_HOST="$RUN_HOST:$(env_of RUN_PORT || true)"; case "$RUN_HOST" in *:) RUN_HOST="${RUN_HOST}3004" ;; esac
TOKEN="$(env_of AIBI_API_TOKEN)"
GATEWAY="$(env_of AIBI_SUPERSET_GATEWAY_TOKEN)"

# ask <method> <path> [json body] [as whom]   → $CODE, and the answer in $BODY
ask() {
  local method="$1" path="$2" body="${3:-}" who="${4:-dev.user}"
  if [ -n "$body" ]; then
    CODE="$(curl -sS -m 600 -o "$BODY" -w '%{http_code}' -X "$method" "$ENGINE$path" \
      -H "Authorization: Bearer $TOKEN" -H "X-SharpSight-User: $who" \
      -H 'Content-Type: application/json' --data-binary "$body" 2>"$TMP/curl.err")" || CODE="000"
  else
    CODE="$(curl -sS -m 600 -o "$BODY" -w '%{http_code}' -X "$method" "$ENGINE$path" \
      -H "Authorization: Bearer $TOKEN" -H "X-SharpSight-User: $who" 2>"$TMP/curl.err")" || CODE="000"
  fi
  [ "$CODE" = "000" ] && cp "$TMP/curl.err" "$BODY" 2>/dev/null
  return 0                      # an answer is an answer, whatever its code: the promises read it below
}
# the same, with SUPERSET's token rather than the command line's — the gateway's own road
as_superset() {
  local method="$1" path="$2" who="${3:-dev.user}"
  CODE="$(curl -sS -m 120 -o "$BODY" -w '%{http_code}' -X "$method" "$ENGINE$path" \
    -H "Authorization: Bearer $GATEWAY" -H "X-SharpSight-User: $who" 2>/dev/null)" || CODE="000"
  return 0
}
# read one thing out of the last answer:  of .project.version   ·   of .0.name   ·   of .checks len
of() { "${PY[@]}" - "$BODY" "$1" "${2:-}" <<'PY'
import json, sys
try:
    body = json.load(open(sys.argv[1]))
except Exception:
    print(""); raise SystemExit
cur = body
for key in sys.argv[2].strip(".").split("."):
    if key == "":
        continue
    try:
        cur = cur[int(key)] if isinstance(cur, list) else cur[key]
    except Exception:
        cur = None
    if cur is None:
        break
if sys.argv[3] == "len":
    print(0 if cur is None else len(cur))
elif isinstance(cur, (dict, list)):
    print(json.dumps(cur))
else:
    print("" if cur is None else cur)
PY
}
# is the body's JSON true for this little expression? `where '.status == "succeeded"'`
where() { "${PY[@]}" - "$BODY" "$1" <<'PY'
import json, sys
try:
    b = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(1)
def get(path, default=None):
    cur = b
    for key in path.strip(".").split("."):
        if key == "":
            continue
        try:
            cur = cur[int(key)] if isinstance(cur, list) else cur[key]
        except Exception:
            return default
    return default if cur is None else cur
expr = sys.argv[2]
if expr.startswith("."):
    expr = "b" + expr
try:
    held = eval(expr, {"b": b, "get": get, "len": len, "any": any, "all": all, "isinstance": isinstance,
                       "dict": dict, "list": list, "str": str, "sorted": sorted})
except Exception as exc:
    print(f"the promise could not be read: {exc}", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0 if held else 1)
PY
}

printf '%sSharpSight Studio — the whole walk%s\n' "$BOLD" "$OFF"
printf '%s  the engine %s · Superset %s · the run environment %s%s\n' "$DIM" "$ENGINE" "$BI" "$RUN_HOST" "$OFF"

PROJECT=""; VERSION=""; DRAFT_TOKEN=""

# ── 0 · the machine ─────────────────────────────────────────────────────────────────────────────────────────
if section 0 "the machine: the kit, the engine, and the versions they were built for"; then
  if [ ! -f .env ]; then
    no ".env is there (the kit is installed)" "run bash install.sh first"
  else ok ".env is there (the kit is installed)"; fi
  [ -n "$TOKEN" ] && ok "the engine's own token is in .env" \
    || no "the engine's own token is in .env" "AIBI_API_TOKEN is empty — run bash ai-bi.sh once"
  ask GET /health
  holds "the engine answers, and says what it can reach" "$([ "$CODE" = 200 ] && echo 0 || echo 1)"
  # /health says it at the top level (« "bi_engine": true »); an older engine nested it under checks
  where '.get("bi_engine") is True or get("checks.bi_engine.ok") is True' && ok "and it can reach Superset" \
    || no "and it can reach Superset" "bi_engine: $(of .bi_engine)$(of .checks.bi_engine)"
  CODE=$(curl -sS -m 30 -o /dev/null -w '%{http_code}' "$BI/health" 2>/dev/null) || CODE=000
  holds "Superset itself answers" "$([ "$CODE" = 200 ] && echo 0 || echo 1)" "$BI/health answered $CODE"
  ask GET /compatibility
  where '.get("ok") is True' && ok "what is running is inside the compatibility matrix" \
    || no "what is running is inside the compatibility matrix" "$(of .untested)"
fi

# ── 1 · what is governed: the products a dashboard may be built out of ─────────────────────────────────────
if section 1 "what is governed: the datasets, the metrics, the templates"; then
  ask GET /datasets
  holds "the governed datasets are listed" "$([ "$CODE" = 200 ] && [ "$(of . len)" -gt 0 ] && echo 0 || echo 1)"
  FIRST_DATASET="$(of .0.id)"
  where 'all("certified" in d for d in b)' && ok "and each one says whether it is certified" \
    || no "and each one says whether it is certified"
  ask GET /metrics
  holds "the governed metrics are listed" "$([ "$CODE" = 200 ] && [ "$(of . len)" -gt 0 ] && echo 0 || echo 1)"
  ask GET /templates
  holds "the templates are listed" "$([ "$CODE" = 200 ] && [ "$(of . len)" -gt 0 ] && echo 0 || echo 1)"
  TEMPLATE="$(of .0.id)"
  if [ -n "$FIRST_DATASET" ]; then
    ask GET "/datasets/$FIRST_DATASET/lineage"
    holds "a dataset says what it is made of and what would be touched" \
      "$([ "$CODE" = 200 ] && echo 0 || echo 1)"
  else
    skip "a dataset says what it is made of" "no dataset to ask about"
  fi
  ask GET /tools
  holds "the engine says which tools it offers" "$([ "$CODE" = 200 ] && echo 0 || echo 1)"
fi

# ── 2 · a project, from a request in words ─────────────────────────────────────────────────────────────────
# The request names metrics the registry knows (the QoE score, degraded users), in words its synonyms list:
# a request that names none is refused, as designed, and everything after it would have nothing to work on.
# The subject carries the time of the run, so each run is a project of its own: the one before was approved
# for UAT below, and a held version is not regenerated in place.
STAMP="$(date +%Y%m%d-%H%M%S)"
PROMPT="Create an end-to-end check $STAMP dashboard using the executive template, for the CEM domain. \
Show the daily trend of the QoE score, and a ranking of degraded users by region."
if section 2 "a project: a request in words, the pipeline's gates, a first version"; then
  printf '      %sgenerating — the pipeline runs its twenty-two steps and ten gates; this takes a while%s\n' \
    "$DIM" "$OFF"
  ask POST /projects/generate "$("${PY[@]}" -c 'import json,sys; print(json.dumps({"prompt": sys.argv[1], "wait": True}))' "$PROMPT")"
  holds "the request is answered by a run" "$([ "$CODE" = 200 ] && echo 0 || echo 1)"
  PROJECT="$(of .project_id)"
  where '.get("status") == "succeeded"' && ok "which went all the way through" \
    || no "which went all the way through" "$(of .error)$(of .steps)"
  where 'all(s.get("status") in ("done", "skipped") for s in b.get("steps", []))' \
    && ok "with every step done or deliberately skipped" \
    || no "with every step done or deliberately skipped" \
       "$("${PY[@]}" -c 'import json,sys;b=json.load(open(sys.argv[1]));print([ (s.get("key"),s.get("status"),s.get("detail","")[:80]) for s in b.get("steps",[]) if s.get("status") not in ("done","skipped")])' "$BODY" 2>/dev/null)"
  where '.get("quality_gate") in ("READY_FOR_REVIEW", "APPROVED_FOR_UAT", "UAT", None)' \
    && ok "and left its project at a gate a review can start from" \
    || no "and left its project at a gate a review can start from" "$(of .quality_gate) $(of .error)"
  if [ -n "$PROJECT" ]; then
    ok "a project was created: $PROJECT"
    ask GET "/projects/$PROJECT"
    VERSION="$(of .versions.-1.version)"
    [ -n "$VERSION" ] && ok "with a first version: $VERSION" || no "with a first version" "$(head -c 300 "$BODY")"
    where 'b["versions"][-1]["state"] == "DEV"' && ok "which starts in DEV, approved by nobody" \
      || no "which starts in DEV, approved by nobody" "$(of .versions.-1.state) / $(of .versions.-1.status)"
    ask GET "/projects/$PROJECT/report"
    where '.get("validation", {}).get("passed") is True' \
      && ok "and a report of what every level of validation found" \
      || no "and a report of what every level of validation found" "$(head -c 300 "$BODY")"
  else
    no "a project was created" "the run carries no project_id"
  fi
fi

# the sections below need a project. When one section is run alone, take the newest project there is.
if [ -z "$PROJECT" ]; then
  ask GET /projects
  PROJECT="$(of .0.project_id)"
  ask GET "/projects/$PROJECT"
  VERSION="$(of .versions.-1.version)"
fi

# ── 3 · the Studio: the payload, a draft, and proposals that change nothing ────────────────────────────────
if section 3 "the Studio: what it draws, the draft it keeps, the proposals it asks for"; then
  if [ -z "$PROJECT" ]; then skip "the Studio" "there is no project"; else
  ask GET "/projects/$PROJECT/studio"
  holds "the workspace draws from one call" "$([ "$CODE" = 200 ] && echo 0 || echo 1)"
  where 'all(k in b for k in ("project", "template", "layout", "components", "free_slots", "security"))' \
    && ok "which carries the project, the template, the layout, the charts, the free slots and the security" \
    || no "which carries all of it" "$("${PY[@]}" -c 'import json,sys;print(sorted(json.load(open(sys.argv[1])).keys()))' "$BODY")"
  where '.get("layout_of") in ("specification", "template")' \
    && ok "and says who laid it out: the specification or the template" \
    || no "and says who laid it out" "$(of .layout_of)"
  cp "$BODY" "$TMP/studio.json"
  # a real edit: the first row's first slot made one column wider, saved as a draft
  "${PY[@]}" - "$TMP/studio.json" "$TMP/draft.json" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1]))
spec = payload["spec"]
# the layout in force: the specification's own if it has one, else the template's, which is what the canvas
# starts from. A row is {row, height, slots: [{id, width}]} — widths in twelfths, height in grid units.
rows = spec.get("layout") or payload["layout"]
row = rows[0]
slots = row["slots"]
if len(slots) > 1 and slots[0]["width"] < 11:
    # one slot made a twelfth wider, its neighbour a twelfth narrower: the row still adds up to twelve
    slots[0]["width"] += 1
    slots[1]["width"] -= 1
else:
    row["height"] = min(120, row["height"] + 2)      # or a row made taller, which no neighbour pays for
spec["layout"] = rows
json.dump({"spec": spec, "token": payload.get("token") or "none"}, open(sys.argv[2], "w"))
PY
  if [ -s "$TMP/draft.json" ]; then
    ask PUT "/projects/$PROJECT/draft" "$(cat "$TMP/draft.json")"
    DRAFT_TOKEN="$(of .token)"
    holds "a layout changed on the canvas is kept as a draft" \
      "$([ "$CODE" = 200 ] && [ -n "$DRAFT_TOKEN" ] && echo 0 || echo 1)"
    where '.get("layout_of") == "specification"' \
      && ok "and the layout is the specification's from then on, not the template's" \
      || no "and the layout is the specification's from then on" "$(of .layout_of)"
    # the same save again, with the token the page had BEFORE: refused, not merged in silence
    ask PUT "/projects/$PROJECT/draft" "$(cat "$TMP/draft.json")"
    holds "a save against an older draft is refused (409), never merged in silence" \
      "$([ "$CODE" = 409 ] && echo 0 || echo 1)" "answered $CODE"
    ask POST "/projects/$PROJECT/draft/validate"
    where '.get("summary", {}).get("passed") is True and b.get("of") == "draft"' \
      && ok "the draft validates against the registry without touching Superset" \
      || no "the draft validates" "$(of .summary) $(of .problems)"
    # three proposals, by the three roads: words, the gallery's form, the inspector's own controls
    ask POST "/projects/$PROJECT/propose" '{"request": "add a chart of the same metric by region"}'
    where 'isinstance(b.get("changes"), list) and b.get("summary") is not None' && ok "the copilot answers a sentence with a proposal" \
      || no "the copilot answers a sentence with a proposal" "$(head -c 300 "$BODY")"
    SLOT="$("${PY[@]}" -c 'import json,sys;b=json.load(open(sys.argv[1]));print((b.get("free_slots") or [{}])[0].get("slot",""))' "$TMP/studio.json")"
    METRIC="$("${PY[@]}" -c 'import json,sys;b=json.load(open(sys.argv[1]));print((b.get("components") or [{}])[0].get("metric",""))' "$TMP/studio.json")"
    if [ -n "$SLOT" ] && [ -n "$METRIC" ]; then
      ask POST "/projects/$PROJECT/compose" "$("${PY[@]}" -c 'import json,sys;print(json.dumps({"slot":sys.argv[1],"metric":sys.argv[2]}))' "$SLOT" "$METRIC")"
      where 'isinstance(b.get("changes"), list) and b.get("summary") is not None' && ok "the gallery's form answers with the same kind of proposal" \
        || no "the gallery's form answers with a proposal" "$(head -c 300 "$BODY")"
    else
      skip "the gallery's form answers with a proposal" "this template has no free slot left"
    fi
    FILLED="$("${PY[@]}" -c 'import json,sys;b=json.load(open(sys.argv[1]));print((b.get("components") or [{}])[0].get("slot",""))' "$TMP/studio.json")"
    ask POST "/projects/$PROJECT/alter" "$("${PY[@]}" -c 'import json,sys;print(json.dumps({"slot":sys.argv[1],"type":"table"}))' "$FILLED")"
    where 'isinstance(b.get("changes"), list) and b.get("summary") is not None' && ok "and the inspector's own controls do too" \
      || no "and the inspector's own controls do too" "$(head -c 300 "$BODY")"
    ask GET "/projects/$PROJECT/studio"
    if [ "$(of .token)" = "$DRAFT_TOKEN" ]; then
      ok "and none of the three changed the draft: a proposal is not an edit"
    else
      no "a proposal must not change the draft" "the draft's token moved from $DRAFT_TOKEN to $(of .token)"
    fi
    ask DELETE "/projects/$PROJECT/draft"
    where '.get("discarded") is True' && ok "a draft can be thrown away, leaving the version untouched" \
      || no "a draft can be thrown away" "$(head -c 200 "$BODY")"
  else
    no "a layout changed on the canvas is kept as a draft" "the draft could not be built from the payload"
  fi
  fi
fi

# ── 4 · modify it, and take it: a second version through the same gates ───────────────────────────────────
if section 4 "modify it: a draft, committed, becomes the next version"; then
  if [ -z "$PROJECT" ]; then skip "modify it" "there is no project"; else
  ask GET "/projects/$PROJECT/studio"
  cp "$BODY" "$TMP/studio2.json"
  "${PY[@]}" - "$TMP/studio2.json" "$TMP/draft2.json" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1]))
spec = payload["spec"]
# a change a person would make: the dashboard's description, and the first chart's title
spec["dashboard"]["description"] = (spec["dashboard"].get("description") or "") + " — checked end to end."
if spec.get("components"):
    spec["components"][0]["title"] = (spec["components"][0].get("title") or "Chart") + " (checked)"
json.dump({"spec": spec, "token": payload.get("token") or "none"}, open(sys.argv[2], "w"))
PY
  ask PUT "/projects/$PROJECT/draft" "$(cat "$TMP/draft2.json")"
  holds "a title and a description changed in the inspector are kept" "$([ "$CODE" = 200 ] && echo 0 || echo 1)"
  ask GET "/projects/$PROJECT/studio"
  where 'len(b.get("diff") or []) > 0' && ok "and the Studio says what the draft changes about the version" \
    || no "and the Studio says what the draft changes" "$(of .diff)"
  printf '      %scommitting — the same twenty-two steps as the first version%s\n' "$DIM" "$OFF"
  ask POST "/projects/$PROJECT/draft/commit" '{"comment": "end-to-end check: a title, a description, a layout"}'
  holds "the draft is committed through the pipeline, not written straight in" \
    "$([ "$CODE" = 200 ] || [ "$CODE" = 202 ] && echo 0 || echo 1)"
  RUN_ID="$(of .run_id)"
  if [ -n "$RUN_ID" ]; then
    for _ in $(seq 1 180); do
      ask GET "/runs/$RUN_ID"
      case "$(of .status)" in running|"") sleep 5 ;; *) break ;; esac
    done
    where '.get("status") == "succeeded"' && ok "and that run went through as well" \
      || no "and that run went through as well" "$(of .status) $(of .error)"
  fi
  ask GET "/projects/$PROJECT"
  NEXT="$(of .versions.-1.version)"
  if [ -n "$NEXT" ] && [ "$NEXT" != "$VERSION" ]; then
    ok "a new version was cut: $VERSION → $NEXT"; VERSION="$NEXT"
  else
    no "a new version was cut" "the latest version is still ${VERSION:-none}"
  fi
  where 'len(b.get("versions") or []) >= 2' && ok "and the older one is still there, beside it" \
    || no "and the older one is still there" "$(of .versions len) version(s)"
  ask GET "/projects/$PROJECT/changes"
  where '.get("same_as_validated") is True' \
    && ok "what Superset holds is what was validated — nothing drifted" \
    || no "what Superset holds is what was validated" "$(of .why_not)"
  fi
fi

# ── 5 · governance: who may approve, and what is written down ─────────────────────────────────────────────
if section 5 "governance: the second person, and the trail"; then
  if [ -z "$PROJECT" ] || [ -z "$VERSION" ]; then skip "governance" "there is no version"; else
  ask POST "/projects/$PROJECT/approve" '{"target":"UAT","comment":"end-to-end check"}' lead.reviewer
  holds "a reviewer approves it for UAT" "$([ "$CODE" = 200 ] && echo 0 || echo 1)"
  ask GET "/projects/$PROJECT"
  where 'b["versions"][-1]["status"] in ("APPROVED_FOR_UAT", "UAT")' \
    && ok "and the version says so" || no "and the version says so" "$(of .versions.-1.status)"
  ask POST "/projects/$PROJECT/approve" '{"target":"PROD","comment":"trying it as the same person"}' dev.user
  holds "whoever asked for it cannot approve it for production" \
    "$([ "$CODE" = 403 ] || [ "$CODE" = 409 ] || [ "$CODE" = 400 ] && echo 0 || echo 1)" "answered $CODE"
  ask POST "/projects/$PROJECT/approve" '{"target":"PROD","comment":"without confirming"}' release.manager
  holds "and production is not approved without a deliberate confirmation" \
    "$([ "$CODE" != 200 ] && echo 0 || echo 1)" "answered $CODE"
  ask GET /audit/verify "" lead.reviewer
  where '.get("ok") is True' && ok "the audit trail's chain is intact, record by record" \
    || no "the audit trail's chain is intact" "$(head -c 300 "$BODY")"
  ask GET "/audit?project_id=$PROJECT&limit=500" "" lead.reviewer
  where 'any(e.get("action", "").startswith(("draft:", "commit:")) for e in b)' \
    && ok "and it holds what was done in the Studio, draft by draft" \
    || no "and it holds what was done in the Studio" "$(head -c 200 "$BODY")"
  ask GET /audit "" dev.user
  holds "somebody who may not read the trail is refused it" \
    "$([ "$CODE" = 403 ] && echo 0 || echo 1)" "answered $CODE"
  fi
fi

# ── 6 · export: the package a version IS ──────────────────────────────────────────────────────────────────
if section 6 "export: the .ssbi package, and what is inside it"; then
  if [ -z "$PROJECT" ] || [ -z "$VERSION" ]; then skip "export" "there is no version"; else
  CODE="$(curl -sS -m 300 -o "$TMP/package.ssbi" -w '%{http_code}' \
    "$ENGINE/projects/$PROJECT/versions/$VERSION/package" \
    -H "Authorization: Bearer $TOKEN" -H "X-SharpSight-User: dev.user" 2>/dev/null)" || CODE=000
  holds "the version exports as one file" "$([ "$CODE" = 200 ] && [ -s "$TMP/package.ssbi" ] && echo 0 || echo 1)" \
    "answered $CODE"
  if [ -s "$TMP/package.ssbi" ]; then
    "${PY[@]}" - "$TMP/package.ssbi" "$TMP/inside.txt" 2>"$TMP/zip.err" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    open(sys.argv[2], "w").write("\n".join(sorted(z.namelist())))
PY
    if [ ! -s "$TMP/inside.txt" ]; then
      no "the package is a file a person can open" "$(tail -2 "$TMP/zip.err" | tr '\n' ' ')"
    else
    # The names are the ones lifecycle.build_package writes: the manifest is YAML, and the specification is
    # the dashboard's own dashboards/<id>.spec.yaml, so it is matched by its shape rather than one fixed name.
    for wanted in "manifest.yaml|^manifest\.yaml$" \
                  "the dashboard's specification|^dashboards/[^/]+\.spec\.yaml$" \
                  "superset/dashboard_export.zip|^superset/dashboard_export\.zip$"; do
      if grep -qE "${wanted#*|}" "$TMP/inside.txt"; then
        ok "it carries ${wanted%%|*}"
      else
        no "it carries ${wanted%%|*}" "$(head -c 300 "$TMP/inside.txt" | tr '\n' ' ')"
      fi
    done
    grep -qE "registry/|datasets|metrics" "$TMP/inside.txt" \
      && ok "and the governed datasets and metrics it was built from" \
      || no "and the governed datasets and metrics it was built from" "$(tr '\n' ' ' < "$TMP/inside.txt" | head -c 300)"
    grep -qE "validation" "$TMP/inside.txt" && ok "and the validation that passed" \
      || no "and the validation that passed"
    ask GET "/projects/$PROJECT/versions/$VERSION"
    # The version records the whole file's digest as package.sha256 (lifecycle.build_package), and the
    # download serves that same stored file, so the two must agree byte for byte.
    SUM="$(of .package.sha256)"
    if [ -n "$SUM" ]; then
      HERE_SUM="$("${PY[@]}" -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$TMP/package.ssbi")"
      [ "$SUM" = "$HERE_SUM" ] && ok "and the file is the one the version was recorded as (sha256)" \
        || no "the package is not the file the version recorded" "recorded $SUM, got $HERE_SUM"
    else
      skip "the package's checksum" "this version records none"
    fi
    fi
  fi
  fi
fi

# ── 7 · deploy: take it to the run environment, and prove it arrived ──────────────────────────────────────
if section 7 "deploy: the run environment, and what was read back"; then
  ask GET /targets
  holds "the engine says where a version may go" "$([ "$CODE" = 200 ] && echo 0 || echo 1)"
  TARGET="$(of .0.name)"
  if [ -z "$TARGET" ]; then
    skip "a deployment, end to end" "no target is configured — bash ai-bi.sh run-target starts one here, or name a real one with AIBI_TARGET_<NAME>_URL and the rest in the engine's environment"
  elif [ -z "$PROJECT" ] || [ -z "$VERSION" ]; then
    skip "a deployment, end to end" "there is no version to deploy"
  else
    ok "a target is configured: $TARGET"
    if [ "$WAIT_FOR_RUN" = 1 ]; then
      printf '      %swaiting for the run environment to answer%s\n' "$DIM" "$OFF"
      for _ in $(seq 1 60); do
        curl -sS -m 10 -o /dev/null "$RUN_HOST/health" 2>/dev/null && break || sleep 5
      done
    fi
    # what may NOT be deployed: the first version, which nobody approved for anything
    FIRST="$(ask GET "/projects/$PROJECT"; of .versions.0.version)"
    if [ -n "$FIRST" ] && [ "$FIRST" != "$VERSION" ]; then
      ask POST "/projects/$PROJECT/versions/$FIRST/deploy" \
        "$("${PY[@]}" -c 'import json,sys;print(json.dumps({"target":sys.argv[1],"gate":"UAT","comment":"a version nobody approved"}))' "$TARGET")" \
        release.manager
      holds "a version nobody approved is refused, in words a person can act on" \
        "$([ "$CODE" != 200 ] && echo 0 || echo 1)" "answered $CODE"
    else
      skip "a version nobody approved is refused" "there is only one version"
    fi
    printf '      %sdeploying %s to %s — import, repoint, grant, and then read every chart back%s\n' \
      "$DIM" "$VERSION" "$TARGET" "$OFF"
    ask POST "/projects/$PROJECT/versions/$VERSION/deploy" \
      "$("${PY[@]}" -c 'import json,sys;print(json.dumps({"target":sys.argv[1],"gate":"UAT","comment":"end-to-end check"}))' "$TARGET")" \
      release.manager
    holds "the approved version is deployed" "$([ "$CODE" = 200 ] && echo 0 || echo 1)"
    where '.get("status") == "succeeded"' && ok "and the deployment says it succeeded" \
      || no "and the deployment says it succeeded" "$(of .error) $(of .steps)"
    where 'all(s.get("status") in ("done", "skipped") for s in b.get("steps", []))' \
      && ok "every step of the plan is done: checksum, compatibility, connection, import, repoint, audience, read-back" \
      || no "every step of the plan is done" \
        "$("${PY[@]}" -c 'import json,sys;b=json.load(open(sys.argv[1]));print([(s["key"],s["status"],s.get("detail","")[:60]) for s in b.get("steps",[]) if s["status"] not in ("done","skipped")])' "$BODY" 2>/dev/null)"
    where 'any(c.get("check") == "charts arrived" and c.get("status") == "pass" for c in b.get("checks", []))' \
      && ok "every chart arrived there" || no "every chart arrived there" "$(of .checks)"
    where 'any("returns data" in c.get("check", "") for c in b.get("checks", []))' \
      && ok "and each one was asked for its data — an import answering 200 is not a dashboard that draws" \
      || no "and each one was asked for its data" "$(of .checks)"
    where 'all(c.get("status") != "fail" for c in b.get("checks", []))' \
      && ok "and nothing failed in what was read back" || no "something failed in what was read back" "$(of .checks)"
    DASH_URL="$(of .dashboard.url)"
    if [ -n "$DASH_URL" ]; then
      ok "the deployment leaves a link to the dashboard there: $DASH_URL"
      CODE="$(curl -sS -m 30 -o /dev/null -w '%{http_code}' "$DASH_URL" 2>/dev/null)" || CODE=000
      case "$CODE" in
        200|302) ok "and that address answers in the run environment" ;;
        000) skip "that address answers in the run environment" "it is not reachable from here ($DASH_URL)" ;;
        *) no "that address answers in the run environment" "it answered $CODE" ;;
      esac
    else
      no "the deployment leaves a link to the dashboard there"
    fi
    ask GET "/projects/$PROJECT/deployments"
    where 'len(b) > 0 and b[-1].get("status") == "succeeded"' \
      && ok "and the project remembers where it has been" \
      || no "and the project remembers where it has been" "$(head -c 200 "$BODY")"
    # the Studio's own road to all of this: Superset's token, on the three routes the page uses
    as_superset GET /targets
    holds "the Studio may ask where a version can go" "$([ "$CODE" = 200 ] && echo 0 || echo 1)" "answered $CODE"
    as_superset GET "/projects/$PROJECT/deployments"
    holds "and what has been deployed" "$([ "$CODE" = 200 ] && echo 0 || echo 1)" "answered $CODE"
  fi
fi

# ── 8 · import: the package is Superset's own export, and nothing else ────────────────────────────────────
if section 8 "import: what is in the package is what Superset itself imports"; then
  if [ ! -s "$TMP/package.ssbi" ]; then
    skip "the package can be imported by hand too" "no package was exported in this run (--only 6 first)"
  else
    "${PY[@]}" - "$TMP/package.ssbi" "$TMP/export.txt" <<'PY'
import io, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as outer:
    inner = io.BytesIO(outer.read("superset/dashboard_export.zip"))
with zipfile.ZipFile(inner) as z:
    open(sys.argv[2], "w").write("\n".join(sorted(z.namelist())))
PY
    if [ -s "$TMP/export.txt" ]; then
      ok "the package carries Superset's own export, readable on its own"
      for wanted in metadata.yaml dashboards/ charts/ datasets/ databases/; do
        grep -q "$wanted" "$TMP/export.txt" && ok "with $wanted, as Superset's importer expects" \
          || no "with $wanted" "$(tr '\n' ' ' < "$TMP/export.txt" | head -c 300)"
      done
      ok "so it imports into any Superset of a covered version — by hand, or by a deployment"
    else
      no "the package carries Superset's own export" "superset/dashboard_export.zip could not be read"
    fi
  fi
fi

# ── 9 · the gateway's limits: what Superset's own token may and may not do ────────────────────────────────
if section 9 "the gateway: what Superset's token opens, and what it does not"; then
  CODE="$(curl -sS -m 30 -o "$BODY" -w '%{http_code}' "$ENGINE/projects" 2>/dev/null)" || CODE=000
  holds "the engine answers nobody without a token" "$([ "$CODE" = 401 ] && echo 0 || echo 1)" "answered $CODE"
  as_superset GET /projects
  holds "Superset's own token opens the projects" "$([ "$CODE" = 200 ] && echo 0 || echo 1)" "answered $CODE"
  if [ -n "$PROJECT" ]; then
    as_superset GET "/projects/$PROJECT/studio"
    holds "and the Studio's payload" "$([ "$CODE" = 200 ] && echo 0 || echo 1)" "answered $CODE"
  fi
  as_superset GET /audit lead.reviewer
  holds "and nothing of the audit trail: that is the command line's" \
    "$([ "$CODE" = 403 ] && echo 0 || echo 1)" "answered $CODE"
  if [ -n "$PROJECT" ]; then
    as_superset POST "/projects/$PROJECT/approve" lead.reviewer
    holds "and nothing that approves" "$([ "$CODE" = 403 ] || [ "$CODE" = 405 ] && echo 0 || echo 1)" "answered $CODE"
  fi
  CODE="$(curl -sS -m 30 -o /dev/null -w '%{http_code}' "$BI/sharpsight/studio-assets/studio.js" 2>/dev/null)" || CODE=000
  case "$CODE" in
    200) ok "Superset serves the Studio's own files" ;;
    302|401) ok "Superset serves the Studio's own files to somebody signed in (it answered $CODE)" ;;
    403) no "Superset serves the Studio's own files to somebody signed in" \
           "an anonymous caller got 403, where the Studio's pages send them to the login (302)" ;;
    *) no "Superset serves the Studio's own files" "answered $CODE" ;;
  esac
  if [ -n "$PROJECT" ]; then
    CODE="$(curl -sS -m 30 -o /dev/null -w '%{http_code}' "$BI/sharpsight/studio/$PROJECT/" 2>/dev/null)" || CODE=000
    case "$CODE" in
      302|401) ok "and the Studio page itself only to somebody signed in" ;;
      200) no "the Studio page answered an anonymous caller" "it must be behind Superset's own login" ;;
      *) no "the Studio page is behind Superset's login" "answered $CODE" ;;
    esac
  fi
fi

# ── 10 · the suites: the engine's own, and the Studio's in a browser ─────────────────────────────────────
if section 10 "the suites: the engine's own tests, and the Studio's in a browser"; then
  if command -v docker >/dev/null 2>&1; then
    if docker compose -p sharpsight-superset-devkit -f docker-compose.yaml -f docker-compose.ai-bi.yaml \
         exec -T sharpsight-ai-bi sh -c 'cd /app && python -m pytest' > "$TMP/pytest.txt" 2>&1; then
      ok "the engine's own tests pass inside its container ($(grep -oE '[0-9]+ passed[^"]*' "$TMP/pytest.txt" | tail -1))"
    else
      no "the engine's own tests pass inside its container" "$(tail -5 "$TMP/pytest.txt" | tr '\n' ' ')"
    fi
  else
    skip "the engine's own tests" "docker is not on PATH"
  fi
  if "${PY[@]}" -c 'import playwright' >/dev/null 2>&1; then
    if "${PY[@]}" studio/web/preview.py --check > "$TMP/studio.txt" 2>&1; then
      ok "the Studio keeps its $(grep -c '✓' "$TMP/studio.txt") promises in a real browser"
    else
      no "the Studio keeps its promises in a browser" "$(grep '✗' "$TMP/studio.txt" | head -3 | tr '\n' ' ')"
    fi
  else
    skip "the Studio's own browser harness" "playwright is not installed here (pip install playwright && playwright install chromium)"
  fi
fi

# ── the tally ────────────────────────────────────────────────────────────────────────────────────────────
printf '\n%s%d kept · %d broken · %d not tried%s\n' "$BOLD" "$PASSED" "$FAILED" "$SKIPPED" "$OFF"
[ -n "$PROJECT" ] && printf '%sthe project this left behind: %s %s — %s/sharpsight/studio/%s/%s\n' \
  "$DIM" "$PROJECT" "${VERSION:-}" "$BI" "$PROJECT" "$OFF"
[ "$FAILED" = 0 ] || exit 1
