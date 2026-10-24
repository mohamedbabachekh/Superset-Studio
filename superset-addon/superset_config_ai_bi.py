"""Superset's configuration when the AI BI engine is on: the kit's own, and what SharpSight adds to Superset.

docker-compose.ai-bi.yaml points SUPERSET_CONFIG_PATH here. The kit's superset/superset_config.py is not edited and not
copied: it is imported, whole, and extended in two places — one hook (the SharpSight menu, sharpsight_bi.py)
and Superset's own extension system (the bundle sharpsight-bi-<version>.supx, superset-extension/). Without the
engine's overlay this file is not mounted and Superset starts exactly as the kit ships it.
"""
import hashlib
import logging
import os
from pathlib import Path

from superset_config import *  # noqa: F401,F403  — every setting of the kit, as it is
from superset_config import FEATURE_FLAGS as _kit_flags
from superset_config import FLASK_APP_MUTATOR as _kit_mutator

import sharpsight_bi


def FLASK_APP_MUTATOR(app):  # noqa: N802,F811  (Superset calls this hook with the built application)
    _kit_mutator(app)
    sharpsight_bi.install(app)


def _recorded_bundles(folder: str, expected: str):
    """Superset's extension loader checks nothing about a bundle: no checksum, no signature (read from 6.1.0).
    So it is checked here, before Superset is told where bundles are. The folder must hold one .supx, and it
    must be the file whose checksum the compose file names — said THERE, not in the folder: whoever could
    replace the bundle could replace a checksum lying beside it. Otherwise Superset starts WITHOUT extensions,
    saying why, rather than with code nobody recorded. A checksum, not a signature: it says the bundle is the
    one the repository built, not who built it."""
    found = sorted(Path(folder).glob("*.supx")) if Path(folder).is_dir() else []
    if not found:
        return None
    actual = [hashlib.sha256(bundle.read_bytes()).hexdigest() for bundle in found]
    if len(found) != 1 or actual != [expected.strip().lower()]:
        logging.getLogger("sharpsight").error(
            "sharpsight: %s — sha256 %s — is not the one bundle that was recorded (%s): Superset starts without "
            "extensions", ", ".join(b.name for b in found), ", ".join(actual), expected or "no checksum was given")
        return None
    return folder


EXTENSIONS_PATH = _recorded_bundles(os.environ.get("SHARPSIGHT_EXTENSIONS_DIR", "/app/sharpsight/extensions"),
                                    os.environ.get("SHARPSIGHT_EXTENSION_SHA256", ""))
# apache-superset-core is a release candidate in 6.1.0 and its flag is off by default: it is on here only when
# there is a recorded bundle to load.
FEATURE_FLAGS = {**_kit_flags, "ENABLE_EXTENSIONS": EXTENSIONS_PATH is not None}
