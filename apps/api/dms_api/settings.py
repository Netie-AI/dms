from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
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
    #: Seconds to wait on a Cortex call before giving up.
    #:
    #: The client's own default is 30s and nothing ever overrode it, which was
    #: shorter than the path it calls. A free-form (L2) ask generates SQL through
    #: a model provider inside the submit, and measured submits on this stack take
    #: 32.6s and 45.4s. So every free-form question failed at the client, before
    #: the engine had answered - not because the engine was wrong, but because DMS
    #: stopped listening. Four of ten questions in the demo gate died this way.
    #:
    #: 120s is chosen to clear the measured worst case with headroom while staying
    #: bounded: a hung engine must still fail rather than hold a worker forever.
    #: Raise it for a slower provider, do not remove it.
    cortex_timeout_seconds: float = 120.0
    # Key for Cortex's keyed, off-contract surfaces (ontology/eval reads, Insights,
    # the ask lane's generate call). KEY-01 (dms#273): NO default and no fallback.
    # It comes only from explicit config (CORTEX_API_KEY, supplied from OpenVault
    # by the operator). Unset, empty or blank all normalise to None, and every keyed
    # Cortex call then refuses with a named error (cortex_key_missing on the reads
    # and the Insights routes, insights_bearer_missing on the ask lane) instead of
    # going out under a published demo key. Offline demo mode (dms_demo_fallback)
    # never calls Cortex, so it needs no key.
    cortex_api_key: str | None = None
    openvault_url: str = "http://127.0.0.1:5000"
    database_url: str | None = None
    # Product default = live (Cortex bind→ask). demo = offline fallback only.
    dms_ask_mode: AskMode = "live"
    # Off by default — silent success-with-demo-numbers is a lying affordance.
    # Set DMS_DEMO_FALLBACK=1 only for local bring-up; UI must show a permanent banner.
    dms_demo_fallback: bool = False
    # EPIC-014 MCP-01. Swap: IDE MCP client on /v1/mcp/*. Off = no extra surface.
    dms_mcp: bool = False
    # GEN-03 (dms#194). Swap: a measurement origin runs the isolated
    # ask_path=exact|generative lanes for A/B scoring; customer deploys never set
    # it, and POST /v1/chat/ask refuses those lanes with 400 (DR-0004 refuse, not
    # ignore). Server config only - never a request header.
    dms_harness_ask_paths: bool = False
    # T5 lite — default tenant/user for seed until OIDC
    dms_tenant_id: str = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    dms_actor_user_id: str = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    dms_actor_role: str = "steward"
    cortex_contract_major: int = 1
    cortex_contract_version: str = "1.2.0"
    cortex_engine_image: str = "ghcr.io/netie/cortex:2.5.0-core"

    @field_validator("cortex_api_key", mode="before")
    @classmethod
    def _blank_key_is_missing(cls, value: object) -> object:
        """``CORTEX_API_KEY=`` (empty or whitespace) is a missing key, not a key."""
        if value is None:
            return None
        text = str(value).strip()
        return text or None


@lru_cache
def get_settings() -> Settings:
    return Settings()
