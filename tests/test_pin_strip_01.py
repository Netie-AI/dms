"""PIN-STRIP-01. Padded body served ids are not the pin.

Padding sits on the response body. Served headers are the exact pin.
A header-only pad is not a wire case and is not tested here.

On f0e6c61f, ``_body_field`` (strict_pin.py:178) strips body ``served_model``
before ``interpret`` compares it to the exact header, so preflight accepts
that body as a match. Body ``served_provider`` is not stripped.
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
from cortex_client.strict_pin import interpret, next_answer  # noqa: E402

_PIN = harness._PIN
_PROVIDER = harness._PROVIDER
# Body field, padded value. Headers in the shot stay the exact pin.
_PADDED = (
    ("provider", " groq"),
    ("provider", "groq "),
    ("model", "openai/gpt-oss-120b "),
    ("model", " openai/gpt-oss-120b"),
)
_EXACT = (
    ("groq", "openai/gpt-oss-120b"),
    ("openrouter", "google/gemma-4-31b-it:free"),
)


def _exact_headers(provider: str = _PROVIDER, model: str = _PIN) -> dict[str, str]:
    return harness._served_headers(provider, model)


def _shot(provider: str, model: str, *, header_provider: str, header_model: str) -> dict[str, Any]:
    return {
        "status": 200,
        "headers": _exact_headers(header_provider, header_model),
        "body": {
            "served_provider": provider,
            "served_model": model,
            "choices": [{"message": {"content": "ok"}}],
        },
    }


def _padded_body(which: str, padded: str) -> dict[str, Any]:
    """Pad one body field. Headers stay the exact groq pin."""
    provider = padded if which == "provider" else _PROVIDER
    model = padded if which == "model" else _PIN
    return _shot(provider, model, header_provider=_PROVIDER, header_model=_PIN)


def _pin_env(monkeypatch: pytest.MonkeyPatch, provider: str, model: str) -> None:
    monkeypatch.setenv("OPENVAULT_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("DMS_STRICT_PROVIDER", provider)
    monkeypatch.setenv("DMS_STRICT_MODEL", model)
    monkeypatch.delenv("OPENVAULT_API_KEY", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)


def _assert_body_pad(incoming: dict[str, Any], which: str, padded: str) -> None:
    headers = incoming["headers"]
    body = incoming["body"]
    assert headers["X-OpenVault-Served-Provider"] == _PROVIDER
    assert headers["X-OpenVault-Served-Model"] == _PIN
    assert padded != _PROVIDER and padded != _PIN
    if which == "provider":
        assert body["served_provider"] == padded
        assert body["served_model"] == _PIN
    else:
        assert body["served_provider"] == _PROVIDER
        assert body["served_model"] == padded


@pytest.mark.parametrize(("which", "padded"), _PADDED)
def test_interpret_padded_body_is_pin_mismatch(
    which: str, padded: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight interpret() rejects a padded body next to exact headers."""
    _pin_env(monkeypatch, _PROVIDER, _PIN)
    incoming = _padded_body(which, padded)
    _assert_body_pad(incoming, which, padded)
    got = interpret(incoming["status"], incoming["body"], incoming["headers"])
    assert got.header_provider == _PROVIDER
    assert got.header_model == _PIN
    assert got.kind == "mismatch", (
        f"preflight interpret accepted padded body {which}={padded!r} "
        "via strict_pin.py:178"
    )
    assert str(got.name).startswith("pin_mismatch")
    if which == "provider":
        assert got.body_provider == padded
    else:
        assert got.body_model == padded


@pytest.mark.parametrize(("which", "padded"), _PADDED)
def test_next_answer_padded_body_is_pin_mismatch(
    which: str, padded: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """next_answer() rejects the same padded body. Headers stay exact."""
    _pin_env(monkeypatch, _PROVIDER, _PIN)
    incoming = _padded_body(which, padded)
    _assert_body_pad(incoming, which, padded)
    script = harness._patch_pin(monkeypatch, repeat=incoming)
    got = next_answer()
    assert len(script.calls) == 1
    assert got.header_provider == _PROVIDER
    assert got.header_model == _PIN
    assert got.kind == "mismatch", (
        f"next_answer accepted padded body {which}={padded!r} "
        "via strict_pin.py:178"
    )
    assert str(got.name).startswith("pin_mismatch")
    if which == "provider":
        assert got.body_provider == padded
    else:
        assert got.body_model == padded


@pytest.mark.parametrize(("which", "padded"), _PADDED)
def test_preflight_padded_body_stops_at_n0(
    which: str, padded: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A padded body on the preflight call stops the round. n=0. No case call."""
    incoming = _padded_body(which, padded)
    _assert_body_pad(incoming, which, padded)
    db = harness._oracle_db(tmp_path)
    asks, script = harness._arm(monkeypatch, tmp_path, repeat=incoming)
    harness.live("http://score.test", 1.0, db)
    report = harness._report(tmp_path)
    assert report["n"] == 0, (
        f"preflight accepted padded body {which}={padded!r} "
        f"n={report.get('n')} asks={len(asks)} vault_calls={len(script.calls)} "
        "via strict_pin.py:178"
    )
    assert asks == []
    assert len(script.calls) == 1
    assert report["cases"] == []
    assert str(report.get("reason") or "").startswith("pin_mismatch")
    assert int(report.get("abstained") or 0) == 0


def test_exact_body_and_headers_full_id_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exact body and exact headers match, including the full OpenRouter id."""
    for provider, model in _EXACT:
        _pin_env(monkeypatch, provider, model)
        incoming = _shot(
            provider, model, header_provider=provider, header_model=model
        )
        assert incoming["body"]["served_provider"] == provider
        assert incoming["body"]["served_model"] == model
        assert incoming["headers"]["X-OpenVault-Served-Provider"] == provider
        assert incoming["headers"]["X-OpenVault-Served-Model"] == model
        got = interpret(incoming["status"], incoming["body"], incoming["headers"])
        assert got.kind == "ok"
        assert got.served_provider == provider
        assert got.served_model == model
        assert got.body_model == model
        assert got.header_model == model
        script = harness._patch_pin(monkeypatch, repeat=incoming)
        answered = next_answer()
        assert len(script.calls) == 1
        assert answered.kind == "ok"
        assert answered.served_provider == provider
        assert answered.served_model == model
        assert answered.body_model == model
        assert answered.header_model == model
