"""The dbt-runner's job: build the governed project into production, and publish what dbt knows about it.

This is what Airflow triggers in the target architecture. In this kit it runs once at every start, with a role
the AI service never holds (bi_builder), into a schema the AI service cannot write to (`bi`).

It publishes dbt's artifacts — manifest.json, catalog.json, run_results.json, and semantic_manifest.json when
dbt writes one — into a volume the AI service mounts READ-ONLY. They are what a generated model defers to, and
where lineage and model metadata come from. A build whose tests fail publishes nothing: the service keeps
working on the last good state rather than on a mart that failed its own checks.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# index.html is dbt's OWN documentation site (models, columns, tests, the lineage graph): one file, which reads
# manifest.json and catalog.json from beside it. The AI BI service serves it as it is, and BI Studio frames it.
ARTIFACTS = ("manifest.json", "catalog.json", "semantic_manifest.json", "index.html")


def main() -> int:
    source = Path(os.environ.get("AIBI_DBT_PROJECT_DIR", "/app/dbt"))
    published = Path(os.environ.get("AIBI_DBT_PROD_STATE", "/var/lib/dbt-prod"))
    work = Path(tempfile.mkdtemp(prefix="dbt-build-")) / "dbt"
    shutil.copytree(source, work, ignore=shutil.ignore_patterns("target", "logs", "dbt_packages", ".user.yml"))
    env = {**os.environ, "DBT_TARGET": "prod", "DBT_SEND_ANONYMOUS_USAGE_STATS": "false", "DO_NOT_TRACK": "1"}
    base = ["--project-dir", str(work), "--profiles-dir", str(work / "profiles"), "--target", "prod"]

    def dbt(*args: str) -> int:
        print(f"── dbt {' '.join(args)}", flush=True)
        return subprocess.run(["dbt", "--no-use-colors", *args, *base], cwd=str(work), env=env).returncode

    if dbt("build") != 0:
        print("the production build FAILED — nothing is published, the last good artifacts stay in place",
              file=sys.stderr)
        return 1
    # `docs generate` rewrites run_results.json with its own compile; the build's results are the ones to keep
    shutil.copy2(work / "target" / "run_results.json", work / "run_results.build.json")
    if dbt("docs", "generate") != 0:
        print("the build passed but catalog.json could not be generated", file=sys.stderr)
        return 1

    published.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS:
        artifact = work / "target" / name
        if artifact.exists():
            shutil.copy2(artifact, published / (name + ".tmp"))
            os.replace(published / (name + ".tmp"), published / name)
    shutil.copy2(work / "run_results.build.json", published / "run_results.json.tmp")
    os.replace(published / "run_results.json.tmp", published / "run_results.json")
    print("published: " + ", ".join(sorted(p.name for p in published.iterdir())), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
