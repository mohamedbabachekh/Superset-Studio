"""SharpSight Superset DEV KIT — the workbench's configuration.

It is deliberately NOT the product's configuration (services/superset/superset_config.py):

  · the product trusts identity headers from the SharpSight gateway and shows no login screen; this one has an
    ordinary Superset login, because there is no gateway in front of it;
  · the product gives readers one read-only connection and no SQL Lab; this one opens SQL Lab and lets a
    developer add any connection they need;
  · the product imports its dashboards from files at start and never lets a reader edit them; here you draw.

What it DOES keep is the look — the same brand assets, colours, spinner and French interface — so a dashboard
built here is the dashboard the customer will see.

Everything secret comes from the environment; nothing is written in this file.
"""
import os

# Oracle in thin mode on SQLAlchemy 1.4 — devkit_drivers.py explains why this is needed at all.
import devkit_drivers

_ORACLE = devkit_drivers.install()


def FLASK_APP_MUTATOR(app):  # noqa: N802  (Superset calls this hook with the built application)
    """Two things the application needs that configuration alone cannot express.

    The forms: SQL Server and Oracle get the « host, port, user, password » dialog PostgreSQL has — done here
    and not at import time, because the engine specs pull in half of Superset and this file is read before the
    application exists (devkit_forms.py says what it changes).

    The sign-in page: our own template folder goes AHEAD of Flask-AppBuilder's, so
    `appbuilder/general/security/login_db.html` resolves to the branded one. Prepending rather than replacing
    means every other template FAB and Superset ship is still found where it always was.
    """
    import jinja2

    import devkit_forms

    for line in devkit_forms.install():
        app.logger.info("devkit: %s", line)

    # The scheduled refresh records what a dashboard asks for, so that devkit-refresh can ask it again before
    # anyone opens the page (devkit_refresh.py says how, and why Superset's own warm-up warms the wrong entries).
    import devkit_refresh

    devkit_refresh.install(app)

    # NO OUTBOUND BEACON. SCARF_ANALYTICS = False removes Apache's telemetry pixel from the page Superset
    # renders, but the FRONTEND BUNDLE adds one of its own at runtime (found in 8085.*.entry.js), so a kit
    # that is meant to run on an air-gapped machine still announced itself whenever it could reach the
    # internet. One header settles it, and it restricts images only: nothing else this kit draws comes from
    # outside — the fonts are local, the logos are in the image, and there are no map tiles here.
    @app.after_request
    def _images_from_here_only(response):  # noqa: ANN001
        response.headers.setdefault("Content-Security-Policy", "img-src 'self' data: blob:")
        return response

    app.jinja_loader = jinja2.ChoiceLoader([
        jinja2.FileSystemLoader("/app/pythonpath/templates"),
        app.jinja_loader,
    ])

# ── identity ──────────────────────────────────────────────────────────────────────────────────────────────────
# A real login, with Superset's own user store in its own metadata database. The admin is created by the init
# step (docker-compose.yaml) from DEVKIT_ADMIN_* — change the password there, not here.
SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = os.environ["SUPERSET_META_URI"]
AUTH_TYPE = 1  # AUTH_DB — username and password
AUTH_USER_REGISTRATION = False
PUBLIC_ROLE_LIKE = None          # nothing is visible without signing in
WTF_CSRF_ENABLED = True
HTML_SANITIZATION = True
# Superset's own telemetry pixel (apachesuperset.gateway.scarf.sh) is loaded by the login page. Measured here on
# 2026-09-27. On an air-gapped machine it simply fails; on a connected one it tells a third party that this
# instance exists, every time someone signs in. A workbench has no reason to.
SCARF_ANALYTICS = False
TALISMAN_ENABLED = False         # no TLS-terminating proxy in front of a laptop's dev kit
SESSION_COOKIE_NAME = "superset-devkit.session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = os.environ.get("DEVKIT_COOKIE_SECURE", "false").lower() == "true"

# A dev kit may be reached through nginx/Caddy on a server; honour the forwarding headers when it is.
ENABLE_PROXY_FIX = os.environ.get("DEVKIT_BEHIND_PROXY", "false").lower() == "true"
PROXY_FIX_CONFIG = {"x_for": 1, "x_proto": 1, "x_host": 1, "x_port": 0, "x_prefix": 0}

# ── caches ────────────────────────────────────────────────────────────────────────────────────────────────────
# Redis, like the product. A chart's result is kept ONE HOUR by default (DEVKIT_DATA_CACHE_TTL; six hours suits a
# server whose sources load once a day). It was 60 seconds, « so that a stale chart is never a lie about the query
# you just changed » — but the cache key IS the query: change a chart, a metric or a filter and the key changes
# with it, so nothing stale is served for a query you edited. What an hour can hide is new DATA behind the same
# query; « Force refresh » on a chart or a dashboard asks again. (Until 2026-10-05 compose did not pass this
# setting at all: the value in .env was read by nobody, and this default was the only one that ever applied.)
_REDIS = os.environ["SUPERSET_REDIS_URL"]
_META_TTL = int(os.environ.get("DEVKIT_META_CACHE_TTL", "300"))
_DATA_TTL = int(os.environ.get("DEVKIT_DATA_CACHE_TTL", "3600"))
_cache = lambda ttl, db: {"CACHE_TYPE": "RedisCache", "CACHE_DEFAULT_TIMEOUT": ttl,
                          "CACHE_KEY_PREFIX": f"devkit_{db}_", "CACHE_REDIS_URL": _REDIS}
CACHE_CONFIG = _cache(_META_TTL, "meta")
DATA_CACHE_CONFIG = _cache(_DATA_TTL, "data")
FILTER_STATE_CACHE_CONFIG = _cache(_META_TTL, "filter")
EXPLORE_FORM_DATA_CACHE_CONFIG = _cache(_META_TTL, "explore")
RATELIMIT_STORAGE_URI = _REDIS

# ── querying ──────────────────────────────────────────────────────────────────────────────────────────────────
# SQL Lab is the point of this kit: write the query, see the rows, then « Explore » it into a chart.
SQLLAB_TIMEOUT = int(os.environ.get("DEVKIT_SQLLAB_TIMEOUT", "300"))          # seconds a query may run
SUPERSET_WEBSERVER_TIMEOUT = int(os.environ.get("DEVKIT_WEB_TIMEOUT", "300"))  # gunicorn must outlive it

# THE METADATA POOL FOLLOWS THE THREADS. The image runs `gunicorn --threads 20` per worker; SQLAlchemy's
# default pool is 5 with 10 of overflow, so twenty threads queue on fifteen connections and a burst — SQL Lab's
# bootstrap is one — stalls behind the pool rather than behind the database. The platform met exactly this in
# production (v1.15.70) and answered it the same way. A thread that cannot get a connection in POOL_TIMEOUT
# seconds raises rather than hanging for ever, and pre_ping drops a connection the database closed under us.
_THREADS = int(os.environ.get("SERVER_THREADS_AMOUNT", "20"))
SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_size": _THREADS,
    "max_overflow": _THREADS,
    "pool_timeout": int(os.environ.get("DEVKIT_POOL_TIMEOUT", "30")),
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
SQL_MAX_ROW = int(os.environ.get("DEVKIT_SQL_MAX_ROW", "100000"))
DISPLAY_MAX_ROW = int(os.environ.get("DEVKIT_DISPLAY_MAX_ROW", "10000"))
DEFAULT_SQLLAB_LIMIT = 1000
SQLLAB_CTAS_NO_LIMIT = True        # CREATE TABLE AS ... is not truncated by the UI's row limit
# A source you should not be able to reach by typing a URI (sqlite on the container's own disk, and friends).
PREVENT_UNSAFE_DB_CONNECTIONS = True

FEATURE_FLAGS = {
    # what a dashboard author needs
    "DASHBOARD_CROSS_FILTERS": True,
    "DRILL_TO_DETAIL": True,
    "DRILL_BY": True,
    "DASHBOARD_RBAC": True,
    "HORIZONTAL_FILTER_BAR": True,
    "ENABLE_TEMPLATE_PROCESSING": True,   # Jinja in SQL Lab and in datasets
    "ESTIMATE_QUERY_COST": True,
    "ALLOW_FULL_CSV_EXPORT": True,
    "EMBEDDED_SUPERSET": False,
    # the two that make a dashboard portable into the product
    "VERSIONED_EXPORT": True,
    "TAGGING_SYSTEM": True,
}

# Uploads: a CSV or an Excel file becomes a table you can chart — handy for a mock-up before the source exists.
CSV_EXTENSIONS = {"csv", "tsv", "txt"}
EXCEL_EXTENSIONS = {"xlsx", "xls"}
UPLOAD_FOLDER = "/app/superset_home/uploads/"

# ── the look: the product's, so what you draw is what they will see ───────────────────────────────────────────
APP_NAME = os.environ.get("DEVKIT_APP_NAME", "SharpSight Superset — atelier")
APP_ICON = "/static/assets/images/sharpsight/sharpsight-lockup.png"
FAVICONS = [{"href": "/static/assets/images/sharpsight/sharpsight-mark.svg"}]

SHARPSIGHT_BLUE, SHARPSIGHT_CORAL, SHARPSIGHT_TEAL, SHARPSIGHT_YELLOW, SHARPSIGHT_GREEN = (
    "#0972BC", "#F8644E", "#26ABA8", "#F2B72D", "#37A15E")
# The series a chart is drawn in by default: SharpSight's five, and a dark slate as the sixth. Measured
# (CIE76 after Machado 2009, severity 1): two neighbours stay 35 or more apart for normal vision, deuteranopia
# and protanopia; coral and green — which are never neighbours — come within 8 under protanopia. A violet
# sixth, tried first, fell within 5 of the blue for both. A measure, not a certificate.
SHARPSIGHT_SERIES = [SHARPSIGHT_BLUE, SHARPSIGHT_CORAL, SHARPSIGHT_TEAL, SHARPSIGHT_YELLOW, SHARPSIGHT_GREEN, "#2F3B52"]
# A client's colours: a variant a dashboard may wear (governance/registry/design/tokens.yaml, `brands`), never the default.
INWI_MAGENTA = "#E6007E"
INWI_SERIES = ["#E6007E", "#0E7FA8", "#B8730B", "#00926F", "#8360C3", "#C1443B"]

_TOKENS = {
    "brandAppName": APP_NAME,
    "brandLogoAlt": "SharpSight",
    "brandLogoUrl": APP_ICON,
    "brandLogoHref": "/",
    "brandLogoHeight": "30px",
    "brandLogoMargin": "14px 0",
    "brandSpinnerUrl": "/static/assets/images/sharpsight/sharpsight-loader.svg",
    "brandSpinnerSvg": None,
    "colorPrimary": SHARPSIGHT_BLUE,      # SharpSight's own colours lead (decided 2026-10-04): 5.07 on white
    "colorLink": SHARPSIGHT_BLUE,
    "colorInfo": SHARPSIGHT_TEAL,
    "colorSuccess": SHARPSIGHT_GREEN,
    "colorWarning": SHARPSIGHT_YELLOW,
    "colorError": "#D64545",
    "fontUrls": [],
    "fontFamily": "Inter, 'Segoe UI', Helvetica, Arial, sans-serif",
    "fontFamilyCode": "'IBM Plex Mono', 'Courier New', monospace",
    "borderRadius": 10,
    "transitionTiming": 0.3,
    "brandIconMaxWidth": 37,
    "fontSizeXS": "8",
    "fontSizeXXL": "28",
    "fontWeightNormal": "400",
    "fontWeightLight": "300",
    "fontWeightStrong": "500",
    "fontWeightBold": "700",
    "colorEditorSelection": "#fff5cf",
}
THEME_DEFAULT = {"token": _TOKENS, "algorithm": "default"}
# One theme, the light one — same reason as the product: a dark browser must not repaint a dashboard that is
# drawn on white. DEVKIT_DARK=1 brings the dark theme and the reader's switch back.
THEME_DARK = ({"token": {**_TOKENS,
                         "brandLogoUrl": "/static/assets/images/sharpsight/sharpsight-lockup-white.png",
                         "colorBgBase": "#0C0E1F", "colorBgContainer": "#141733",
                         "colorEditorSelection": "#5c4d1a"},
               "algorithm": "dark"}
              if os.environ.get("DEVKIT_DARK", "0") == "1" else None)

EXTRA_CATEGORICAL_COLOR_SCHEMES = [{
    "id": "sharpsight",
    "description": "SharpSight's own series — the default for a dashboard",
    "label": "SharpSight",
    "colors": SHARPSIGHT_SERIES,
}, {
    "id": "inwi",
    "description": "inwi magenta leading — a client's series, for a dashboard made in its colours",
    "label": "inwi",
    "colors": INWI_SERIES,
}]
EXTRA_SEQUENTIAL_COLOR_SCHEMES = []

# The interface in French, like the product; both languages are offered here because a developer may want the
# English names when reading Superset's own documentation.
BABEL_DEFAULT_LOCALE = os.environ.get("DEVKIT_LOCALE", "fr")
LANGUAGES = {"fr": {"flag": "fr", "name": "Français"}, "en": {"flag": "us", "name": "English"}}

# The four offered as buttons in « + Base de données », in this order, instead of Superset's own list — which
# leads with PostgreSQL, Presto, MySQL and SQLite and puts the two this kit exists for in a dropdown of eighty.
# A name here must be the spec's engine_name exactly, or Superset silently ignores it.
PREFERRED_DATABASES = ["Microsoft SQL Server", "Oracle", "PostgreSQL", "Apache Hive"]

# ── connections you may add, and the drivers that are installed for them ──────────────────────────────────────
# PostgreSQL        postgresql+psycopg2://user:password@host:5432/database
# SQL Server        mssql+pymssql://user:password@host:1433/database
# Oracle            oracle+cx_oracle://user:password@host:1521/?service_name=ORCLPDB1
#                   (python-oracledb in THIN mode under that name — nothing else to install, no Instant
#                    Client; oracle+oracledb:// is registered as an alias. For a SID: .../?sid=ORCL)
# Hive / Spark      hive://host:10000/default
# The CEM's Yacht JDBC gateway has no SQLAlchemy dialect — Superset cannot dial it directly. Use
# cem_to_mart.py (in this image) to land a query's result in this kit's own Postgres, then chart that.
