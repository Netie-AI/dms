"""dms#317 Part A. Strict pin through live(), mocked OpenVault, no Groq call.

Pin-rule tests fail on 22deaa35 on their own assertion. The two positive
checks pass on the head and do not have to fail on the parent.
No existing test is edited here.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from score_curated import live  # noqa: E402

_ENGINE_DAY = "2024-06-15"
_TZ = "UTC"
_PIN = "openai/gpt-oss-120b"
_PROVIDER = "groq"
# One served row. Empty gold is WRONG. The same row as gold is CORRECT (OK).
_ANSWER_ROWS = [{"country": "MY", "spend": 1}]
_OTHER = "openai/gpt-oss-20b"
_VAULT_REASONS = (
    "parked",
    "quota_exhausted",
    "circuit_open",
    "no_hop",
    "not_in_catalog",
)
_CALLER_ERRORS = ("auto", "default", "", "gpt-oss-120b")


def _questions() -> list[dict[str, Any]]:
    from score_curated import DEFAULT_PACK, load_pack, merge_pack_questions

    pack = load_pack(DEFAULT_PACK)
    return merge_pack_questions(list(pack["questions"]))


def _oracle_db(tmp_path: Path) -> Path:
    db = tmp_path / "oracle.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR)")
        con.execute("INSERT INTO meta VALUES ('schema_version', 'strict-pin-01')")
    finally:
        con.close()
    return db


def _report(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "score_curated.json").read_text(encoding="utf-8"))


def _record_line(report: dict[str, Any], qid: str) -> dict[str, Any]:
    path = Path(str(report["case_record"]))
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("id") == qid:
            return row
    raise AssertionError(qid)


class _Http:
    def __init__(self, status: int, body: dict[str, Any], headers: dict[str, str] | None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code))


class _Script:
    def __init__(self, shots: list[dict[str, Any]] | None, *, repeat: dict[str, Any] | None):
        self.shots = list(shots or [])
        self.repeat = repeat
        self.calls: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []

    def client(self) -> type:
        script = self

        class _Client:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

            def post(
                self,
                url: str,
                json: dict[str, Any] | None = None,
                headers: dict[str, str] | None = None,
            ) -> _Http:
                script.calls.append({"url": url, "json": json or {}, "headers": headers or {}})
                return _Http(**script._next())

            def request(
                self,
                method: str,
                url: str,
                headers: dict[str, str] | None = None,
                json: dict[str, Any] | None = None,
                params: dict[str, Any] | None = None,
            ) -> _Http:
                script.requests.append(
                    {
                        "method": method,
                        "url": url,
                        "json": json or {},
                        "headers": headers or {},
                    }
                )
                if "/v1/insights" in url:
                    down = _down("quota_exhausted", _PIN)
                    return _Http(503, down["body"], {"Retry-After": "30"})
                return _Http(200, {"ok": False, "status": "ABSTAIN", "values": []}, {})

        return _Client

    def _next(self) -> dict[str, Any]:
        if self.shots:
            return self.shots.pop(0)
        if self.repeat is not None:
            return dict(self.repeat)
        raise AssertionError("unexpected pin call")


def _served_headers(provider: str, model: str) -> dict[str, str]:
    """The three headers served_response_headers returns at 0d0ef3f0."""
    return {
        "X-OpenVault-Served-Provider": provider,
        "X-OpenVault-Served-Model": model,
        "X-OpenVault-Served-Local": "false",
    }


def _ok(
    model: str,
    provider: str = _PROVIDER,
    *,
    upstream: str = "gemini-3.5-flash",
    headers: bool = True,
) -> dict[str, Any]:
    return {
        "status": 200,
        "headers": _served_headers(provider, model) if headers else {},
        "body": {
            "model": upstream,
            "served_provider": provider,
            "served_model": model,
            "choices": [{"message": {"content": "ok"}}],
        },
    }


def _down(reason: str, model: str) -> dict[str, Any]:
    return {
        "status": 503,
        "headers": {"Retry-After": "120"},
        "body": {
            "error": {
                "type": "pin_unavailable",
                "reason": reason,
                "model": model,
                "message": "pinned",
            },
            "served_provider": None,
            "served_model": None,
            "served_local": False,
        },
    }


def _patch_pin(
    monkeypatch: pytest.MonkeyPatch,
    *,
    shots: list[dict[str, Any]] | None = None,
    repeat: dict[str, Any] | None = None,
) -> _Script:
    script = _Script(shots, repeat=repeat)
    try:
        import cortex_client.strict_pin as pin
    except ImportError:
        return script
    monkeypatch.setattr(pin.httpx, "Client", script.client())
    return script


def _install_ask(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, Any]]:
    from dms_executor.demo_warehouse import clear_engine_clock

    clear_engine_clock()
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path))
    asks: list[dict[str, Any]] = []

    def score_http(
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 0,
    ) -> Any:
        if method == "GET" and url.rstrip("/").endswith("/health"):
            return _Http(
                200,
                {
                    "status": "ok",
                    "engine_as_of": _ENGINE_DAY,
                    "engine_as_of_after": _ENGINE_DAY,
                    "engine_timezone": _TZ,
                    "engine_timezone_after": _TZ,
                },
                {},
            )
        if method == "POST" and url.rstrip("/").endswith("/v1/chat/ask"):
            asks.append(dict(json_body or {}))
            # Confident L0 with a row. Gold is empty, so the judge returns WRONG.
            # badge ABSTAIN never reaches that compare (is_confident is false).
            rows = [dict(item) for item in _ANSWER_ROWS]
            # Echo served_* only when a pin env is set. Exact value. No strip.
            # No fallback to _PROVIDER / _PIN. Neither set: no served_* keys.
            body: dict[str, Any] = {
                "badge": "L0_CERTIFIED",
                "abstained": False,
                "rows": rows,
                "values": [dict(item) for item in rows],
                "text": "1",
                "engine_as_of": _ENGINE_DAY,
                "engine_as_of_after": _ENGINE_DAY,
                "engine_timezone": _TZ,
                "engine_timezone_after": _TZ,
            }
            has_provider = "DMS_STRICT_PROVIDER" in os.environ
            has_model = "DMS_STRICT_MODEL" in os.environ
            if has_provider or has_model:
                if "DMS_PIN02_ASK_PROVIDER" in os.environ:
                    body["served_provider"] = os.environ["DMS_PIN02_ASK_PROVIDER"]
                elif has_provider:
                    body["served_provider"] = os.environ["DMS_STRICT_PROVIDER"]
                if has_model:
                    body["served_model"] = os.environ["DMS_STRICT_MODEL"]
                body["served_attribution"] = "reported"
            return _Http(200, body, {})
        raise RuntimeError(f"unexpected {method} {url}")

    monkeypatch.setattr("score_curated.score_http", score_http)
    return asks


def _arm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    model: str | None = _PIN,
    provider: str | None = _PROVIDER,
    shots: list[dict[str, Any]] | None = None,
    repeat: dict[str, Any] | None = None,
    pinned: bool = True,
) -> tuple[list[dict[str, Any]], _Script]:
    if pinned:
        monkeypatch.setenv("OPENVAULT_URL", "http://127.0.0.1:9")
    else:
        monkeypatch.delenv("OPENVAULT_URL", raising=False)
    monkeypatch.delenv("OPENVAULT_API_KEY", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    if model is None:
        monkeypatch.delenv("DMS_STRICT_MODEL", raising=False)
    else:
        monkeypatch.setenv("DMS_STRICT_MODEL", model)
    if provider is None:
        monkeypatch.delenv("DMS_STRICT_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("DMS_STRICT_PROVIDER", provider)
    asks = _install_ask(monkeypatch, tmp_path)
    # Empty gold against the L0 row is a row mismatch, so the judge returns WRONG.
    monkeypatch.setattr("score_curated.run_oracle_select", lambda *_a, **_k: ([], None))
    script = _patch_pin(monkeypatch, shots=shots, repeat=repeat)
    return asks, script


def _match_gold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gold is the served row, so an L0 case that clears min_rows is OK."""
    gold = [dict(item) for item in _ANSWER_ROWS]
    monkeypatch.setattr(
        "score_curated.run_oracle_select",
        lambda *_a, **_k: ([dict(item) for item in gold], None),
    )


def _outside_record_dir(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Absolute case-record dir outside the repo. Eligible when the round is not INVALID."""
    path = Path(tempfile.mkdtemp(prefix="dms-pin-pos-", dir="/tmp"))
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        monkeypatch.setenv("DMS_CASE_RECORD_DIR", str(path))
        return path
    shutil.rmtree(path, ignore_errors=True)
    raise AssertionError(str(path))


def _wipe_records(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _wire(script: _Script) -> tuple[dict[str, Any], dict[str, Any]]:
    if not script.calls:
        return {}, {}
    call = script.calls[0]
    return dict(call.get("json") or {}), dict(call.get("headers") or {})


def _assert_strict_call(call: dict[str, Any], model: str) -> None:
    assert call["url"].endswith("/v1/chat/completions")
    assert call["json"]["strict"] is True
    assert call["json"]["model"] == model
    assert call["json"]["max_tokens"] >= 512
    assert call["headers"].get("X-OpenVault-Strict") == "true"
    blob = json.dumps(call["json"])
    assert "gsk_" not in blob
    assert "api_key" not in call["json"]
    assert "RATE_LIMIT" not in blob


def _patch_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("score_curated.probe_climb_host", lambda *_a, **_k: ("ok", "up"))
    monkeypatch.setattr(
        "score_curated.probe_climb_health",
        lambda *_a, **_k: (
            "ok",
            "up",
            {"product": "dms", "ask_mode": "live", "demo_fallback": False},
        ),
    )


@pytest.mark.parametrize("reason", _VAULT_REASONS)
def test_live_preflight_pin_unavailable_is_invalid_n0(
    reason: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One pinned call. 503 stops the round. n=0. Not RATE_LIMIT. No retry."""
    db = _oracle_db(tmp_path)
    asks, script = _arm(
        monkeypatch, tmp_path, repeat=_down(reason, _PIN)
    )
    started = time.monotonic()
    code = live("http://score.test", 1.0, db)
    elapsed = time.monotonic() - started
    report = _report(tmp_path)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report.get("n_planned") == 52 and report["n"] == 0
    name = f"pin_unavailable:{_PROVIDER}/{_PIN}"
    assert (
        report["reason"] == name
        and report["round_label"] == "INVALID"
        and report["pin_reason"] == reason
    )
    assert elapsed < 3.0
    assert code != 0
    assert asks == []
    assert len(script.calls) == 1
    _assert_strict_call(script.calls[0], _PIN)
    assert report["total"] == 0
    assert report["cases"] == []
    assert report["passed"] is False
    assert report["reason"] != "RATE_LIMIT"
    assert report["pin_reason"] != "RATE_LIMIT"
    assert int(report["invalid"]) == 0
    assert int(report["wrong"]) == 0


def test_live_reads_model_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wire model is the env pin, not a literal at the call site."""
    db = _oracle_db(tmp_path)
    _asks, script = _arm(
        monkeypatch,
        tmp_path,
        model=_OTHER,
        provider="groq",
        repeat=_down("parked", _OTHER),
    )
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    wire, hdr = _wire(script)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert wire.get("strict") is True and wire.get("model") == _OTHER
    assert hdr.get("X-OpenVault-Strict") == "true"
    assert wire.get("model") != _PIN
    _assert_strict_call(script.calls[0], _OTHER)
    assert report["reason"] == f"pin_unavailable:groq/{_OTHER}"


def test_live_default_pin_is_groq_catalog_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _oracle_db(tmp_path)
    _asks, script = _arm(
        monkeypatch,
        tmp_path,
        model=None,
        provider=None,
        repeat=_down("parked", _PIN),
    )
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    wire, hdr = _wire(script)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert (
        wire.get("strict") is True
        and wire.get("model") == _PIN
        and wire.get("model") != "gpt-oss-120b"
        and hdr.get("X-OpenVault-Strict") == "true"
    )
    _assert_strict_call(script.calls[0], _PIN)
    assert report["reason"] == f"pin_unavailable:{_PROVIDER}/{_PIN}"


@pytest.mark.parametrize("reason", _VAULT_REASONS)
def test_live_case_pin_unavailable_stays_in_n(
    reason: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a healthy preflight, one 503 is ABSTAIN in n. No retry, no switch."""
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    shots = [_ok(_PIN), _down(reason, _PIN)] + [_ok(_PIN) for _ in range(n_pack - 1)]
    asks, script = _arm(monkeypatch, tmp_path, shots=shots)
    started = time.monotonic()
    live("http://score.test", 1.0, db)
    elapsed = time.monotonic() - started
    report = _report(tmp_path)
    name = f"pin_unavailable:{_PROVIDER}/{_PIN}"
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    first = report["cases"][0]
    assert (
        first["verdict"] == "ABSTAIN"
        and first["reason"] == name
        and first.get("pin_reason") == reason
    )
    assert elapsed < 5.0
    assert first["verdict"] != "INVALID"
    assert first["verdict"] != "WRONG"
    assert first["verdict"] != "RATE_LIMIT"
    assert first["rows"] == 0
    assert report["n"] == n_pack
    assert report["invalid"] == 0
    assert report["n_without_invalid"] == n_pack
    assert report["wrong"] == n_pack - 1
    assert len(asks) == n_pack - 1
    assert len(script.calls) == n_pack + 1
    models = {call["json"]["model"] for call in script.calls}
    assert models == {_PIN}
    assert all(call["json"]["strict"] is True for call in script.calls)
    assert report["cases"][1]["verdict"] != "RATE_LIMIT"
    assert int(report.get("rate_limit") or 0) == 0


def test_live_quota_exhausted_is_pin_abstain_not_rate_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """503 quota_exhausted is the pin abstain. It is not the 429 RATE_LIMIT path."""
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    shots = [_ok(_PIN), _down("quota_exhausted", _PIN)] + [
        _ok(_PIN) for _ in range(n_pack - 1)
    ]
    _asks, _script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    name = f"pin_unavailable:{_PROVIDER}/{_PIN}"
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    first = report["cases"][0]
    assert (
        first["verdict"] == "ABSTAIN"
        and first["reason"] == name
        and first.get("pin_reason") == "quota_exhausted"
        and first["verdict"] != "RATE_LIMIT"
    )
    assert int(report.get("rate_limit") or 0) == 0
    assert "pin_preflight_unavailable" not in report["baseline_ineligible_reasons"]
    assert report["n"] == n_pack


def test_live_body_pin_mismatch_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    bad = _ok("gemini-3.5-flash", "google")
    shots = [_ok(_PIN), bad] + [_ok(_PIN) for _ in range(n_pack - 1)]
    asks, script = _arm(monkeypatch, tmp_path, shots=shots)
    code = live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    row = report["cases"][0]
    assert row["verdict"] == "INVALID" and row["reason"] == "pin_mismatch:google/gemini-3.5-flash"
    assert code != 0
    assert row["verdict"] != "WRONG"
    assert report["n"] == n_pack
    assert report["invalid"] == 1
    assert report["n_without_invalid"] == n_pack - 1
    assert report["wrong"] == n_pack - 1
    assert len(asks) == n_pack - 1
    assert len(script.calls) == n_pack + 1
    assert all(item["verdict"] != "INVALID" for item in report["cases"][1:])


def test_live_missing_served_model_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upstream ``model`` is not served_model. A missing served field is INVALID."""
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    missing = {
        "status": 200,
        "headers": {},
        "body": {"model": _PIN, "choices": [{"message": {"content": "ok"}}]},
    }
    shots = [_ok(_PIN), missing] + [_ok(_PIN) for _ in range(n_pack - 1)]
    _asks, _script = _arm(monkeypatch, tmp_path, shots=shots)
    code = live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    row = report["cases"][0]
    assert row["verdict"] == "INVALID" and row["reason"] == "pin_mismatch:missing/missing"
    assert code != 0
    assert report["n"] == n_pack
    assert report["invalid"] == 1
    assert report["wrong"] == n_pack - 1


def _other(provider: str, where: str) -> dict[str, Any]:
    """Same model, other provider. body, header, or both. Not the groq pin."""
    body: dict[str, Any] = {"choices": [{"message": {"content": "ok"}}]}
    headers: dict[str, str] = {}
    if where in ("body", "both"):
        body["served_provider"] = provider
        body["served_model"] = _PIN
    if where in ("header", "both"):
        headers = _served_headers(provider, _PIN)
    return {"status": 200, "headers": headers, "body": body}


def _nvidia(where: str) -> dict[str, Any]:
    """Provider id nvidia, the OpenVault #81 NIM id. Not groq."""
    return _other("nvidia", where)


def test_live_nvidia_same_model_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same model from nvidia, headers plus body, is not the groq pin. Stays in n."""
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    bad = _nvidia("both")
    shots = [_ok(_PIN), bad] + [_ok(_PIN) for _ in range(n_pack - 1)]
    _asks, _script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    row = report["cases"][0]
    assert row["verdict"] == "INVALID" and row["reason"] == (
        f"pin_mismatch:nvidia/{_PIN}"
    )
    assert report["n"] == n_pack
    assert report["invalid"] == 1
    assert row["served_provider_body"] == "nvidia"
    assert row["served_model_body"] == _PIN
    assert row["served_provider_header"] == "nvidia"
    assert row["served_model_header"] == _PIN
    rec = _record_line(report, "cq_spend_by_country")
    assert rec["outcome"] == "INVALID"
    assert rec["served_provider"] == "unknown"
    assert rec["served_model"] == "unknown"
    assert rec["served_provider_body"] == "nvidia"
    assert rec["served_model_body"] == _PIN
    assert rec["served_provider_header"] == "nvidia"
    assert rec["served_model_header"] == _PIN


def test_live_together_same_model_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """together serving openai/gpt-oss-120b is not the groq pin.

    providers.py line 287 is id="together". Not an allowed pin.
    Headers plus body. INVALID pin_mismatch, stays in n. Values stay exact.
    """
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    bad = _other("together", "both")
    shots = [_ok(_PIN), bad] + [_ok(_PIN) for _ in range(n_pack - 1)]
    _asks, _script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    row = report["cases"][0]
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    assert row["verdict"] == "INVALID" and row["reason"] == (
        f"pin_mismatch:together/{_PIN}"
    )
    assert report["n"] == n_pack
    assert report["invalid"] == 1
    assert row["served_provider_body"] == "together"
    assert row["served_model_body"] == _PIN
    assert row["served_provider_header"] == "together"
    assert row["served_model_header"] == _PIN
    rec = _record_line(report, "cq_spend_by_country")
    assert rec["outcome"] == "INVALID"
    assert rec["served_provider_body"] == "together"
    assert rec["served_model_body"] == _PIN
    assert rec["served_provider_header"] == "together"
    assert rec["served_model_header"] == _PIN
    assert rec["served_provider"] == "unknown"
    assert rec["served_model"] == "unknown"


def test_live_missing_served_provider_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model present, provider absent from body and headers. INVALID."""
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    bad = {
        "status": 200,
        "headers": {"X-OpenVault-Served-Model": _PIN},
        "body": {
            "served_model": _PIN,
            "choices": [{"message": {"content": "ok"}}],
        },
    }
    shots = [_ok(_PIN), bad] + [_ok(_PIN) for _ in range(n_pack - 1)]
    _asks, _script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    row = report["cases"][0]
    assert row["verdict"] == "INVALID" and row["reason"] == f"pin_mismatch:missing/{_PIN}"
    assert report["n"] == n_pack
    assert report["invalid"] == 1
    assert row["served_provider_body"] is None
    assert row["served_model_body"] == _PIN
    assert row["served_provider_header"] is None
    assert row["served_model_header"] == _PIN
    rec = _record_line(report, "cq_spend_by_country")
    assert rec["served_provider_body"] is None
    assert rec["served_model_body"] == _PIN
    assert rec["served_provider_header"] is None
    assert rec["served_model_header"] == _PIN


def test_live_preflight_other_provider_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight rejects nvidia headers plus body even when the model is the pin."""
    db = _oracle_db(tmp_path)
    asks, script = _arm(monkeypatch, tmp_path, repeat=_nvidia("both"))
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["reason"] == f"pin_mismatch:nvidia/{_PIN}"
    assert report["round_label"] == "INVALID"
    assert report.get("n_planned") == 52 and report["n"] == 0
    assert asks == []
    assert len(script.calls) == 1
    assert report["cases"] == []


def test_live_header_mismatch_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    bad = {
        "status": 200,
        "headers": {
            "X-OpenVault-Served-Provider": "google",
            "X-OpenVault-Served-Model": "gemini-3.5-flash",
        },
        "body": {"served_provider": None, "served_model": None, "choices": []},
    }
    shots = [_ok(_PIN), bad] + [_ok(_PIN) for _ in range(n_pack - 1)]
    _asks, _script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    row = report["cases"][0]
    assert row["verdict"] == "INVALID" and row["reason"] == "pin_mismatch:google/gemini-3.5-flash"
    assert report["n"] == n_pack
    assert report["invalid"] == 1


def test_live_headers_without_body_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Served headers without served body fields are not the pin. Stays in n."""
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    bad = {
        "status": 200,
        "headers": _served_headers(_PROVIDER, _PIN),
        "body": {"model": "gemini-3.5-flash", "choices": [{"message": {"content": "ok"}}]},
    }
    shots = [_ok(_PIN), bad] + [_ok(_PIN) for _ in range(n_pack - 1)]
    asks, script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    row = report["cases"][0]
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    assert "served_provider" not in bad["body"]
    assert "served_model" not in bad["body"]
    assert row["verdict"] == "INVALID" and row["reason"] == (
        f"pin_mismatch:{_PROVIDER}/{_PIN}"
    )
    assert row["verdict"] != "OK"
    assert report["n"] == n_pack
    assert report["invalid"] == 1
    assert row["served_provider_body"] is None
    assert row["served_model_body"] is None
    assert row["served_provider_header"] == _PROVIDER
    assert row["served_model_header"] == _PIN
    assert len(asks) == n_pack - 1
    assert len(script.calls) == n_pack + 1


def test_live_body_and_header_disagree_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both sides present and they disagree. Neither wins.

    OpenVault #81 at 0d0ef3f0 sends the served ids in the body and the headers.
    """
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    bad = {
        "status": 200,
        "headers": {
            "X-OpenVault-Served-Provider": _PROVIDER,
            "X-OpenVault-Served-Model": _PIN,
        },
        "body": {"served_provider": "google", "served_model": "gemini-3.5-flash"},
    }
    shots = [_ok(_PIN), bad] + [_ok(_PIN) for _ in range(n_pack - 1)]
    _asks, _script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    row = report["cases"][0]
    assert row["verdict"] == "INVALID" and row["reason"] == (
        "pin_mismatch:google/gemini-3.5-flash+groq/openai/gpt-oss-120b"
    )
    assert row["served_model_body"] == "gemini-3.5-flash"
    assert row["served_provider_body"] == "google"
    assert row["served_model_header"] == _PIN
    assert row["served_provider_header"] == _PROVIDER
    rec = _record_line(report, "cq_spend_by_country")
    assert rec["outcome"] == "INVALID"
    assert rec["served_model"] == "unknown"
    assert rec["served_provider"] == "unknown"
    assert rec["served_model_body"] == "gemini-3.5-flash"
    assert rec["served_provider_body"] == "google"
    assert rec["served_model_header"] == _PIN
    assert rec["served_provider_header"] == _PROVIDER
    assert report["n"] == n_pack
    assert report["invalid"] == 1


def test_live_body_pin_header_other_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Body names the pin. The header does not. Still INVALID.

    OpenVault #81 at 0d0ef3f0 sends the served ids in the body and the headers.
    """
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    bad = {
        "status": 200,
        "headers": {
            "X-OpenVault-Served-Provider": "google",
            "X-OpenVault-Served-Model": "gemini-3.5-flash",
        },
        "body": {"served_provider": _PROVIDER, "served_model": _PIN},
    }
    shots = [_ok(_PIN), bad] + [_ok(_PIN) for _ in range(n_pack - 1)]
    _asks, _script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    row = report["cases"][0]
    assert row["verdict"] == "INVALID" and row["reason"] == (
        f"pin_mismatch:{_PROVIDER}/{_PIN}+google/gemini-3.5-flash"
    )
    assert row["served_model_body"] == _PIN
    assert row["served_model_header"] == "gemini-3.5-flash"
    rec = _record_line(report, "cq_spend_by_country")
    assert rec["served_model"] == "unknown"
    assert rec["served_model_body"] == _PIN
    assert rec["served_provider_body"] == _PROVIDER
    assert rec["served_model_header"] == "gemini-3.5-flash"
    assert rec["served_provider_header"] == "google"
    assert report["n"] == n_pack
    assert report["invalid"] == 1


def test_live_pin_preflight_unavailable_blocks_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blocked pin preflight sets pin_preflight_unavailable on baseline_eligibility."""
    db = _oracle_db(tmp_path)
    asks, _script = _arm(monkeypatch, tmp_path, repeat=_down("parked", _PIN))
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    reasons = report["baseline_ineligible_reasons"]
    name = f"pin_unavailable:{_PROVIDER}/{_PIN}"
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert (
        "pin_preflight_unavailable" in reasons
        and report["baseline_eligible"] is False
        and report["reason"] == name
        and report["round_label"] == "INVALID"
        and report["n"] == 0
    )
    assert asks == []


@pytest.mark.parametrize("model", _CALLER_ERRORS)
def test_live_caller_error_sends_nothing(
    model: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _oracle_db(tmp_path)
    asks, script = _arm(monkeypatch, tmp_path, model=model, repeat=_ok(_PIN))
    code = live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    shown = model.strip().lower() or "empty"
    if model == "gpt-oss-120b":
        shown = model
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert (
        report["reason"] == f"pin_caller_error:{shown}"
        and report["round_label"] == "INVALID"
        and report["n"] == 0
        and script.calls == []
    )
    assert code != 0
    assert asks == []
    assert report["cases"] == []


def test_live_nvidia_pin_refused_at_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """nvidia is not an allowed pin. Setup refuses. No vault call, no case call."""
    db = _oracle_db(tmp_path)
    asks, script = _arm(
        monkeypatch,
        tmp_path,
        provider="nvidia",
        model=_PIN,
        repeat=_nvidia("both"),
    )
    code = live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["reason"] == "pin_caller_error:nvidia"
    assert report["round_label"] == "INVALID"
    assert report.get("n_planned") == 52 and report["n"] == 0
    assert asks == []
    assert script.calls == []
    assert report["cases"] == []
    assert code != 0


@pytest.mark.parametrize(
    "entry",
    ("live", "climb", "climb_ab_live", "prove_path_live", "grid_score_hook"),
)
def test_each_live_entry_preflight_aborts(
    entry: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every live entry shares the preflight. A 503 asks nothing."""
    import score_curated

    db = _oracle_db(tmp_path)
    asks, script = _arm(monkeypatch, tmp_path, repeat=_down("quota_exhausted", _PIN))
    _patch_probe(monkeypatch)
    fn = getattr(score_curated, entry)
    result = fn("http://score.test", 1.0, db)
    name = f"pin_unavailable:{_PROVIDER}/{_PIN}"
    if entry == "grid_score_hook":
        assert (
            result.get("reason") == name
            and result.get("round_label") == "INVALID"
            and result.get("n") == 0
            and result.get("pin_reason") == "quota_exhausted"
        )
        assert asks == []
        assert script.calls
        assert all(call["json"]["strict"] is True for call in script.calls)
        assert all(call["json"]["model"] == _PIN for call in script.calls)
        assert result.get("n_planned") == 52
        assert result["cases"] == []
        assert result["passed"] is False
        return
    if entry == "live":
        report = _report(tmp_path)
    elif entry == "climb":
        report = json.loads((tmp_path / "score_climb.json").read_text(encoding="utf-8"))
    elif entry == "climb_ab_live":
        report = json.loads((tmp_path / "score_climb_ab.json").read_text(encoding="utf-8"))
    else:
        report = json.loads((tmp_path / "score_gen_path_prove.json").read_text(encoding="utf-8"))
    assert report.get("oracle_as_of") == _ENGINE_DAY
    assert (
        report.get("reason") == name
        and report.get("round_label") == "INVALID"
        and report.get("pin_reason") == "quota_exhausted"
    )
    assert report.get("n_planned") == 52
    if entry == "climb":
        assert report["measured"]["n"] == 0
    elif entry == "climb_ab_live":
        assert report["exact_match"]["n"] == 0
        assert report["generative"]["n"] == 0
    elif entry == "prove_path_live":
        assert report["exact_n"] == 0
    assert result != 0
    assert asks == []
    assert script.calls
    assert all(call["json"]["strict"] is True for call in script.calls)


def test_live_generate_posts_send_strict_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Insights generate does not send model or strict. A 503 stops that leg.

    No second post, no GET. The OpenVault chat preflight is a different
    request and still pins.
    """
    import score_curated
    from cortex_client.compute import (
        _insights_body,
        _insights_generate_post,
        _run_insights_legs,
        insights_fail_reason,
    )
    from cortex_client.insights import insights_post

    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    asks, script = _arm(monkeypatch, tmp_path, shots=[_ok(_PIN) for _ in range(n_pack + 1)])
    seen: dict[str, Any] = {}
    installed_fn = score_curated.score_http

    def wrapped(
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 0,
    ) -> Any:
        if method == "POST" and url.rstrip("/").endswith("/v1/chat/ask") and not seen:
            body = _insights_body(
                "how many skus", session_id="s", space_id="sp", ontology=None
            )
            seen["body"] = body
            seen["preference"] = body.get("model_preference")

            class _Once:
                def __init__(self) -> None:
                    self.n = 0
                    self.gets = 0

                def post(
                    self,
                    url: str,
                    json: dict[str, Any] | None = None,
                    headers: dict[str, str] | None = None,
                ) -> _Http:
                    self.n += 1
                    seen["gen_json"] = json
                    seen["gen_headers"] = headers
                    return _Http(503, _down("quota_exhausted", _PIN)["body"], {"Retry-After": "30"})

                def get(self, *a: Any, **k: Any) -> _Http:
                    self.gets += 1
                    return _Http(200, {}, {})

            once = _Once()
            auth = {"Authorization": "Bearer ov_test_pin_key"}
            try:
                out = _run_insights_legs(
                    once,  # type: ignore[arg-type]
                    "http://127.0.0.1:9",
                    "how many skus",
                    body,
                    auth,
                    None,
                )
            except Exception as exc:  # noqa: BLE001 - recorded, asserted after live()
                out = None
                seen["legs_error"] = type(exc).__name__
            seen["legs_posts"] = once.n
            seen["legs_gets"] = once.gets
            seen["out"] = out
            try:
                again = _insights_generate_post(
                    once,  # type: ignore[arg-type]
                    "http://127.0.0.1:9",
                    body,
                    auth,
                )
            except Exception as exc:  # noqa: BLE001
                again = None
                seen["post_error"] = type(exc).__name__
            seen["again"] = again
            seen["posts_after_second"] = once.n
            try:
                seen["hosted"] = insights_post(
                    "http://127.0.0.1:9",
                    intent="how many skus",
                    generate=True,
                    api_key="ov_test_pin_key",
                )
            except Exception as exc:  # noqa: BLE001
                seen["hosted"] = None
                seen["hosted_error"] = type(exc).__name__
            seen["done"] = True
        return installed_fn(method, url, json_body=json_body, timeout=timeout)

    monkeypatch.setattr("score_curated.score_http", wrapped)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    body = seen.get("body") or {}
    gen_json = seen.get("gen_json") or {}
    gen_headers = seen.get("gen_headers") or {}
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][0]["verdict"] == "WRONG"
    assert "model" not in body and "strict" not in body
    assert "model" not in gen_json and "strict" not in gen_json
    assert gen_headers.get("X-OpenVault-Strict") == "true"
    name = f"pin_unavailable:{_PROVIDER}/{_PIN}"
    out = seen.get("out") or {}
    assert out.get("insights_fail") == name and out.get("pin_reason") == "quota_exhausted"
    assert out.get("pin_stop") is True
    assert insights_fail_reason(out) == name
    assert insights_fail_reason(out) != "RATE_LIMIT"
    assert seen.get("legs_posts") == 1
    assert seen.get("legs_gets") == 0
    again = seen.get("again") or {}
    assert again.get("pin_stop") is True
    assert seen.get("posts_after_second") == 2
    hosted = seen.get("hosted") or {}
    assert hosted.get("insights_fail") == name and hosted.get("pin_reason") == "quota_exhausted"
    assert script.requests
    sent = script.requests[0]
    assert "model" not in sent["json"] and "strict" not in sent["json"]
    assert sent["headers"].get("X-OpenVault-Strict") == "true"
    assert "ov_test_pin_key" not in json.dumps(sent["json"])
    assert seen.get("preference") == "free+normal"
    assert seen.get("done") is True
    assert len(asks) == n_pack


def test_live_body_and_header_pin_match_oracle_is_correct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Groq pin matched in the body and the served headers together.

    CORRECT (verdict OK), no pin_* reason, baseline_eligible. Passes on the
    head. Body alone is not this test.
    """
    outside = _outside_record_dir(monkeypatch)
    try:
        db = _oracle_db(tmp_path)
        n_pack = len(_questions())
        shot = _ok(_PIN, headers=True)
        asks, script = _arm(monkeypatch, tmp_path, repeat=shot)
        _match_gold(monkeypatch)
        live("http://score.test", 1.0, db)
        report = _report(tmp_path)
        row = report["cases"][0]
        assert report["oracle_as_of"] == _ENGINE_DAY
        assert shot["headers"] == _served_headers("groq", _PIN)
        assert shot["body"]["served_provider"] == "groq"
        assert shot["body"]["served_model"] == _PIN
        assert row["id"] == "cq_spend_by_country" and row["verdict"] == "OK"
        assert report["correct"] >= 1
        assert not str(row.get("reason") or "").startswith("pin_")
        assert not str(report.get("reason") or "").startswith("pin_")
        assert report.get("pin_reason") in (None, "")
        assert report["baseline_eligible"] is True
        assert report["baseline_ineligible_reasons"] == []
        rec = _record_line(report, "cq_spend_by_country")
        assert rec["outcome"] == "OK"
        assert not str(rec.get("reason") or "").startswith("pin_")
        assert len(asks) == n_pack
        assert len(script.calls) == n_pack + 1
    finally:
        _wipe_records(outside)


@pytest.mark.parametrize(
    ("provider", "model"),
    (
        ("google", "gemini-3.5-flash"),
        ("openrouter", "google/gemma-4-31b-it:free"),
    ),
    ids=("google", "openrouter"),
)
def test_live_fa01_pin_match_is_correct(
    provider: str,
    model: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allowed pin matched in the body and the served headers together.

    google model is providers.py line 205, gemini-3.5-flash.
    openrouter model is line 150, google/gemma-4-31b-it:free.
    Headers plus body. Exact ==. No case fold. CORRECT, no pin_* reason,
    baseline_eligible.
    """
    outside = _outside_record_dir(monkeypatch)
    try:
        db = _oracle_db(tmp_path)
        shot = _ok(model, provider, upstream="upstream-not-the-pin", headers=True)
        asks, script = _arm(
            monkeypatch,
            tmp_path,
            model=model,
            provider=provider,
            repeat=shot,
        )
        _match_gold(monkeypatch)
        live("http://score.test", 1.0, db)
        report = _report(tmp_path)
        row = report["cases"][0]
        assert report["oracle_as_of"] == _ENGINE_DAY
        assert shot["headers"] == _served_headers(provider, model)
        assert shot["body"]["served_provider"] == provider
        assert shot["body"]["served_model"] == model
        assert script.calls[0]["json"]["model"] == model
        assert row["id"] == "cq_spend_by_country" and row["verdict"] == "OK"
        assert not str(row.get("reason") or "").startswith("pin_")
        assert not str(report.get("reason") or "").startswith("pin_")
        assert report["baseline_eligible"] is True
        assert report["baseline_ineligible_reasons"] == []
        assert len(asks) == len(_questions())
    finally:
        _wipe_records(outside)


def _headers_named(provider: str, model: str, *, lower: bool) -> dict[str, str]:
    headers = _served_headers(provider, model)
    if not lower:
        return headers
    return {key.lower(): value for key, value in headers.items()}


@pytest.mark.parametrize("lower", (False, True), ids=("canonical", "lower"))
def test_live_served_header_name_case_is_correct(
    lower: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Body plus served headers. Name case does not matter. Values stay exact.

    canonical is X-OpenVault-Served-Provider. lower is the live hop:
    x-openvault-served-provider=groq, x-openvault-served-model=openai/gpt-oss-120b,
    x-openvault-served-local=false. CORRECT, no pin_* reason, baseline_eligible.
    """
    outside = _outside_record_dir(monkeypatch)
    try:
        db = _oracle_db(tmp_path)
        shot = _ok(_PIN, headers=True)
        shot["body"]["served_local"] = False
        shot["headers"] = _headers_named("groq", _PIN, lower=lower)
        asks, script = _arm(monkeypatch, tmp_path, repeat=shot)
        _match_gold(monkeypatch)
        live("http://score.test", 1.0, db)
        report = _report(tmp_path)
        row = report["cases"][0]
        assert report["oracle_as_of"] == _ENGINE_DAY
        assert shot["body"]["served_provider"] == "groq"
        assert shot["body"]["served_model"] == _PIN
        assert shot["body"]["served_local"] is False
        names = {key.lower() for key in shot["headers"]}
        assert "x-openvault-served-provider" in names
        assert "x-openvault-served-model" in names
        assert "x-openvault-served-local" in names
        assert row["id"] == "cq_spend_by_country" and row["verdict"] == "OK"
        assert not str(row.get("reason") or "").startswith("pin_")
        assert not str(report.get("reason") or "").startswith("pin_")
        assert report["baseline_eligible"] is True
        assert report["baseline_ineligible_reasons"] == []
        assert len(asks) == len(_questions())
        assert len(script.calls) == len(_questions()) + 1
    finally:
        _wipe_records(outside)


def test_live_groq_value_case_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Served provider Groq is not the groq pin. Values are not case-folded."""
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    bad = _ok(_PIN, "Groq", headers=True)
    shots = [_ok(_PIN), bad] + [_ok(_PIN) for _ in range(n_pack - 1)]
    _asks, _script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    row = report["cases"][0]
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    assert row["verdict"] == "INVALID" and row["reason"] == (
        f"pin_mismatch:Groq/{_PIN}"
    )
    assert report["n"] == n_pack
    assert report["invalid"] == 1
    assert row["served_provider_body"] == "Groq"
    assert row["served_model_body"] == _PIN
    assert row["served_provider_header"] == "Groq"
    assert row["served_model_header"] == _PIN
    rec = _record_line(report, "cq_spend_by_country")
    assert rec["served_provider_body"] == "Groq"
    assert rec["served_model_body"] == _PIN
    assert rec["outcome"] == "INVALID"


def test_live_pin_unavailable_without_served_ids_is_abstain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """503 no_hop with no served headers and no served body fields is ABSTAIN.

    It is pin_unavailable, never pin_mismatch.
    """
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    bare = {
        "status": 503,
        "headers": {},
        "body": {
            "error": {
                "type": "pin_unavailable",
                "reason": "no_hop",
                "model": _PIN,
                "message": "pinned model has no healthy hop",
            }
        },
    }
    shots = [_ok(_PIN), bare] + [_ok(_PIN) for _ in range(n_pack - 1)]
    _asks, _script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    name = f"pin_unavailable:{_PROVIDER}/{_PIN}"
    row = report["cases"][0]
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    assert bare["headers"] == {}
    assert "served_provider" not in bare["body"]
    assert "served_model" not in bare["body"]
    assert row["verdict"] == "ABSTAIN" and row["reason"] == name
    assert row.get("pin_reason") == "no_hop"
    assert row["verdict"] != "INVALID"
    assert not str(row["reason"]).startswith("pin_mismatch")
    assert report["n"] == n_pack
    assert report["invalid"] == 0
    assert int(report.get("rate_limit") or 0) == 0


def test_live_body_without_headers_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Body fields that match the pin, with no served headers, are INVALID.

    Kept in n. Not CORRECT.
    """
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    bad = _ok(_PIN, headers=False)
    shots = [_ok(_PIN), bad] + [_ok(_PIN) for _ in range(n_pack - 1)]
    _asks, _script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    row = report["cases"][0]
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    assert bad["headers"] == {}
    assert bad["body"]["served_provider"] == _PROVIDER
    assert bad["body"]["served_model"] == _PIN
    assert row["verdict"] == "INVALID" and row["reason"] == (
        f"pin_mismatch:{_PROVIDER}/{_PIN}"
    )
    assert row["verdict"] != "OK"
    assert report["n"] == n_pack
    assert report["invalid"] == 1
    assert row["served_provider_body"] == _PROVIDER
    assert row["served_model_body"] == _PIN
    assert row["served_provider_header"] is None
    assert row["served_model_header"] is None


def test_live_header_groq_body_together_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Header groq and body together do not agree. Never CORRECT. Stays in n."""
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    bad = {
        "status": 200,
        "headers": _served_headers(_PROVIDER, _PIN),
        "body": {
            "served_provider": "together",
            "served_model": _PIN,
            "choices": [{"message": {"content": "ok"}}],
        },
    }
    shots = [_ok(_PIN), bad] + [_ok(_PIN) for _ in range(n_pack - 1)]
    _asks, _script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    row = report["cases"][0]
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    assert row["verdict"] == "INVALID" and row["reason"] == (
        f"pin_mismatch:together/{_PIN}+{_PROVIDER}/{_PIN}"
    )
    assert row["verdict"] != "OK"
    assert report["n"] == n_pack
    assert report["invalid"] == 1
    assert row["served_provider_body"] == "together"
    assert row["served_model_body"] == _PIN
    assert row["served_provider_header"] == _PROVIDER
    assert row["served_model_header"] == _PIN


def test_live_preflight_headers_stripped_stops_before_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight body with the pin and no served headers stops the round.

    n=0. The named mismatch reason. Zero case calls.
    """
    db = _oracle_db(tmp_path)
    asks, script = _arm(monkeypatch, tmp_path, repeat=_ok(_PIN, headers=False))
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["reason"] == f"pin_mismatch:{_PROVIDER}/{_PIN}"
    assert report["round_label"] == "INVALID"
    assert report.get("n_planned") == 52 and report["n"] == 0
    assert asks == []
    assert len(script.calls) == 1
    assert report["cases"] == []


def test_live_pin_unavailable_with_served_body_is_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 503 that still carries served body fields is not pin_unavailable."""
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    bad = _down("no_hop", _PIN)
    bad["body"]["served_provider"] = _PROVIDER
    bad["body"]["served_model"] = _PIN
    shots = [_ok(_PIN), bad] + [_ok(_PIN) for _ in range(n_pack - 1)]
    _asks, _script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    row = report["cases"][0]
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][1]["verdict"] == "WRONG"
    assert row["verdict"] == "INVALID" and row["reason"] == (
        f"pin_mismatch:{_PROVIDER}/{_PIN}"
    )
    assert row["verdict"] != "ABSTAIN"
    assert not str(row["reason"]).startswith("pin_unavailable")
    assert report["n"] == n_pack
    assert report["invalid"] == 1


def test_live_pin_matched_round_keeps_parent_correct_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin matched on every case. CORRECT stays the 22deaa35 count.

    The parent has no pin, so the unpinned fixture round is that count.
    No extra ABSTAIN or INVALID. Passes on the head. Does not have to fail
    on the parent.
    """
    outside = _outside_record_dir(monkeypatch)
    try:
        db = _oracle_db(tmp_path)
        n_pack = len(_questions())
        _arm(monkeypatch, tmp_path, pinned=False)
        monkeypatch.delenv("DMS_STRICT_PROVIDER", raising=False)
        monkeypatch.delenv("DMS_STRICT_MODEL", raising=False)
        _match_gold(monkeypatch)
        live("http://score.test", 1.0, db)
        parent = _report(tmp_path)
        seen_env = 0
        rec_path = Path(str(parent["case_record"]))
        for line in rec_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            stored = json.loads(line).get("envelope")
            assert isinstance(stored, dict)
            assert not any(str(key).startswith("served_") for key in stored)
            seen_env += 1
        assert seen_env == n_pack
        assert all(
            not str(row.get("reason") or "").startswith("pin_")
            for row in parent["cases"]
        )
        _asks, script = _arm(monkeypatch, tmp_path, repeat=_ok(_PIN))
        _match_gold(monkeypatch)
        live("http://score.test", 1.0, db)
        head = _report(tmp_path)
        assert head["oracle_as_of"] == _ENGINE_DAY
        assert (head["correct"], parent["correct"]) == (
            parent["correct"],
            parent["correct"],
        )
        assert (head["abstained"], head["invalid"]) == (
            parent["abstained"],
            parent["invalid"],
        )
        assert head["n"] == n_pack == parent["n"]
        assert parent["correct"] == head["correct"]
        assert head["correct"] > 0
        assert len(script.calls) == n_pack + 1
        assert all(
            not str(row.get("reason") or "").startswith("pin_") for row in head["cases"]
        )
    finally:
        _wipe_records(outside)
