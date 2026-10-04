"""What SharpSight adds to Superset itself when the AI BI engine is on.

Superset is the interface. Nothing here is a screen of our own: it is three entries in Superset's own top bar
and one route.

    SharpSight ▸ BI Factory          a Superset dashboard over the engine's records (projects, gates, checks)
               ▸ Audit trail         a Superset dashboard over the engine's audit trail
               ▸ Data models (dbt)   dbt's own documentation site, served here behind Superset's sign-in

The two dashboards are made by the engine through Superset's REST API, like any other dashboard. The
documentation is the file `dbt docs generate` writes; the dbt-runner publishes it into a volume this container
mounts read-only.

Everything goes through Flask-AppBuilder's public API (`add_link`, a blueprint, `has_access`) — the same calls
Superset makes for its own menu — so a Superset upgrade does not have to know about it. A menu entry is a
permission like any other (« menu access on BI Factory »): administrators hold it, and it can be granted to a
role in Superset's own role editor.
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, abort, redirect, request, send_from_directory
from flask_login import current_user

CATEGORY = "SharpSight"
DBT_DOCS = "Data models (dbt)"
_FRENCH = {"BI Factory": "Fabrique BI", "Audit trail": "Journal d’audit", DBT_DOCS: "Modèles de données (dbt)"}
LINKS = (
    ("BI Factory", "/superset/dashboard/sharpsight-bi-factory/"),
    ("Audit trail", "/superset/dashboard/sharpsight-audit-trail/"),
    (DBT_DOCS, "/sharpsight/dbt-docs/"),
)
# dbt's site is one file that reads these two from beside it; nothing else in the volume is served
_DOCS_FILES = ("index.html", "manifest.json", "catalog.json")


def install(app) -> None:  # noqa: ANN001  (the Flask application Superset built)
    from superset.extensions import appbuilder

    french = os.environ.get("DEVKIT_LOCALE", "fr") == "fr"
    for name, href in LINKS:
        appbuilder.add_link(name, href=href, label=_FRENCH[name] if french else name,
                            category=CATEGORY, category_label=CATEGORY)
    # Superset builds its AppBuilder with update_perms=False: permissions are written by `superset init`, which
    # the kit runs with its own configuration and so has never heard of these entries. Find-or-create, and
    # granted to the administrator role — what `superset init` does for every entry of Superset's own menu —
    # and to Superset's standard roles, which is what `superset init` does with them at its next run anyway
    # (an entry it does not know is everyone's): the same from the first start, not only after the second.
    # Seeing an entry opens nothing: each dashboard has its own audience, and Superset enforces it.
    for name in (CATEGORY, *(name for name, _ in LINKS)):
        appbuilder.sm.add_permissions_menu(name)
        permission = appbuilder.sm.find_permission_view_menu("menu_access", name)
        for role_name in ("Alpha", "Gamma"):
            role = appbuilder.sm.find_role(role_name)
            if role is not None and permission is not None:
                appbuilder.sm.add_permission_role(role, permission)

    state = {"ordered": False}

    @app.before_request
    def _sharpsight_menu_last():  # noqa: ANN202
        """This hook was called before Superset added its own entries, which puts ours first. Moved to the
        end at the first request, and never looked at again."""
        if state["ordered"]:
            return
        entries = appbuilder.menu.menu
        ours = next((entry for entry in entries if entry.name == CATEGORY), None)
        if ours is not None and entries[-1] is not ours:
            entries.remove(ours)
            entries.append(ours)
        state["ordered"] = True

    docs_dir = Path(os.environ.get("SHARPSIGHT_DBT_DOCS_DIR", "/app/sharpsight/dbt-docs"))
    pages = Blueprint("sharpsight_bi", __name__, url_prefix="/sharpsight")

    @pages.route("/dbt-docs/")
    @pages.route("/dbt-docs/<path:name>")
    def dbt_docs(name: str = "index.html"):  # noqa: ANN202
        if not current_user.is_authenticated:
            return redirect(f"{appbuilder.get_url_for_login}?next={request.path}")
        if not appbuilder.sm.has_access("menu_access", DBT_DOCS):
            abort(403)
        if name not in _DOCS_FILES:
            abort(404)
        if not (docs_dir / name).is_file():
            return ("dbt's documentation has not been published yet: it is written by the engine's production "
                    "build (bash ai-bi.sh).", 404, {"Content-Type": "text/plain; charset=utf-8"})
        return send_from_directory(docs_dir, name, max_age=0)

    app.register_blueprint(pages)
    app.logger.info("sharpsight: %d menu entries under « %s », dbt documentation from %s", len(LINKS), CATEGORY, docs_dir)
