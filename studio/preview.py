"""Look at the Studio without Superset: a stand-in for the extension's API, and the workspace on top of it.

    python studio/preview.py                 # http://127.0.0.1:8099/  — open it and use it
    python studio/preview.py --shot out.png  # the same page, photographed with Playwright (needs it installed)

Why this exists. The workspace is drawn by studio/studio.js, which asks the extension's API inside Superset.
Starting Superset to see whether a tile is the right width is a minute; this is a second, and it answers the
same question for everything that does not depend on real data: the three panes, the rows and widths, the
moves a canvas may make, the inspector's fields, what is said when something is refused.

What it is NOT: a test of the engine. It serves a payload of the same SHAPE as aibi/main.py's `/studio`, by
hand. The rules themselves are tested in services/ai-bi/tests/test_studio_layout.py and test_studio_draft.py
against the real registry, and the whole thing is run for real by `bash ai-bi.sh demo` and this kit's Superset.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
# what the page is made of — the same names superset-addon/sharpsight_bi.py serves inside Superset
FILES = ("studio.js", "view.js", "state.js", "api.js", "ui.js", "studio.css")
TEMPLATE = {
    "id": "executive", "version": "1.2", "name": "Executive overview",
    "grid": {"columns": 12, "row_unit_px": 8},
    "rows": [
        {"row": "kpis", "height": 16, "slots": [{"id": "kpi_1", "width": 3}, {"id": "kpi_2", "width": 3},
                                                {"id": "kpi_3", "width": 3}, {"id": "kpi_4", "width": 3}]},
        {"row": "trend", "height": 44, "slots": [{"id": "trend_1", "width": 8},
                                                 {"id": "distribution_1", "width": 4}]},
        {"row": "breakdown", "height": 52, "slots": [{"id": "ranking_1", "width": 6}, {"id": "map_1", "width": 6}]},
    ],
    "slots": {
        "kpi_1": {"purpose": "kpi", "accepts": ["big_number"], "mandatory": True},
        "kpi_2": {"purpose": "kpi", "accepts": ["big_number"], "mandatory": False},
        "kpi_3": {"purpose": "kpi", "accepts": ["big_number"], "mandatory": False},
        "kpi_4": {"purpose": "kpi", "accepts": ["big_number"], "mandatory": False},
        "trend_1": {"purpose": "trend", "accepts": ["line", "area"], "mandatory": True},
        "distribution_1": {"purpose": "distribution", "accepts": ["histogram", "pie"], "mandatory": False},
        "ranking_1": {"purpose": "ranking", "accepts": ["bar", "table"], "mandatory": False},
        "map_1": {"purpose": "geo", "accepts": ["map", "table"], "mandatory": False},
    },
    "sections": [{"id": "headline", "title": "Executive KPIs", "rows": ["kpis"]},
                 {"id": "evolution", "title": "Trend and distribution", "rows": ["trend"]},
                 {"id": "breakdown", "title": "Where it stands", "rows": ["breakdown"]}],
    "min_slot_width": {"kpi": 2, "chart": 4},
    "viewports": [{"name": "desktop", "width": 1440}, {"name": "laptop", "width": 1180}],
    "filters": {"max": 5, "allowed_roles": ["time", "dimension"], "orientation": "horizontal"},
}
CHARTS = [
    ("kpi_1", "big_number", "cem.qoe_score", None, "QoE Score"),
    ("kpi_2", "big_number", "cem.active_users", None, "Active users"),
    ("kpi_3", "big_number", "cem.degraded_users", None, "Degraded users"),
    ("kpi_4", "big_number", "cem.degraded_rate", None, "Degraded rate"),
    ("trend_1", "line", "cem.qoe_score", "date", "QoE Score, daily trend"),
    ("distribution_1", "histogram", "cem.qoe_score", None, "QoE Score, distribution"),
    ("ranking_1", "bar", "cem.qoe_score", "region", "Top regions by QoE"),
    # map_1 is left free on purpose: a dashboard with a slot still to fill is what the gallery is for
]
SPEC = {
    "dashboard": {"id": "cem-qoe", "template": "executive", "title": "CEM QoE Overview",
                  "description": "Headline QoE, how it moves, where it stands.", "owner": "cem-team"},
    "filters": ["date", "region", "rat"],
    "components": [{"slot": slot, "type": kind, "metric": metric, "dimension": dimension, "title": title,
                    "dataset": "cem.qoe_daily", "options": {}} for slot, kind, metric, dimension, title in CHARTS],
    "sections": [], "layout": [],
}
state = {"draft": None, "deployments": []}


def tile(slot: str) -> bytes:
    """A chart in place of a chart's photograph.

    The real tiles are cut out of a picture of the real dashboard, which Superset drew (aibi/picture.py).
    There is no Superset here, so these are drawn — as SVG, from the standard library — and they are drawn
    to LOOK like what Superset draws, because a canvas full of flat rectangles says nothing about whether
    the workspace around it reads well. Nothing about them is data: every number below is invented."""
    kind, colour = STAND_IN.get(slot, ("line", "#0972BC"))
    if kind == "big_number":
        label, value, delta, up = BIG.get(slot, ("", "0", "", True))
        spark = " ".join(f"{6 + n * 13},{44 - v * 0.3:.0f}" for n, v in enumerate(SPARK))
        body = f"""
          <text x="6" y="18" font-size="11" fill="#8A94A6">{label}</text>
          <text x="6" y="46" font-size="30" font-weight="700" fill="#1F2430">{value}</text>
          <text x="6" y="64" font-size="11" fill="{'#37A15E' if up else '#D64545'}">
            {'↑' if up else '↓'} {delta}</text>
          <polyline points="{spark}" fill="none" stroke="{colour}" stroke-width="2"
            transform="translate(118, 14)" opacity=".85"/>"""
        return _svg(220, 80, body)
    if kind == "line":
        points = " ".join(f"{10 + n * 17},{86 - v * 0.62:.0f}" for n, v in enumerate(TREND))
        body = f"""
          {_grid(300, 110)}
          <polygon points="10,96 {points} {10 + (len(TREND) - 1) * 17},96" fill="{colour}" opacity=".12"/>
          <polyline points="{points}" fill="none" stroke="{colour}" stroke-width="2.4"/>
          {''.join(f'<circle cx="{10 + n * 17}" cy="{86 - v * 0.62:.0f}" r="2.2" fill="{colour}"/>'
                   for n, v in enumerate(TREND) if n % 3 == 0)}
          <text x="10" y="107" font-size="8" fill="#8A94A6">Jan 1</text>
          <text x="265" y="107" font-size="8" fill="#8A94A6">Jan 31</text>"""
        return _svg(300, 112, body)
    if kind == "histogram":
        bars = "".join(
            f'<rect x="{14 + n * 22}" y="{92 - v * 0.8:.0f}" width="15" height="{v * 0.8:.0f}" rx="2"'
            f' fill="{colour}" opacity="{0.45 + n * 0.06:.2f}"/>' for n, v in enumerate(SPREAD))
        return _svg(200, 112, _grid(200, 110) + bars
                    + '<text x="14" y="107" font-size="8" fill="#8A94A6">QoE buckets</text>')
    if kind == "bar":
        rows = "".join(
            f'<text x="4" y="{20 + n * 22}" font-size="9" fill="#5B6472">{name}</text>'
            f'<rect x="66" y="{11 + n * 22}" width="{v * 1.7:.0f}" height="11" rx="3" fill="{colour}"'
            f' opacity="{0.95 - n * 0.13:.2f}"/>'
            f'<text x="{72 + v * 1.7:.0f}" y="{20 + n * 22}" font-size="9" fill="#8A94A6">{v}</text>'
            for n, (name, v) in enumerate(REGIONS))
        return _svg(240, 118, rows)
    if kind == "table":
        rows = "".join(
            f'<text x="6" y="{22 + n * 18}" font-size="9" fill="#1F2430">{name}</text>'
            f'<text x="150" y="{22 + n * 18}" font-size="9" fill="#5B6472" text-anchor="end">{v}</text>'
            f'<line x1="6" y1="{26 + n * 18}" x2="150" y2="{26 + n * 18}" stroke="#F0F2F5"/>'
            for n, (name, v) in enumerate(REGIONS))
        return _svg(160, 112,
                    '<text x="6" y="10" font-size="8" fill="#8A94A6">REGION</text>'
                    '<text x="150" y="10" font-size="8" fill="#8A94A6" text-anchor="end">QOE</text>' + rows)
    return _svg(200, 110, f'<rect x="8" y="8" width="184" height="94" rx="6" fill="{colour}" opacity=".2"/>')


def _svg(width: int, height: int, body: str) -> bytes:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}"'
            f' height="{height}" font-family="Inter, sans-serif">{body}</svg>').encode()


def _grid(width: int, height: int) -> str:
    return "".join(f'<line x1="8" y1="{y}" x2="{width - 8}" y2="{y}" stroke="#F0F2F5"/>'
                   for y in range(20, height - 10, 22))


STAND_IN = {"kpi_1": ("big_number", "#0972BC"), "kpi_2": ("big_number", "#26ABA8"),
            "kpi_3": ("big_number", "#F2B72D"), "kpi_4": ("big_number", "#37A15E"),
            "trend_1": ("line", "#0972BC"), "distribution_1": ("histogram", "#26ABA8"),
            "ranking_1": ("bar", "#0972BC"), "map_1": ("table", "#5B6472")}
BIG = {"kpi_1": ("QoE Score", "92.4", "3.2% vs last month", True),
       "kpi_2": ("Active users", "1.24M", "12.6% vs last month", True),
       "kpi_3": ("Degraded users", "56.2K", "8.4% vs last month", False),
       "kpi_4": ("Degraded rate", "4.5%", "0.3 pt vs last month", False)}
SPARK = [38, 44, 41, 52, 48, 60, 57, 66]
TREND = [52, 61, 58, 70, 66, 74, 71, 80, 77, 85, 82, 90, 87, 94, 91, 99, 96]
SPREAD = [18, 34, 58, 86, 104, 92, 61, 37]
REGIONS = [("Rabat", 94), ("Casablanca", 91), ("Tanger", 88), ("Fès", 85), ("Agadir", 81)]


def token(draft) -> str:
    if not draft:
        return "none"
    return hashlib.sha256(json.dumps(draft["spec"], sort_keys=True).encode()).hexdigest()[:16]


def payload() -> dict:
    spec = copy.deepcopy(state["draft"]["spec"] if state["draft"] else SPEC)
    rows = spec.get("layout") or TEMPLATE["rows"]
    return {
        "project": {"project_id": "cem-qoe", "title": "CEM QoE", "template": "executive",
                    "created_by": "dev.user", "dashboard_title": spec["dashboard"]["title"],
                    "description": spec["dashboard"]["description"], "owner": "cem-team",
                    "version": "1.0.0", "gate": "READY_FOR_REVIEW", "environment": "DEV", "held": False,
                    "dashboard": "/dashboard", "may_edit": True},
        "template": TEMPLATE, "spec": spec, "layout": rows,
        "layout_of": "specification" if spec.get("layout") else "template",
        "components": [{
            "slot": slot, "type": kind, "metric": metric, "dimension": dimension, "title": title,
            "dataset": "cem.qoe_daily", "options": {},
            "section": next(s["id"] for s in TEMPLATE["sections"]
                            if any(slot in [p["id"] for p in r["slots"]]
                                   for r in TEMPLATE["rows"] if r["row"] in s["rows"])),
            "purpose": TEMPLATE["slots"][slot]["purpose"], "accepts": TEMPLATE["slots"][slot]["accepts"],
            "mandatory": TEMPLATE["slots"][slot]["mandatory"], "chart_id": 100 + n,
            "governance": {"metric_certified": metric != "cem.degraded_rate", "metric_version": "1.0",
                           "metric_owner": "CEM", "metric_definition": f"{title} — as the registry defines it",
                           "dataset_certified": True, "dataset_owner": "CEM",
                           "dataset_domain": "network", "dataset_grain": ["date", "region", "rat"]},
        } for n, (slot, kind, metric, dimension, title) in enumerate(CHARTS)],
        "filters": spec["filters"], "sections": [], "theme": {},
        "editing": "draft" if state["draft"] else "version",
        "draft": {k: state["draft"][k] for k in ("saved_at", "saved_by")} if state["draft"] else None,
        "token": token(state["draft"]), "problems": [],
        "diff": ["~ the layout: " + ", ".join(r["row"] for r in rows)] if state["draft"] else [],
        "versions": [{"version": "1.0.0", "status": "APPROVED_FOR_UAT", "state": "DEV",
                      "created_at": "2026-10-19T21:00:00Z", "created_by": "dev.user", "title": "CEM QoE Overview",
                      "git_commit": "a1b2c3d", "package": "cem-qoe-1.0.0.ssbi", "validation": {"passed": True}}],
        "visualizations": [
            {"id": "big_number", "status": "available", "needs": {"metric": True},
             "slots": ["kpi_1", "kpi_2", "kpi_3", "kpi_4"], "purposes": ["kpi"]},
            {"id": "line", "status": "available", "needs": {"metric": True}, "slots": ["trend_1"],
             "purposes": ["trend"]},
            {"id": "area", "status": "available", "needs": {"metric": True}, "slots": ["trend_1"],
             "purposes": ["trend"]},
            {"id": "bar", "status": "available", "needs": {"metric": True}, "slots": ["ranking_1"],
             "purposes": ["ranking"]},
            {"id": "histogram", "status": "available", "needs": {"metric": True}, "slots": ["distribution_1"],
             "purposes": ["distribution"]},
            {"id": "table", "status": "available", "needs": {"metric": True},
             "slots": ["ranking_1", "map_1"], "purposes": ["geo", "ranking"]},
            {"id": "map", "status": "available", "needs": {"metric": True}, "slots": ["map_1"],
             "purposes": ["geo"]},
            {"id": "TelecomQoEChart", "status": "approved_not_installed", "needs": {"metric": True},
             "slots": [], "purposes": []},
        ],
        "filter_options": [
            {"column": "date", "role": "time", "label": "Date", "datasets": ["cem.qoe_daily"], "used": True,
             "reaches_every_chart": True},
            {"column": "region", "role": "dimension", "label": "Region", "datasets": ["cem.qoe_daily"],
             "used": True, "reaches_every_chart": True},
            {"column": "rat", "role": "dimension", "label": "RAT", "datasets": ["cem.qoe_daily"],
             "used": True, "reaches_every_chart": True},
            {"column": "site", "role": "dimension", "label": "Site", "datasets": ["cem.qoe_site_daily"],
             "used": False, "reaches_every_chart": False},
        ],
        "security": {"audience": ["bi_developer", "bi_reviewer", "cem_analyst"],
                     "row_rules": [{"role": "cem_analyst", "dataset": "cem.qoe_daily",
                                    "clause": "region in ('Casablanca-Settat')"}],
                     "classifications": ["internal"], "uncertified": ["cem.degraded_rate"]},
        "ask": ["add a distribution of QoE Score", "make the trend weekly", "show only the top 5 regions",
                "make all the KPIs compact", "what is the QoE Score?"],
        "free_slots": [{"slot": slot, "purpose": TEMPLATE["slots"][slot]["purpose"],
                        "accepts": TEMPLATE["slots"][slot]["accepts"],
                        "mandatory": TEMPLATE["slots"][slot]["mandatory"],
                        "section": next(s["id"] for s in TEMPLATE["sections"]
                                        if any(slot in [p["id"] for p in r["slots"]]
                                               for r in TEMPLATE["rows"] if r["row"] in s["rows"]))}
                       for slot in [p["id"] for r in TEMPLATE["rows"] for p in r["slots"]]
                       if slot not in [c["slot"] for c in spec["components"]]],
        "pictures": {"taken_at": "2026-10-19T22:00:00Z", "dashboard": 7, "width": 1440, "load_ms": 4200,
                     "slots": {slot: {"width": 300, "height": 200, "drawn": True} for slot, *_ in CHARTS},
                     "missing": [], "console_errors": []},
    }


def proposal(body: dict) -> dict:
    """A proposal of the engine's own shape. The engine's reading of a request is tested against the real
    registry (services/ai-bi/tests/test_studio_copilot.py); what is held here is that the WORKSPACE shows a
    proposal, lets it be applied, and does not apply it by itself."""
    spec = copy.deepcopy((state["draft"] or {}).get("spec") or SPEC)
    asked = str(body.get("request") or f"add {body.get('metric')} on {body.get('slot')}")
    if "weekly" in asked:
        for component in spec["components"]:
            if component["slot"] == "trend_1":
                component["options"] = {**component.get("options", {}), "time_grain": "P1W"}
        return {"request": asked, "read_by": "none (deterministic copilot)", "understood": "trend_1: time grain P1W",
                "summary": "~ trend_1: time_grain P1D → P1W", "spec": spec,
                "changes": [{"kind": "option", "slot": "trend_1", "says": "trend_1 is read by P1W"}],
                "touches": {"metrics": ["cem.qoe_score"], "datasets": ["cem.qoe_daily"], "slots": ["trend_1"]},
                "validation": {"passed": True, "checks": 42, "failures": [], "warnings": []},
                "problems": [], "unresolved": []}
    if body.get("slot"):
        slot = body["slot"]
        spec["components"].append({"slot": slot, "type": body.get("type") or "big_number",
                                   "metric": body.get("metric"), "dataset": "cem.qoe_daily", "options": {}})
        # a chart is given the template's own place for its slot: a specification that carries a layout and
        # a chart with no place on it would not be drawn, and the engine refuses that (aibi/layout.py)
        for template_row in TEMPLATE["rows"]:
            place = next((p for p in template_row["slots"] if p["id"] == slot), None)
            row = next((r for r in spec.get("layout") or [] if r["row"] == template_row["row"]), None)
            if place and row:
                row["slots"].append(dict(place))
        return {"request": asked, "read_by": "the Studio's own form", "understood": f"a chart on {slot}",
                "summary": f"+ {slot}: a chart of {body.get('metric')}", "spec": spec,
                "changes": [{"kind": "add", "slot": slot, "says": f"a chart of {body.get('metric')} on {slot}"}],
                "touches": {"metrics": [body.get("metric")], "datasets": ["cem.qoe_daily"], "slots": [slot]},
                "validation": {"passed": True, "checks": 42, "failures": [], "warnings": []},
                "problems": [], "unresolved": []}
    return {"request": asked, "read_by": "none (deterministic copilot)", "understood": "nothing this copilot knows",
            "summary": "nothing is proposed", "changes": [], "spec": None, "touches": {},
            "validation": {}, "problems": [],
            "unresolved": [f"« {asked} » was not recognised. This copilot is deterministic — no language model "
                           "has been chosen for this kit — so it understands a short list."]}


# What Superset's own standalone dashboard looks like to the page that frames it: holders carrying the chart
# id, which is all the overlay reads. The real one is Superset's; this is enough to hold the mechanism to
# account without starting it.
DASHBOARD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>CEM QoE Overview</title><style>
body { margin: 0; font: 13px Inter, sans-serif; background: #fff; padding: 16px; }
.row { display: grid; grid-template-columns: repeat(12, 1fr); gap: 12px; margin-bottom: 12px; }
.dashboard-component-chart-holder { border: 1px solid #E3E6EC; border-radius: 8px; padding: 8px;
  min-height: 120px; }
.t { font-size: 12px; font-weight: 600; margin-bottom: 6px; }
.b { height: 86px; border-radius: 4px; }
</style></head><body>
<h2>CEM QoE Overview</h2>
<div class="row">
  <div class="dashboard-component-chart-holder" style="grid-column: span 3">
    <div class="t">QoE Score</div><div data-test-chart-id="100" data-test-viz-type="big_number_total"
      class="b" style="background:#0972BC"></div></div>
  <div class="dashboard-component-chart-holder" style="grid-column: span 3">
    <div class="t">Active users</div><div data-test-chart-id="101" class="b" style="background:#26ABA8"></div></div>
  <div class="dashboard-component-chart-holder" style="grid-column: span 3">
    <div class="t">Degraded users</div><div data-test-chart-id="102" class="b" style="background:#F2B72D"></div></div>
  <div class="dashboard-component-chart-holder" style="grid-column: span 3">
    <div class="t">Degraded rate</div><div data-test-chart-id="103" class="b" style="background:#37A15E"></div></div>
</div>
<div class="row">
  <div class="dashboard-component-chart-holder" style="grid-column: span 8">
    <div class="t">QoE Score, daily trend</div>
    <div data-test-chart-id="104" class="b" style="height:220px;background:#0972BC"></div></div>
  <div class="dashboard-component-chart-holder" style="grid-column: span 4">
    <div class="t">QoE Score, distribution</div>
    <div data-test-chart-id="105" class="b" style="height:220px;background:#26ABA8"></div></div>
</div>
<div class="row">
  <div class="dashboard-component-chart-holder" style="grid-column: span 6">
    <div class="t">Top regions by QoE</div>
    <div data-test-chart-id="106" class="b" style="height:200px;background:#5B6472"></div></div>
</div>
</body></html>
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>SharpSight Studio — preview</title>
<link rel="stylesheet" href="/studio.css">
<style>body { margin: 0; } #bar { background: #0972BC; color: #fff; padding: 8px 20px; font: 13px Inter,
sans-serif; } </style></head>
<body><div id="bar">Superset's own top bar stands here · this page is studio/preview.py, not Superset</div>
<main id="sharpsight-studio" data-project="cem-qoe" data-user="dev.user" data-csrf="preview"
 data-links='[{"name": "Projects", "href": "/sharpsight/projects/"},
              {"name": "Data Quality", "href": "/superset/dashboard/sharpsight-data-quality/"},
              {"name": "Releases", "href": "/superset/dashboard/sharpsight-releases/"}]'></main>
<script type="module" src="/studio.js"></script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: ANN002 — quiet; the point is the page, not the log
        pass

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, body: dict) -> None:
        self._send(code, json.dumps(body).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        if path == "/dashboard":
            return self._send(200, DASHBOARD.encode(), "text/html; charset=utf-8")
        if path.lstrip("/") in FILES:
            kind = "text/javascript" if path.endswith(".js") else "text/css"
            return self._send(200, (HERE / path.lstrip("/")).read_bytes(), f"{kind}; charset=utf-8")
        if path.endswith("/studio"):
            return self._json(200, {"result": payload()})
        if "/picture/" in path:
            slot = path.rsplit("/", 1)[-1].split("?")[0]
            return self._send(200, tile(slot), "image/svg+xml; charset=utf-8")
        if path.endswith("/datasets"):
            return self._json(200, {"result": [{"id": "cem.qoe_daily", "name": "CEM QoE Daily", "certified": True},
                                               {"id": "cem.region", "name": "Regions", "certified": True},
                                               {"id": "cem.qoe_site_daily", "name": "QoE by site",
                                                "certified": False}]})
        if path.endswith("/metrics"):
            return self._json(200, {"result": [
                {"id": m, "name": m.split(".")[-1].replace("_", " ").title(), "certified": m != "cem.degraded_rate",
                 "owner": "CEM", "documentation": f"{m} as the registry defines it",
                 "dimensions": ["region", "rat"]}
                for m in ("cem.qoe_score", "cem.active_users", "cem.degraded_users", "cem.degraded_rate")]})
        if path == "/targets" or path.endswith("/targets"):
            return self._json(200, {"result": [
                {"name": "run", "url": "https://bi.example.com", "database": "SharpSight BI",
                 "from_studio": True, "gate": "UAT"},
                {"name": "prod", "url": "https://bi-prod.example.com", "database": "SharpSight BI",
                 "from_studio": False, "gate": "PROD"}]})
        if path.endswith("/deployments"):
            return self._json(200, {"result": state.get("deployments", [])})
        if path.endswith("/report"):
            return self._json(200, {"result": {
                "version": "1.0.0", "status": "READY_FOR_REVIEW", "created_at": "2026-10-19T21:00:00Z",
                "created_by": "dev.user", "dashboard": {"id": 7}, "package": {"name": "cem-qoe-1.0.0.ssbi"},
                "validation": {"checks": [
                    {"category": "data", "name": "every chart's query succeeds", "status": "pass",
                     "subject": "7 charts", "detail": ""},
                    {"category": "security", "name": "a reader sees only its rows", "status": "pass",
                     "subject": "probe account", "detail": ""},
                    {"category": "visual", "name": "no label is clipped", "status": "warn",
                     "subject": "ranking_1", "detail": "« Casablanca-Settat » is cut by 3 px"},
                    {"category": "performance", "name": "the dashboard loads under 10 s", "status": "pass",
                     "subject": "4.2 s", "detail": ""}]}}})
        if path.endswith("/package"):
            return self._send(200, b"PK\x05\x06" + b"\0" * 18, "application/zip")
        if "/lineage" in path:
            return self._json(200, {"result": {"dataset": "cem.qoe_daily", "model": "mart_cem_qoe_daily",
                                               "built_from": ["stg_cem_qoe", "dim_region"],
                                               "tests": {"passed": 24, "failed": 0}}})
        if path.endswith("/changes"):
            return self._json(200, {"result": {"same_as_validated": True, "charts": [], "why_not": None,
                                               "on_the_dashboard": [], "would_take": []}})
        return self._json(404, {"message": "the preview has no answer for that"})

    def do_PUT(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        if body.get("token") != token(state["draft"]):
            return self._json(409, {"message": "this draft was saved by someone else since the page was opened"})
        state["draft"] = {"spec": body["spec"], "saved_at": "2026-10-19T22:05:00Z", "saved_by": "dev.user"}
        return self._json(200, {"result": {"token": token(state["draft"]), "saved_at": "2026-10-19T22:05:00Z",
                                           "problems": [], "layout_of": "specification"}})

    def do_DELETE(self) -> None:  # noqa: N802
        state["draft"] = None
        return self._json(200, {"result": {"discarded": True, "token": "none"}})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path.endswith("/draft/validate"):
            return self._json(200, {"result": {"of": "draft", "summary": {
                "passed": True, "checks": 3, "failures": 0, "warnings": 1}, "problems": [], "checks": [
                    {"category": "specification", "name": "layout is the template's, or a change of it the "
                     "template allows", "status": "pass", "subject": "3 rows, placed in the specification",
                     "detail": ""},
                    {"category": "specification", "name": "metric is certified", "status": "warn",
                     "subject": "kpi_4 (big_number)", "detail": "cem.degraded_rate is not certified"}]}})
        if path.endswith("/pictures"):
            return self._json(200, {"result": payload()["pictures"]})
        if "/deploy" in path:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            done = {"id": "dep1", "project_id": "cem-qoe", "version": "1.0.0",
                    "target": body.get("target"), "by": "dev.user", "started_at": "2026-10-20T10:00:00Z",
                    "ended_at": "2026-10-20T10:00:31Z", "status": "succeeded",
                    "steps": [{"key": key, "title": title, "status": "done", "detail": detail}
                              for key, title, detail in [
                                  ("read_package", "Read the package and check its checksum",
                                   "cem-qoe-1.0.0.ssbi, 41 files"),
                                  ("check_compatibility", "Check it against the target's versions",
                                   "built against 6.1.0, run runs 6.1.0"),
                                  ("ensure_connection", "Point the governed connection at this environment",
                                   "SharpSight BI (#7)"),
                                  ("import_assets", "Import the dashboard, its charts and its datasets",
                                   "the export was accepted"),
                                  ("repoint_connection", "Repoint the imported connection", "points at run"),
                                  ("apply_access", "Grant the audience and the row-level rules",
                                   "3 roles, 1 row-level rule"),
                                  ("verify_assets", "Read back what arrived", "7 charts, 3 roles"),
                                  ("verify_data", "Ask every chart for its data", "7 charts answered"),
                                  ("record", "Record the deployment", "")]],
                    "dashboard": {"id": 42, "slug": "cem-qoe",
                                  "url": "https://bi.example.com/superset/dashboard/cem-qoe/"},
                    # the real engine adds one line per chart (deploy.py, step 8): an import answers 200
                    # for a dashboard whose charts cannot draw, so each one is asked for its data
                    "checks": [{"check": "charts arrived", "status": "pass", "detail": "7 of 7"},
                               {"check": "audience as SharpSight decided", "status": "pass",
                                "detail": "sharpsight_bi_developer, sharpsight_cem_analyst"}]
                               + [{"check": f"{title} returns data", "status": "pass", "detail": rows}
                                  for title, rows in [("QoE Score", "1 row"), ("Active users", "1 row"),
                                                      ("Degraded users", "1 row"), ("Degraded rate", "1 row"),
                                                      ("QoE Score, daily trend", "31 rows"),
                                                      ("QoE Score, distribution", "8 rows"),
                                                      ("Top regions by QoE", "5 rows")]],
                    "error": None}
            state.setdefault("deployments", []).append(done)
            return self._json(200, {"result": done})
        if path.endswith("/draft/commit"):
            return self._json(202, {"result": {"run_id": "preview00", "status": "running"}})
        if path.endswith("/alter"):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            spec = copy.deepcopy((state["draft"] or {}).get("spec") or SPEC)
            slot = body.get("slot")
            asked = {k: v for k, v in body.items() if k != "slot" and v}
            # an empty dimension is the one taken away — the inspector's "none" — as the engine reads it;
            # a null is a field not said
            cleared = [k for k, v in body.items() if k == "dimension" and v == ""]
            for component in spec["components"]:
                if component["slot"] == slot:
                    component.update(asked)
                    for name in cleared:
                        component.pop(name, None)
            said = ", ".join([f"{name} → {value}" for name, value in asked.items()]
                             + [f"{name} → none" for name in cleared])
            return self._json(200, {"result": {
                "request": f"change {slot}", "read_by": "the Studio's own controls",
                "understood": f"{slot}: {', '.join([*asked, *cleared])}", "summary": f"~ {slot}: {said}",
                "spec": spec,
                "changes": [{"kind": "option", "slot": slot, "says": f"{slot}: {said}"}],
                "touches": {"metrics": [], "datasets": [], "slots": [slot]},
                "validation": {"passed": True, "checks": 42, "failures": [], "warnings": []},
                "problems": [], "unresolved": []}})
        if path.endswith("/propose") or path.endswith("/compose"):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            return self._json(200, {"result": proposal(body)})
        return self._json(404, {"message": "the preview has no answer for that"})


def main() -> int:
    parsed = argparse.ArgumentParser(description=__doc__)
    parsed.add_argument("--port", type=int, default=8099)
    parsed.add_argument("--shot", help="photograph the page with Playwright and write it here, then stop")
    parsed.add_argument("--check", action="store_true",
                        help="drive the workspace in a browser and hold it to what it promises")
    parsed.add_argument("--width", type=int, default=1600)
    # a browser of another build than this Playwright's own, when the machine has one (the kit's engine image
    # carries its own; a developer's machine may not)
    parsed.add_argument("--browser", default=None, help="the chromium executable to use, if not the default")
    arguments = parsed.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", arguments.port), Handler)
    if not arguments.shot and not arguments.check:
        print(f"the Studio, with a stand-in engine: http://127.0.0.1:{arguments.port}/")
        server.serve_forever()
        return 0
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox"], executable_path=arguments.browser)
        page = browser.new_page(viewport={"width": arguments.width, "height": 1200})
        problems: list[str] = []
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(f"http://127.0.0.1:{arguments.port}/", wait_until="networkidle")
        page.wait_for_selector(".ss-tile")
        if arguments.shot:
            page.screenshot(path=arguments.shot, full_page=True)
            tiles = page.eval_on_selector_all(".ss-tile", "nodes => nodes.map(n => n.dataset.slot)")
            print(f"{len(tiles)} tiles: {', '.join(tiles)}")
        if arguments.check:
            problems += check(page)
        print("console and page errors:", "; ".join(problems) if problems else "none")
        browser.close()
    return 1 if problems else 0


ROW = ("() => Array.from(document.querySelectorAll('.ss-row')).map(row => row.dataset.row + ': ' + "
       "Array.from(row.querySelectorAll('.ss-tile')).map(tile => tile.dataset.slot + ' ' + "
       "tile.style.getPropertyValue('--w')).join(', '))")


def check(page) -> list[str]:  # noqa: ANN001 — a Playwright page
    """Hold the workspace to what it promises: the moves it offers, the minimum widths it must not go under,
    the section it must not let a chart leave, the draft it saves, and undo.

        python studio/preview.py --check [--browser /path/to/chromium]
    """
    import time

    said: list[str] = []
    expect = lambda what, ok: said.append(f"✗ {what}") if not ok else print(f"  ✓ {what}")  # noqa: E731

    expect("the canvas draws the template's three rows",
           [row.split(":")[0] for row in page.evaluate(ROW)] == ["kpis", "trend", "breakdown"])
    page.click('[data-slot="kpi_1"]')
    inspector = ".ss-stack > .ss-card:first-child"
    expect("selecting a chart opens its inspector",
           "kpi_1" in page.inner_text(f"{inspector} header").lower())
    expect("whose first tab is what the chart measures, as a control bound to the registry",
           page.eval_on_selector(f"{inspector} select", "n => n.value") == "cem.qoe_score")
    page.click(f'{inspector} .ss-tabs >> text=Governance')
    expect("and whose governance tab says who owns it and whether it is certified",
           "Certified" in page.inner_text(inspector) and "Owner" in page.inner_text(inspector))

    page.click(f'{inspector} .ss-tabs >> text=Chart')
    page.click('button[title^="Later on the row"]')
    time.sleep(0.2)
    expect("a chart moves later on its row",
           page.evaluate(ROW)[0].startswith("kpis: kpi_2 3, kpi_1 3"))
    page.click('button[title^="Wider"]')
    time.sleep(0.2)
    expect("widening a chart narrows its neighbour, and the row still fits",
           page.evaluate(ROW)[0] == "kpis: kpi_2 3, kpi_1 4, kpi_3 2, kpi_4 3")
    for _ in range(4):
        page.click('button[title^="Narrower"]')
        time.sleep(0.15)
    expect("a kpi is not taken below the template's minimum width, and is told so",
           page.evaluate(ROW)[0] == "kpis: kpi_2 3, kpi_1 2, kpi_3 4, kpi_4 3"
           and "as narrow as" in page.inner_text(".ss-toasts"))

    page.locator('[data-slot="kpi_3"]').scroll_into_view_if_needed()
    grip = page.eval_on_selector('[data-slot="kpi_3"] .ss-grip',
                                 "n => { const r = n.getBoundingClientRect();"
                                 "  return {x: r.x + r.width / 2, y: r.y + r.height / 2}; }")
    page.mouse.move(grip["x"], grip["y"])
    page.mouse.down()
    page.mouse.move(grip["x"] + 150, grip["y"], steps=10)
    page.mouse.up()
    time.sleep(0.3)
    expect("dragging the grip resizes, taking from the neighbour",
           page.evaluate(ROW)[0] == "kpis: kpi_2 3, kpi_1 2, kpi_3 5, kpi_4 2")

    page.click('.ss-rowbar button[title="Move this row down"]')
    time.sleep(0.2)
    expect("a row moves down", [row.split(":")[0] for row in page.evaluate(ROW)] == ["trend", "kpis", "breakdown"])

    page.drag_and_drop('[data-slot="kpi_1"]', '.ss-row[data-row="trend"]')
    time.sleep(0.4)
    expect("a chart may not leave its section, and is told why",
           "accountable to" in page.inner_text(".ss-toasts"))

    time.sleep(1.6)                                   # the draft saves itself a moment after the last change
    expect("the draft is saved", bool(state["draft"]) and "Draft saved" in page.inner_text(".ss-bar"))
    expect("and the drawer says what it changes about the version",
           bool(page.query_selector(".ss-drawer")))
    expect("and it carries the layout that was placed",
           state["draft"]["spec"]["layout"][1]["slots"][2]["id"] == "kpi_3")

    before = page.evaluate(ROW)
    page.click('button[title^="Undo"]')
    time.sleep(0.2)
    expect("undo takes back one step, not a whole drag", page.evaluate(ROW) != before
           and page.evaluate(ROW)[0].startswith("kpis"))

    page.click(".ss-bar >> text=Validate")
    page.wait_for_selector(".ss-checks")
    expect("validate shows the engine's own report", "passed" in page.inner_text(".ss-drawer"))

    # ── the keyboard: every move the mouse can make (§47.4) ────────────────────────────────────────────────
    page.click('[data-slot="trend_1"]')
    page.keyboard.press("BracketLeft")
    time.sleep(0.2)
    expect("the keyboard narrows the selected chart",
           "trend_1 7" in page.evaluate(ROW)[1] or "trend_1 7" in " ".join(page.evaluate(ROW)))
    page.keyboard.press("Shift+ArrowRight")
    time.sleep(0.2)
    expect("and moves it along its row", page.evaluate(ROW)[1].startswith("trend: distribution_1"))
    page.keyboard.press("Escape")
    expect("escape deselects", page.eval_on_selector_all('.ss-tile[aria-selected="true"]', "n => n.length") == 0)
    page.keyboard.press("?")
    page.wait_for_selector(".ss-modal")
    expect("« ? » lists the keyboard", "undo" in page.inner_text(".ss-modal"))
    page.keyboard.press("Escape")

    # ── the gallery: the manual path, which is not a prompt (§48.3) ────────────────────────────────────────
    page.click(".ss-bar >> text=Add chart")
    page.wait_for_selector(".ss-modal")
    expect("the gallery offers the template's free slot", "map_1" in page.inner_text(".ss-modal"))
    page.check('.ss-modal input[value="cem.qoe_score"]')
    page.click(".ss-modal >> text=Propose")
    page.wait_for_selector(".ss-proposal")
    expect("it answers with a proposal, and changes nothing yet",
           "map_1" in page.inner_text(".ss-proposal")
           and page.eval_on_selector_all('[data-slot="map_1"]', "n => n.length") == 0)
    page.click(".ss-proposal >> text=Apply to the draft")
    time.sleep(0.3)
    expect("applying it puts the chart on the canvas",
           page.eval_on_selector_all('[data-slot="map_1"]', "n => n.length") == 1)
    page.keyboard.press("Control+z")
    time.sleep(0.2)
    expect("and one undo takes it back",
           page.eval_on_selector_all('[data-slot="map_1"]', "n => n.length") == 0)

    # ── the copilot: a proposal, never a change (§47.6) ────────────────────────────────────────────────────
    page.click(".ss-chip >> text=make the trend weekly")
    page.wait_for_selector(".ss-proposal")
    expect("the copilot answers a request with a proposal",
           "P1W" in page.inner_text(".ss-proposal"))
    page.click(".ss-proposal >> text=Reject")
    time.sleep(0.2)
    expect("rejecting it leaves the draft alone",
           page.eval_on_selector_all(".ss-proposal", "n => n.length") == 0)

    # ── taking a chart off the dashboard ───────────────────────────────────────────────────────────────────
    page.click('[data-slot="kpi_4"]')
    page.keyboard.press("Backspace")
    time.sleep(0.3)
    expect("a chart taken off the dashboard is kept, parked",
           page.eval_on_selector_all('[data-slot="kpi_4"]', "n => n.length") == 0
           and "parked" in page.inner_text(".ss-card.ss-pane").lower())
    page.click('[data-slot="trend_1"]')
    page.keyboard.press("Backspace")
    time.sleep(0.3)
    expect("a mandatory chart is not", page.eval_on_selector_all('[data-slot="trend_1"]', "n => n.length") == 1)

    # ── the drawer: what a commit would change, what the last run found, where the versions stand ──────────
    page.click(".ss-drawer >> text=Releases")
    expect("the drawer lists the versions and says promotion is elsewhere",
           "1.0.0" in page.inner_text(".ss-drawer") and "command line" in page.inner_text(".ss-drawer"))
    page.click(".ss-drawer >> text=Last run")
    page.click(".ss-drawer >> text=Read the last run")
    page.wait_for_selector(".ss-table")
    expect("and shows the levels only a run can produce",
           "security" in page.inner_text(".ss-drawer") and "performance" in page.inner_text(".ss-drawer"))
    page.click(".ss-drawer >> text=What this is")
    expect("and says what this workspace is", "not a dashboard" in page.inner_text(".ss-drawer"))

    # ── the command palette: everything the workspace can do, by name ──────────────────────────────────────
    page.keyboard.press("Control+k")
    page.wait_for_selector(".ss-palette")
    expect("the palette lists what can be done, and the charts",
           "Validate" in page.inner_text(".ss-palette") and "QoE" in page.inner_text(".ss-palette"))
    page.keyboard.type("releases")
    time.sleep(0.2)
    expect("and narrows to what was typed",
           page.eval_on_selector_all(".ss-palette-row", "n => n.length") == 1)
    page.keyboard.press("Enter")
    time.sleep(0.2)
    expect("and running an entry does what the bar would",
           "1.0.0" in page.inner_text(".ss-drawer"))

    # ── light and dark ─────────────────────────────────────────────────────────────────────────────────────
    expect("light is the default", page.evaluate("() => document.documentElement.dataset.ssTheme") == "light")
    page.click('.ss-bar button[aria-label="Dark or light"]')
    time.sleep(0.2)
    expect("and dark is one button away, kept in the browser",
           page.evaluate("() => document.documentElement.dataset.ssTheme") == "dark"
           and page.evaluate("() => localStorage.getItem('sharpsight-studio-theme')") == "dark")
    expect("with every colour coming from the tokens, so the page follows",
           page.evaluate("() => getComputedStyle(document.querySelector('.ss-card')).backgroundColor")
           != "rgb(255, 255, 255)")
    page.click('.ss-bar button[aria-label="Dark or light"]')

    # ── the package a version was cut as ───────────────────────────────────────────────────────────────────
    expect("the latest version's package can be taken from the bar",
           page.eval_on_selector('.ss-bar >> text=Export .ssbi', "n => !n.disabled"))

    # ── narrow: the panes become drawers rather than squeezing the canvas ──────────────────────────────────
    page.set_viewport_size({"width": 900, "height": 900})
    time.sleep(0.3)
    expect("on a narrow screen the canvas keeps the width",
           page.eval_on_selector(".ss-canvas", "n => n.getBoundingClientRect().width") > 700)
    if page.eval_on_selector('.ss-bar button[aria-label="The explorer"]',
                             "n => n.getAttribute('aria-pressed') === 'true'"):
        page.click('.ss-bar button[aria-label="The explorer"]')       # close it, so the next click opens it
        time.sleep(0.2)
    page.click('.ss-bar button[aria-label="The explorer"]')
    time.sleep(0.3)
    expect("and the explorer opens over it, as a drawer",
           page.eval_on_selector(".ss-card.ss-pane", "n => getComputedStyle(n).position") == "fixed")
    page.set_viewport_size({"width": 1600, "height": 1200})
    time.sleep(0.3)

    # ── the component library (§47.3) ──────────────────────────────────────────────────────────────────────
    if page.eval_on_selector('.ss-bar button[aria-label="The explorer"]',
                             "n => n.getAttribute('aria-pressed') !== 'true'"):
        page.click('.ss-bar button[aria-label="The explorer"]')
        time.sleep(0.3)
    expect("the explorer offers the chart kinds, grouped by what they are for, each with its own icon",
           page.eval_on_selector_all(".ss-kind", "n => n.length") >= 6
           and page.eval_on_selector_all(".ss-kind-icon", "n => n.length") >= 6)
    expect("and says which are approved but not installed, rather than hiding them",
           "not installed" in page.inner_text(".ss-card.ss-pane"))
    page.click('.ss-kind[data-kind="map"]')
    page.wait_for_selector(".ss-modal")
    expect("clicking one opens the gallery on the slot that accepts it, with the kind chosen",
           "map_1" in page.inner_text(".ss-modal"))
    page.click(".ss-modal >> text=Cancel")

    # ── the chrome of a drawing tool: a label, handles, and the columns while something moves ──────────────
    page.click('[data-slot="trend_1"]')
    time.sleep(0.2)
    expect("a selected chart wears a label and eight handles",
           page.eval_on_selector_all('[data-slot="trend_1"] .ss-handle', "n => n.length") == 8
           and page.eval_on_selector('[data-slot="trend_1"] .ss-label',
                                     "n => getComputedStyle(n).opacity") == "1")
    page.locator('[data-slot="kpi_3"]').scroll_into_view_if_needed()
    time.sleep(0.2)
    grip = page.eval_on_selector('[data-slot="kpi_3"] .ss-grip',
                                 "n => { const r = n.getBoundingClientRect();"
                                 "  return {x: r.x + r.width / 2, y: r.y + r.height / 2}; }")
    page.mouse.move(grip["x"], grip["y"])
    page.mouse.down()
    page.mouse.move(grip["x"] + 40, grip["y"], steps=4)
    expect("and the twelve columns are shown while a width is dragged",
           page.eval_on_selector(".ss-canvas", "n => n.dataset.guides") == "true")
    page.mouse.up()
    time.sleep(0.2)
    expect("and are gone when it is let go",
           page.eval_on_selector(".ss-canvas", "n => n.dataset.guides") is None)

    # ── live: Superset's own dashboard, inside the workspace, with the chrome over it ──────────────────────
    page.click('.ss-switch >> text=Live')
    page.wait_for_selector(".ss-live-frame")
    page.wait_for_function("() => document.querySelector('.ss-overlay')"
                           " && Number(document.querySelector('.ss-overlay').dataset.boxes) > 0")
    expect("live shows the dashboard Superset draws, not a photograph",
           page.eval_on_selector_all(".ss-live-frame", "n => n.length") == 1)
    expect("and a box over every chart it holds, read from that dashboard's own DOM",
           int(page.eval_on_selector(".ss-overlay", "n => n.dataset.boxes")) == 7)
    page.click('.ss-overlay [data-slot="trend_1"]')
    time.sleep(0.3)
    expect("clicking one selects that chart in the inspector",
           "trend_1" in page.inner_text(".ss-stack > .ss-card:first-child header").lower())
    expect("and the Studio says the draft is not in what is shown",
           page.eval_on_selector_all(".ss-callout",
                                     "ns => ns.some(n => n.textContent.includes('until it is committed'))"))
    page.click('.ss-switch >> text=Compose')
    time.sleep(0.3)
    expect("compose comes back to the layout that can be moved",
           page.eval_on_selector_all(".ss-tile", "n => n.length") > 0)

    # ── the rail, and the areas of the workspace ───────────────────────────────────────────────────────────
    expect("a rail of where to go next: SharpSight's pages this person may open, and Superset's own",
           page.eval_on_selector_all(".ss-rail-link", "n => n.length") >= 7
           and "SQL Lab" in page.inner_text(".ss-rail")
           and "Data Quality" in page.inner_text(".ss-rail"))
    expect("and the Studio is the one marked as where you are",
           page.eval_on_selector('.ss-rail-link[aria-current="page"]', "n => n.textContent") == "Studio")
    page.click('.ss-areas >> text=Filters')
    time.sleep(0.2)
    expect("the Filters area is the dashboard's filters, with the template's limit",
           "of 5" in page.inner_text(".ss-panes") and "Region" in page.inner_text(".ss-panes"))
    page.click('.ss-areas >> text=Data')
    time.sleep(0.2)
    expect("the Data area is what the dashboard reads, with its lineage to ask for",
           "cem.qoe_daily" in page.inner_text(".ss-panes"))
    page.click('.ss-areas >> text=Interactions')
    time.sleep(0.2)
    expect("the Interactions area says what a specification carries and what is Superset's",
           "cross-filtering" in page.inner_text(".ss-panes"))
    page.click('.ss-areas >> text=Design')
    time.sleep(0.2)

    # ── the inspector's own controls: a change is a proposal ───────────────────────────────────────────────
    page.click('[data-slot="ranking_1"]')
    page.click('.ss-stack > .ss-card:first-child .ss-tabs >> text=Chart')
    expect("the kinds the slot accepts are a row of icons, the current one pressed",
           page.eval_on_selector_all(".ss-kindbtn", "n => n.length") >= 2
           and page.eval_on_selector('.ss-kindbtn[data-kind="bar"]',
                                     "n => n.getAttribute('aria-pressed')") == "true")
    page.click('.ss-kindbtn[data-kind="table"]')
    page.wait_for_selector(".ss-proposal")
    expect("and switching to another asks for a proposal rather than changing the draft",
           "table" in page.inner_text(".ss-proposal"))
    page.click(".ss-proposal >> text=Reject")
    page.click('.ss-stack > .ss-card:first-child .ss-tabs >> text=Content')
    page.select_option('.ss-stack select >> nth=0', "cem.active_users")
    page.wait_for_selector(".ss-proposal")
    expect("changing what a chart measures asks for one too",
           "cem.active_users" in page.inner_text(".ss-proposal"))
    page.click(".ss-proposal >> text=Reject")

    # ── the width it is read at, as devices ────────────────────────────────────────────────────────────────
    page.click('.ss-devices button[aria-label="Phone, 420"]')
    time.sleep(0.2)
    expect("and the canvas can be seen at a phone's width",
           page.eval_on_selector(".ss-frame", "n => n.style.width") == "420px")
    page.click('.ss-devices button[aria-label="Fit to the pane"]')

    # ── deploying to another Superset (§55) ────────────────────────────────────────────────────────────────
    page.click(".ss-bar >> text=Deploy")
    page.wait_for_selector(".ss-table")
    expect("the Deployments tab says where a version may go, and what is the command line's",
           "run" in page.inner_text(".ss-drawer") and "command line only" in page.inner_text(".ss-drawer"))
    # (what may NOT be deployed — a version nobody signed off, a package that changed, a chart that cannot
    #  draw — is held by services/ai-bi/tests/test_deploy.py, against the engine's own refusals)
    page.click('.ss-drawer button:has-text("Deploy 1.0.0")')
    page.wait_for_selector(".ss-modal")
    expect("asking for it opens a sheet that says what a deployment does, in order",
           page.locator(".ss-modal .ss-steps li").count() == 5
           and "repointed" in page.inner_text(".ss-modal"))
    page.fill(".ss-modal .ss-body input", "checked the QoE trend against the warehouse")
    page.click('.ss-modal button:has-text("Deploy to run")')
    page.wait_for_selector(".ss-panel")
    expect("and a deployment is shown step by step, with what was read back afterwards",
           "Ask every chart for its data" in page.inner_text(".ss-drawer")
           and "charts arrived" in page.inner_text(".ss-drawer"))
    # …and all of it is READ, not scrolled inside a window of its own: the last steps are the ones that say
    # whether it worked, and they were hidden by the cap a validation's hundreds of checks need
    expect("and none of its steps is hidden inside a scrolling box",
           page.eval_on_selector(".ss-panel .ss-checks",
                                 "n => n.scrollHeight <= n.clientHeight + 1"))

    # ── the dashboard's own inspector (§47.5) ──────────────────────────────────────────────────────────────
    if page.eval_on_selector('.ss-bar button[aria-label="The copilot"]',
                             "n => n.getAttribute('aria-pressed') !== 'true'"):
        page.click('.ss-bar button[aria-label="The copilot"]')
        time.sleep(0.3)
    page.keyboard.press("Escape")
    time.sleep(0.2)
    inspector = ".ss-stack > .ss-card:first-child"
    expect("with nothing selected the inspector is the dashboard's",
           "dashboard" in page.inner_text(f"{inspector} header").lower())
    page.click(f'{inspector} .ss-tabs >> text=Filters')
    expect("whose filters are the columns the template allows, on datasets it already uses",
           "Region" in page.inner_text(inspector) and "of 5" in page.inner_text(inspector))
    page.click(f'{inspector} .ss-tabs >> text=Security')
    expect("and whose security says who may open it and which rows they see",
           "bi_reviewer" in page.inner_text(inspector) and "cem_analyst" in page.inner_text(inspector))
    page.click(f'{inspector} .ss-tabs >> text=Layout')
    expect("and whose layout says who laid it out", "template" in page.inner_text(inspector).lower())

    # ── zoom and the width it is read at (§47.4) ───────────────────────────────────────────────────────────
    page.click('.ss-card button[aria-label="Zoom out"]')
    time.sleep(0.2)
    expect("the canvas zooms out", "scale(0.9)" in page.eval_on_selector(".ss-frame", "n => n.style.transform"))
    page.click('.ss-devices button[aria-label="Tablet, 1024"]')
    time.sleep(0.2)
    expect("and can be seen at the width it will be read at",
           page.eval_on_selector(".ss-frame", "n => n.style.width") == "1024px")
    page.click('.ss-devices button[aria-label="Fit to the pane"]')
    return said


if __name__ == "__main__":
    sys.exit(main())
