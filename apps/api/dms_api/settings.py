from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Literal
from urllib.parse import unquote

from pydantic_settings import BaseSettings, SettingsConfigDict

AskMode = Literal["demo", "live"]

# ENV-EXAMPLE-SENTINEL (#388). The one definition of a placeholder: the word the
# shipped .env.example files put where a secret goes. Boot refuses it, by the
# variable's name and never its value, so an unedited copy cannot start.
#: What .env.example ships in a secret's place.
PLACEHOLDER = "CHANGE_ME"
# Any case, with - _ . space or nothing between the words, percent-encoded or
# not, alone or inside a URL. Letter boundaries keep "exchange-medium" a real value.
_PLACEHOLDER_RE = re.compile(r"(?<![a-z])change[-_. ]?me(?![a-z])", re.IGNORECASE)
#: Settings fields that carry a secret (env name = the field name upper-cased).
_SECRET_FIELDS = ("database_url", "cortex_api_key")
#: Secrets the packages read straight from the environment, not through Settings.
_SECRET_ENV = ("OPENVAULT_API_KEY", "DMS_OV_SERVICE_TOKEN")


class PlaceholderSecretError(RuntimeError):
    """A secret-bearing setting still holds the shipped placeholder."""


def is_placeholder(value: str | None) -> bool:
    text = str(value or "")
    for _ in range(3):  # a percent-encoded sentinel, encoded more than once
        text = unquote(text)
    return bool(_PLACEHOLDER_RE.search(text))


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
    cortex_contract_version: str = "1.9.0"
    cortex_engine_image: str = "ghcr.io/netie/cortex:2.5.0-core"


def refuse_placeholder_secrets(settings: Settings) -> None:
    """Raise, naming every secret-bearing variable that holds a placeholder."""
    found = {name.upper(): getattr(settings, name) for name in _SECRET_FIELDS}
    found.update({name: os.environ.get(name) for name in _SECRET_ENV})
    names = sorted(name for name, value in found.items() if is_placeholder(value))
    if names:
        raise PlaceholderSecretError(
            f"placeholder_secret: {', '.join(names)} still holds the placeholder shipped "
            "in .env.example. Set a real value. Refusing to start."
            + (
                " In compose, DATABASE_URL is built from DMS_DB_PASSWORD."
                if "DATABASE_URL" in names
                else ""
            )
        )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # MUTATION: guard disabled
    return settings
