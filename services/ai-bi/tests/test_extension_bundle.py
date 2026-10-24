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
    # what Superset's configuration verifies is the checksum the compose file names — not one lying in the
    # bundle's own folder, which whoever replaced the bundle could replace with it
    compose = next((c for c in (Path("/app/docker-compose.ai-bi.yaml"), folder.parent / "docker-compose.ai-bi.yaml")
                    if c.is_file()), None)
    if compose is not None:
        assert f'SHARPSIGHT_EXTENSION_SHA256: "{digest}"' in compose.read_text(encoding="utf-8"), "run build.py"


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
                       ("/projects/<project_id>/commit", "POST"), ("/runs/<run_id>", "GET"),
                       # the Studio (the brief's sections 46–60): what it draws, the draft it keeps, its tiles
                       ("/projects/<project_id>/studio", "GET"), ("/projects/<project_id>/draft", "PUT"),
                       ("/projects/<project_id>/draft", "DELETE"),
                       ("/projects/<project_id>/draft/validate", "POST"),
                       ("/projects/<project_id>/draft/commit", "POST"),
                       ("/projects/<project_id>/pictures", "POST"),
                       ("/projects/<project_id>/picture/<slot>", "GET"),
                       ("/datasets", "GET"), ("/metrics", "GET"), ("/templates", "GET"),
                       # the copilot, the gallery, and what the last run found
                       ("/projects/<project_id>/propose", "POST"), ("/projects/<project_id>/compose", "POST"),
                       ("/projects/<project_id>/report", "GET"),
                       ("/datasets/<dataset_id>/lineage", "GET"),
                       ("/projects/<project_id>/versions/<version>/package", "GET"),
                       ("/projects/<project_id>/alter", "POST"),
                       # where a version may go, where it has been, and taking it there
                       ("/targets", "GET"), ("/projects/<project_id>/deployments", "GET"),
                       ("/projects/<project_id>/versions/<version>/deploy", "POST")]
    assert not re.search(r"approve|reject|restore|discard|/audit|/people", api.split('"""', 2)[2])
    # a POST is refused without Superset's CSRF token: Flask-AppBuilder exempts an API unless told otherwise
    assert "    csrf_exempt = False\n" in api
    # who is asking comes from Superset's session, on the server
    assert '"X-SharpSight-User": g.user.username' in api and "request.headers" not in api


def test_the_engine_answers_superset_s_token_for_what_a_developer_does_and_no_other():
    """Superset holds a token of its own. « The extension approves nothing » must not rest on the extension's
    source: with that token the engine itself refuses everything but what a developer DOES — the projects,
    the Studio and its draft, a validation, a commit, a run, and the governed lists the explorer shows — so
    nothing inside Superset can approve, promote, restore or read the audit trail."""
    from aibi.main import SUPERSET_MAY, app, superset_may

    routes = {(method, route.path) for route in app.routes for method in getattr(route, "methods", None) or ()
              if method not in ("HEAD", "OPTIONS")}
    assert SUPERSET_MAY <= routes, "a call Superset may make is not a route of the engine"
    allowed = sorted(pair for pair in routes if superset_may(*pair))
    assert allowed == [("DELETE", "/projects/{project_id}/draft"),
                       ("GET", "/datasets"),
                       ("GET", "/datasets/{dataset_id}/lineage"),
                       ("GET", "/metrics"),
                       ("GET", "/projects"),
                       ("GET", "/projects/{project_id}/changes"),
                       ("GET", "/projects/{project_id}/deployments"),
                       ("GET", "/projects/{project_id}/picture/{slot}"),
                       ("GET", "/projects/{project_id}/report"),
                       ("GET", "/projects/{project_id}/studio"),
                       ("GET", "/projects/{project_id}/versions/{version}/package"),
                       ("GET", "/runs/{run_id}"),
                       ("GET", "/targets"),
                       ("GET", "/templates"),
                       ("POST", "/projects/{project_id}/alter"),
                       ("POST", "/projects/{project_id}/commit"),
                       ("POST", "/projects/{project_id}/compose"),
                       ("POST", "/projects/{project_id}/draft/commit"),
                       ("POST", "/projects/{project_id}/draft/validate"),
                       ("POST", "/projects/{project_id}/pictures"),
                       ("POST", "/projects/{project_id}/propose"),
                       # a deployment CARRIES OUT an approval that exists; the approval itself is refused
                       # below with every other decision, and stays on the command line
                       ("POST", "/projects/{project_id}/versions/{version}/deploy"),
                       ("PUT", "/projects/{project_id}/draft")]
    for refused in (("POST", "/projects/{project_id}/approve"), ("POST", "/projects/{project_id}/reject"),
                    ("POST", "/projects/{project_id}/restore"), ("POST", "/projects/{project_id}/discard"),
                    ("POST", "/projects/generate"), ("GET", "/audit"), ("POST", "/people/sync"),
                    ("POST", "/tools/{name}")):
        assert refused in routes and not superset_may(*refused), refused
    # and what the extension's backend asks is exactly what the engine answers it for
    api = (_folder() / "backend" / "src" / "sharpsight" / "bi" / "api.py").read_text(encoding="utf-8")
    asked = set(re.findall(r'self\._engine(?:_file)?\("(GET|POST|PUT|DELETE)", f?"([^"]+)"', api))
    assert asked == set(SUPERSET_MAY)
