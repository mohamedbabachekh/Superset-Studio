"""The upgrade rehearsal on REAL content: what every connection, dashboard and chart answers, before and after.

  python scripts/rehearse/rehearse_real_content.py snapshot <base url> <admin user> <out.json>     (password in DEVKIT_REHEARSAL_PW)
  python scripts/rehearse/rehearse_real_content.py compare  <before.json> <after.json>

A snapshot asks Superset itself, as a reader would: each connection runs `select 1` through SQL Lab (its saved
password must decrypt and be accepted), each dashboard and its page answer, each chart is QUERIED (force — not
read from a cache) and its rows are fingerprinted. `compare` then says, item by item, what the upgrade changed.
Something that failed before and fails the same way after is not the upgrade's doing, and is reported as such:
a copy of a kit carries connections to places the rehearsal cannot reach.
"""
import hashlib
import json
import os
import re
import sys
import uuid

import requests


def snapshot(base: str, user: str, out: str) -> None:
    s = requests.Session()
    page = s.get(f"{base}/login/", timeout=60).text
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page) or re.search(r'id="csrf_token"[^>]*value="([^"]+)"', page)
    s.post(f"{base}/login/", data={"username": user, "password": os.environ["DEVKIT_REHEARSAL_PW"],
                                    "csrf_token": m.group(1)}, timeout=60)
    if s.get(f"{base}/api/v1/me/", timeout=60).status_code != 200:
        sys.exit("cannot sign in")
    s.headers.update({"X-CSRFToken": s.get(f"{base}/api/v1/security/csrf_token/").json()["result"], "Referer": base})

    def every(kind: str, cols: str) -> list[dict]:
        rows, page_no = [], 0
        while True:
            r = s.get(f"{base}/api/v1/{kind}/", params={"q": f"(columns:!({cols}),page:{page_no},page_size:100)"})
            got = r.json().get("result", [])
            rows += got
            if len(got) < 100:
                return rows
            page_no += 1

    snap = {"connections": {}, "dashboards": {}, "charts": {}, "datasets": 0, "saved_queries": 0}
    for d in every("database", "id,database_name"):
        r = s.post(f"{base}/api/v1/sqllab/execute/", json={"database_id": d["id"], "sql": "select 1", "runAsync": False,
                                                         "client_id": uuid.uuid4().hex[:10], "queryLimit": 1})
        err = "" if r.status_code == 200 else (r.json().get("message") or r.text)[:120] if r.text else str(r.status_code)
        snap["connections"][d["database_name"]] = "answers" if r.status_code == 200 else f"fails: {err}"
    for d in every("dashboard", "id,dashboard_title"):
        api = s.get(f"{base}/api/v1/dashboard/{d['id']}").status_code
        page_status = s.get(f"{base}/superset/dashboard/{d['id']}/").status_code
        charts = s.get(f"{base}/api/v1/dashboard/{d['id']}/charts").json().get("result", [])
        snap["dashboards"][f"{d['id']} {d['dashboard_title']}"] = f"api {api}, page {page_status}, {len(charts)} charts"
    for c in every("chart", "id,slice_name,viz_type"):
        key = f"{c['id']} {c['slice_name']}"
        chart = s.get(f"{base}/api/v1/chart/{c['id']}").json()["result"]
        # a chart saved without a query context (the legacy ones, most imported ones) is drawn by explore_json, as
        # its dashboard draws it; Superset's refusal says so in the interface's language, so it is not matched on
        if not chart.get("query_context"):
            params = json.loads(chart.get("params") or "{}")
            params["slice_id"] = c["id"]
            r = s.post(f"{base}/superset/explore_json/", params={"force": "true"},
                       data={"form_data": json.dumps(params)}, timeout=300)
            results = [r.json()] if r.status_code == 200 else []
            if r.status_code != 200 and "__" in str(params.get("datasource", "")):
                # a modern chart type has no server-side drawing: only the browser builds its query (the browser
                # half of the rehearsal opens its dashboard). Here its DATASET is asked — through its connection,
                # with its SQL — so the part an upgrade could break is still proved.
                ds_id, ds_type = str(params["datasource"]).split("__", 1)
                r = s.post(f"{base}/api/v1/chart/data", timeout=300, json={
                    "datasource": {"id": int(ds_id), "type": ds_type}, "force": True,
                    "queries": [{"columns": [], "row_limit": 1, "metrics": [
                        {"expressionType": "SQL", "sqlExpression": "COUNT(*)", "label": "rows"}]}],
                    "result_format": "json", "result_type": "full"})
                results = r.json().get("result", []) if r.status_code == 200 else []
                key += " [its dataset]"
        else:
            r = s.get(f"{base}/api/v1/chart/{c['id']}/data/", params={"force": "true"}, timeout=300)
            results = r.json().get("result", []) if r.status_code == 200 else []
        if r.status_code != 200:
            snap["charts"][key] = f"fails: HTTP {r.status_code}"
            continue
        rows = []
        for res in results:
            data = res.get("data")
            if isinstance(data, list):
                rows += sorted(json.dumps(x, sort_keys=True, default=str) for x in data)
            elif data is not None:
                rows.append(json.dumps(data, sort_keys=True, default=str))
        snap["charts"][key] = f"{len(rows)} rows, data {hashlib.sha1(chr(10).join(rows).encode()).hexdigest()[:12]}"
    snap["datasets"] = len(every("dataset", "id"))
    snap["saved_queries"] = len(every("saved_query", "id"))
    json.dump(snap, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ok = sum(v == "answers" for v in snap["connections"].values())
    drawn = sum(not v.startswith("fails") for v in snap["charts"].values())
    print(f"  {len(snap['connections'])} connections ({ok} answer), {len(snap['dashboards'])} dashboards, "
          f"{len(snap['charts'])} charts ({drawn} return data), {snap['datasets']} datasets, "
          f"{snap['saved_queries']} saved queries")


def compare(before_path: str, after_path: str) -> int:
    before, after = (json.load(open(p, encoding="utf-8")) for p in (before_path, after_path))
    fail = 0
    for kind in ("connections", "dashboards", "charts"):
        b, a = before[kind], after[kind]
        missing = [k for k in b if k not in a]
        changed = [k for k in b if k in a and a[k] != b[k]]
        same_failure = [k for k in b if k in a and a[k] == b[k] and str(b[k]).startswith("fails")]
        if missing or changed:
            fail = 1
            print(f"  FAIL  {kind}: {len(missing)} missing, {len(changed)} answer differently")
            for k in (missing + changed)[:8]:
                print(f"          {k}: before « {b[k]} », after « {a.get(k, 'MISSING')} »")
        else:
            note = f" ({len(same_failure)} failed before and fail the same way: not the upgrade)" if same_failure else ""
            print(f"  PASS  all {len(b)} {kind} still there and answering as before{note}")
            for k in same_failure[:3]:
                print(f"          {k}: {b[k][:110]}")
    for kind in ("datasets", "saved_queries"):
        ok = after[kind] >= before[kind]
        fail |= not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {kind.replace('_', ' ')}: {before[kind]} -> {after[kind]}")
    return fail


def compare_screen(before_path: str, after_path: str) -> int:
    """Each dashboard opened in a browser: no fewer charts drawn, no more in error, than before the upgrade."""
    before, after = (json.load(open(p, encoding="utf-8")) for p in (before_path, after_path))

    def nums(text: str) -> list[int]:
        return [int(x) for x in re.findall(r"(\d+) (?:charts on|drawn|in error|still loading|failed)", text)]

    worse = []
    for k, b in before.items():
        a = after.get(k)
        if a is None:
            worse.append((k, b, "MISSING"))
            continue
        (_, bd, be, _, bf), (_, ad, ae, _, af) = nums(b), nums(a)
        if not a.startswith("page 200") or ad < bd or ae > be or af > bf:
            worse.append((k, b, a))
    if worse:
        print(f"  FAIL  {len(worse)} dashboard(s) look worse in a browser after the upgrade")
        for k, b, a in worse[:8]:
            print(f"          {k}\n            before: {b}\n            after:  {a}")
        return 1
    drawn = sum(nums(v)[1] for v in after.values())
    print(f"  PASS  all {len(before)} dashboards open in a browser as before ({drawn} charts drawn on their first tabs)")
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "compare-screen":
        sys.exit(compare_screen(sys.argv[2], sys.argv[3]))
    if sys.argv[1] == "snapshot":
        snapshot(sys.argv[2].rstrip("/"), sys.argv[3], sys.argv[4])
    else:
        sys.exit(compare(sys.argv[2], sys.argv[3]))
