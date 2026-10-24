"""A picture of each chart, for the Studio's canvas — cut out of one picture of the dashboard.

The canvas has to show something true in every tile while a person moves tiles around. Four ways were
weighed, and three were refused:

  · a second renderer of ours — refused: it is the one thing the brief forbids outright (§0, principle 5),
    and it would answer a different question than the dashboard does;
  · Superset's own screenshot endpoints — they cannot work in this kit: its image carries no browser
    (docs/lessons.md). That is why the engine brought its own;
  · one browser page per chart — a chart's own address in 6.1.0 is Explore, a page built for editing, and
    nothing promises it renders standalone. Unverified roads do not go into a canvas.

What is left is the one road already measured every time a version is cut: open the DASHBOARD in the
engine's own browser, signed in as its service account, wait until every chart has drawn (visual_qa_agent
does all of this), and cut each chart's rectangle out of the picture — the browser already reports where
each one landed, and which chart it is. One page load gives every tile at once, and a tile is then exactly
what the dashboard shows, because it IS what the dashboard showed.

A tile is therefore a photograph, not a live chart: it is as old as the picture it was cut from. The route
says when it was taken, and `fresh=1` takes another. Interaction — filters, cross-filters, a tooltip — is
Superset's own, in Preview, which opens the real dashboard.
"""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any, Optional

from .models import utcnow
from .visual_qa_agent import VisualQaAgent

# One render at a time per service: a browser is expensive, and two of them answering the same canvas would
# be two page loads for one answer.
_lock = threading.Lock()


class Pictures:
    def __init__(self, visual: VisualQaAgent, state_dir: Path, width: int = 1440):
        self.visual = visual
        self.root = state_dir / "studio"
        self.width = width

    def _folder(self, project_id: str) -> Path:
        return self.root / project_id

    def taken(self, project_id: str) -> Optional[dict[str, Any]]:
        """When this project's tiles were cut, and from what — None when there are none."""
        manifest = self._folder(project_id) / "taken.json"
        if not manifest.exists():
            return None
        try:
            return json.loads(manifest.read_text())
        except ValueError:
            return None

    def tile(self, project_id: str, slot: str) -> Optional[Path]:
        path = self._folder(project_id) / f"{slot}.png"
        return path if path.exists() else None

    def cut(self, project_id: str, dashboard_id: int, charts: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """One picture of the dashboard, cut into one per slot. `charts` is the project's slot → chart."""
        from PIL import Image

        folder = self._folder(project_id)
        with _lock:
            scratch = folder.with_name(folder.name + ".cutting")
            if scratch.exists():
                shutil.rmtree(scratch)
            scratch.mkdir(parents=True)
            render = self.visual.render(dashboard_id, expected_charts=len(charts), out_dir=scratch,
                                        viewport="studio", width=self.width)
            if render.error or not render.screenshot:
                shutil.rmtree(scratch, ignore_errors=True)
                raise PictureError(render.error or "the dashboard produced no picture")
            by_chart = {chart["id"]: slot for slot, chart in charts.items() if chart.get("id")}
            cut: dict[str, Any] = {}
            with Image.open(render.screenshot) as page:
                for component in render.components:
                    slot = by_chart.get(component.get("chart_id"))
                    box = component.get("rect") or {}
                    if not slot or not box.get("w") or not box.get("h"):
                        continue
                    right, bottom = min(box["x"] + box["w"], page.width), min(box["y"] + box["h"], page.height)
                    if right <= box["x"] or bottom <= box["y"]:
                        continue                      # a chart below the bottom of the picture
                    page.crop((box["x"], box["y"], right, bottom)).save(scratch / f"{slot}.png")
                    cut[slot] = {"width": right - box["x"], "height": bottom - box["y"],
                                 "drawn": component.get("drawn"), "error": component.get("error"),
                                 "chart_id": component.get("chart_id")}
            taken = {"taken_at": utcnow(), "dashboard": dashboard_id, "width": self.width,
                     "load_ms": render.load_ms, "slots": cut,
                     "missing": sorted(set(charts) - set(cut)), "console_errors": render.console_errors[:5]}
            (scratch / "taken.json").write_text(json.dumps(taken, ensure_ascii=False, indent=2))
            if folder.exists():
                shutil.rmtree(folder)
            scratch.rename(folder)
        return taken


class PictureError(RuntimeError):
    """The dashboard could not be photographed. A canvas without tiles is still a canvas: the Studio draws
    the layout and says why the pictures are missing."""
