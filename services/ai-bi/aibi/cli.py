"""A terminal in front of the API, for the kit — what the portal's screens will do, typed.

    python -m aibi.cli demo                                    the brief's own request, end to end
    python -m aibi.cli generate "<request>" [--user dev.user]
    python -m aibi.cli run <run id>
    python -m aibi.cli project <project id>
    python -m aibi.cli approve <project id> UAT --user lead.reviewer
    python -m aibi.cli approve <project id> PROD --user release.manager --confirm --comment "…"
    python -m aibi.cli reject <project id> --user lead.reviewer --comment "…"
    python -m aibi.cli discard <project id>                    a project still in DEV, taken away
    python -m aibi.cli people --user release.manager           BI-engine accounts given the roles rbac.yaml says
    python -m aibi.cli person <rbac user> --user release.manager   an account for someone who has none yet
    python -m aibi.cli audit [--project <id>] [--request <id>] --user lead.reviewer
    python -m aibi.cli compatibility                           the matrix, beside what is actually running

It calls the service like any other client: with the gateway's token, and a user named on each call.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import requests

DEMO = ("Create a CEM QoE dashboard using the CEM Executive template.\n\n"
        "Use QoE Score, Active Users and Degraded Users.\n\n"
        "Show daily trend, regional ranking, QoE distribution and geographic view.\n\n"
        "Add Date, Region and RAT filters.")

BASE = os.environ.get("AIBI_URL", "http://127.0.0.1:8080")
MARK = {"done": "✓", "skipped": "–", "failed": "✗", "running": "…", "pending": " "}


def call(method: str, path: str, user: str, **kwargs: Any) -> Any:
    headers = {"Authorization": f"Bearer {os.environ.get('AIBI_API_TOKEN', '')}", "X-SharpSight-User": user}
    answer = requests.request(method, BASE + path, headers=headers, timeout=900, **kwargs)
    if answer.status_code >= 400:
        try:
            body = answer.json()
            reason = body.get("error") or body.get("detail") or body
        except ValueError:
            reason = answer.text[:300]
        print(f"refused ({answer.status_code}): {reason}", file=sys.stderr)
        sys.exit(1)
    return answer.json()


def _brief(summary: dict[str, Any]) -> str:
    """One line of what a step found — the facts a person looks for, not the whole record."""
    picks = []
    for key in ("reason", "project_id", "template", "transformation_needed", "model", "dataset", "rows",
                "version", "git_commit", "status", "package", "checks", "failures", "warnings", "iterations"):
        if key in summary and not isinstance(summary[key], (dict, list)):
            value = summary[key]
            picks.append(f"{key}={value[:12] if key == 'git_commit' and isinstance(value, str) else value}")
    if "candidates" in summary:
        picks.append("candidates=" + ", ".join(c["id"] for c in summary["candidates"]))
    if "metrics" in summary and summary["metrics"] and isinstance(summary["metrics"][0], dict):
        picks.append("metrics=" + ", ".join(m["id"] for m in summary["metrics"]))
    if "missing" in summary and summary["missing"]:
        picks.append("missing=" + "; ".join(f"{m['slot']}: {m['missing']}" for m in summary["missing"]))
    if "charts" in summary and isinstance(summary["charts"], dict):
        picks.append(f"charts={len(summary['charts'])}")
    if "fixes" in summary and summary["fixes"]:
        picks.append("fixes=" + "; ".join(f"{f['slot']}: {f['fix']}" for f in summary["fixes"]))
    if "renders" in summary:
        picks.append("renders=" + ", ".join(f"{r['viewport']} {r['load_ms']} ms" for r in summary["renders"]))
    return "  ".join(picks)


def follow(run_id: str, user: str) -> dict[str, Any]:
    shown: dict[int, str] = {}
    while True:
        run = call("GET", f"/runs/{run_id}", user)
        for step in run["steps"]:
            if step["status"] in ("done", "skipped", "failed") and shown.get(step["number"]) != step["status"]:
                shown[step["number"]] = step["status"]
                print(f" {MARK[step['status']]} {step['number']:>2}. {step['title']:<48} {_brief(step['summary'])}")
                if step["error"]:
                    print(f"       {step['error']}")
                for failed in step["summary"].get("failed", [])[:8]:
                    print(f"       ✗ {failed['category']} · {failed['check']} · {failed['subject']}: {failed['detail']}")
                # a warning does not stop a version, and a reviewer should still see it without asking
                for warned in step["summary"].get("warned", [])[:6]:
                    print(f"       ! {warned['category']} · {warned['check']} · {warned['subject']}: {warned['detail']}")
        if run["status"] != "running":
            return run
        time.sleep(2)


def validate(project_id: str, user: str) -> dict[str, Any]:
    """What Superset holds now against the project's specification — said chart by chart."""
    found = call("GET", f"/projects/{project_id}/changes", user)
    same = found["same_as_validated"]
    print(f"{project_id} {found['version'] or '(no version)'} — {found['gate']}   {found['dashboard']}")
    print("  Superset shows " + ("the version as it was validated" if same else
                                 "something other than the validated version" if same is False else "a dashboard never validated"))
    for chart in found["charts"]:
        if chart["in_step"]:
            continue
        print(f"  {chart['title']}  ({chart['slot']})")
        for field, change in chart["changed"].items():
            print(f"      {field}: {change['specification']} → {change['superset']}")
        for reason in chart["refused"]:
            print(f"      ✗ cannot be taken — {reason}")
        if chart["not_carried"]:
            print(f"      · a specification has no place for: {', '.join(chart['not_carried'])}  (a commit sets it back)")
    for difference in found["on_the_dashboard"]:
        print(f"  on the dashboard itself: {difference}")
    if found["on_the_dashboard"]:
        print("      · a specification has no place for these: A COMMIT UNDOES THEM (the template's layout and the "
              "specification's filters are applied again)")
    untouched = sum(1 for chart in found["charts"] if chart["in_step"])
    print(f"  {untouched} of {len(found['charts'])} charts are as specified")
    print("  a commit would take: " + (", ".join(found["would_take"]) or "nothing")
          if found["can_commit"] else f"  cannot be committed: {found['why_not']}")
    return found


def commit(project_id: str, comment: str, user: str) -> int:
    found = validate(project_id, user)
    if not found["can_commit"]:
        return 1
    started = call("POST", f"/projects/{project_id}/commit", user, json={"comment": comment})
    print(f"\ncommit, as {user}: taking {', '.join(started['taken'])}"
          + (f"; setting back {', '.join(started['set_back'])}" if started["set_back"] else "")
          + (f"; undoing, on the dashboard: {'; '.join(started['undone_on_the_dashboard'])}"
             if started["undone_on_the_dashboard"] else "")
          + f"\nrun {started['run_id']} · request {started['request_id']}\n")
    return report(follow(started["run_id"], user), user)


def generate(prompt: str, user: str) -> int:
    print(f"request, as {user}:\n  " + prompt.replace("\n", "\n  ") + "\n")
    started = call("POST", "/projects/generate", user, json={"prompt": prompt})
    print(f"run {started['run_id']} · request {started['request_id']}\n")
    return report(follow(started["run_id"], user), user)


def report(run: dict[str, Any], user: str) -> int:
    print()
    if run["status"] != "succeeded":
        print(f"the run FAILED, and left the project at {run.get('quality_gate')}: {run['error']}")
        return 1
    record = call("GET", f"/projects/{run['project_id']}/versions/{run['version']}", user)
    print(f"{record['project_id']} {record['version']} — {record['status']}")
    print("  gates          " + " → ".join(h["status"] for h in record["history"]))
    print(f"  package        {record['package'].get('exported_to') or record['package'].get('file')}  "
          f"sha256 {record['package'].get('sha256', '')[:16]}…")
    print(f"  dashboard      {record['dashboard'].get('url')}")
    print(f"  git commit     {record['git_commit']}")
    print(f"  dbt commit     {record['dbt_commit']}")
    print(f"  superset       {record['superset_asset_version']}")
    print(f"  template       {record['template_version']}")
    print(f"  validation     {record['validation']['checks']} checks, {record['validation']['failures']} failed, "
          f"{record['validation']['warnings']} warnings")
    return 0


def deploy(project_id: str, version: str, target: str, gate: str, comment: str, user: str) -> int:
    """Take a version to another Superset, and say what was done, step by step. What is deployed is the
    package that was validated; a version nobody else signed off for that gate is refused before anything
    is sent."""
    done = call("POST", f"/projects/{project_id}/versions/{version}/deploy", user,
                json={"target": target, "gate": gate, "comment": comment})
    mark = {"done": "✓", "failed": "✗", "skipped": "·", "pending": " "}
    print(f"{project_id} {version} → {target} ({gate})")
    for step in done["steps"]:
        print(f"  {mark.get(step['status'], '?')} {step['title']:<48} {step['detail'][:60]}")
    for check in done["checks"]:
        print(f"  {mark.get('done' if check['status'] == 'pass' else 'failed')} {check['check']:<48} "
              f"{check['detail'][:60]}")
    if done["status"] == "succeeded":
        print(f"\n{done['dashboard'].get('url', '')}")
        return 0
    print(f"\nthe deployment failed: {done.get('error')}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="aibi", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "generate", "run", "project", "approve", "reject", "discard", "audit", "health", "people",
                 "person", "compatibility", "validate", "commit", "deploy", "targets", "deployments"):
        command = sub.add_parser(name)
        command.add_argument("--user", default="dev.user")
        if name == "generate":
            command.add_argument("prompt")
        if name == "run":
            command.add_argument("run_id")
        if name in ("project", "approve", "reject", "discard", "validate", "commit", "deploy", "deployments"):
            command.add_argument("project_id")
        if name == "deploy":
            command.add_argument("version")
            command.add_argument("--to", required=True, help="a target of this service's environment")
            command.add_argument("--gate", choices=["UAT", "PROD"], default="UAT")
            command.add_argument("--comment", default="")
        if name == "approve":
            command.add_argument("target", choices=["UAT", "PROD"])
            command.add_argument("--confirm", action="store_true")
        if name in ("approve", "reject", "commit"):
            command.add_argument("--comment", default="")
        if name == "person":
            command.add_argument("username")
        if name == "audit":
            command.add_argument("--project")
            command.add_argument("--request")
            command.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()

    if args.command == "health":
        print(json.dumps(requests.get(BASE + "/health", timeout=30).json(), indent=2))
    elif args.command == "demo":
        return generate(DEMO, args.user)
    elif args.command == "generate":
        return generate(args.prompt, args.user)
    elif args.command == "validate":
        validate(args.project_id, args.user)
    elif args.command == "commit":
        return commit(args.project_id, args.comment, args.user)
    elif args.command == "targets":
        found = call("GET", "/targets", args.user)
        if not found:
            print("no deployment target is configured. A target is a Superset somewhere else, named in this\n"
                  "service's environment: AIBI_TARGET_<NAME>_URL, _USER, _PASSWORD, _DB_NAME, _DB_URI, and\n"
                  "_GATE=UAT for one that takes versions approved for UAT (without it, a target is held to PROD).")
            return 1
        for target in found:
            # the gate is the target's: a version reaches it only once approved for that gate, whatever was asked
            print(f"{target['name']:<12} needs {target.get('gate', 'PROD'):<4}  {target['url']}  "
                  f"({target['database']})"
                  + ("" if target["from_studio"] else "  — the command line only"))
    elif args.command == "deployments":
        for one in call("GET", f"/projects/{args.project_id}/deployments", args.user):
            print(f"{one['started_at']}  {one['version']} → {one['target']:<10} {one['status']:<10} "
                  f"by {one['by']}" + (f"  — {one['error'][:90]}" if one.get("error") else ""))
    elif args.command == "deploy":
        return deploy(args.project_id, args.version, args.to, args.gate, args.comment, args.user)
    elif args.command == "run":
        print(json.dumps(follow(args.run_id, args.user), indent=2, ensure_ascii=False))
    elif args.command == "project":
        print(json.dumps(call("GET", f"/projects/{args.project_id}", args.user), indent=2, ensure_ascii=False))
    elif args.command == "approve":
        record = call("POST", f"/projects/{args.project_id}/approve", args.user,
                      json={"target": args.target, "comment": args.comment, "confirm": args.confirm})
        print(f"{record['project_id']} {record['version']} — {record['status']}")
        print("  gates    " + " → ".join(h["status"] for h in record["history"]))
        if record.get("package"):
            print(f"  package  {record['package'].get('exported_to') or record['package']['file']}  "
                  f"sha256 {record['package']['sha256']}  (verified: the file cut when the version was generated)")
    elif args.command == "reject":
        record = call("POST", f"/projects/{args.project_id}/reject", args.user, json={"comment": args.comment})
        print(f"{record['project_id']} {record['version']} — {record['status']}")
    elif args.command == "compatibility":
        found = call("GET", "/compatibility", args.user)
        own = found["sharpsight_ai_bi"]
        print(f"  {'sharpsight ai-bi':<14} running {own['running']:<10} matrix written for {own['matrix_written_for']}")
        for name, body in found["components"].items():
            if body.get("status") == "not_used":
                print(f"  {name:<14} not used      {body.get('note', '')}")
            else:
                print(f"  {name:<14} running {str(body['running']):<10} tested with {', '.join(body['tested']):<8} "
                      f"→ {body['verdict']}")
        print("  connectors     " + ", ".join(f"{n}: {b['status'].replace('_', ' ')}" for n, b in found["connectors"].items()))
        print("  " + ("everything running is a tested version" if found["ok"]
                      else "UNTESTED: " + "; ".join(found["untested"])))
    elif args.command == "discard":
        done = call("POST", f"/projects/{args.project_id}/discard", args.user)
        print(f"{done['project_id']} discarded — {done['removed_from_bi_engine']} assets removed from the BI engine"
              + (f", generated datasets unregistered: {', '.join(done['generated_datasets'])}"
                 if done["generated_datasets"] else ""))
    elif args.command == "people":
        done = call("POST", "/people/sync", args.user)
        for change in done["changed"]:
            print(f"  {change['account']:<22} granted {', '.join(change['granted']) or '—'}; "
                  f"withdrawn {', '.join(change['withdrawn']) or '—'}  ({change['why']})")
        for same in done["unchanged"]:
            print(f"  {same['account']:<22} already holds {', '.join(same['roles'])}")
        if done["without_an_account"]:
            print("  no account in the BI engine yet: " + ", ".join(done["without_an_account"])
                  + "   (bash ai-bi.sh person <name>)")
        if not (done["changed"] or done["unchanged"] or done["without_an_account"]):
            print("  nobody to bring up to date")
    elif args.command == "person":
        made = call("POST", f"/people/{args.username}", args.user)
        print(f"  account   {made['account']}")
        print(f"  roles     {', '.join(made['roles'])}")
        print(f"  password  {made['password']}     ({made['note']})")
    elif args.command == "audit":
        params = {k: v for k, v in (("project_id", args.project), ("request_id", args.request),
                                    ("limit", args.limit)) if v}
        for record in call("GET", "/audit", args.user, params=params):
            what = record["detail"].get("purpose") or record["detail"].get("error") or ""
            print(f"{record['seq']:>5} {record['timestamp']} {record['actor']:<6} {record['user']:<16} "
                  f"{record['action']:<34} {record['outcome']:<8} {str(what)[:60]}")
        print(json.dumps(call("GET", "/audit/verify", args.user)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
