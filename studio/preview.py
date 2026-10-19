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
import struct
import sys
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
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
    ("map_1", "table", "cem.qoe_score", "region", "QoE by region"),
]
SPEC = {
    "dashboard": {"id": "cem-qoe", "template": "executive", "title": "CEM QoE Overview",
                  "description": "Headline QoE, how it moves, where it stands.", "owner": "cem-team"},
    "filters": ["date", "region", "rat"],
    "components": [{"slot": slot, "type": kind, "metric": metric, "dimension": dimension, "title": title,
                    "dataset": "cem.qoe_daily", "options": {}} for slot, kind, metric, dimension, title in CHARTS],
    "sections": [], "layout": [],
}
COLOURS = {"kpi_1": (9, 114, 188), "kpi_2": (38, 171, 168), "kpi_3": (242, 183, 45), "kpi_4": (55, 161, 94),
           "trend_1": (9, 114, 188), "distribution_1": (38, 171, 168), "ranking_1": (91, 100, 114),
           "map_1": (214, 69, 69)}
state = {"draft": None}


def png(width: int, height: int, colour: tuple[int, int, int]) -> bytes:
    """A flat picture, in place of a chart's. Enough to see where a tile is and how wide; the real tiles are
    cut out of a photograph of the real dashboard (aibi/picture.py)."""
    rows = b"".join(b"\x00" + bytes(colour) * width for _ in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


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
                    "dashboard": "http://127.0.0.1:3002/superset/dashboard/7/", "may_edit": True},
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
        "pictures": {"taken_at": "2026-10-19T22:00:00Z", "dashboard": 7, "width": 1440, "load_ms": 4200,
                     "slots": {slot: {"width": 300, "height": 200, "drawn": True} for slot, *_ in CHARTS},
                     "missing": [], "console_errors": []},
    }


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>SharpSight Studio — preview</title>
<link rel="stylesheet" href="/studio.css">
<style>body { margin: 0; } #bar { background: #0972BC; color: #fff; padding: 8px 20px; font: 13px Inter,
sans-serif; } </style></head>
<body><div id="bar">Superset's own top bar stands here · this page is studio/preview.py, not Superset</div>
<main id="sharpsight-studio" data-project="cem-qoe" data-user="dev.user" data-csrf="preview"></main>
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
        if path in ("/studio.js", "/studio.css"):
            kind = "text/javascript" if path.endswith(".js") else "text/css"
            return self._send(200, (HERE / path.lstrip("/")).read_bytes(), f"{kind}; charset=utf-8")
        if path.endswith("/studio"):
            return self._json(200, {"result": payload()})
        if "/picture/" in path:
            slot = path.rsplit("/", 1)[-1]
            return self._send(200, png(60, 40, COLOURS.get(slot, (200, 200, 200))), "image/png")
        if path.endswith("/datasets"):
            return self._json(200, {"result": [{"id": "cem.qoe_daily", "name": "CEM QoE Daily", "certified": True},
                                               {"id": "cem.region", "name": "Regions", "certified": True},
                                               {"id": "cem.qoe_site_daily", "name": "QoE by site",
                                                "certified": False}]})
        if path.endswith("/metrics"):
            return self._json(200, {"result": [{"id": m, "name": m, "certified": m != "cem.degraded_rate"}
                                               for m in ("cem.qoe_score", "cem.active_users",
                                                         "cem.degraded_users", "cem.degraded_rate")]})
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
        if path.endswith("/draft/commit"):
            return self._json(202, {"result": {"run_id": "preview00", "status": "running"}})
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
    expect("selecting a chart opens its inspector", "KPI_1" in page.inner_text(".ss-pane .ss-card header"))

    page.click('button[title="Later on the row"]')
    time.sleep(0.2)
    expect("a chart moves later on its row",
           page.evaluate(ROW)[0].startswith("kpis: kpi_2 3, kpi_1 3"))
    page.click('button[title="Wider"]')
    time.sleep(0.2)
    expect("widening a chart narrows its neighbour, and the row still fits",
           page.evaluate(ROW)[0] == "kpis: kpi_2 3, kpi_1 4, kpi_3 2, kpi_4 3")
    for _ in range(4):
        page.click('button[title="Narrower"]')
        time.sleep(0.1)
    expect("a kpi is not taken below the template's minimum width",
           page.evaluate(ROW)[0] == "kpis: kpi_2 3, kpi_1 2, kpi_3 4, kpi_4 3")

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
    time.sleep(0.3)
    refused = page.query_selector('.ss-callout[data-kind="bad"]')
    expect("a chart may not leave its section, and is told why",
           bool(refused) and "accountable to" in refused.inner_text())

    time.sleep(1.6)                                   # the draft saves itself a moment after the last change
    expect("the draft is saved", bool(state["draft"]) and "Draft saved" in page.inner_text(".ss-bar"))
    expect("and it carries the layout that was placed",
           state["draft"]["spec"]["layout"][1]["slots"][2]["id"] == "kpi_3")

    before = page.evaluate(ROW)
    page.click('button[title="Undo"]')
    time.sleep(0.2)
    expect("undo takes back one step, not a whole drag", page.evaluate(ROW) != before
           and page.evaluate(ROW)[0].startswith("kpis"))

    page.click("text=Validate")
    page.wait_for_selector(".ss-checks")
    expect("validate shows the engine's own report", "passed" in page.inner_text(".ss-drawer"))
    page.click("text=What this is")
    expect("and the drawer says what this workspace is", "not a dashboard" in page.inner_text(".ss-drawer"))
    return said


if __name__ == "__main__":
    sys.exit(main())
