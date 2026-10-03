"""Looking at the dashboard, the way a person would — and measuring what is seen.

Superset 6.1's image carries no headless browser, so its screenshot endpoints cannot work in this kit. The
engine therefore opens the dashboard in a browser of its own, signed in as its own service account, and takes
three kinds of evidence from one page load:

  · the PICTURE  — a full-page screenshot, kept with the version;
  · the GEOMETRY — where each chart landed, what text is clipped, what the page itself reports as an error;
  · the TRAFFIC  — every data request the real charts made: status, duration, rows, bytes. This is the only
                   honest answer to « does the dashboard's own query succeed », because it is the dashboard
                   asking, through each chart plugin's own request, not this service imitating it.

No model looks at the picture today (aibi/llm.py). Every check below is a measurement, so it means the same
thing on every run; a vision model, when one is chosen, adds judgement on top and replaces none of it.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, quote, urlparse

import prison

from .models import ComponentOptions, DashboardSpec, TemplateDef, ValidationReport
from .superset_adapter import sign_in

_COMPONENTS_JS = """
() => Array.from(document.querySelectorAll('.dashboard-component-chart-holder')).map(holder => {
  const slice = holder.querySelector('[data-test-chart-id]');
  const box = holder.getBoundingClientRect();
  const clipped = [];
  holder.querySelectorAll('*').forEach(el => {
    if (el.children.length !== 0 || !el.textContent.trim()) return;
    if (el.scrollWidth <= el.clientWidth || el.clientWidth === 0) return;
    const style = getComputedStyle(el);
    if (style.overflowX === 'visible' && style.textOverflow !== 'ellipsis') return;
    clipped.push({text: el.textContent.trim().slice(0, 80), needs: el.scrollWidth, has: el.clientWidth,
                  in_title: !!el.closest('.header-title, [data-test="slice-header"]')});
  });
  let smallest = null;
  holder.querySelectorAll('*').forEach(el => {
    if (el.children.length !== 0 || !el.textContent.trim() || el.closest('svg')) return;
    const size = parseFloat(getComputedStyle(el).fontSize);
    if (size && (!smallest || size < smallest.px)) smallest = {px: size, text: el.textContent.trim().slice(0, 60)};
  });
  const alert = holder.querySelector('[role="alert"], .ant-alert-error, [data-test="chart-error"]');
  const loading = holder.querySelector('[data-test="loading-indicator"], .loading');
  return {
    chart_id: slice ? Number(slice.getAttribute('data-test-chart-id')) : null,
    viz_type: slice ? slice.getAttribute('data-test-viz-type') : null,
    name: slice ? slice.getAttribute('data-test-chart-name') : null,
    rect: {x: Math.round(box.x + window.scrollX), y: Math.round(box.y + window.scrollY),
           w: Math.round(box.width), h: Math.round(box.height)},
    clipped: clipped.slice(0, 6),
    smallest_text: smallest,
    error: alert ? alert.textContent.trim().slice(0, 240) : null,
    loading: !!loading,
    drawn: !!holder.querySelector('canvas, svg path, svg rect, table'),
  };
})
"""

# The dashboard's own title. The engine's header sizes it from the text, and gets it wrong by a few pixels
# for some titles: the last word then ends in an ellipsis (seen on « CEM QoE regional Operations »).
_HEADER_JS = """
() => {
  const title = document.querySelector('[data-test="editable-title-input"]');
  return title ? {text: (title.value || title.textContent || '').trim(), needs: title.scrollWidth,
                  has: title.clientWidth} : null;
}
"""

_SETTLED_JS = """
(expected) => {
  const holders = document.querySelectorAll('.dashboard-component-chart-holder');
  if (holders.length < expected) return false;
  return !document.querySelector(
    '.dashboard-component-chart-holder [data-test="loading-indicator"], .dashboard-component-chart-holder .loading');
}
"""


@dataclass
class Render:
    viewport: str
    width: int
    screenshot: Optional[str]
    load_ms: int
    components: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    header: Optional[dict[str, Any]] = None
    page_width: int = 0
    error: Optional[str] = None

    def describe(self) -> dict[str, Any]:
        body = asdict(self)
        body["components"] = [{k: v for k, v in c.items() if k != "clipped"} | {"clipped": len(c.get("clipped", []))}
                              for c in self.components]
        return body


@dataclass
class Fix:
    slot: str
    code: str
    description: str
    apply: Callable[[DashboardSpec], None]


class VisualQaAgent:
    def __init__(self, base_url: str, username: str, password: str, policies: dict[str, Any]):
        self.base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self.policies = policies

    # ── signing in ──────────────────────────────────────────────────────────────────────────────────────────
    def session_cookie(self) -> dict[str, Any]:
        """The service account's session, for the browser to carry: it never loads the sign-in page
        (superset_adapter.sign_in says why)."""
        session = sign_in(self.base_url, self._username, self._password)
        cookie = next(c for c in session.cookies if "session" in c.name)
        return {"name": cookie.name, "value": cookie.value, "url": self.base_url, "httpOnly": True}

    # ── rendering ───────────────────────────────────────────────────────────────────────────────────────────
    def render(self, dashboard_id: int, expected_charts: int, out_dir: Path, viewport: str, width: int,
               timeout_ms: int = 90000, native_filters: Optional[dict[str, Any]] = None) -> Render:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        out_dir.mkdir(parents=True, exist_ok=True)
        shot = out_dir / f"dashboard-{viewport}.png"
        render = Render(viewport=viewport, width=width, screenshot=None, load_ms=0)
        responses: list[Any] = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
                try:
                    # Tall on purpose: Superset draws a chart only once it scrolls into view, and a chart that
                    # was never drawn would be reported as empty.
                    context = browser.new_context(viewport={"width": width, "height": 2600},
                                                  device_scale_factor=1, locale="en-US")
                    context.add_cookies([self.session_cookie()])
                    page = context.new_page()
                    page.on("console", lambda m: render.console_errors.append(m.text[:300])
                            if m.type == "error" else None)

                    page.on("response", lambda r: responses.append(r)
                            if ("/api/v1/chart/data" in r.url or "/explore_json" in r.url) else None)
                    address = f"{self.base_url}/superset/dashboard/{dashboard_id}/?standalone=1"
                    if native_filters:
                        address += "&native_filters=" + quote(prison.dumps(native_filters), safe="")
                    started = time.monotonic()
                    page.goto(address, wait_until="domcontentloaded", timeout=timeout_ms)
                    if "/login" in page.url:
                        raise RuntimeError("the BI engine sent the browser to its sign-in page: the session "
                                           "it was given was not accepted")
                    page.wait_for_selector(".dashboard-component-chart-holder", timeout=timeout_ms)
                    page.wait_for_function(_SETTLED_JS, arg=expected_charts, timeout=timeout_ms)
                    try:
                        page.wait_for_load_state("networkidle", timeout=20000)
                    except PlaywrightError:
                        pass
                    render.load_ms = int((time.monotonic() - started) * 1000)
                    page.wait_for_timeout(1500)        # let the charts finish animating before the picture
                    render.components = page.evaluate(_COMPONENTS_JS)
                    render.header = page.evaluate(_HEADER_JS)
                    render.page_width = page.evaluate("() => document.documentElement.scrollWidth")
                    # the picture stops where the dashboard does, not at the bottom of a tall window
                    bottom = max((c["rect"]["y"] + c["rect"]["h"] for c in render.components), default=900) + 24
                    page.screenshot(path=str(shot), clip={"x": 0, "y": 0, "width": width,
                                                          "height": min(bottom, 2600)})
                    render.screenshot = str(shot)
                    render.calls = [self._call(r) for r in responses]
                finally:
                    browser.close()
        except Exception as exc:  # noqa: BLE001 — a render that fails is a finding, not a crash
            render.error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:300]}"
        if render.screenshot:
            self._measure_blankness(render)
        return render

    @staticmethod
    def _call(response: Any) -> dict[str, Any]:
        request = response.request
        chart_id = None
        try:
            form = parse_qs(urlparse(response.url).query).get("form_data", ["{}"])[0]
            chart_id = json.loads(form).get("slice_id")
        except (ValueError, AttributeError):
            pass
        call: dict[str, Any] = {"kind": "chart" if chart_id else "filter", "chart_id": chart_id,
                                "status": response.status, "duration_ms": None, "bytes": None,
                                "rowcount": None, "error": None, "digest": None}
        try:
            timing = request.timing
            if timing.get("responseEnd", -1) >= 0:
                call["duration_ms"] = int(timing["responseEnd"] - max(timing.get("requestStart", 0), 0))
        except Exception:  # noqa: BLE001
            pass
        try:
            body = response.body()
            call["bytes"] = len(body)
            payload = json.loads(body)
            result = (payload.get("result") or [payload])[0] if isinstance(payload, dict) else {}
            call["rowcount"] = result.get("rowcount")
            # a fingerprint of the rows, kept instead of the rows: enough to tell that two answers differ
            call["digest"] = hashlib.sha1(
                json.dumps(result.get("data"), sort_keys=True, default=str).encode()).hexdigest()
            call["error"] = result.get("error") or (payload.get("message") if response.status >= 400 else None)
            if call["error"] is None and payload.get("errors"):
                call["error"] = str(payload["errors"][0].get("message", payload["errors"][0]))[:300]
        except Exception:  # noqa: BLE001
            pass
        return call

    def _measure_blankness(self, render: Render) -> None:
        """How much of each chart's body is one single colour. A chart that drew nothing is nearly all of it."""
        from PIL import Image

        with Image.open(render.screenshot) as image:
            picture = image.convert("RGB")
            for component in render.components:
                r = component["rect"]
                # below the title, inside the card's padding
                box = (r["x"] + 12, r["y"] + 48, r["x"] + r["w"] - 12, r["y"] + r["h"] - 12)
                if box[2] - box[0] < 20 or box[3] - box[1] < 20:
                    component["blank_ratio"] = None
                    continue
                crop = picture.crop(box)
                colors = crop.getcolors(maxcolors=crop.width * crop.height)
                component["blank_ratio"] = round(max(count for count, _ in colors) / (crop.width * crop.height), 4)

    # ── checking ────────────────────────────────────────────────────────────────────────────────────────────
    def check(self, render: Render, spec: DashboardSpec, template: TemplateDef, charts: dict[str, dict[str, Any]],
              data: dict[str, dict[str, Any]]) -> ValidationReport:
        """`charts`: slot → the engine's chart; `data`: slot → what the component's query returned."""
        report = ValidationReport()
        view = render.viewport
        if render.error or not render.screenshot:
            report.add("visual", "screenshot generated", "fail", view, render.error or "no picture was produced",
                       code="render_failed")
            return report
        report.add("visual", "screenshot generated", "pass", view, Path(render.screenshot).name,
                   load_ms=render.load_ms)

        by_chart = {c["chart_id"]: c for c in render.components if c.get("chart_id")}
        slot_of = {chart["id"]: slot for slot, chart in charts.items()}
        mandatory = {slot for slot, body in template.slots.items() if body.mandatory}
        blank_at = float(self.policies.get("visual", {}).get("blank_ratio", 0.995))
        min_px = int(self.policies.get("visual", {}).get("min_px_per_category", 16))
        min_font = float(self.policies.get("visual", {}).get("min_font_px", 9))

        # A warning, not a failure: the specification cannot cure it (the title is the template's pattern
        # and the request's subject) and the cut is the engine's own sizing. A reviewer should still know.
        header = render.header
        # one pixel is enough: with an ellipsis, a title 1 px too long loses its last letters (« Experien… »
        # at 372 px needed for 371 given)
        if header and header["needs"] > header["has"]:
            report.add("visual", "dashboard title is not clipped", "warn", view,
                       f"« {header['text']} » needs {header['needs']} px and the header gives it {header['has']}",
                       code="dashboard_title_clipped")
        elif header:
            report.add("visual", "dashboard title is not clipped", "pass", view, header["text"])

        missing = [slot for slot, chart in charts.items() if chart["id"] not in by_chart]
        report.add("visual", "every component is on the page", "fail" if missing else "pass", view,
                   ", ".join(missing), code="component_missing" if missing else None)

        # overlap, and nothing wider than the page
        boxes = [(slot_of.get(cid, str(cid)), c["rect"]) for cid, c in by_chart.items()]
        overlaps = []
        for i, (slot_a, a) in enumerate(boxes):
            for slot_b, b in boxes[i + 1:]:
                wide = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
                high = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
                if wide > 1 and high > 1:
                    overlaps.append(f"{slot_a} × {slot_b}")
        report.add("visual", "no component overlaps another", "fail" if overlaps else "pass", view,
                   ", ".join(overlaps), code="overlap" if overlaps else None)
        spill = [slot for slot, r in boxes if r["x"] + r["w"] > render.width + 1]
        report.add("visual", "nothing is wider than the page", "fail" if spill else "pass", view, ", ".join(spill))

        # layout consistency: a template row is one line of equal-height cards, in the template's order
        problems = []
        previous_bottom = -1
        for row in template.layout:
            rects = [by_chart[charts[p.id]["id"]]["rect"] for p in row.slots
                     if p.id in charts and charts[p.id]["id"] in by_chart]
            if not rects:
                continue
            if max(r["y"] for r in rects) - min(r["y"] for r in rects) > 2:
                problems.append(f"row {row.row}: cards do not start on one line")
            if max(r["h"] for r in rects) - min(r["h"] for r in rects) > 2:
                problems.append(f"row {row.row}: cards differ in height")
            if [r["x"] for r in rects] != sorted(r["x"] for r in rects):
                problems.append(f"row {row.row}: cards are not in the template's order")
            if min(r["y"] for r in rects) < previous_bottom - 1:
                problems.append(f"row {row.row}: starts above the end of the row before it")
            previous_bottom = max(r["y"] + r["h"] for r in rects)
        report.add("visual", "layout follows the template", "fail" if problems else "pass", view,
                   "; ".join(problems), code="layout" if problems else None)

        for slot, chart in charts.items():
            component = by_chart.get(chart["id"])
            if component is None:
                continue
            spec_component = spec.component(slot)
            subject = f"{slot} · {view}"
            if component.get("error") or component.get("loading"):
                report.add("visual", "component rendered", "fail", subject,
                           component.get("error") or "still loading when the picture was taken",
                           code="chart_error", slot=slot)
                continue

            rows = (data.get(slot) or {}).get("rowcount")
            blank = component.get("blank_ratio")
            empty = rows == 0 or (blank is not None and blank >= blank_at)
            if empty:
                why = "its query returned no row" if rows == 0 else f"{blank:.1%} of its body is one colour"
                report.add("visual", "component is not empty", "fail" if slot in mandatory else "warn", subject,
                           why, code="empty_component", slot=slot, blank_ratio=blank)
            else:
                report.add("visual", "component is not empty", "pass", subject, blank_ratio=blank)

            clipped = component.get("clipped", [])
            titles = [c for c in clipped if c.get("in_title")]
            others = [c for c in clipped if not c.get("in_title")]
            if titles:
                report.add("visual", "text is not clipped", "fail", subject,
                           f"the title « {titles[0]['text']} » needs {titles[0]['needs']} px and has {titles[0]['has']}",
                           code="title_clipped", slot=slot)
            elif others:
                report.add("visual", "text is not clipped", "fail", subject,
                           f"« {others[0]['text']} » needs {others[0]['needs']} px and has {others[0]['has']}",
                           code="text_clipped", slot=slot)
            else:
                report.add("visual", "text is not clipped", "pass", subject)

            smallest = component.get("smallest_text")
            if smallest and smallest["px"] < min_font:
                report.add("visual", "text is large enough to read", "fail", subject,
                           f"« {smallest['text']} » is drawn at {smallest['px']:g} px, under {min_font:g}",
                           code="text_too_small", slot=slot)
            elif smallest:
                report.add("visual", "text is large enough to read", "pass", subject, f"smallest {smallest['px']:g} px")

            # labels: a ranking needs room for each of its categories, and for the longest of their names
            if spec_component is not None and spec_component.type == "bar" and rows:
                rect = component["rect"]
                available = rect["h"] - 110          # title, axis and padding
                per_category = available / rows
                longest = max((len(str(r.get(spec_component.dimension, ""))) for r in
                               (data.get(slot) or {}).get("data", [])), default=0)
                if per_category < min_px:
                    fits = max(3, int(available // min_px))
                    report.add("visual", "labels are readable", "fail", subject,
                               f"{rows} categories in {available} px is {per_category:.0f} px each, under {min_px}",
                               code="labels_too_dense", slot=slot, fits=fits)
                elif longest * 6.5 > rect["w"] * 0.5:
                    report.add("visual", "labels are readable", "warn", subject,
                               f"the longest label ({longest} characters) takes over half the chart's width",
                               code="labels_too_long", slot=slot)
                else:
                    report.add("visual", "labels are readable", "pass", subject,
                               f"{per_category:.0f} px per category")
        return report

    # ── fixing ──────────────────────────────────────────────────────────────────────────────────────────────
    def propose_fixes(self, report: ValidationReport, spec: DashboardSpec, template: TemplateDef,
                      short_names: dict[str, str]) -> list[Fix]:
        """What the engine may change on its own to cure a visual finding. Each fix touches ONE bounded option
        of ONE component — the same knobs the specification allows anyone — and the corrected specification
        is validated again before the engine is called."""
        fixes: list[Fix] = []
        seen: set[tuple[str, str]] = set()
        for check in report.failures:
            if check.category != "visual" or not check.slot or (check.slot, check.code) in seen:
                continue
            seen.add((check.slot, check.code or ""))
            component = spec.component(check.slot)
            if component is None:
                continue
            slot = template.slots[check.slot]
            if check.code == "labels_too_dense" and component.options.row_limit is None:
                fits = int(check.evidence.get("fits", 10))
                fixes.append(Fix(check.slot, check.code, f"show the top {fits} only, which the slot has room for",
                                 lambda s, slot=check.slot, n=fits: _set_option(s, slot, row_limit=n)))
            elif check.code == "title_clipped" and component.title is None:
                short = short_names.get(component.metric)
                if short:
                    words = (component.dimension or "").replace("_", " ")
                    title = short if slot.purpose == "kpi" or not words else (
                        f"{short} by {words.upper() if len(words) <= 3 else words}")
                    fixes.append(Fix(check.slot, check.code, f"shorten the title to « {title} »",
                                     lambda s, slot=check.slot, t=title: _set_title(s, slot, t)))
            elif check.code == "text_clipped" and component.type == "big_number" \
                    and component.options.number_format != "compact":
                fixes.append(Fix(check.slot, check.code, "show the number compact (1.2M)",
                                 lambda s, slot=check.slot: _set_option(s, slot, number_format="compact")))
            elif check.code == "chart_error":
                alternatives = [k for k in slot.accepts if k != component.type]
                if alternatives and component.dimension and alternatives[0] in ("table", "bar", "pie"):
                    kind = alternatives[0]
                    fixes.append(Fix(check.slot, check.code, f"draw it as a {kind}, which the slot also accepts",
                                     lambda s, slot=check.slot, k=kind: _set_type(s, slot, k)))
        return fixes


def _set_option(spec: DashboardSpec, slot: str, **changes: Any) -> None:
    component = spec.component(slot)
    component.options = ComponentOptions(**{**component.options.model_dump(), **changes})


def _set_title(spec: DashboardSpec, slot: str, title: str) -> None:
    spec.component(slot).title = title


def _set_type(spec: DashboardSpec, slot: str, kind: str) -> None:
    spec.component(slot).type = kind
