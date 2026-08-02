from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter

from dms_api.deps import SettingsDep, StoreBindingDep

router = APIRouter()

#: Fallback shown when OpenVault is offline and no root hint is available.
#: Kept as a module constant because a backslash inside f-string braces requires
#: Python 3.12 (PEP 701) and this package targets 3.11.
_OPENVAULT_DEFAULT_ROOT = "D:\\\\OpenVault"


def _probe(url: str, path: str = "/health", timeout: float = 1.2) -> dict[str, Any]:
    base = url.rstrip("/")
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(f"{base}{path}")
            return {
                "ok": r.status_code < 500 and r.status_code != 404,
                "status_code": r.status_code,
                "url": f"{base}{path}",
            }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200], "url": f"{base}{path}"}


def _probe_cortex_contract(base_url: str, timeout: float = 1.2) -> dict[str, Any]:
    """Detect stale Cortex that answers /health but lacks /v1/contract/* (submit 404)."""
    base = base_url.rstrip("/")
    features = _probe(base, "/health/features", timeout=timeout)
    if features.get("ok"):
        return {
            "ok": True,
            "contract_routes": True,
            "probe": features,
        }
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                f"{base}/v1/contract/submit",
                json={},
                headers={"Content-Type": "application/json"},
            )
            alive = r.status_code in {400, 401, 403, 422}
            return {
                "ok": alive,
                "contract_routes": alive,
                "status_code": r.status_code,
                "url": f"{base}/v1/contract/submit",
                "hint": (
                    None
                    if alive
                    else (
                        "Cortex /health is up but /v1/contract/submit returned "
                        f"{r.status_code}. Restart Cortex from current tree "
                        "(stale process often still serves legacy /dms/* only)."
                    )
                ),
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "contract_routes": False,
            "error": str(exc)[:200],
            "url": f"{base}/v1/contract/submit",
        }


def _probe_openvault_trust(base_url: str, timeout: float = 1.5) -> dict[str, Any]:
    """JWKS reachable + non-empty keys preferred; empty JWKS means no active intermediates."""
    base = base_url.rstrip("/")
    url = f"{base}/keys/jwks"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url)
            if r.status_code == 404:
                return {
                    "ok": False,
                    "jwks_ok": False,
                    "key_count": 0,
                    "url": url,
                    "hint": "OpenVault /keys/jwks missing — wrong OPENVAULT_URL or old OV build.",
                }
            if r.status_code >= 500:
                return {"ok": False, "jwks_ok": False, "status_code": r.status_code, "url": url}
            _is_json = r.headers.get("content-type", "").startswith("application/json")
            body = r.json() if _is_json else {}
            keys = body.get("keys") if isinstance(body, dict) else None
            count = len(keys) if isinstance(keys, list) else 0
            return {
                "ok": True,
                "jwks_ok": True,
                "key_count": count,
                "status_code": r.status_code,
                "url": url,
                "hint": (
                    None
                    if count > 0
                    else (
                        "JWKS is empty — DMS has not minted an intermediate yet, "
                        "or OPENVAULT_HOME differs across processes. "
                        "Pin OPENVAULT_HOME=D:\\OpenVault\\.openvault then restart OV + DMS."
                    )
                ),
            }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "jwks_ok": False, "error": str(exc)[:200], "url": url}


def _probe_cortex_jwks_refresh(base_url: str, timeout: float = 2.0) -> dict[str, Any]:
    """Cold-path refresh endpoint — required after DMS mints a new int- kid."""
    base = base_url.rstrip("/")
    url = f"{base}/v1/contract/jwks/refresh"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url)
            if r.status_code == 404:
                return {
                    "ok": False,
                    "refresh_ok": False,
                    "status_code": 404,
                    "url": url,
                    "hint": (
                        "Cortex missing POST /v1/contract/jwks/refresh — restart from "
                        "current D:\\Cortex tree so newly minted intermediates verify."
                    ),
                }
            if r.status_code >= 500:
                return {
                    "ok": False,
                    "refresh_ok": False,
                    "status_code": r.status_code,
                    "url": url,
                }
            body = r.json() if r.content else {}
            kids = body.get("kids") if isinstance(body, dict) else None
            return {
                "ok": True,
                "refresh_ok": bool(body.get("ok", True)) if isinstance(body, dict) else True,
                "key_count": len(kids) if isinstance(kids, list) else None,
                "status_code": r.status_code,
                "url": url,
            }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "refresh_ok": False, "error": str(exc)[:200], "url": url}


def _openvault_root_hint() -> str | None:
    for candidate in (
        os.environ.get("OPENVAULT_ROOT"),
        r"D:\OpenVault",
        r"D:\Open Vault",
    ):
        if candidate and Path(candidate).is_dir():
            return str(Path(candidate))
    return None


@router.get("/health")
def health(settings: SettingsDep, binding: StoreBindingDep) -> dict[str, Any]:
    cortex_liveness = _probe(settings.cortex_url, "/health")
    cortex_contract = _probe_cortex_contract(settings.cortex_url)
    cortex_jwks = _probe_cortex_jwks_refresh(settings.cortex_url)
    cortex = {
        **cortex_liveness,
        "ok": bool(
            cortex_liveness.get("ok")
            and cortex_contract.get("ok")
            and cortex_jwks.get("ok")
        ),
        "contract_routes": cortex_contract.get("contract_routes"),
        "contract_probe": cortex_contract,
        "jwks_refresh": cortex_jwks,
    }
    if cortex_liveness.get("ok") and not cortex_contract.get("ok"):
        cortex["error"] = cortex_contract.get("hint") or cortex_contract.get("error")
    elif cortex_contract.get("ok") and not cortex_jwks.get("ok"):
        cortex["error"] = cortex_jwks.get("hint") or cortex_jwks.get("error")

    ov = _probe(settings.openvault_url, "/api/healthz")
    if not ov.get("ok"):
        for alt in ("/health", "/keys/jwks", "/api/health"):
            cand = _probe(settings.openvault_url, alt)
            if cand.get("ok"):
                ov = cand
                break
    trust = _probe_openvault_trust(settings.openvault_url)
    ov_root = _openvault_root_hint()
    host = urlparse(settings.openvault_url).hostname or "127.0.0.1"
    return {
        "status": "ok",
        "product": "dms",
        "version": "0.1.0",
        "contract": settings.cortex_contract_version,
        "ask_mode": settings.dms_ask_mode,
        "demo_fallback": settings.dms_demo_fallback,
        # The control plane is "configured" when it is actually serving, not when
        # a URL is present. A DATABASE_URL pointing at a Postgres that is down
        # used to report true here while every Postgres-backed page fell back to
        # process memory — a flag that could not report the failure it exists to
        # report. `database` carries the distinction for anyone who needs it.
        "database_configured": binding.persistent,
        "database": {
            **binding.as_dict(),
            "url_set": bool(settings.database_url),
            "hint": binding.hint,
        },
        "dependencies": {
            "cortex": cortex,
            "openvault": {
                **ov,
                "trust": trust,
                "root_hint": ov_root,
                "start_hint": (
                    None
                    if ov.get("ok")
                    else (
                        f"OpenVault offline at {settings.openvault_url}. "
                        # _OPENVAULT_DEFAULT_ROOT is a module constant, not an inline
                        # literal: a backslash inside f-string braces is PEP 701, which
                        # landed in 3.12. This package targets 3.11, where it is a
                        # SyntaxError - so the whole route module failed to import on CI.
                        f"From {ov_root or _OPENVAULT_DEFAULT_ROOT} run "
                        "scripts\\\\windows\\\\Start-OpenVaultDemo.ps1 "
                        f"(API {host}:5000, UI :3010). "
                        "Demo continues without signed manifests."
                    )
                ),
            },
        },
    }
