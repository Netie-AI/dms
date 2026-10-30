"""MASK-VALUES-01 ruling 3. A case clock must equal its connection read.

Synthetic dates only. HTTP is mocked. No network, no keys, no live model.
The planted-DOB test and the missing-read test fail on effabd24 on their
own assertion. Record files are deleted.
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
CONN = "2024-06-15"
HEALTH = "2024-01-01"
_TZ = "UTC"


def _pack_n() -> int:
    pack = load_pack(score_curated.DEFAULT_PACK)
    return len(merge_pack_questions(list(pack["questions"])))


def _oracle_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR)")
        con.execute("INSERT INTO meta VALUES ('schema_version', 'mask-clock-02')")
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


def _plant(env: dict[str, Any], day: str) -> dict[str, Any]:
    env["engine_as_of"] = day
    env["engine_as_of_after"] = day
    env["engine_timezone"] = _TZ
    env["engine_timezone_after"] = _TZ
    return env


def _install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    read: tuple[str | None, str | None],
    plant: str | None,
) -> None:
    import dms_executor.demo_warehouse as warehouse
    from dms_executor import Executor

    monkeypatch.setattr(warehouse, "_read_con_clock", lambda _con: read)
    exe = Executor(warehouse_path=tmp_path / "conn.duckdb")
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(tmp_path / "records"))

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
            env = exe.answer_user_sql(
                "SELECT 1 AS n FROM inventory", session_id="ses_mask_clock"
            )
            if plant is not None:
                _plant(env, plant)
            return _Resp(env)
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


def test_live_planted_dob_case_clock_is_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four-field DOB with matching zones, not equal to the connection read.

    Fails on effabd24: _preserved_case_clock keeps the shape.
    AssertionError at index 1: True != False. n is 52.
    1990-01-15 stays in the record. baseline_ineligible_reasons is empty.
    """
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install(monkeypatch, tmp_path, read=(CONN, _TZ), plant=DOB)
    try:
        live("http://score.test", 1.0, db)
        report = _report(tmp_path)
        file_text = _record_text(report)
        reasons = list(report.get("baseline_ineligible_reasons") or [])
        case_reasons = {row.get("reason") for row in report["cases"]}
        assert (
            report["n"],
            DOB in file_text,
            "engine_clock_masked" in reasons,
            case_reasons,
        ) == (_pack_n(), False, True, {"engine_clock_masked"})
    finally:
        _unlink_records(tmp_path)


def test_live_missing_connection_read_masks_case_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connection read returned nothing. The planted clock is masked.

    Fails on effabd24: AssertionError at index 1, True != False. n is 52.
    2024-03-03 stays in the record. engine_clock_masked is absent.
    """
    planted = "2024-03-03"
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install(monkeypatch, tmp_path, read=(None, None), plant=planted)
    try:
        live("http://score.test", 1.0, db)
        report = _report(tmp_path)
        file_text = _record_text(report)
        reasons = list(report.get("baseline_ineligible_reasons") or [])
        case_reasons = {row.get("reason") for row in report["cases"]}
        assert (
            report["n"],
            planted in file_text,
            "engine_clock_masked" in reasons,
            case_reasons,
        ) == (_pack_n(), False, True, {"engine_clock_masked"})
    finally:
        _unlink_records(tmp_path)


def test_live_connection_read_match_stays_literal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A case clock equal to its own connection read stays literal.

    Positive check. The health date is a different day. A mask-everything
    masker fails this. That masker is not committed.
    """
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install(monkeypatch, tmp_path, read=(CONN, _TZ), plant=None)
    try:
        live("http://score.test", 1.0, db)
        report = _report(tmp_path)
        line = json.loads(_record_text(report).splitlines()[0])
        env = line["envelope"]
        reasons = list(report.get("baseline_ineligible_reasons") or [])
        assert (
            report["n"],
            env.get("engine_as_of"),
            env.get("engine_as_of_after"),
            env.get("engine_timezone"),
            env.get("engine_timezone_after"),
            "engine_clock_masked" in reasons,
        ) == (_pack_n(), CONN, CONN, _TZ, _TZ, False)
    finally:
        _unlink_records(tmp_path)
