"""Off-contract Cortex POST /dms/query (same seam Crew EngineBridge uses).

Contract 1.2.0 has ask/submit/ledger only, and this is not a planner. It sends
``mode="ontology_plan"`` and an ``ontology`` context, but Cortex origin/main does
not read either: its DMSQueryRequest takes question, session_id and space_id
only (CortexOS/api/dms_query.py @ ceb7fc2), and it does not return a typed
``query_plan.measure``. So a caller expecting a generate+validate plan always
misses (KB F-0055).

No ask-path caller remains after GEN-03 (dms#194): ``Executor.live_ask`` passes a
closed, no-network compute seam instead. Deleting this module and
``CortexClient.compute_query`` is tracked in CONTRACT-FAKE-01. DMS never invents
provider keys and never puts secrets in the body.
"""

from __future__ import annotations

from typing import Any

import httpx

COMPUTE_PATH = "/dms/query"


def compute_query(
    base_url: str,
    *,
    question: str,
    session_id: str | None = None,
    space_id: str | None = None,
    ontology: dict[str, Any] | None = None,
    api_key: str | None = None,
    timeout: float = 45.0,
) -> dict[str, Any] | None:
    """POST /dms/query. None on unreachable / 4xx / 5xx - caller misses or abstains.

    Cortex ignores ``mode`` and ``ontology`` (see module docstring), so a 200 here
    is the engine's own /dms/query answer, not a plan built from the context sent.
    No ask-path caller since GEN-03; deletion tracked in CONTRACT-FAKE-01.

    ``api_key`` is forwarded when the caller already has one (viewer/engine).
    An empty key is not replaced with a guessed secret.
    """
    url = f"{base_url.rstrip('/')}{COMPUTE_PATH}"
    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = str(api_key)
        headers["Authorization"] = f"Bearer {api_key}"
    body: dict[str, Any] = {
        "question": question,
        "session_id": session_id or "demo",
        "space_id": space_id,
        "mode": "ontology_plan",
    }
    if ontology is not None:
        # Retrieved short context (schema + ontology slice). Not a secret bag.
        body["ontology"] = ontology
    try:
        with httpx.Client(timeout=timeout) as http:
            res = http.post(url, json=body, headers=headers or None)
    except httpx.HTTPError:
        return None
    if res.status_code != 200:
        return None
    try:
        payload = res.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


__all__ = ["COMPUTE_PATH", "compute_query"]
