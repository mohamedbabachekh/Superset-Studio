"""Everything the service is told from outside. Nothing secret is written in this package."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote


@dataclass(frozen=True)
class Settings:
    registry_dir: Path
    dbt_project_dir: Path          # the governed project, mounted read-only
    dbt_prod_state_dir: Path       # the production build's artifacts, published by the dbt-runner
    state_dir: Path                # this service's own volume: repositories, workspaces, audit, packages
    exports_dir: Path              # the kit's ./exports, where a package lands for whoever carries it on

    warehouse_host: str
    warehouse_port: int
    warehouse_db: str
    reader_user: str
    reader_password: str
    records_reader_user: str
    records_reader_password: str
    dbt_dev_user: str
    dbt_dev_password: str
    dbt_dev_schema: str

    superset_url: str              # as this container reaches it
    superset_public_url: str       # as a person's browser reaches it
    superset_user: str
    superset_password: str
    superset_database_name: str

    api_token: str
    superset_gateway_token: str
    # The model in front of the deterministic planner, if any. `none` (the default) calls nobody and needs no
    # key or dependency; `claude` and `openai` are wired in aibi/llm.py and are opt-in per deployment.
    llm_provider: str
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""

    @property
    def reader_dsn(self) -> str:
        return (f"host={self.warehouse_host} port={self.warehouse_port} dbname={self.warehouse_db} "
                f"user={self.reader_user} password={self.reader_password}")

    @property
    def records_dsn(self) -> str:
        """Where the engine copies its OWN records for Superset to draw: the schema `bi_factory`, which the
        draft-building role owns (sql/bootstrap.sql). The same role, and no new secret: it can write to two
        schemas — drafts and this copy — and to nothing governed."""
        return (f"host={self.warehouse_host} port={self.warehouse_port} dbname={self.warehouse_db} "
                f"user={self.dbt_dev_user} password={self.dbt_dev_password}")

    @property
    def records_database_uri(self) -> str:
        """The connection Superset draws the engine's own records through: a role that reads the schema
        `bi_factory` and nothing else. A connection of its own, closed to SQL Lab — the audit trail is in it,
        and the connection every BI chart uses must not be a way to it."""
        return (f"postgresql+psycopg2://{quote(self.records_reader_user)}:{quote(self.records_reader_password, safe='')}"
                f"@{self.warehouse_host}:{self.warehouse_port}/{self.warehouse_db}")

    @property
    def superset_database_uri(self) -> str:
        """The read-only connection Superset charts through — the same role the data gateway uses."""
        return (f"postgresql+psycopg2://{quote(self.reader_user)}:{quote(self.reader_password, safe='')}"
                f"@{self.warehouse_host}:{self.warehouse_port}/{self.warehouse_db}")

    def dbt_env(self) -> dict[str, str]:
        return {
            "DBT_TARGET": "dev",
            "DBT_HOST": self.warehouse_host,
            "DBT_PORT": str(self.warehouse_port),
            "DBT_DBNAME": self.warehouse_db,
            "DBT_USER": self.dbt_dev_user,
            "DBT_PASSWORD": self.dbt_dev_password,
            "DBT_SCHEMA": self.dbt_dev_schema,
            "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
            "DO_NOT_TRACK": "1",
        }

    def secrets(self) -> list[str]:
        """Every value that must never appear in a log, an audit record or an API answer."""
        return [s for s in (self.reader_password, self.records_reader_password, self.dbt_dev_password,
                            self.superset_password, self.api_token, self.superset_gateway_token,
                            self.llm_api_key)
                if s]


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"{name} is not set — see docs/ai-bi.md, « the settings »")
    return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        registry_dir=Path(_env("AIBI_REGISTRY_DIR", "/app/registry")),
        dbt_project_dir=Path(_env("AIBI_DBT_PROJECT_DIR", "/app/dbt")),
        dbt_prod_state_dir=Path(_env("AIBI_DBT_PROD_STATE", "/var/lib/dbt-prod")),
        state_dir=Path(_env("AIBI_STATE_DIR", "/var/lib/ai-bi")),
        exports_dir=Path(_env("AIBI_EXPORTS_DIR", "/app/exports")),
        warehouse_host=_env("AIBI_WAREHOUSE_HOST", "devkit-db"),
        warehouse_port=int(_env("AIBI_WAREHOUSE_PORT", "5432")),
        warehouse_db=_env("AIBI_WAREHOUSE_DB", "devkit"),
        reader_user=_env("AIBI_READER_USER", "bi_reader"),
        reader_password=_env("AIBI_READER_PASSWORD", ""),
        records_reader_user=_env("AIBI_RECORDS_READER_USER", "bi_records_reader"),
        records_reader_password=_env("AIBI_RECORDS_READER_PASSWORD", ""),
        dbt_dev_user=_env("AIBI_DBT_DEV_USER", "aibi_dbt_dev"),
        dbt_dev_password=_env("AIBI_DBT_DEV_PASSWORD", ""),
        dbt_dev_schema=_env("AIBI_DBT_DEV_SCHEMA", "bi_dev"),
        superset_url=_env("AIBI_SUPERSET_URL", "http://superset-devkit:8088").rstrip("/"),
        superset_public_url=_env("AIBI_SUPERSET_PUBLIC_URL", "http://127.0.0.1:3002").rstrip("/"),
        superset_user=_env("AIBI_SUPERSET_USER", "sharpsight-ai"),
        superset_password=_env("AIBI_SUPERSET_PASSWORD", ""),
        superset_database_name=_env("AIBI_SUPERSET_DATABASE", "SharpSight BI (read-only)"),
        api_token=_env("AIBI_API_TOKEN", ""),
        superset_gateway_token=_env("AIBI_SUPERSET_GATEWAY_TOKEN", ""),
        llm_provider=_env("AIBI_LLM_PROVIDER", "none"),
        llm_model=_env("AIBI_LLM_MODEL", ""),
        llm_api_key=_env("AIBI_LLM_API_KEY", ""),
        llm_base_url=_env("AIBI_LLM_BASE_URL", ""),
    )


def targets() -> dict[str, Any]:
    """Where a version may be deployed, read from the environment.

    A target is a SUPERSET SOMEWHERE ELSE — the « run » environment — named by four or five variables:

        AIBI_TARGET_RUN_URL        https://bi.example.com
        AIBI_TARGET_RUN_USER       a Superset account there that may import
        AIBI_TARGET_RUN_PASSWORD   its password
        AIBI_TARGET_RUN_DB_NAME    what the governed connection is called THERE — the same name the package
                                   expects, because Superset 6 records a catalog per dataset and for
                                   PostgreSQL the catalog is the database (docs/lessons.md)
        AIBI_TARGET_RUN_DB_URI     that environment's warehouse
        AIBI_TARGET_RUN_FROM_STUDIO=false   to make it the command line's alone
        AIBI_TARGET_RUN_GATE=UAT    the approval a version needs to go there; PROD when it is not said, so a
                                    target nobody described is held to the release manager's approval
        AIBI_TARGET_RUN_PUBLIC_URL  where a PERSON reaches it, when that is not _URL (a container name, a
                                    private address): the link a deployment leaves behind is for somebody
                                    to open

    Nothing is deployed anywhere that is not named here, and nothing here is written down in this
    repository: a target is somebody else's server, with somebody else's password.
    """
    from .deploy import Target                      # imported here: config is read before deploy is loaded

    found: dict[str, Target] = {}
    for key, url in os.environ.items():
        if not key.startswith("AIBI_TARGET_") or not key.endswith("_URL") or not url.strip():
            continue
        name = key[len("AIBI_TARGET_"):-len("_URL")].lower()
        read = lambda part, default="": os.environ.get(f"AIBI_TARGET_{name.upper()}_{part}", default)  # noqa: E731
        if not read("DB_URI"):
            continue                                 # a target without its warehouse is not a target
        found[name] = Target(name=name, url=url.strip(), user=read("USER", "admin"),
                             password=read("PASSWORD"), database_name=read("DB_NAME", "SharpSight BI"),
                             database_uri=read("DB_URI"),
                             from_studio=read("FROM_STUDIO", "true").lower() != "false",
                             gate="UAT" if read("GATE", "PROD").strip().upper() == "UAT" else "PROD",
                             public_url=read("PUBLIC_URL").strip())
    return found
