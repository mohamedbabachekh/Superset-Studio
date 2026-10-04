"""Superset's configuration when the AI BI engine is on: the kit's own, and what SharpSight adds to Superset.

docker-compose.ai-bi.yaml points SUPERSET_CONFIG_PATH here. The kit's superset_config.py is not edited and not
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


def _recorded_bundles(folder: str):
    """Superset's extension loader checks nothing about a bundle: no checksum, no signature (read from 6.1.0).
    So it is checked here, before Superset is told where bundles are: every .supx of the folder must be the
    file whose checksum was written beside it when it was built. One that is not — or has none — and Superset
    starts WITHOUT extensions, saying which and why, rather than with code nobody recorded."""
    found = sorted(Path(folder).glob("*.supx")) if Path(folder).is_dir() else []
    for bundle in found:
        recorded = bundle.with_name(bundle.name + ".sha256")
        actual = hashlib.sha256(bundle.read_bytes()).hexdigest()
        if not recorded.is_file() or recorded.read_text(encoding="utf-8").split()[:1] != [actual]:
            logging.getLogger("sharpsight").error(
                "sharpsight: %s is not the bundle that was recorded (its sha256 is %s) — Superset starts "
                "without extensions", bundle.name, actual)
            return None
    return folder if found else None


EXTENSIONS_PATH = _recorded_bundles(os.environ.get("SHARPSIGHT_EXTENSIONS_DIR", "/app/sharpsight/extensions"))
# apache-superset-core is a release candidate in 6.1.0 and its flag is off by default: it is on here only when
# there is a recorded bundle to load.
FEATURE_FLAGS = {**_kit_flags, "ENABLE_EXTENSIONS": EXTENSIONS_PATH is not None}
