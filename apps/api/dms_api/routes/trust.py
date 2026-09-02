"""Trust — the evidence behind the badges, read from Cortex's benchmark artifacts.

Invariant 12 says a green badge on a wrong number is a P0. This is the surface
that lets a customer *check* that claim rather than take it: corpus size, wrong
count, abstain rate, the live persona probes, and whether the claim is currently
supported at all.

DMS adds nothing to the numbers. It forwards Cortex's ``claim`` verdict verbatim,
including the blockers — a product that softened "N=47, target 310" into a green
tick would be lying with a different font.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from dms_api.cortex_read import cortex_get
from dms_api.deps import SettingsDep

router = APIRouter(prefix="/v1/trust", tags=["trust"])


def _score_dir() -> Path:
    raw = os.environ.get("DMS_SCORE_DIR")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[4] / ".tmp"


def ask_path_scores(directory: Path | None = None) -> list[dict[str, Any]]:
    """DMS live score_answers / score_curated artifacts. Not the Cortex corpus."""
    folder = directory or _score_dir()
    out: list[dict[str, Any]] = []
    for name in ("score_hostile.json", "score_curated.json"):
        path = folder / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and "wrong" in data:
            out.append(data)
    return out


@router.get("/summary")
def trust_summary(settings: SettingsDep) -> dict[str, Any]:
    result = cortex_get(
        settings.cortex_url,
        "/dms/eval/summary",
        api_key=settings.cortex_api_key,
        timeout=6.0,
    )
    if not result["ok"]:
        return {
            "ok": False,
            "error": result.get("error"),
            "hint": result.get("hint"),
            "claim": {
                "statement": "0 confidently wrong",
                "supported": False,
                "blockers": ["evidence unavailable — Cortex did not answer"],
            },
            "ask_path": ask_path_scores(),
        }
    return {"ok": True, **(result["data"] or {}), "ask_path": ask_path_scores()}


@router.get("/runs/{name}")
def trust_run(
    name: str,
    settings: SettingsDep,
    outcome: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if outcome:
        params["outcome"] = outcome
    result = cortex_get(
        settings.cortex_url,
        f"/dms/eval/runs/{name}",
        api_key=settings.cortex_api_key,
        params=params,
        timeout=6.0,
    )
    if not result["ok"]:
        return {
            "ok": False,
            "id": name,
            "items": [],
            "error": result.get("error"),
            "hint": result.get("hint"),
        }
    return {"ok": True, **(result["data"] or {})}
