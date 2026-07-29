from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "DMS API"
    cors_origins: str = "http://127.0.0.1:3000,http://localhost:3000"
    cortex_url: str = "http://127.0.0.1:8010"
    openvault_url: str = "http://127.0.0.1:5000"
    # Pin: cortex-contract major 1; engine tag floats in compose
    cortex_contract_major: int = 1
    cortex_engine_image: str = "ghcr.io/netie/cortex:2.5.0-core"


@lru_cache
def get_settings() -> Settings:
    return Settings()
