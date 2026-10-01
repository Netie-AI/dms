"""PIN-NOMODEL-01. The pin applies only when the answer called a model.

Each test drives ``live()`` into ``_ask``. On 7a8d6c11 ``envelope_mismatch``
ignores ``model_calls`` and ``lane``. No served fields is INVALID
``pin_mismatch:missing/missing``. A served pair that matches the pin is scored
as the pin.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import test_strict_pin_01 as harness  # noqa: E402
from score_curated import live  # noqa: E402

_PIN = harness._PIN
_PROVIDER = harness._PROVIDER


def _wipe(tmp_path: Path) -> None:
    for path in tmp_path.glob("score_*"):
        if path.is_file():
            path.unlink()


def _rows() -> list[dict[str, Any]]:
    return [dict(item) for item in harness._ANSWER_ROWS]


def _envelope(**fields: Any) -> dict[str, Any]:
    rows = _rows()
    body: dict[str, Any] = {
        "badge": "L0_CERTIFIED",
        "abstained": False,
        "rows": rows,
        "values": [dict(item) for item in rows],
        "text": "1",
        "engine_as_of": harness._ENGINE_DAY,
        "engine_as_of_after": harness._ENGINE_DAY,
        "engine_timezone": harness._TZ,
        "engine_timezone_after": harness._TZ,
    }
    body.update(fields)
    return body


def _matched() -> dict[str, Any]:
    return _envelope(
        served_provider=_PROVIDER,
        served_model=_PIN,
        served_attribution="reported",
    )


def _install_scored(
    monkeypatch: pytest.MonkeyPatch,
    first: dict[str, Any],
    rest: dict[str, Any],
) -> list[dict[str, Any]]:
    """Replace the chat ask body. Question 1 is ``first``. The rest match."""
    sent: list[dict[str, Any]] = []
    n = {"i": 0}

    def score_http(
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 0,
    ) -> Any:
        if method == "GET" and url.rstrip("/").endswith("/health"):
            return harness._Http(
                200,
                {
                    "status": "ok",
                    "engine_as_of": harness._ENGINE_DAY,
                    "engine_as_of_after": harness._ENGINE_DAY,
                    "engine_timezone": harness._TZ,
                    "engine_timezone_after": harness._TZ,
                },
                {},
            )
        if method == "POST" and url.rstrip("/").endswith("/v1/chat/ask"):
            sent.append(dict(json_body or {}))
            n["i"] += 1
            body = first if n["i"] == 1 else rest
            return harness._Http(200, body, {})
        raise RuntimeError(f"unexpected {method} {url}")

    monkeypatch.setattr("score_curated.score_http", score_http)
    return sent


def _score(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    first: dict[str, Any],
    *,
    gold: bool = False,
) -> tuple[dict[str, Any], Any, list[dict[str, Any]]]:
    db = harness._oracle_db(tmp_path)
    _asks, script = harness._arm(monkeypatch, tmp_path, repeat=harness._ok(_PIN))
    if gold:
        harness._match_gold(monkeypatch)
    sent = _install_scored(monkeypatch, first, _matched())
    live("http://score.test", 1.0, db)
    return harness._report(tmp_path), script, sent


def _pack_n(report: dict[str, Any]) -> int:
    n_pack = len(harness._questions())
    assert report["n"] == n_pack
    assert n_pack > 1
    assert int(report["abstained"]) != n_pack
    return n_pack


@pytest.mark.parametrize("lane", ["rules", "curated"])
def test_live_nomodel_lane_scores_correct(
    lane: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero calls, a no-model lane, no served fields: CORRECT, not the pin."""
    body = _envelope(model_calls=0, lane=lane)
    assert not any(str(key).startswith("served_") for key in body)
    try:
        report, script, sent = _score(monkeypatch, tmp_path, body, gold=True)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        assert len(script.calls) == n_pack + 1
        row = report["cases"][0]
        assert row["verdict"] == "OK", (
            f"no-model lane {lane!r} with zero calls and no served fields was "
            f"{row['verdict']} reason={row.get('reason')!r}"
        )
        assert not str(row.get("reason") or "").startswith("pin_")
        assert int(report["correct"]) >= 1
        assert row["rows"] == 1
        rec = harness._record_line(report, str(row["id"]))
        assert rec["outcome"] == "OK"
        assert rec["served_provider"] != _PROVIDER
        assert rec["served_model"] != _PIN
    finally:
        _wipe(tmp_path)


def test_live_zero_calls_with_served_pin_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero calls plus a served pair, even the pin itself, is pin_mismatch."""
    body = _envelope(
        model_calls=0,
        lane="rules",
        served_provider=_PROVIDER,
        served_model=_PIN,
    )
    try:
        report, script, sent = _score(monkeypatch, tmp_path, body, gold=True)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        assert len(script.calls) == n_pack + 1
        row = report["cases"][0]
        assert row["verdict"] == "INVALID", (
            f"zero calls carrying served ids was {row['verdict']} "
            f"reason={row.get('reason')!r}"
        )
        assert str(row["reason"]).startswith("pin_mismatch")
        assert int(report["invalid"]) >= 1
        assert report["n"] == n_pack
    finally:
        _wipe(tmp_path)


def test_live_model_lane_zero_calls_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model lane with zero calls is pin_mismatch and stays in n."""
    body = _envelope(model_calls=0, lane="generative")
    assert not any(str(key).startswith("served_") for key in body)
    try:
        report, _script, sent = _score(monkeypatch, tmp_path, body)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        assert report["cases"][1]["verdict"] == "WRONG"
        row = report["cases"][0]
        assert row["verdict"] == "INVALID"
        assert row["reason"] == "pin_mismatch:generative", row
        assert report["n"] == n_pack
        assert int(report["invalid"]) >= 1
    finally:
        _wipe(tmp_path)


def test_live_missing_lane_is_lane_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero calls and no lane is INVALID lane_unknown, kept in n."""
    body = _envelope(model_calls=0)
    assert "lane" not in body
    assert not any(str(key).startswith("served_") for key in body)
    try:
        report, _script, sent = _score(monkeypatch, tmp_path, body)
        n_pack = _pack_n(report)
        assert len(sent) == n_pack
        assert report["cases"][1]["verdict"] == "WRONG"
        row = report["cases"][0]
        assert row["verdict"] == "INVALID"
        assert row["reason"] == "lane_unknown", row
        assert report["n"] == n_pack
        assert int(report["invalid"]) >= 1
    finally:
        _wipe(tmp_path)
