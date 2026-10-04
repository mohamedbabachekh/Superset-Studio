"""What SharpSight adds to Superset itself when the AI BI engine is on.

Superset is the interface. Nothing here is a screen of our own: it is entries in Superset's own top bar and
one route.

    SharpSight ▸ BI Factory          projects, gates, the checks of each validation      ┐
               ▸ Data Quality        governed datasets, dbt models and their tests       │ Superset dashboards
               ▸ Impact Analysis     what a change to a dataset or a metric would touch  │ over the engine's
               ▸ Releases            what was approved, by whom, and what shipped        │ own records
               ▸ Audit trail         every action, by whom, how it ended                 ┘
               ▸ Data models (dbt)   dbt's own documentation site, served here behind Superset's sign-in

The dashboards are made by the engine through Superset's REST API, like any other dashboard. The documentation
is the file `dbt docs generate` writes; the dbt-runner publishes it into a volume this container mounts
read-only.

WHO SEES WHAT. An entry is shown to a person who may open what it leads to, and to nobody else. The rule is
SharpSight's — registry/security/rbac.yaml, mounted here read-only — and it is the same rule the engine uses to
grant the dashboards (aibi/people.py, aibi/superset_view.py): a capability, held by a role, carried into
Superset as the role `sharpsight_<role>`.

    the four dashboards of the factory   bi.generate, bi.approve_uat or bi.approve_prod
    the audit trail                      audit.read
    dbt's documentation                  a role that is granted at least one dataset, or one of the above —
                                         the engine refuses a model's schema to anyone else, so this does too

An administrator of Superset sees all of it. The route checks the rule itself: hiding an entry is not what
protects the documentation.

Everything goes through Flask-AppBuilder's public API (`add_link`, a blueprint, the security manager) — the same
calls Superset makes for its own menu — so a Superset upgrade does not have to know about it.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from flask import Blueprint, abort, redirect, request, send_from_directory
from flask_login import current_user

CATEGORY = "SharpSight"
ROLE_PREFIX = "sharpsight_"                       # aibi/people.py: an rbac.yaml role, as a Superset role
FACTORY = ("bi.generate", "bi.approve_uat", "bi.approve_prod")
TRAIL = ("audit.read",)
DBT_DOCS = "Data models (dbt)"
# name, French label, where it leads, the capabilities it is for (None: the documentation's own rule)
LINKS = (
    ("BI Factory", "Fabrique BI", "/superset/dashboard/sharpsight-bi-factory/", FACTORY),
    ("Data Quality", "Qualité des données", "/superset/dashboard/sharpsight-data-quality/", FACTORY),
    ("Impact Analysis", "Analyse d’impact", "/superset/dashboard/sharpsight-impact-analysis/", FACTORY),
    ("Releases", "Livraisons", "/superset/dashboard/sharpsight-releases/", FACTORY),
    ("Audit trail", "Journal d’audit", "/superset/dashboard/sharpsight-audit-trail/", TRAIL),
    (DBT_DOCS, "Modèles de données (dbt)", "/sharpsight/dbt-docs/", None),
)
# dbt's site is one file that reads these two from beside it; nothing else in the volume is served
_DOCS_FILES = ("index.html", "manifest.json", "catalog.json")


def audiences(rbac_file: Path) -> dict[str, set[str]]:
    """For each entry, the Superset roles that may open it — read from rbac.yaml. Empty when the file is
    not there: an administrator still sees everything, nobody else anything."""
    try:
        roles = (yaml.safe_load(rbac_file.read_text(encoding="utf-8")) or {}).get("roles") or {}
    except (OSError, yaml.YAMLError):
        roles = {}

    def holding(capabilities: tuple[str, ...]) -> set[str]:
        return {ROLE_PREFIX + name for name, role in roles.items()
                if any(c in (role.get("capabilities") or []) for c in capabilities)}

    reads_models = {ROLE_PREFIX + name for name, role in roles.items() if role.get("datasets")}
    return {name: (reads_models | holding(FACTORY + TRAIL)) if capabilities is None else holding(capabilities)
            for name, _, _, capabilities in LINKS}


def install(app) -> None:  # noqa: ANN001  (the Flask application Superset built)
    from superset.extensions import appbuilder

    allowed = audiences(Path(os.environ.get("SHARPSIGHT_RBAC_FILE", "/app/sharpsight/rbac.yaml")))

    def may(entry: str) -> bool:
        if not getattr(current_user, "is_authenticated", False):
            return False
        held = {role.name for role in current_user.roles}
        return "Admin" in held or bool(held & allowed[entry])

    french = os.environ.get("DEVKIT_LOCALE", "fr") == "fr"
    for name, label, href, _ in LINKS:
        appbuilder.add_link(name, href=href, label=label if french else name, category=CATEGORY,
                            category_label=CATEGORY, cond=lambda name=name: may(name))
    # the category itself, for someone who may open none of its entries: not an empty menu, no menu
    category = appbuilder.menu.find(CATEGORY)
    if category is not None:
        category.cond = lambda: any(may(link[0]) for link in LINKS)
    # Superset builds its AppBuilder with update_perms=False: permissions are written by `superset init`, which
    # the kit runs with its own configuration and so has never heard of these entries. Find-or-create, granted
    # to the administrator role — what `superset init` does for every entry of Superset's own menu — and to
    # Superset's standard roles, which is what `superset init` does with them at its next run anyway (an entry
    # it does not know is everyone's). Holding the permission shows nothing by itself: `cond` above decides.
    for name in (CATEGORY, *(link[0] for link in LINKS)):
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
        if not may(DBT_DOCS):
            abort(403)
        if name not in _DOCS_FILES:
            abort(404)
        if not (docs_dir / name).is_file():
            return ("dbt's documentation has not been published yet: it is written by the engine's production "
                    "build (bash ai-bi.sh).", 404, {"Content-Type": "text/plain; charset=utf-8"})
        return send_from_directory(docs_dir, name, max_age=0)

    app.register_blueprint(pages)
    app.logger.info("sharpsight: %d menu entries under « %s », dbt documentation from %s, audiences of %d roles",
                    len(LINKS), CATEGORY, docs_dir, len(set().union(*allowed.values())))
