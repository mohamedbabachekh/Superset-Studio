"""Build sharpsight-bi-<version>.supx from this folder — the bundle Superset's extension loader reads.

    python superset-extension/build.py            # writes bundle/sharpsight-bi-<version>.supx and its .sha256

A .supx is a zip of three things (read from Superset 6.1.0's loader, superset/extensions/utils.py):

    manifest.json                 id, publisher, name, displayName, version, backend.entrypoint, frontend
    backend/src/<package>/…       the Python Superset imports
    frontend/dist/<file>          the built panel (webpack, module federation) — when frontend/dist exists

The backend needs no tool to build: it is copied. The panel is built first, with Node, on a developer's machine
— `npm ci && npm run build` in frontend/ — and what that left in frontend/dist is taken. The kit has no Node:
frontend/dist is in the repository, and

    python superset-extension/build.py --check-frontend

builds the panel again, somewhere else, and says whether frontend/dist is what frontend/src builds.

The zip is made the same way every time — sorted names, one fixed date — so the same sources give the same
bytes on any machine, and its checksum means something. Superset's loader checks nothing about a bundle: the
kit's configuration layer verifies the checksum before it lets Superset load the file — the one written into
docker-compose.ai-bi.yaml (SHARPSIGHT_EXTENSION_SHA256), not into the bundle's own folder.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EPOCH = (2026, 1, 1, 0, 0, 0)


def files() -> dict[str, bytes]:
    config = json.loads((HERE / "extension.json").read_text(encoding="utf-8"))
    found: dict[str, bytes] = {}
    for pattern in (config.get("backend") or {}).get("files", []):
        for path in sorted(HERE.glob(pattern)):
            found[path.relative_to(HERE).as_posix()] = path.read_bytes().replace(b"\r\n", b"\n")
    manifest = {key: config[key] for key in ("publisher", "name", "displayName", "version", "license", "description",
                                             "permissions") if key in config}
    manifest["id"] = f"{config['publisher']}.{config['name']}"
    package = next(iter(sorted(found))).removeprefix("backend/src/").rsplit("/", 1)[0].replace("/", ".")
    manifest["backend"] = {"entrypoint": f"{package}.entrypoint"}
    built = HERE / "frontend" / "dist"
    if built.is_dir():
        for path in sorted(p for p in built.iterdir() if p.is_file()):
            found[f"frontend/dist/{path.name}"] = path.read_bytes()
        remote = sorted(name for name in found if name.startswith("frontend/dist/remoteEntry"))
        if remote:
            manifest["frontend"] = {"remoteEntry": remote[0].rsplit("/", 1)[1],
                                    "moduleFederationName": f"{config['publisher']}_{config['name']}"}
    found["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return found


def bundle() -> tuple[str, bytes]:
    found = files()
    version = json.loads(found["manifest.json"])["version"]
    buffer = io.BytesIO()
    # stored, not deflated: two builds of zlib do not compress alike, and the bytes must be the same everywhere
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name in sorted(found):
            entry = zipfile.ZipInfo(name, date_time=EPOCH)
            entry.compress_type = zipfile.ZIP_STORED
            entry.create_system = 3              # « made on Unix », said on Windows too: the header records it
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, found[name])
    return f"sharpsight-bi-{version}.supx", buffer.getvalue()


COMPOSE = HERE.parent / "docker-compose.ai-bi.yaml"
_RECORDED = re.compile(r'(SHARPSIGHT_EXTENSION_SHA256: ")[0-9a-f]{64}(")')


def check_frontend() -> int:
    """frontend/dist against what frontend/src builds, with the versions package-lock.json pins."""
    npm = shutil.which("npm")
    if npm is None:
        print("Node is not on this machine: the panel cannot be rebuilt here", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as scratch:
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=HERE / "frontend", check=True, capture_output=True)
        subprocess.run([npm, "exec", "--", "webpack", "--mode", "production", "--output-path", scratch],
                       cwd=HERE / "frontend", check=True, capture_output=True)
        built = {p.name: p.read_bytes() for p in Path(scratch).iterdir() if p.is_file()}
    kept = {p.name: p.read_bytes() for p in (HERE / "frontend" / "dist").iterdir() if p.is_file()}
    if built == kept:
        print(f"frontend/dist is what frontend/src builds: {', '.join(sorted(kept))}")
        return 0
    print("frontend/dist is NOT what frontend/src builds — built now: " + ", ".join(sorted(built))
          + " | in the repository: " + ", ".join(sorted(kept)), file=sys.stderr)
    return 1


def main() -> int:
    if "--check-frontend" in sys.argv[1:]:
        return check_frontend()
    name, content = bundle()
    out = HERE / "bundle"
    out.mkdir(exist_ok=True)
    for stale in out.glob("*.supx*"):
        stale.unlink()
    (out / name).write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    (out / (name + ".sha256")).write_text(f"{digest}  {name}\n", encoding="utf-8", newline="\n")
    # …and where Superset's configuration reads it from: the compose file, which is not in the bundle's folder
    compose = COMPOSE.read_bytes().decode("utf-8")
    assert len(_RECORDED.findall(compose)) == 1, "docker-compose.ai-bi.yaml no longer names SHARPSIGHT_EXTENSION_SHA256"
    COMPOSE.write_bytes(_RECORDED.sub(lambda m: m.group(1) + digest + m.group(2), compose).encode("utf-8"))
    print(f"{name}: {len(content)} bytes, sha256 {digest}")
    print("  " + "\n  ".join(sorted(files())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
