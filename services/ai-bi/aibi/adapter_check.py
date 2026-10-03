"""Every road of the Superset adapter that a generation does not take, driven against the live BI engine.

    python -m aibi.adapter_check <project id>          (bash ai-bi.sh check-adapter <project id>)

A generation creates, updates and exports. Copy, import, delete and the embedded configuration are in the
adapter because the control plane needs them, and code that is never run is code nobody knows the state of —
the first time this ran, the copy came out with no layout. So they are run: on a COPY of a project's
dashboard, which is deleted at the end, and never on the dashboard itself.

It is also the first thing to run after Superset is upgraded: it says which of the adapter's assumptions
about the REST API still hold, before a generation finds out the hard way.
"""
from __future__ import annotations

import io
import sys
import zipfile
from typing import Any, Callable

from .config import get_settings
from .lifecycle import canonical_assets
from .store import Store
from .superset_adapter import SupersetAdapter, SupersetError


def check(adapter: SupersetAdapter, dashboard_id: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    state: dict[str, Any] = {}

    def step(road: str, action: Callable[[], str]) -> bool:
        try:
            results.append({"road": road, "ok": True, "detail": action()})
            return True
        except Exception as exc:  # noqa: BLE001 — each road reports for itself
            results.append({"road": road, "ok": False, "detail": str(exc).splitlines()[0][:300]})
            return False

    source = adapter.get_dashboard(dashboard_id)
    title = f"{source['dashboard_title']} (adapter check)"
    step("metadata", lambda: f"« {source['dashboard_title']} »: {len(adapter.dashboard_charts(dashboard_id))} charts, "
                             f"{len(adapter.dashboard_datasets(dashboard_id))} datasets")

    def copy() -> str:
        state["copy"] = adapter.copy_dashboard(dashboard_id, title)
        charts = adapter.dashboard_charts(state["copy"])
        state["charts"] = [c["id"] for c in charts]
        original = {c["id"] for c in adapter.dashboard_charts(dashboard_id)}
        if not charts or original & set(state["charts"]):
            raise SupersetError("the copy shares its charts with the original, or has none")
        return f"dashboard {state['copy']} with {len(charts)} charts of its own"

    if not step("copy", copy):
        return results

    def export() -> str:
        state["bundle"] = adapter.export_dashboard(state["copy"])
        state["told"] = canonical_assets(state["bundle"])
        with zipfile.ZipFile(io.BytesIO(state["bundle"])) as archive:
            return f"{len(archive.namelist())} files, {len(state['bundle'])} bytes"

    step("export", export)

    def embedded() -> str:
        current = adapter.get_embedded(state["copy"])
        made = adapter.set_embedded(state["copy"], [])
        if adapter.get_embedded(state["copy"]) is None:
            raise SupersetError("the configuration was accepted and cannot be read back")
        return f"was {'set' if current else 'not set'}; set, and read back as {made.get('uuid')}"

    step("embedded configuration", embedded)

    def delete() -> str:
        for chart_id in state["charts"]:
            adapter.delete_chart(chart_id)
        adapter.delete_dashboard(state["copy"])
        if adapter.get_dashboard(state["copy"]) is not None:
            raise SupersetError("the copy is still there after its deletion")
        return f"dashboard {state['copy']} and its {len(state['charts'])} charts"

    deleted = step("delete", delete)

    def bring_back() -> str:
        adapter.import_dashboard(state["bundle"], overwrite=True)
        found = adapter.find_dashboards(title)
        if len(found) != 1:
            raise SupersetError(f"{len(found)} dashboards carry the imported title")
        charts = adapter.dashboard_charts(found[0]["id"])
        if canonical_assets(adapter.export_dashboard(found[0]["id"])) != state["told"]:
            raise SupersetError("what was imported is not what was exported")
        return f"dashboard {found[0]['id']} with {len(charts)} charts, identical in content to the export"

    def sweep() -> str:
        """Whatever happened above, no copy stays behind."""
        left = adapter.find_dashboards(title)
        for dashboard in left:
            for chart in adapter.dashboard_charts(dashboard["id"]):
                adapter.delete_chart(chart["id"])
            adapter.delete_dashboard(dashboard["id"])
        if adapter.find_dashboards(title):
            raise SupersetError("a copy is still in the BI engine")
        return f"{len(left)} copy removed, none left" if left else "nothing left to remove"

    if deleted and "bundle" in state:
        step("import", bring_back)
    step("clean up", sweep)
    return results


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    settings = get_settings()
    project = Store(settings.state_dir / "control-plane.sqlite").get_project(sys.argv[1])
    if project is None or "dashboard" not in project.get("engine", {}):
        print(f"no BI project {sys.argv[1]} with a dashboard in the BI engine", file=sys.stderr)
        return 1
    adapter = SupersetAdapter(settings.superset_url, settings.superset_user, settings.superset_password)
    results = check(adapter, project["engine"]["dashboard"]["id"])
    for result in results:
        print(f" {'✓' if result['ok'] else '✗'} {result['road']:<28} {result['detail']}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
