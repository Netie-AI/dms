"""Google AI Overview as a cited source. Flag ``DMS_WEB_INSIGHT``, digit 1 only.

The search query is the user's question and nothing else. A pluggable
fetcher talks to a headless browser endpoint (``DMS_WEB_INSIGHT_FETCH_URL``).
CI replays a fixture instead of that endpoint. No overview, no endpoint, or
a failed fetch is an honest empty citation. The provider infers afterwards
from this citation and the governed data it already has. This module does
not send rows, SQL, or schema, and it does not add a Gemini key.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from dms_core.skills.errors import SkillError

FLAG = "DMS_WEB_INSIGHT"
FETCH_ENV = "DMS_WEB_INSIGHT_FETCH_URL"
LIMIT_ENV = "DMS_WEB_INSIGHT_PER_MINUTE"
TOOL_NAME = "google_ai_overview"
LABEL = "Google AI Overview"
_WINDOW_S = 60.0
_DEFAULT_LIMIT = 6
_QUERY_CAP = 500
_HITS: dict[str, list[float]] = {}

Transport = Callable[[str], Mapping[str, Any]]

_INFER = (
    "Infer using this citation and the governed data already in context. "
    "The overview is not a measured figure."
)
_NONE = "No Google AI Overview appeared for this query."
_UNCONFIGURED = "Google AI Overview fetch is not configured. DMS did not invent one."
_FAILED = "Google AI Overview could not be fetched. DMS did not invent one."
_LIMITED = "Google AI Overview is rate limited. Try again in a minute."
_OFF = "Google AI insights is off. DMS_WEB_INSIGHT is not the digit 1."


def enabled() -> bool:
    """True only when ``DMS_WEB_INSIGHT`` is ``1`` after stripping."""
    return os.environ.get(FLAG, "0").strip() == "1"


def tool_spec() -> dict[str, Any]:
    """Registry entry. The only argument is the question."""
    return {
        "name": TOOL_NAME,
        "description": (
            "Fetch the Google AI Overview for the user's question. "
            "Use it for a general question or a basic visual insight. "
            "Pass the question only. DMS cites the overview with the query "
            "and the time, then you infer from that citation and the governed "
            "data already in context. Do not put rows, SQL, or schema in the query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The user's question. Nothing else.",
                }
            },
            "required": ["question"],
        },
    }


def status() -> dict[str, Any]:
    on = enabled()
    return {
        "enabled": on,
        "flag": FLAG,
        "tool": TOOL_NAME if on else "",
        "label": LABEL if on else "",
        "message": (
            "Google AI insights cites a Google AI Overview, then the provider infers."
            if on
            else _OFF
        ),
    }


def search_query(question: str) -> str:
    """The question text only, capped. Not rows, SQL, or schema."""
    return " ".join((question or "").split())[:_QUERY_CAP]


def fetch_payload(query: str) -> dict[str, str]:
    """The only JSON the fetcher receives."""
    return {"query": query}


def fixture_transport(fixture: Mapping[str, Any]) -> Transport:
    """Replay a saved overview. The fixture cannot replace the query."""

    def _fetch(query: str) -> dict[str, Any]:
        del query
        overview = fixture.get("overview")
        if not isinstance(overview, str):
            return {"overview": ""}
        return {"overview": overview}

    return _fetch


def allow(
    tenant: str,
    *,
    now: float,
    limit: int | None = None,
    hits: dict[str, list[float]] | None = None,
) -> bool:
    """Sliding window. False means do not call the fetcher."""
    cap = _limit() if limit is None else limit
    book = _HITS if hits is None else hits
    key = tenant or "local"
    kept = [stamp for stamp in book.get(key, []) if now - stamp < _WINDOW_S]
    if len(kept) >= cap:
        book[key] = kept
        return False
    kept.append(now)
    book[key] = kept
    return True


def run(
    question: str,
    *,
    transport: Transport | None = None,
    tenant: str = "local",
    now: float | None = None,
    fetched_at: str | None = None,
    hits: dict[str, list[float]] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Cite an overview, or say that none appeared. Does not call a model."""
    if not enabled():
        raise SkillError("web_insight_disabled")
    query = search_query(question)
    if not query:
        raise SkillError("question_required")
    stamp = fetched_at or datetime.now(UTC).isoformat()
    moment = now if now is not None else datetime.now(UTC).timestamp()
    if not allow(tenant, now=moment, limit=limit, hits=hits):
        return _citation(query, stamp, overview="", fallback=_LIMITED, cited=False)
    fetch = transport or default_transport
    try:
        payload = fetch(query)
    except SkillError as exc:
        if exc.code == "web_insight_fetch_unconfigured":
            return _citation(query, stamp, overview="", fallback=_UNCONFIGURED, cited=False)
        return _citation(query, stamp, overview="", fallback=_FAILED, cited=False)
    overview = ""
    if isinstance(payload, Mapping):
        raw = payload.get("overview")
        if isinstance(raw, str):
            overview = " ".join(raw.split())[:4000]
    if not overview:
        return _citation(query, stamp, overview="", fallback=_NONE, cited=False)
    return _citation(query, stamp, overview=overview, fallback="", cited=True)


def default_transport(query: str) -> dict[str, Any]:
    """POST the question to the configured browser endpoint. No provider key."""
    url = os.environ.get(FETCH_ENV, "").strip()
    if not url:
        raise SkillError("web_insight_fetch_unconfigured")
    try:
        import httpx
    except ImportError as exc:
        raise SkillError("web_insight_fetch_failed") from exc
    try:
        response = httpx.post(url, json=fetch_payload(query), timeout=8.0)
    except httpx.HTTPError as exc:
        raise SkillError("web_insight_fetch_failed") from exc
    if response.status_code >= 400:
        raise SkillError("web_insight_fetch_failed")
    try:
        body = response.json()
    except ValueError as exc:
        raise SkillError("web_insight_fetch_failed") from exc
    if not isinstance(body, dict):
        return {"overview": ""}
    overview = body.get("overview")
    return {"overview": overview if isinstance(overview, str) else ""}


def _citation(
    query: str,
    fetched_at: str,
    *,
    overview: str,
    fallback: str,
    cited: bool,
) -> dict[str, Any]:
    return {
        "label": LABEL,
        "query": query,
        "fetched_at": fetched_at,
        "overview": overview,
        "cited": cited,
        "fallback": fallback,
        "served": False,
        "measured": False,
        "infer": _INFER,
    }


def _limit() -> int:
    raw = os.environ.get(LIMIT_ENV, "").strip()
    if raw.isdigit():
        value = int(raw)
        if 1 <= value <= 30:
            return value
    return _DEFAULT_LIMIT
