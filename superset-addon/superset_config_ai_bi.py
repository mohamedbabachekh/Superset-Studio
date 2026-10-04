"""Superset's configuration when the AI BI engine is on: the kit's own, and what SharpSight adds to Superset.

docker-compose.ai-bi.yaml points SUPERSET_CONFIG_PATH here. The kit's superset_config.py is not edited and not
copied: it is imported, whole, and one hook is extended. Without the engine's overlay this file is not mounted
and Superset starts exactly as the kit ships it.
"""
from superset_config import *  # noqa: F401,F403  — every setting of the kit, as it is
from superset_config import FLASK_APP_MUTATOR as _kit_mutator

import sharpsight_bi


def FLASK_APP_MUTATOR(app):  # noqa: N802,F811  (Superset calls this hook with the built application)
    _kit_mutator(app)
    sharpsight_bi.install(app)
