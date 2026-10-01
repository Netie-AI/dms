"""MASK-VALUES-01 clock keep / dms#303. Health-matched clocks stay literal.

Synthetic dates only. HTTP is mocked. No network, no keys, no live model.
(a) (b) (c) fail on 87a94978 on their own assertion. Record files are deleted.
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
REAL = "2024-06-15"
NEXT = "2024-06-16"
_TZ = "UTC"


def _pack_n() -> int:
    pack = load_pack(score_curated.DEFAULT_PACK)
    return len(merge_pack_questions(list(pack["questions"])))


def _oracle_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR)")
        con.execute("INSERT INTO meta VALUES ('schema_version', 'mask-clock-01')")
    finally:
        con.close()
    return path


def _health(day: str, *, after: str | None = None) -> dict[str, Any]:
    end = day if after is None else after
    return {
        "status": "ok",
        "product": "dms",
        "ask_mode": "live",
        "demo_fallback": False,
        "engine_as_of": day,
        "engine_as_of_after": end,
        "engine_timezone": _TZ,
        "engine_timezone_after": _TZ,
    }


def _clock_env(before: str, after: str) -> dict[str, Any]:
    return {
        "engine_as_of": before,
        "engine_as_of_after": after,
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


def _install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    answer: dict[str, Any],
    *,
    health_bodies: list[dict[str, Any] | None],
    end_error: bool = False,
) -> None:
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(tmp_path / "records"))
    gets = {"n": 0}

    def score_http(
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 0,
    ) -> _Resp:
        del json_body, timeout
        if method == "GET" and url.rstrip("/").endswith("/health"):
            gets["n"] += 1
            if end_error and gets["n"] >= 2:
                raise OSError("end health unread")
            body = health_bodies[gets["n"] - 1] if gets["n"] - 1 < len(health_bodies) else None
            if body is None:
                return _Resp({"status": "ok", "product": "dms"})
            return _Resp(body)
        if method == "POST" and url.rstrip("/").endswith("/v1/chat/ask"):
            return _Resp(answer)
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


def _abstain(**extra: Any) -> dict[str, Any]:
    env: dict[str, Any] = {
        "badge": "ABSTAIN",
        "abstained": True,
        "text": "no",
        "rows": [],
        "values": [],
    }
    env.update(extra)
    return env


def test_live_bare_dob_clock_is_masked_and_ineligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Top-level engine_as_of DOB matches no /health read.

    Fails on 87a94978 at the tuple assert:
    (52, True, False, {'engine_date_mismatch'})
    != (52, False, True, {'engine_clock_masked'}).
    1990-01-15 stays in the record. n is 52.
    """
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install(
        monkeypatch,
        tmp_path,
        _abstain(engine_as_of=DOB),
        health_bodies=[_health(REAL), _health(REAL)],
    )
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


def test_live_nested_engine_as_of_in_values_is_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """engine_as_of nested inside values is scanned. A DOB there is masked.

    Fails on 87a94978 at the tuple assert:
    (52, True, False) != (52, False, True).
    1990-01-15 stays in values. n is 52.
    """
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install(
        monkeypatch,
        tmp_path,
        _abstain(values=[{"engine_as_of": DOB}]),
        health_bodies=[_health(REAL), _health(REAL)],
    )
    try:
        live("http://score.test", 1.0, db)
        report = _report(tmp_path)
        line = json.loads(_record_text(report).splitlines()[0])
        blob = json.dumps(line["envelope"].get("values"))
        assert (report["n"], DOB in blob, "DMSMASK_dob_" in blob) == (
            _pack_n(),
            False,
            True,
        )
    finally:
        _unlink_records(tmp_path)


def test_live_unreadable_health_masks_real_clock_and_records_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End /health unread. A real engine_as_of is masked. Both reasons. n stays 52.

    Fails on 87a94978 at the tuple assert:
    (52, True, False, True, 52) != (52, False, True, True, 52).
    2024-06-15 stays in the record. n is 52.
    """
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install(
        monkeypatch,
        tmp_path,
        _abstain(engine_as_of=REAL),
        health_bodies=[_health(REAL)],
        end_error=True,
    )
    try:
        live("http://score.test", 1.0, db)
        report = _report(tmp_path)
        file_text = _record_text(report)
        reasons = list(report.get("baseline_ineligible_reasons") or [])
        assert (
            report["n"],
            REAL in file_text,
            "engine_clock_masked" in reasons,
            "round_end_unread" in reasons,
            report["invalid"],
        ) == (_pack_n(), False, True, True, _pack_n())
    finally:
        _unlink_records(tmp_path)


def test_live_matched_clock_stays_literal_and_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clock that matches both /health reads stays literal. The round stays eligible.

    Positive check. Passes on this head. A mask-everything masker fails it.
    """
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install(
        monkeypatch,
        tmp_path,
        _abstain(**_clock_env(REAL, REAL)),
        health_bodies=[_health(REAL), _health(REAL)],
    )
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
            report.get("baseline_eligible"),
            "engine_clock_masked" in reasons,
        ) == (_pack_n(), REAL, REAL, _TZ, _TZ, True, False)
    finally:
        _unlink_records(tmp_path)


def test_live_matched_next_day_is_not_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """engine_as_of_after matching the next-day end read stays literal.

    Outcome is round_spans_midnight, not engine_clock_masked. Positive check.
    """
    db = _oracle_db(tmp_path / "oracle.duckdb")
    _install(
        monkeypatch,
        tmp_path,
        _abstain(**_clock_env(REAL, NEXT)),
        health_bodies=[_health(REAL), _health(NEXT)],
    )
    try:
        live("http://score.test", 1.0, db)
        report = _report(tmp_path)
        line = json.loads(_record_text(report).splitlines()[0])
        env = line["envelope"]
        reasons = list(report.get("baseline_ineligible_reasons") or [])
        case_reasons = {row.get("reason") for row in report["cases"]}
        assert (
            report["n"],
            env.get("engine_as_of"),
            env.get("engine_as_of_after"),
            case_reasons,
            "engine_clock_masked" in reasons,
        ) == (_pack_n(), REAL, NEXT, {"round_spans_midnight"}, False)
    finally:
        _unlink_records(tmp_path)
