"""The upgrade rehearsal's content: made on the OLD kit as a developer would, looked for on the NEW one.

  python rehearse_upgrade_content.py make  <kit folder> <base url>     creates a connection (with a password), a table, a
                                                              dataset, a chart, a dashboard and a saved query
  python rehearse_upgrade_content.py check <kit folder> <base url>     the same things, asked for after the upgrade

Reads the admin's name and password from the folder's .env and never prints them.
"""
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MODE, KIT, BASE = sys.argv[1], sys.argv[2], sys.argv[3].rstrip("/")
env = {}
for line in open(KIT + "/.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k] = v.strip().strip('"').strip("'")
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
auth = {}
CONNECTION, DASHBOARD, SLUG, CHART, QUERY = ("Mes ventes (PostgreSQL)", "Mon tableau de bord", "mon-tableau-de-bord",
                                             "Total des ventes", "Ma requête")


def call(method, path, body=None, form=None):
    headers = dict(auth, Referer=BASE + "/", Origin=BASE)
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    if form is not None:
        data, headers["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    try:
        with opener.open(urllib.request.Request(BASE + path, data=data, method=method, headers=headers), timeout=180) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def need(label, status, text, good=(200, 201)):
    if status not in good:
        print("  FAILED  %s — HTTP %s: %s" % (label, status, text[:300].replace(env.get("DEVKIT_DB_PASSWORD", "\0"), "***")))
        sys.exit(1)
    return json.loads(text)


def sql(database_id, statement):
    status, text = call("POST", "/api/v1/sqllab/execute/",
                        {"database_id": database_id, "sql": statement, "runAsync": False, "select_as_cta": False,
                         "ctas_method": "TABLE", "queryLimit": 100, "tab": "upgrade",
                         "client_id": ("u%x" % int(time.time() * 1000))[-10:]})
    return status, text


_, page = call("GET", "/login/")
found = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page) or re.search(r'"csrf_token"\s*:\s*"([^"]+)"', page)
call("POST", "/login/", form={"username": env.get("DEVKIT_ADMIN_USER", "admin"), "password": env.get("DEVKIT_ADMIN_PASSWORD", ""),
                              "csrf_token": found.group(1) if found else ""})
status, text = call("GET", "/api/v1/me/")
if status != 200:
    print("  FAILED  the admin of %s/.env cannot sign in at %s (HTTP %s)" % (KIT, BASE, status))
    sys.exit(1)
print("  signed in at %s as the admin of this folder's .env" % BASE)
auth["X-CSRFToken"] = json.loads(call("GET", "/api/v1/security/csrf_token/")[1])["result"]


def databases():
    return {d["database_name"]: d["id"] for d in json.loads(call("GET", "/api/v1/database/?q=(page_size:100)")[1])["result"]}


def total_of_chart(dataset_id):
    body = {"datasource": {"id": dataset_id, "type": "table"}, "force": True, "result_format": "json", "result_type": "full",
            "queries": [{"metrics": [{"expressionType": "SQL", "sqlExpression": "SUM(v)", "label": "total"}], "columns": [],
                         "filters": [], "extras": {}, "row_limit": 10}]}
    status_, text_ = call("POST", "/api/v1/chart/data", body)
    return json.loads(text_)["result"][0]["data"][0]["total"] if status_ == 200 else "HTTP %s" % status_


if MODE == "make":
    store = databases().get("Dev kit store")
    status, text = sql(store, "create table if not exists public.ventes_demo as select g as jour, g * 3 as v from generate_series(1, 10) g")
    need("a table in the kit's own store", status, text)
    uri = "postgresql+psycopg2://devkit:%s@devkit-db:5432/devkit" % urllib.parse.quote(env["DEVKIT_DB_PASSWORD"], safe="")
    made = need("a connection with a password", *call("POST", "/api/v1/database/",
                {"database_name": CONNECTION, "sqlalchemy_uri": uri, "expose_in_sqllab": True}))
    dataset = need("a dataset on it", *call("POST", "/api/v1/dataset/",
                   {"database": made["id"], "schema": "public", "table_name": "ventes_demo"}))
    params = {"viz_type": "big_number_total", "datasource": "%s__table" % dataset["id"],
              "metric": {"expressionType": "SQL", "sqlExpression": "SUM(v)", "label": "total"}}
    chart = need("a chart", *call("POST", "/api/v1/chart/", {"slice_name": CHART, "viz_type": "big_number_total",
                 "datasource_id": dataset["id"], "datasource_type": "table", "params": json.dumps(params)}))
    dash = need("a dashboard", *call("POST", "/api/v1/dashboard/", {"dashboard_title": DASHBOARD, "slug": SLUG, "published": True}))
    need("the chart on the dashboard", *call("PUT", "/api/v1/chart/%s" % chart["id"], {"dashboards": [dash["id"]]}))
    need("a saved query", *call("POST", "/api/v1/saved_query/", {"label": QUERY, "sql": "select sum(v) from public.ventes_demo", "db_id": made["id"]}))
    print("  made: connection « %s » (password saved), dataset ventes_demo, chart « %s », dashboard « %s », saved query « %s »" % (
        CONNECTION, CHART, DASHBOARD, QUERY))
    print("  the chart's figure before the upgrade: %s" % total_of_chart(dataset["id"]))

if MODE == "check":
    ok = True

    def say(label, good, detail=""):
        global ok
        ok = ok and good
        print("  %s  %s%s" % ("PASS" if good else "FAIL", label, (" — " + str(detail)) if detail != "" else ""))

    known = databases()
    say("the connection « %s » is still there" % CONNECTION, CONNECTION in known, sorted(known))
    if CONNECTION in known:
        status, text = sql(known[CONNECTION], "select sum(v) as total from public.ventes_demo")
        state = json.loads(text).get("status") if status == 200 else None
        value = (json.loads(text).get("data") or [{}])[0].get("total") if status == 200 else None
        say("…and its saved password still opens it (a query THROUGH it answers)", state == "success" and value == 165,
            "%s, total %s" % (state or "HTTP %s: %s" % (status, text[:160]), value))
    status, text = call("GET", "/api/v1/dashboard/%s" % SLUG)
    say("the dashboard « %s » is still there, at its address" % DASHBOARD, status == 200, "HTTP %s" % status)
    status, text = call("GET", "/api/v1/dashboard/%s/charts" % SLUG)
    charts = json.loads(text).get("result", []) if status == 200 else []
    say("…with its chart « %s »" % CHART, [c.get("slice_name") for c in charts] == [CHART], [c.get("slice_name") for c in charts])
    if charts:
        dataset_id = int(str(charts[0]["form_data"]["datasource"]).split("__")[0])
        say("…which draws the same figure (165)", total_of_chart(dataset_id) == 165, total_of_chart(dataset_id))
    status, text = call("GET", "/api/v1/saved_query/?q=(page_size:100)")
    labels = [q.get("label") for q in json.loads(text).get("result", [])] if status == 200 else []
    say("the saved query « %s » is still there" % QUERY, QUERY in labels, labels)
    status, text = call("GET", "/api/v1/menu/")
    say("no SharpSight menu and no BI engine in this kit", status == 200 and "sharpsight" not in text.lower(), "HTTP %s" % status)
    sys.exit(0 if ok else 1)
