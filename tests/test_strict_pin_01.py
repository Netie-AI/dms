"""dms#317 Part A. Strict pin through live(), mocked OpenVault, no Groq call.

Each test fails on a63988b2: that tree has no pin gate, so the assertions do
not hold. No existing test is edited here.
"""

from __future__ import annotations

import json
import sys
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


def _ok(
    model: str, provider: str = _PROVIDER, *, upstream: str = "gemini-3.5-flash"
) -> dict[str, Any]:
    return {
        "status": 200,
        "headers": {},
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
            return _Http(
                200,
                {
                    "badge": "ABSTAIN",
                    "abstained": True,
                    "rows": [],
                    "values": [],
                    "text": "abstain",
                    "engine_as_of": _ENGINE_DAY,
                    "engine_as_of_after": _ENGINE_DAY,
                    "engine_timezone": _TZ,
                    "engine_timezone_after": _TZ,
                },
                {},
            )
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
) -> tuple[list[dict[str, Any]], _Script]:
    monkeypatch.setenv("OPENVAULT_URL", "http://127.0.0.1:9")
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
    # Gold rows come back. The judge scores ABSTAIN. It does not ORACLE_ERROR.
    monkeypatch.setattr("score_curated.run_oracle_select", lambda *_a, **_k: ([], None))
    script = _patch_pin(monkeypatch, shots=shots, repeat=repeat)
    return asks, script


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
    name = f"pin_unavailable:{_PROVIDER}/{_PIN}"
    assert (
        report["reason"] == name
        and report["round_label"] == "INVALID"
        and report["n"] == 0
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
    assert report["cases"][1]["verdict"] == "ABSTAIN"
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
    assert report["wrong"] == 0
    assert len(asks) == n_pack - 1
    assert len(script.calls) == n_pack + 1
    models = {call["json"]["model"] for call in script.calls}
    assert models == {_PIN}
    assert all(call["json"]["strict"] is True for call in script.calls)
    assert report["cases"][1]["verdict"] != "RATE_LIMIT"


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
    assert report["cases"][1]["verdict"] == "ABSTAIN"
    row = report["cases"][0]
    assert row["verdict"] == "INVALID" and row["reason"] == "pin_mismatch:google/gemini-3.5-flash"
    assert code != 0
    assert row["verdict"] != "WRONG"
    assert report["n"] == n_pack
    assert report["invalid"] == 1
    assert report["n_without_invalid"] == n_pack - 1
    assert report["wrong"] == 0
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
    assert report["cases"][1]["verdict"] == "ABSTAIN"
    row = report["cases"][0]
    assert row["verdict"] == "INVALID" and row["reason"] == "pin_mismatch:missing/missing"
    assert code != 0
    assert report["n"] == n_pack
    assert report["invalid"] == 1
    assert report["wrong"] == 0


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
    assert report["cases"][1]["verdict"] == "ABSTAIN"
    row = report["cases"][0]
    assert row["verdict"] == "INVALID" and row["reason"] == "pin_mismatch:google/gemini-3.5-flash"
    assert report["n"] == n_pack
    assert report["invalid"] == 1


def test_live_header_match_is_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _oracle_db(tmp_path)
    n_pack = len(_questions())
    via_header = {
        "status": 200,
        "headers": {
            "X-OpenVault-Served-Provider": _PROVIDER,
            "X-OpenVault-Served-Model": _PIN,
        },
        "body": {"served_provider": None, "served_model": None, "model": "gemini-3.5-flash"},
    }
    shots = [via_header for _ in range(n_pack + 1)]
    asks, script = _arm(monkeypatch, tmp_path, shots=shots)
    live("http://score.test", 1.0, db)
    report = _report(tmp_path)
    wire, hdr = _wire(script)
    assert report["oracle_as_of"] == _ENGINE_DAY
    assert report["cases"][0]["verdict"] == "ABSTAIN"
    assert (
        wire.get("strict") is True
        and wire.get("model") == _PIN
        and hdr.get("X-OpenVault-Strict") == "true"
    )
    assert len(asks) == n_pack
    assert len(script.calls) == n_pack + 1
    assert report["invalid"] == 0
    assert report["n"] == n_pack
    assert report["wrong"] == 0
    assert all(row["verdict"] != "INVALID" for row in report["cases"])


def test_live_body_mismatch_wins_over_matching_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    assert report["cases"][1]["verdict"] == "ABSTAIN"
    row = report["cases"][0]
    assert row["verdict"] == "INVALID" and row["reason"] == "pin_mismatch:google/gemini-3.5-flash"


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
    """Generate posts stamp the pin. A 503 stops that leg. No second post, no GET."""
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
    assert report["cases"][0]["verdict"] == "ABSTAIN"
    assert (
        body.get("strict") is True
        and body.get("model") == _PIN
        and gen_json.get("strict") is True
        and gen_headers.get("X-OpenVault-Strict") == "true"
    )
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
    assert sent["json"].get("strict") is True and sent["json"].get("model") == _PIN
    assert sent["headers"].get("X-OpenVault-Strict") == "true"
    assert "ov_test_pin_key" not in json.dumps(sent["json"])
    assert seen.get("preference") == "free+normal"
    assert seen.get("done") is True
    assert len(asks) == n_pack
