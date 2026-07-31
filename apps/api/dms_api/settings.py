from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

AskMode = Literal["demo", "live"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "DMS API"
    cors_origins: str = (
        "http://127.0.0.1:3000,http://localhost:3000,"
        "http://127.0.0.1:5173,http://localhost:5173"
    )
    cortex_url: str = "http://127.0.0.1:8010"
    # Viewer key for Cortex's read-only ontology/eval surfaces (off-contract, see
    # cortex_read.py). Matches Cortex's built-in demo key so local bring-up works;
    # set CORTEX_API_KEY wherever DMS_API_KEYS is set.
    cortex_api_key: str = "dms-demo-viewer-key"
    openvault_url: str = "http://127.0.0.1:5000"
    database_url: str | None = None
    # Product default = live (Cortex bind→ask). demo = offline fallback only.
    dms_ask_mode: AskMode = "live"
    # Off by default — silent success-with-demo-numbers is a lying affordance.
    # Set DMS_DEMO_FALLBACK=1 only for local bring-up; UI must show a permanent banner.
    dms_demo_fallback: bool = False
    # T5 lite — default tenant/user for seed until OIDC
    dms_tenant_id: str = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    dms_actor_user_id: str = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    dms_actor_role: str = "steward"
    cortex_contract_major: int = 1
    cortex_contract_version: str = "1.2.0"
    cortex_engine_image: str = "ghcr.io/netie/cortex:2.5.0-core"


@lru_cache
def get_settings() -> Settings:
    return Settings()
