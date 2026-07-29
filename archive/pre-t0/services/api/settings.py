from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "dms-api"
    environment: str = "local"
    database_url: str = "postgresql://dms:dms@127.0.0.1:5432/dms"
    cortex_url: str = "http://127.0.0.1:8010"
    openvault_url: str = "http://127.0.0.1:5000"
    cortex_proxy: bool = True
    jwt_secret: str = "dms-dev-secret-change-me"
    jwt_ttl_hours: int = 12
    demo_api_keys: str = (
        "viewer:dms-demo-viewer-key;steward:dms-demo-steward-key;admin:dms-demo-admin-key"
    )
    cors_origins: str = "http://127.0.0.1:3000,http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        cortex_url=os.environ.get("CORTEX_URL", Settings.model_fields["cortex_url"].default),
        openvault_url=os.environ.get("OPENVAULT_URL", Settings.model_fields["openvault_url"].default),
        database_url=os.environ.get("DATABASE_URL", Settings.model_fields["database_url"].default),
        cortex_proxy=os.environ.get("CORTEX_PROXY", "1").strip().lower() in ("1", "true", "yes", "on"),
        jwt_secret=os.environ.get("DMS_JWT_SECRET", Settings.model_fields["jwt_secret"].default),
    )
