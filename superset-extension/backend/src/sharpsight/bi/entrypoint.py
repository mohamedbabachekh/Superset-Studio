"""SharpSight BI — the backend of the Superset extension. Superset imports this module when it loads the bundle.

Importing the API class is what registers it: `@api` is Superset's own decorator for an extension's endpoints
(apache-superset-core). Nothing else happens here. The permissions the endpoints are protected by are written
by Superset — `superset init` does it for every API it knows — or, where Superset runs with extensions switched
on by a configuration layer its own `init` never saw, by that layer (superset-addon/sharpsight_bi.py).
"""
from .api import SharpSightBiApi  # noqa: F401  (the import registers the API)
