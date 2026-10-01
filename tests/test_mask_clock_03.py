"""MASK-VALUES-01 ruling 4. No connection read is a missing read.

Synthetic dates only. HTTP is mocked. No network, no keys, no live model.
Neither case connects, so case_connection_read is None. Record files are deleted.
Both tests fail on 86b7771a on their own assertion.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import score_curated  # noqa: E402
from score_curated import live, load_pack, merge_pack_questions  # noqa: E402

DOB = "1990-01-15"
HEALTH = "2024-01-01"
_TZ = "UTC"
_OTHER_TZ = "Asia/Kuala_Lumpur"


def _pack_n() -> int:
    pack = load_pack(score_curated.DEFAULT_PACK)
    return len(merge_pack_questions(list(pack["questions"])))


def _oracle_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR)")
        con.execute("INSERT INTO meta VALUES ('schema_version', 'mask-clock-03')")
    finally:
        con.close()
    return path


def _health() -> dict[str, Any]:
    return {
        "status": "ok",
        "product": "dms",
        "ask_mode": "live",
        "demo_fallback": False,
        "engine_as_of": HEALTH,
        "engine_as_of_after": HEALTH,
        "engine_timezone": _TZ,
        "engine_timezone_after": _TZ,
    }


class _Resp:
    def __init__(self, body: dict[str, Any]) -> None:
        self.status_code = 200
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        return None


def _clock(tz_after: str) -> dict[str, Any]:
    return {
        "badge": "ABSTAIN",
        "abstained": True,
        "rows": [],
        "values": [],
        "text": "abstain",
        "engine_as_of": DOB,
        "engine_as_of_after": DOB,
        "engine_timezone": _TZ,
        "engine_timezone_after": tz_after,
    }


def _install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, tz_after: str) -> None:
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(tmp_path / "records"))
    body = _clock(tz_after)

    def score_http(
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 0,
    ) -> _Resp:
        del json_body, timeout
        if method == "GET" and url.rstrip("/").endswith("/health"):
            return _Resp(_health())
        if method == "POST" and url.rstrip("/").endswith("/v1/chat/ask"):
            return _Resp(dict(body))
        raise RuntimeError(f"unexpected {method} {url}")

    monkeypatch.setattr("score_curated.score_http", score_http)


def _report(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "score_curated.json").read_text(encoding="utf-8"))


def _record_text(report: dict[str, Any]) -> str:
    return Path(str(report["case_record"])).read_text(encoding="utf-8")


def _unlink_records(root: Path) -> None:
    if not root.exists():
        return
    for path in root.rglob("*.jsonl"):
        path.unlink()


def test_live_no_connection_read_masks_planted_dob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No connect. Four-field DOB with matching zones is a missing read.

    Fails on 86b7771a at index 1: True != False. n is 52.
    1990-01-15 stays in the record. baseline_ineligible_reasons is empty.
    """
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install(monkeypatch, tmp_path, tz_after=_TZ)
    try:
        live("http://score.test", 1.0, db)
        report = _report(tmp_path)
        reasons = list(report.get("baseline_ineligible_reasons") or [])
        case_reasons = {row.get("reason") for row in report["cases"]}
        assert (
            report["n"],
            DOB in _record_text(report),
            "engine_clock_masked" in reasons,
            case_reasons,
        ) == (_pack_n(), False, True, {"engine_clock_masked"})
    finally:
        _unlink_records(tmp_path)


def test_live_timezone_mismatch_dob_is_masked_reason_stays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Planted DOB with a mismatched zone. Mask the record. Keep the reason.

    Fails on 86b7771a: (52, True, {'engine_timezone_mismatch'}, False)
    != (52, False, {'engine_timezone_mismatch'}, True). n is 52.
    """
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install(monkeypatch, tmp_path, tz_after=_OTHER_TZ)
    try:
        live("http://score.test", 1.0, db)
        report = _report(tmp_path)
        reasons = list(report.get("baseline_ineligible_reasons") or [])
        case_reasons = {row.get("reason") for row in report["cases"]}
        assert (
            report["n"],
            DOB in _record_text(report),
            case_reasons,
            "engine_clock_masked" in reasons,
        ) == (_pack_n(), False, {"engine_timezone_mismatch"}, True)
    finally:
        _unlink_records(tmp_path)
