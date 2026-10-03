"""Everything the service is told from outside. Nothing secret is written in this package."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
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
    dbt_dev_user: str
    dbt_dev_password: str
    dbt_dev_schema: str

    superset_url: str              # as this container reaches it
    superset_public_url: str       # as a person's browser reaches it
    superset_user: str
    superset_password: str
    superset_database_name: str

    api_token: str
    llm_provider: str

    @property
    def reader_dsn(self) -> str:
        return (f"host={self.warehouse_host} port={self.warehouse_port} dbname={self.warehouse_db} "
                f"user={self.reader_user} password={self.reader_password}")

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
        return [s for s in (self.reader_password, self.dbt_dev_password, self.superset_password, self.api_token)
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
        dbt_dev_user=_env("AIBI_DBT_DEV_USER", "aibi_dbt_dev"),
        dbt_dev_password=_env("AIBI_DBT_DEV_PASSWORD", ""),
        dbt_dev_schema=_env("AIBI_DBT_DEV_SCHEMA", "bi_dev"),
        superset_url=_env("AIBI_SUPERSET_URL", "http://superset-devkit:8088").rstrip("/"),
        superset_public_url=_env("AIBI_SUPERSET_PUBLIC_URL", "http://127.0.0.1:3002").rstrip("/"),
        superset_user=_env("AIBI_SUPERSET_USER", "sharpsight-ai"),
        superset_password=_env("AIBI_SUPERSET_PASSWORD", ""),
        superset_database_name=_env("AIBI_SUPERSET_DATABASE", "SharpSight BI (read-only)"),
        api_token=_env("AIBI_API_TOKEN", ""),
        llm_provider=_env("AIBI_LLM_PROVIDER", "none"),
    )
