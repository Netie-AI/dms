"""Read-only GETs against Cortex surfaces that are **not** in the frozen contract.

The contract (``openapi-1.2.0.json``, via ``cortex_client``) stays the only way DMS
*asks questions, submits SQL, or touches the ledger*. Ontology and benchmark
metadata are read-only descriptions of the engine, so they travel over plain httpx
here rather than forcing a contract minor before their shape has settled — the same
precedent ``routes/health.py`` already sets for probing Cortex.

Two rules this module exists to keep:

* **Never import CortexOS.** These are HTTP reads against ``settings.cortex_url``.
* **Degrade, never crash.** Cortex being down or unauthenticated must render as a
  banner in the product, not a 500 on a page the user opened to read documentation.

When a shape here stabilises, promote it to the contract (minor bump, regenerate
``contract/openapi-*.json``, re-pin) and delete the corresponding helper.
"""

from __future__ import annotations

from typing import Any

import httpx

#: Cortex's own default demo viewer key (packs/dms/security/api_auth._DEMO_KEYS).
#: Overridden by CORTEX_API_KEY in any deployment that sets DMS_API_KEYS.
DEFAULT_VIEWER_KEY = "dms-demo-viewer-key"


class CortexReadResult(dict[str, Any]):
    """A dict that also says whether it came from Cortex or from a failure."""


def cortex_get(
    base_url: str,
    path: str,
    *,
    api_key: str | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 4.0,
) -> CortexReadResult:
    """GET ``path`` from Cortex. Returns ``{"ok": bool, ...}`` — never raises.

    On success the engine payload is merged in under ``data``; on failure the
    reason is carried as ``error`` plus a ``hint`` the UI can print verbatim.
    """
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"X-API-Key": api_key or DEFAULT_VIEWER_KEY}
    try:
        with httpx.Client(timeout=timeout) as client:
            res = client.get(url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        return CortexReadResult(
            ok=False,
            source="cortex",
            url=url,
            error=f"unreachable: {exc.__class__.__name__}",
            hint=f"Cortex is not answering on {base_url}. Start the engine, then reload.",
            data=None,
        )
    if res.status_code in (401, 403):
        return CortexReadResult(
            ok=False,
            source="cortex",
            url=url,
            status_code=res.status_code,
            error="unauthorized",
            hint=(
                "Cortex rejected the read key. "
                "Set CORTEX_API_KEY to a viewer key from DMS_API_KEYS."
            ),
            data=None,
        )
    if res.status_code >= 400:
        return CortexReadResult(
            ok=False,
            source="cortex",
            url=url,
            status_code=res.status_code,
            error=f"http_{res.status_code}",
            hint=res.text[:400],
            data=None,
        )
    try:
        payload = res.json()
    except ValueError:
        return CortexReadResult(
            ok=False,
            source="cortex",
            url=url,
            error="not_json",
            hint="Cortex returned a non-JSON body for a read endpoint.",
            data=None,
        )
    return CortexReadResult(ok=True, source="cortex", url=url, data=payload)


__all__ = ["DEFAULT_VIEWER_KEY", "CortexReadResult", "cortex_get"]
