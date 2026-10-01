"""MASK-VALUES-01 ruling 6. Empty log keeps a clock that equals /health.

Synthetic dates only. HTTP is mocked. No network, no keys, no live model.
The scorer's connection log stays empty. Record files are deleted.
Fails on c0ca540a on its own assert: wrong invalid count and reason, n=52.
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

START = "2024-01-01"
END = "2024-01-02"
_TZ = "UTC"


def _pack_n() -> int:
    pack = load_pack(score_curated.DEFAULT_PACK)
    return len(merge_pack_questions(list(pack["questions"])))


def _oracle_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR)")
        con.execute("INSERT INTO meta VALUES ('schema_version', 'mask-clock-04')")
    finally:
        con.close()
    return path


def _health(day: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "product": "dms",
        "ask_mode": "live",
        "demo_fallback": False,
        "engine_as_of": day,
        "engine_as_of_after": day,
        "engine_timezone": _TZ,
        "engine_timezone_after": _TZ,
    }


def _clock() -> dict[str, Any]:
    """Case clock equal to the open /health read. Not copied into the log."""
    return {
        "badge": "ABSTAIN",
        "abstained": True,
        "rows": [],
        "values": [],
        "text": "abstain",
        "engine_as_of": START,
        "engine_as_of_after": START,
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


def _install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(tmp_path / "records"))
    health_days = [START, END]
    cursor = {"h": 0}

    def score_http(
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 0,
    ) -> _Resp:
        del json_body, timeout
        if method == "GET" and url.rstrip("/").endswith("/health"):
            idx = cursor["h"]
            cursor["h"] += 1
            day = health_days[idx] if idx < len(health_days) else health_days[-1]
            return _Resp(_health(day))
        if method == "POST" and url.rstrip("/").endswith("/v1/chat/ask"):
            return _Resp(_clock())
        raise RuntimeError(f"unexpected {method} {url}")

    monkeypatch.setattr("score_curated.score_http", score_http)


def _report(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "score_curated.json").read_text(encoding="utf-8"))


def _unlink_records(root: Path) -> None:
    if not root.exists():
        return
    for path in root.rglob("*.jsonl"):
        path.unlink()


def test_live_empty_log_health_start_scores_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty log. Clocks equal the open /health read. End read is the next day.

    Scores as before: n=52, invalid 0, reason None, clock stays literal.
    Fails on c0ca540a at this assert. n is 52. invalid is 52.
    reason is engine_clock_masked.
    """
    from dms_executor.demo_warehouse import clear_connection_log, clear_engine_clock

    db = _oracle_db(tmp_path / "oracle.duckdb")
    clear_connection_log()
    clear_engine_clock()
    _install(monkeypatch, tmp_path)
    try:
        live("http://score.test", 1.0, db)
        report = _report(tmp_path)
        line = json.loads(
            Path(str(report["case_record"])).read_text(encoding="utf-8").splitlines()[0]
        )
        env = line["envelope"]
        assert (
            report["n"],
            report["invalid"],
            report.get("reason"),
            env.get("engine_as_of"),
            env.get("engine_as_of_after"),
        ) == (_pack_n(), 0, None, START, START)
    finally:
        _unlink_records(tmp_path)
