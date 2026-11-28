"""Typed DMS client. No model id and no provider id.

Routing stays in OpenVault, inside DMS. This client only calls DMS HTTP.
POST is one shot, so a retried ask cannot append the ledger twice. GET
retries transport errors and 502/503/504 with backoff.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from dms_sdk.outcome import route_stamp, typed_outcome

_RETRY_STATUS = frozenset({502, 503, 504})
_FORBIDDEN = frozenset({"model", "provider", "model_id", "provider_id", "strict"})


class DmsClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        sleeper: Callable[[float], None] = time.sleep,
        attempts: int = 3,
    ) -> None:
        self._owns = client is None
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=headers,
        )
        self._sleeper = sleeper
        self._attempts = max(1, attempts)

    def close(self) -> None:
        if self._owns:
            self._client.close()

    def ask(
        self,
        *,
        space_id: str,
        question: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"question": question, "space_id": space_id}
        if session_id:
            payload["session_id"] = session_id
        return self._stamp(self._json("POST", "/v1/chat/ask", payload, retry=False))

    def answer_clarify(
        self,
        *,
        clarify_id: str,
        reading_id: str | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"clarify_id": clarify_id}
        if reading_id is not None:
            payload["reading_id"] = reading_id
        if text is not None:
            payload["text"] = text
        return self._stamp(self._json("POST", "/v1/chat/clarify/pick", payload, retry=False))

    def benchmark_run(self, *, space_id: str) -> dict[str, Any]:
        body = self._json("POST", "/v1/pipeline/bench", {"space_id": space_id}, retry=False)
        return self._stamp(body)

    def semantic_import(
        self,
        *,
        space_id: str,
        document: dict[str, Any] | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        if dry_run:
            return {
                "outcome": "served",
                "route": "pipeline.semantic_import",
                "dry_run": True,
                "space_id": space_id,
            }
        payload = {"space_id": space_id, "dry_run": False, "document": document or {}}
        return self._stamp(self._json("POST", "/v1/pipeline/semantic/import", payload, retry=False))

    def proof_fetch(self, *, space_id: str) -> dict[str, Any]:
        return self._stamp(
            self._json(
                "GET",
                "/v1/pipeline/proof",
                None,
                retry=True,
                params={"space_id": space_id},
            )
        )

    def _stamp(self, body: dict[str, Any]) -> dict[str, Any]:
        out = dict(body)
        out["outcome"] = typed_outcome(out)
        if not out.get("route"):
            stamped = route_stamp(out)
            out["route"] = stamped
        return out

    def _guard(self, payload: dict[str, Any] | None) -> None:
        if not payload:
            return
        if _FORBIDDEN.intersection(payload):
            raise ValueError("route_field_refused")

    def _json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        retry: bool,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self._guard(payload)
        tries = self._attempts if retry else 1
        delay = 0.05
        for attempt in range(tries):
            try:
                response = self._client.request(method, path, json=payload, params=params)
            except httpx.HTTPError:
                if attempt + 1 >= tries:
                    return {"outcome": "error", "route": None, "code": "transport"}
                self._sleeper(delay)
                delay *= 2
                continue
            if response.status_code in _RETRY_STATUS and attempt + 1 < tries:
                self._sleeper(delay)
                delay *= 2
                continue
            return _read(response)
        return {"outcome": "error", "route": None, "code": "transport"}


def _read(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        return {
            "outcome": "error",
            "route": None,
            "status": response.status_code,
            "code": _error_code(response),
        }
    try:
        body = response.json()
    except ValueError:
        return {"outcome": "error", "route": None, "code": "bad_body"}
    if not isinstance(body, dict):
        return {"outcome": "error", "route": None, "code": "bad_body"}
    return body


def _error_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "http_error"
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        code = detail.get("code")
        if isinstance(code, str) and code:
            return code[:80]
    if isinstance(detail, str) and detail:
        return detail[:80]
    return "http_error"
