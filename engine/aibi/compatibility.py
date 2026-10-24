"""The compatibility matrix (governance/registry/compatibility.yaml), with what is actually running put beside it.

The matrix is a statement — « built and run against these ». This file reads the versions of what the service
is really talking to and says, component by component, whether the statement covers it. Nothing is refused
on the strength of it: an untested version is reported, in /compatibility and in /health, for a person to
decide about.
"""
from __future__ import annotations

import platform
from importlib import metadata
from typing import Any, Optional


def _package(name: str) -> Optional[str]:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def running(superset: Optional[str], postgresql: Optional[str]) -> dict[str, Optional[str]]:
    return {"superset": superset, "dbt_core": _package("dbt-core"), "dbt_postgres": _package("dbt-postgres"),
            "python": platform.python_version(), "postgresql": postgresql, "playwright": _package("playwright"),
            "sqlglot": _package("sqlglot"),
            # Superset's extension system has no version of its own to ask for: it is the one that came inside
            # that Superset (apache-superset-core 0.1.0rc3 in 6.1.0), so it stands or falls with it
            "superset_extensions": superset,
            # the Studio is a page of Superset's own, on Superset's own template: same thing — it has no
            # version to ask for, and it stands or falls with the Superset it is served by
            "superset_studio": superset}


def covers(tested: str, version: str) -> bool:
    """« 6.1 » covers 6.1.0 and 6.1.3 — and not 6.10."""
    return version == tested or version.startswith(tested + ".")


def report(matrix: dict[str, Any], service_version: str, found: dict[str, Optional[str]]) -> dict[str, Any]:
    components: dict[str, Any] = {}
    untested: list[str] = []
    for name, declared in matrix.get("components", {}).items():
        entry = dict(declared)
        if declared.get("status") == "not_used":
            components[name] = entry
            continue
        version = found.get(name)
        tested = [str(t) for t in declared.get("tested", [])]
        entry["running"] = version
        entry["verdict"] = ("not detected" if version is None else
                            "tested" if any(covers(t, version) for t in tested) else "untested")
        if entry["verdict"] != "tested":
            untested.append(f"{name} {version or '(not detected)'}, tested with {', '.join(tested) or 'nothing'}")
        components[name] = entry
    matrix_for = str(matrix.get("sharpsight_ai_bi", ""))
    if not covers(matrix_for, service_version):
        untested.append(f"this is AI BI {service_version}; the matrix was written for {matrix_for or 'no version'}")
    return {"sharpsight_ai_bi": {"running": service_version, "matrix_written_for": matrix_for},
            "components": components, "connectors": matrix.get("connectors", {}),
            "untested": untested, "ok": not untested}
