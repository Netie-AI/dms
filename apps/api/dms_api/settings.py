from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

AskMode = Literal["demo", "live"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "DMS API"
    cors_origins: str = "http://127.0.0.1:3000,http://localhost:3000"
    cortex_url: str = "http://127.0.0.1:8010"
    openvault_url: str = "http://127.0.0.1:5000"
    database_url: str | None = None
    # Product default = live (Cortex bind→ask). demo = offline fallback only.
    dms_ask_mode: AskMode = "live"
    # When live fails and this is true, fall back to demo_ask (labeled).
    dms_demo_fallback: bool = True
    # T5 lite — default tenant/user for seed until OIDC
    dms_tenant_id: str = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    dms_actor_user_id: str = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    dms_actor_role: str = "steward"
    cortex_contract_major: int = 1
    cortex_contract_version: str = "1.1.0"
    cortex_engine_image: str = "ghcr.io/netie/cortex:2.5.0-core"


@lru_cache
def get_settings() -> Settings:
    return Settings()
