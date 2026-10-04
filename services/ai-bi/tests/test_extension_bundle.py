"""sharpsight-bi.supx — the bundle Superset's extension loader reads — is what its sources build, and is laid
out the way that loader reads a bundle (superset/extensions/utils.py in 6.1.0)."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import zipfile
from pathlib import Path

import pytest


def _folder() -> Path:
    here = Path(__file__).resolve()
    for folder in (Path("/app/superset-extension"), *(parent / "superset-extension" for parent in here.parents)):
        if (folder / "build.py").is_file():
            return folder
    pytest.skip("superset-extension/ is not beside this checkout")


def _build(folder: Path):
    loaded = importlib.util.spec_from_file_location("sharpsight_bi_build", folder / "build.py")
    module = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(module)
    return module


def test_the_recorded_bundle_is_the_one_its_sources_build():
    """Superset's loader checks nothing about a bundle; the kit's configuration layer checks the recorded
    checksum. That only means something if the recorded bundle is these sources — rebuilt here, byte for byte."""
    folder = _folder()
    name, content = _build(folder).bundle()
    recorded = folder / "bundle" / name
    assert recorded.is_file(), f"run python superset-extension/build.py: {name} is not in bundle/"
    assert recorded.read_bytes() == content, "the sources changed since the bundle was built: run build.py"
    digest, said = (folder / "bundle" / (name + ".sha256")).read_text(encoding="utf-8").split()
    assert (digest, said) == (hashlib.sha256(content).hexdigest(), name)
    assert sorted(p.name for p in (folder / "bundle").iterdir()) == [name, name + ".sha256"]      # one bundle


def test_the_bundle_is_laid_out_as_superset_reads_one():
    folder = _folder()
    name, content = _build(folder).bundle()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        backend = {n: archive.read(n).decode("utf-8") for n in names if n.startswith("backend/")}
    # the loader refuses a bundle holding anything but these three kinds of file
    frontend, python = re.compile(r"^frontend/dist/([^/]+)$"), re.compile(r"^backend/src/(.+)$")
    assert all(n == "manifest.json" or frontend.match(n) or python.match(n) for n in names), names
    assert manifest["id"] == f"{manifest['publisher']}.{manifest['name']}" == "sharpsight.bi"
    assert name == f"sharpsight-bi-{manifest['version']}.supx"
    assert "backend/src/" + manifest["backend"]["entrypoint"].replace(".", "/") + ".py" in names
    assert "frontend/dist/" + manifest["frontend"]["remoteEntry"] in names
    assert manifest["frontend"]["moduleFederationName"] == "sharpsight_bi"
    # what the extension can do, read from its own endpoints: validate and commit, and nothing that approves
    api = backend["backend/src/sharpsight/bi/api.py"]
    exposed = re.findall(r'@expose\("([^"]+)", methods=\("(\w+)",\)\)', api)
    assert exposed == [("/projects", "GET"), ("/projects/<project_id>/changes", "GET"),
                       ("/projects/<project_id>/commit", "POST"), ("/runs/<run_id>", "GET")]
    assert not re.search(r"approve|reject|restore|discard|/audit|/people", api.split('"""', 2)[2])
    # who is asking comes from Superset's session, on the server
    assert '"X-SharpSight-User": g.user.username' in api and "request.headers" not in api
