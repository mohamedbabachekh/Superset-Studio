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

DEMO = ("Create a CEM QoE dashboard using the executive template.\n\n"
        "Use QoE Score, Active Users and Degraded Users.\n"
        "Show daily trend, regional ranking, QoE distribution and geographic view.\n"
        "Add date, region and RAT filters.")

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
                "version", "git_commit", "review_status", "checks", "failures", "warnings", "iterations"):
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


def generate(prompt: str, user: str) -> int:
    print(f"request, as {user}:\n  " + prompt.replace("\n", "\n  ") + "\n")
    started = call("POST", "/projects/generate", user, json={"prompt": prompt})
    print(f"run {started['run_id']} · request {started['request_id']}\n")
    run = follow(started["run_id"], user)
    print()
    if run["status"] != "succeeded":
        print(f"the run FAILED: {run['error']}")
        return 1
    record = call("GET", f"/projects/{run['project_id']}/versions/{run['version']}", user)
    print(f"{record['project_id']} {record['version']} — {record['state']} · {record['review_status']}")
    print(f"  dashboard      {record['dashboard'].get('url')}")
    print(f"  git commit     {record['git_commit']}")
    print(f"  dbt commit     {record['dbt_commit']}")
    print(f"  superset       {record['superset_asset_version']}")
    print(f"  template       {record['template_version']}")
    print(f"  validation     {record['validation']['checks']} checks, {record['validation']['failures']} failed, "
          f"{record['validation']['warnings']} warnings")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="aibi", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "generate", "run", "project", "approve", "reject", "discard", "audit", "health", "people",
                 "person"):
        command = sub.add_parser(name)
        command.add_argument("--user", default="dev.user")
        if name == "generate":
            command.add_argument("prompt")
        if name == "run":
            command.add_argument("run_id")
        if name in ("project", "approve", "reject", "discard"):
            command.add_argument("project_id")
        if name == "approve":
            command.add_argument("target", choices=["UAT", "PROD"])
            command.add_argument("--confirm", action="store_true")
        if name in ("approve", "reject"):
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
    elif args.command == "run":
        print(json.dumps(follow(args.run_id, args.user), indent=2, ensure_ascii=False))
    elif args.command == "project":
        print(json.dumps(call("GET", f"/projects/{args.project_id}", args.user), indent=2, ensure_ascii=False))
    elif args.command == "approve":
        record = call("POST", f"/projects/{args.project_id}/approve", args.user,
                      json={"target": args.target, "comment": args.comment, "confirm": args.confirm})
        print(f"{record['project_id']} {record['version']} — {record['state']} · {record['review_status']}")
        if record.get("package"):
            print(f"  package  {record['package'].get('exported_to') or record['package']['file']}  "
                  f"sha256 {record['package']['sha256']}")
    elif args.command == "reject":
        record = call("POST", f"/projects/{args.project_id}/reject", args.user, json={"comment": args.comment})
        print(f"{record['project_id']} {record['version']} — {record['state']} · {record['review_status']}")
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
