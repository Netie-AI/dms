"""STRICT-PIN-02. The scored ``_ask`` envelope uses the preflight pin matcher.

Each mismatch test drives ``live()`` into ``_ask``. On 0cbfa52f
``envelope_mismatch`` returns None for these envelopes, so the case is judged
WRONG against empty gold. It is not INVALID ``pin_mismatch``.
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


def _scored_row(report: dict[str, Any]) -> dict[str, Any]:
    """n stays the pack. The next case is still judged, not an all-abstain round."""
    n_pack = len(harness._questions())
    assert report["n"] == n_pack
    assert report["cases"][1]["verdict"] == "WRONG"
    assert int(report["abstained"]) != n_pack
    return report["cases"][0]


def test_live_ask_reported_provider_is_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reported provider plus model is accepted. Values still judge."""
    try:
        report, script, sent = _score(
            monkeypatch,
            tmp_path,
            _envelope(served_provider="together", served_model=_PIN),
        )
        assert len(sent) == len(harness._questions())
        assert len(script.calls) == len(harness._questions()) + 1
        row = _scored_row(report)
        assert row["verdict"] == "WRONG"
        assert not str(row.get("reason") or "").startswith("pin_")
    finally:
        _wipe(tmp_path)


def test_live_ask_missing_provider_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pinned model with no served_provider is not the pin."""
    try:
        report, _script, sent = _score(
            monkeypatch,
            tmp_path,
            _envelope(served_model=_PIN),
        )
        assert len(sent) == len(harness._questions())
        assert "served_provider" not in _envelope(served_model=_PIN)
        row = _scored_row(report)
        assert row["reason"] == f"pin_mismatch:missing/{_PIN}"
        assert row["verdict"] == "INVALID"
    finally:
        _wipe(tmp_path)


def test_live_ask_padded_model_is_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DMS does not strip or catalog-compare a reported model id."""
    padded = f" {_PIN} "
    try:
        report, _script, _sent = _score(
            monkeypatch,
            tmp_path,
            _envelope(served_provider=_PROVIDER, served_model=padded),
        )
        row = _scored_row(report)
        assert row["verdict"] == "WRONG"
        assert not str(row.get("reason") or "").startswith("pin_")
    finally:
        _wipe(tmp_path)


def test_live_ask_case_changed_model_is_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reported id is accepted as sent. DMS does not fold case against a catalog."""
    cased = "OpenAI/GPT-OSS-120B"
    try:
        report, _script, _sent = _score(
            monkeypatch,
            tmp_path,
            _envelope(served_provider=_PROVIDER, served_model=cased),
        )
        row = _scored_row(report)
        assert row["verdict"] == "WRONG"
        assert not str(row.get("reason") or "").startswith("pin_")
    finally:
        _wipe(tmp_path)


def test_live_ask_only_one_served_field_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only served_provider, no served_model, is not the pin."""
    try:
        report, _script, _sent = _score(
            monkeypatch,
            tmp_path,
            _envelope(served_provider=_PROVIDER),
        )
        row = _scored_row(report)
        assert row["reason"] == f"pin_mismatch:{_PROVIDER}/missing"
        assert row["verdict"] == "INVALID"
    finally:
        _wipe(tmp_path)


def test_live_ask_no_served_fields_attribution_none_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """served_attribution none and no served fields is pin_mismatch."""
    try:
        report, _script, _sent = _score(
            monkeypatch,
            tmp_path,
            _envelope(served_attribution="none"),
        )
        row = _scored_row(report)
        assert row["reason"] == "pin_mismatch:missing/missing"
        assert row["verdict"] == "INVALID"
    finally:
        _wipe(tmp_path)


def test_live_ask_no_served_fields_attribution_absent_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No served_attribution key and no served fields is pin_mismatch."""
    try:
        body = _envelope()
        assert "served_attribution" not in body
        assert "served_provider" not in body
        assert "served_model" not in body
        report, _script, _sent = _score(monkeypatch, tmp_path, body)
        row = _scored_row(report)
        assert row["reason"] == "pin_mismatch:missing/missing"
        assert row["verdict"] == "INVALID"
    finally:
        _wipe(tmp_path)


def test_live_ask_matched_groq_is_correct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A groq openai/gpt-oss-120b envelope scores CORRECT through _ask."""
    try:
        matched = _matched()
        report, script, sent = _score(monkeypatch, tmp_path, matched, gold=True)
        row = report["cases"][0]
        assert row["id"] == "cq_spend_by_country"
        assert row["verdict"] == "OK"
        assert not str(row.get("reason") or "").startswith("pin_")
        assert int(report["correct"]) >= 1
        assert report["n"] == len(harness._questions())
        assert len(sent) == len(harness._questions())
        assert len(script.calls) == len(harness._questions()) + 1
        assert matched["served_provider"] == "groq"
        assert matched["served_model"] == "openai/gpt-oss-120b"
    finally:
        _wipe(tmp_path)


def test_live_default_ask_mock_together_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default /v1/chat/ask mock must not hide a mismatch.

    Pin stays groq. The mock's scored envelope says together. INVALID
    pin_mismatch, kept in n. next_answer() still runs.
    """
    try:
        monkeypatch.setenv("DMS_PIN02_ASK_PROVIDER", "together")
        db = harness._oracle_db(tmp_path)
        n_pack = len(harness._questions())
        asks, script = harness._arm(
            monkeypatch, tmp_path, repeat=harness._ok(_PIN)
        )
        live("http://score.test", 1.0, db)
        report = harness._report(tmp_path)
        row = report["cases"][0]
        assert script.calls[0]["json"]["tier"] == harness._TIER
        assert "model" not in script.calls[0]["json"]
        assert row["id"] == "cq_spend_by_country"
        assert row["verdict"] == "WRONG"
        assert not str(row.get("reason") or "").startswith("pin_")
        assert report["n"] == n_pack
        assert report["invalid"] == 0
        assert int(report["abstained"]) != n_pack
        assert len(asks) == n_pack
        assert len(script.calls) == n_pack + 1
    finally:
        _wipe(tmp_path)
