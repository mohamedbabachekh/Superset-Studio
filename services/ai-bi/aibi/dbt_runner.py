"""dbt Core, run the way the engine is allowed to run it.

Always the `dev` target, always in a project workspace, always deferring to the production build: a generated
model's `ref('mart_cem_qoe_daily')` resolves to the governed table in `bi`, and only the generated model is
built, in `bi_dev`. The role dbt connects with can create nowhere else, so this is not a convention.

What dbt did is read back from its own artifacts — run_results.json, manifest.json, catalog.json — not from
what it printed.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class DbtResult:
    command: str
    ok: bool
    returncode: int
    duration_ms: int
    results: list[dict[str, Any]] = field(default_factory=list)
    output_tail: str = ""

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {"command": self.command, "ok": self.ok, "duration_ms": self.duration_ms, "statuses": counts,
                "nodes": [r["unique_id"] for r in self.results]}

    def failures(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r["status"] in ("error", "fail", "runtime error")]


class DbtRunner:
    def __init__(self, env: dict[str, str], prod_state_dir: Path, timeout_s: int = 300):
        self._env = env
        self.prod_state_dir = prod_state_dir
        self.timeout_s = timeout_s

    def _invoke(self, project_dir: Path, verb: str, args: list[str], defer: bool = True) -> DbtResult:
        command = ["dbt", "--no-use-colors", verb, "--project-dir", str(project_dir),
                   "--profiles-dir", str(project_dir / "profiles"), "--target", "dev", *args]
        if defer:
            command += ["--defer", "--favor-state", "--state", str(self.prod_state_dir)]
        results_file = project_dir / "target" / "run_results.json"
        results_file.unlink(missing_ok=True)

        started = time.monotonic()
        try:
            done = subprocess.run(command, cwd=str(project_dir), env={**os.environ, **self._env},
                                  capture_output=True, text=True, timeout=self.timeout_s)
            returncode, output = done.returncode, (done.stdout or "") + (done.stderr or "")
        except subprocess.TimeoutExpired as exc:
            returncode, output = 124, f"dbt {verb} did not finish in {self.timeout_s} s\n{exc.stdout or ''}"
        duration_ms = int((time.monotonic() - started) * 1000)

        results: list[dict[str, Any]] = []
        if results_file.exists():
            for r in json.loads(results_file.read_text(encoding="utf-8")).get("results", []):
                results.append({"unique_id": r.get("unique_id"), "status": r.get("status"),
                                "message": r.get("message"), "failures": r.get("failures"),
                                "execution_time": round(r.get("execution_time") or 0, 3)})
        shown = " ".join(a for a in command[1:] if a not in (str(project_dir), str(project_dir / "profiles")))
        return DbtResult(command=f"dbt {shown}", ok=returncode == 0, returncode=returncode,
                         duration_ms=duration_ms, results=results, output_tail="\n".join(output.splitlines()[-25:]))

    def compile(self, project_dir: Path, select: str) -> DbtResult:
        return self._invoke(project_dir, "compile", ["--select", select])

    def run(self, project_dir: Path, select: str) -> DbtResult:
        return self._invoke(project_dir, "run", ["--select", select])

    def test(self, project_dir: Path, select: str, exclude: Optional[str] = None) -> DbtResult:
        args = ["--select", select]
        if exclude:
            args += ["--exclude", exclude]
        return self._invoke(project_dir, "test", args)

    def docs(self, project_dir: Path, select: str) -> DbtResult:
        """catalog.json for what was just built: the columns and types as the database has them."""
        return self._invoke(project_dir, "docs", ["generate", "--select", select])

    # ── artifacts ───────────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def read_artifact(directory: Path, name: str) -> dict[str, Any]:
        path = directory / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def compiled_sql(self, project_dir: Path, model: str) -> Optional[str]:
        manifest = self.read_artifact(project_dir / "target", "manifest.json")
        for node in manifest.get("nodes", {}).values():
            if node.get("resource_type") == "model" and node.get("name") == model:
                if node.get("compiled_code"):
                    return node["compiled_code"]
                compiled = project_dir / "target" / "compiled" / node["package_name"] / node["original_file_path"]
                return compiled.read_text(encoding="utf-8") if compiled.exists() else None
        return None


def lineage_from_artifacts(target_dir: Path, environment: str) -> list[dict[str, Any]]:
    """Every model and seed dbt knows, with what it is built from, its columns as the database has them, and
    how its tests went — what SharpSight keeps as lineage and model metadata."""
    manifest = DbtRunner.read_artifact(target_dir, "manifest.json")
    catalog = DbtRunner.read_artifact(target_dir, "catalog.json")
    run_results = DbtRunner.read_artifact(target_dir, "run_results.json")
    outcomes = {r.get("unique_id"): r.get("status") for r in run_results.get("results", [])}
    nodes = manifest.get("nodes", {})
    sources = manifest.get("sources", {})

    tests_by_model: dict[str, list[dict[str, Any]]] = {}
    for unique_id, node in nodes.items():
        if node.get("resource_type") != "test":
            continue
        for parent in node.get("depends_on", {}).get("nodes", []):
            tests_by_model.setdefault(parent, []).append(
                {"name": node.get("name"), "status": outcomes.get(unique_id, "not run")})

    found: list[dict[str, Any]] = []
    for unique_id, node in nodes.items():
        if node.get("resource_type") not in ("model", "seed"):
            continue
        parents = node.get("depends_on", {}).get("nodes", [])
        catalog_columns = catalog.get("nodes", {}).get(unique_id, {}).get("columns", {})
        found.append({
            "environment": environment,
            "unique_id": unique_id,
            "name": node.get("name"),
            "resource_type": node.get("resource_type"),
            "schema": node.get("schema"),
            "relation": node.get("relation_name"),
            "materialized": node.get("config", {}).get("materialized"),
            "path": node.get("original_file_path"),
            "description": node.get("description", ""),
            "tags": node.get("tags", []),
            "meta": node.get("meta") or node.get("config", {}).get("meta", {}),
            "depends_on": [
                {"unique_id": p,
                 "name": (nodes.get(p) or sources.get(p) or {}).get("name", p),
                 "resource_type": (nodes.get(p) or sources.get(p) or {}).get("resource_type", "unknown")}
                for p in parents],
            "columns": {name.lower(): {"type": col.get("type")} for name, col in catalog_columns.items()},
            "tests": tests_by_model.get(unique_id, []),
            "status": outcomes.get(unique_id),
            "dbt_version": manifest.get("metadata", {}).get("dbt_version"),
            "generated_at": manifest.get("metadata", {}).get("generated_at"),
        })
    return found
