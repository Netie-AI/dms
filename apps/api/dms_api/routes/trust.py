"""Trust. The evidence behind the badges.

When the engine eval route answers, that payload is forwarded as received.
When it does not, counts are copied from grade52 and pack score files on disk.
No file is an empty state. A raw JSON error body is never the hint.
A file is not a claim verdict: ``supported`` stays false unless the engine said so.
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

#: Files grade52 and the pack scorers already write. Nothing else is a run.
_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("grade52.json", "corpus"),
    ("score_hostile.json", "score_hostile"),
    ("score_curated.json", "score_curated"),
    ("score_bird.json", "score_bird"),
    ("score_climb.json", "score_climb"),
    ("score_gen_path_prove.json", "score_gen_path_prove"),
)
_NAME_FILE = {run_id: filename for filename, run_id in _ARTIFACTS}
_NAME_FILE["grade52"] = "grade52.json"
_ITEM_KEYS = ("id", "bucket", "outcome", "route", "badge", "reason", "abstain_code")
_PROVES = "Counts copied from this file. This page does not score."
EMPTY_TITLE = "No benchmark run yet"
HOW_TO_RUN = (
    "Run python scripts/verify/grade52.py --main "
    "or python scripts/score_answers.py. "
    "DMS reads grade52.json and score_*.json from DMS_SCORE_DIR "
    "(default .tmp) and from the grade52 folder in the temp directory. "
    "This page does not score a run itself."
)


def _score_dir() -> Path:
    raw = os.environ.get("DMS_SCORE_DIR")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[4] / ".tmp"


def _artifact_dirs() -> list[Path]:
    """Score dir, then the grade52 temp dir. ``DMS_GRADE52_DIR`` overrides the temp dir."""
    import tempfile

    dirs = [_score_dir()]
    override = os.environ.get("DMS_GRADE52_DIR")
    if override:
        dirs.append(Path(override))
    else:
        roots: list[str] = []
        for key in ("RUNNER_TEMP", "TMPDIR"):
            value = os.environ.get(key)
            if value:
                roots.append(value)
        roots.append(tempfile.gettempdir())
        for root in roots:
            dirs.append(Path(root) / "grade52")
    out: list[Path] = []
    seen: set[str] = set()
    for folder in dirs:
        key = str(folder)
        if key in seen:
            continue
        seen.add(key)
        out.append(folder)
    return out


def _find_artifact(filename: str) -> Path | None:
    for folder in _artifact_dirs():
        path = folder / filename
        if path.is_file():
            return path
    return None


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        return data
    return None


def _count(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = data.get(key)
        if type(value) is int:
            return value
    return None


def _rate(num: int | None, den: int | None) -> float | None:
    if num is None or den is None or den <= 0:
        return None
    return num / den


def _run_from_file(path: Path, run_id: str) -> dict[str, Any] | None:
    """One run, using only ints and the passed bit the file itself stores."""
    data = _load_json(path)
    if data is None:
        return None
    wrong = _count(data, "wrong")
    correct = _count(data, "correct")
    total = _count(data, "n", "total")
    abstain = _count(data, "abstain", "abstained")
    if wrong is None and correct is None and total is None and abstain is None:
        return None
    totals: dict[str, int] = {}
    if total is not None:
        totals["total"] = total
    if correct is not None:
        totals["correct"] = correct
    if wrong is not None:
        totals["wrong"] = wrong
    if abstain is not None:
        totals["abstain"] = abstain
    rates: dict[str, float] = {}
    correct_rate = _rate(correct, total)
    abstain_rate = _rate(abstain, total)
    wrong_rate = _rate(wrong, total)
    if correct_rate is not None:
        rates["correct"] = correct_rate
    if abstain_rate is not None:
        rates["abstain"] = abstain_rate
    if wrong_rate is not None:
        rates["wrong"] = wrong_rate
    label = data.get("pack") if isinstance(data.get("pack"), str) else data.get("run")
    if not isinstance(label, str) or not label.strip():
        label = path.stem
    run: dict[str, Any] = {
        "id": run_id,
        "label": label,
        "proves": _PROVES,
        "present": True,
        "file": path.name,
        "totals": totals,
        "rates": rates,
        "passed": data.get("passed") if isinstance(data.get("passed"), bool) else None,
    }
    mode = data.get("mode")
    if isinstance(mode, str):
        run["mode"] = mode
    return run


def local_benchmark_runs() -> dict[str, dict[str, Any]]:
    """grade52 first, then pack score files. Absent files are omitted."""
    runs: dict[str, dict[str, Any]] = {}
    for filename, run_id in _ARTIFACTS:
        path = _find_artifact(filename)
        if path is None:
            continue
        run = _run_from_file(path, run_id)
        if run is not None:
            runs[run_id] = run
    return runs


def _claim_from_runs(runs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The tile numbers. ``supported`` stays false: a file is not a claim verdict."""
    claim: dict[str, Any] = {
        "statement": "0 confidently wrong",
        "supported": False,
        "blockers": [],
    }
    source = runs.get("corpus") or next(iter(runs.values()), None)
    if source is None:
        return claim
    totals = source.get("totals") or {}
    if "wrong" in totals:
        claim["confidently_wrong"] = totals["wrong"]
    if "total" in totals:
        claim["corpus_n"] = totals["total"]
    return claim


def _public_hint(hint: object) -> str:
    text = hint.strip() if isinstance(hint, str) else ""
    if not text or text[:1] in "{[":
        return "The engine has no evidence payload for this run."
    return text


def _case_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Case rows with no question text and no SQL. Pack wording stays in the file."""
    raw = data.get("cases")
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        item: dict[str, Any] = {}
        for key in _ITEM_KEYS:
            value = row.get(key)
            if isinstance(value, str) or type(value) is int:
                item[key] = value
        if "outcome" not in item and isinstance(row.get("bucket"), str):
            item["outcome"] = row["bucket"]
        if item:
            items.append(item)
        if len(items) >= 100:
            break
    return items


def _local_run_detail(name: str) -> dict[str, Any] | None:
    filename = _NAME_FILE.get(name)
    if filename is None:
        return None
    path = _find_artifact(filename)
    if path is None:
        return None
    data = _load_json(path)
    return {
        "ok": True,
        "id": name,
        "items": [] if data is None else _case_items(data),
    }


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


def gen_path_prove_scores(directory: Path | None = None) -> list[dict[str, Any]]:
    """Read-only pickup of GEN-PATH-PROVE-01 artifacts. Does not invent counts."""
    folder = directory or _score_dir()
    path = folder / "score_gen_path_prove.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and data.get("kind") == "dms.gen_path_prove":
        return [data]
    return []


def _engine_failure(result: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """No eval payload. Disk runs fill the page. No file is an empty state, not raw JSON."""
    if result.get("error") == "cortex_key_missing":
        return {
            "ok": False,
            "error": result.get("error"),
            "hint": _public_hint(result.get("hint")),
            "claim": {
                "statement": "0 confidently wrong",
                "supported": False,
                "blockers": ["evidence unavailable. Cortex did not answer"],
            },
            **extra,
        }
    runs = local_benchmark_runs()
    if runs:
        return {
            "ok": True,
            "empty": False,
            "source": "artifacts",
            "claim": _claim_from_runs(runs),
            "runs": runs,
            "thresholds": {},
            **extra,
        }
    return {
        "ok": True,
        "empty": True,
        "empty_title": EMPTY_TITLE,
        "how_to_run": HOW_TO_RUN,
        "source": "none",
        "claim": {
            "statement": "0 confidently wrong",
            "supported": False,
            "blockers": [],
        },
        "runs": {},
        "thresholds": {},
        **extra,
    }


@router.get("/summary")
def trust_summary(settings: SettingsDep) -> dict[str, Any]:
    result = cortex_get(
        settings.cortex_url,
        "/dms/eval/summary",
        api_key=settings.cortex_api_key,
        timeout=6.0,
    )
    extra = {
        "ask_path": ask_path_scores(),
        "gen_path_prove": gen_path_prove_scores(),
    }
    if not result["ok"]:
        return _engine_failure(result, extra)
    return {"ok": True, **(result["data"] or {}), **extra}


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
    if result["ok"]:
        return {"ok": True, **(result["data"] or {})}
    if result.get("error") != "cortex_key_missing":
        local = _local_run_detail(name)
        if local is not None:
            return local
    return {
        "ok": False,
        "id": name,
        "items": [],
        "error": result.get("error"),
        "hint": _public_hint(result.get("hint")),
    }
