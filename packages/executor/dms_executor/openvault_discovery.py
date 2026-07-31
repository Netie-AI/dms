"""Probe local OpenVault and surface a start hint when offline."""

from __future__ import annotations

import os
from pathlib import Path

import httpx

_DEFAULT_LOCAL_URLS = (
    "http://127.0.0.1:5000",
    "http://localhost:5000",
)
_HEALTH_PATHS = ("/api/healthz", "/health")
_DEFAULT_ROOT = Path(r"D:\OpenVault")


def openvault_root() -> Path:
    return Path(os.environ.get("OPENVAULT_ROOT", str(_DEFAULT_ROOT)))


def local_start_command(*, root: Path | None = None) -> str:
    """Minimal API-only start (keys + health on :5000)."""
    ov_root = root or openvault_root()
    openmw = ov_root / "OpenMW"
    if (ov_root / "scripts" / "windows" / "Start-LocalMesh.ps1").is_file():
        ps1 = ov_root / "scripts" / "windows" / "Start-LocalMesh.ps1"
        return (
            f'powershell -ExecutionPolicy Bypass -File "{ps1}" '
            f"-Root {ov_root} -SkipBrowser -MockHealth"
        )
    return (
        f'cd "{openmw}" && uv run openmw console '
        f"--host 127.0.0.1 --port 5000 --no-open-browser --mock-health"
    )


def _candidate_urls(preferred: str | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in (preferred, os.environ.get("OPENVAULT_URL"), *_DEFAULT_LOCAL_URLS):
        if not raw:
            continue
        url = raw.strip().rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def probe_openvault(
    *,
    preferred_url: str | None = None,
    timeout: float = 2.0,
) -> tuple[str | None, str]:
    """Return (reachable_base_url_or_none, start_command_hint)."""
    hint = local_start_command()
    http = httpx.Client(timeout=timeout)
    try:
        for base in _candidate_urls(preferred_url):
            for path in _HEALTH_PATHS:
                try:
                    resp = http.get(f"{base}{path}")
                    if 200 <= resp.status_code < 400:
                        return base, hint
                except httpx.HTTPError:
                    continue
    finally:
        http.close()
    return None, hint
