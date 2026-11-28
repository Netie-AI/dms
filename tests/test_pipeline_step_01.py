"""FEAT-PIPELINE-STEP-01. Flag off is a 404. HMAC, registration, and import.

The ask envelope, prompt bytes, and served route stay the same with the flag
unset, set to 0, or set to 1. Synthetic question text only. No held-out pack.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest
from cortex_client.gate import ComplianceDecision
from dms_airflow.bench import DmsBenchmarkError
from dms_airflow.hook import DmsHook
from dms_airflow.operators import DmsBenchmarkOperator, DmsSemanticSyncOperator
from dms_airflow.run import run_ask_then_benchmark
from dms_api.app import create_app
from dms_api.routes.webhooks import enabled
from dms_api.settings import get_settings
from dms_core.control_plane.webhook_sign import (
    build_payload,
    canonical_json,
    outcome_for_envelope,
    signature_header,
    verify_body,
)
from dms_executor.sql_vote import plan_prompt, reading_prompt
from dms_sdk.cli import main as cli_main
from dms_sdk.client import DmsClient
from dms_sdk.outcome import OUTCOMES, typed_outcome
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
FLAG = "DMS_PIPELINE_HOOKS"
SPACE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
QUESTION = "What was total revenue?"
_SECRET = b"synthetic-hook-secret"
_REF = "dms/hookkid"
_URL = "https://hooks.example.test/dms"
_VOLATILE = frozenset(
    {
        "as_of",
        "ticket_id",
        "audit_id",
        "engine_as_of",
        "engine_as_of_after",
        "engine_timezone",
        "engine_timezone_after",
    }
)
_ROUTES = (
    ("post", "/v1/pipeline/webhooks"),
    ("get", "/v1/pipeline/webhooks"),
    ("post", "/v1/pipeline/bench"),
    ("post", "/v1/pipeline/semantic/import"),
    ("get", "/v1/pipeline/proof"),
)


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool | None) -> None:
    if on is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, "1" if on else "0")
    get_settings.cache_clear()


def _allow(*_args: object, **kwargs: object) -> ComplianceDecision:
    action = str(kwargs.get("action") or "pipeline")
    return ComplianceDecision(allowed=True, reason="allowed", action=action)


def _client(monkeypatch: pytest.MonkeyPatch, *, on: bool, demo: bool = False) -> TestClient:
    _flag(monkeypatch, on)
    if demo:
        monkeypatch.setenv("DMS_ASK_MODE", "demo")
        get_settings.cache_clear()
    monkeypatch.setattr("dms_api.routes.webhooks.compliance_gate", _allow)
    return TestClient(create_app())


def _prompt_bytes() -> bytes:
    plan = plan_prompt(QUESTION, '{"tables":["t"]}')
    reading = reading_prompt(QUESTION, reading=1)
    return (plan + "\n" + reading).encode("utf-8")


def _stable(body: dict) -> dict:
    return {key: value for key, value in body.items() if key not in _VOLATILE}


def _ask(client: TestClient) -> dict:
    res = client.post("/v1/chat/ask", json={"question": QUESTION, "space_id": SPACE})
    assert res.status_code == 200
    return res.json()


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, None)
    assert enabled() is False
    _flag(monkeypatch, False)
    assert enabled() is False
    _flag(monkeypatch, True)
    assert enabled() is True


def test_flag_off_webhook_routes_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, on=False)
    for method, path in _ROUTES:
        res = client.request(method, path, json={"space_id": SPACE} if method == "post" else None)
        assert res.status_code == 404
        assert res.json() == {"detail": "Not Found"}


def test_flag_off_is_byte_identical(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prompt bytes, the ask envelope, and the served route do not branch."""
    monkeypatch.delenv("DMS_ASK_TIMINGS", raising=False)
    monkeypatch.setenv("DMS_ASK_MODE", "demo")

    def snap() -> tuple[bytes, set[str], dict, dict, dict]:
        app = create_app()
        spec = app.openapi()
        client = TestClient(app)
        return (
            _prompt_bytes(),
            set(spec["paths"]),
            spec["paths"]["/v1/chat/ask"],
            client.get("/health").json(),
            _stable(_ask(client)),
        )

    _flag(monkeypatch, None)
    unset_prompt, unset_paths, unset_ask, unset_health, unset_env = snap()
    _flag(monkeypatch, False)
    off_prompt, off_paths, off_ask, off_health, off_env = snap()
    assert off_prompt == unset_prompt
    assert off_paths == unset_paths
    assert off_ask == unset_ask
    assert off_health == unset_health
    assert off_env == unset_env
    _flag(monkeypatch, True)
    on_prompt, on_paths, on_ask, on_health, on_env = snap()
    assert on_prompt == unset_prompt
    assert on_ask == unset_ask
    assert on_health == unset_health
    assert on_env == unset_env
    assert on_env.get("route") == unset_env.get("route")
    assert "/v1/pipeline/webhooks" in on_paths
    assert "/v1/pipeline/webhooks" not in unset_paths


def test_hmac_verifies_and_a_changed_body_fails() -> None:
    payload = build_payload(
        event="answer.served",
        space_id=SPACE,
        outcome="served",
        route="l1_governed_metric",
        audit_id="audit-synthetic",
        run_id=None,
    )
    assert "values" not in payload
    raw = canonical_json(payload)
    signature = signature_header(_SECRET, raw)
    assert verify_body(_SECRET, raw, signature) is True
    flipped = bytearray(raw)
    flipped[-1] ^= 0x01
    assert verify_body(_SECRET, bytes(flipped), signature) is False
    last = "0" if signature[-1] != "0" else "1"
    assert verify_body(_SECRET, raw, signature[:-1] + last) is False


def test_register_stores_the_reference_and_signs_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch, on=True)
    captured: dict[str, object] = {}

    def poster(url: str, body: bytes, headers: dict[str, str]) -> None:
        captured["url"] = url
        captured["body"] = body
        captured["signature"] = headers["X-Dms-Signature"]

    client.app.state.webhook_resolver = lambda _ref: _SECRET
    client.app.state.webhook_poster = poster
    created = client.post(
        "/v1/pipeline/webhooks",
        json={
            "space_id": SPACE,
            "url": _URL,
            "secret_ref": _REF,
            "events": ["benchmark.finished"],
            "include_values": False,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["secret_ref"] == _REF
    assert "secret" not in body
    assert _SECRET.decode() not in created.text

    bench = client.post("/v1/pipeline/bench", json={"space_id": SPACE})
    assert bench.status_code == 200
    report = bench.json()
    assert report["code"] == "benchmark_not_configured"
    assert report["wrong"] is None
    assert report["cases"] == 0
    raw = captured["body"]
    assert isinstance(raw, bytes)
    signed = json.loads(raw)
    assert "values" not in signed
    assert signed["outcome"] == "error"
    assert signed["event"] == "benchmark.finished"
    assert verify_body(_SECRET, raw, str(captured["signature"])) is True
    mutated = bytearray(raw)
    mutated[0] ^= 0x01
    assert verify_body(_SECRET, bytes(mutated), str(captured["signature"])) is False


def test_include_values_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, on=True)
    captured: list[bytes] = []
    client.app.state.webhook_resolver = lambda _ref: _SECRET
    client.app.state.webhook_poster = lambda _url, body, _headers: captured.append(body)
    res = client.post(
        "/v1/pipeline/webhooks",
        json={
            "space_id": SPACE,
            "url": _URL,
            "secret_ref": _REF,
            "events": ["answer.served"],
            "include_values": True,
        },
    )
    assert res.status_code == 201
    from dms_api.routes.webhooks import dispatch

    dispatch(
        client.app,
        event="answer.served",
        space_id=SPACE,
        outcome="served",
        route="l1_governed_metric",
        values=[{"n": 1}],
    )
    assert captured
    signed = json.loads(captured[-1])
    assert signed["values"] == [{"n": 1}]
    proof = client.get("/v1/pipeline/proof", params={"space_id": SPACE})
    assert proof.status_code == 200
    assert "values" not in proof.json()


def test_raw_secret_field_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, on=True)
    marker = "raw-secret-should-not-echo"
    res = client.post(
        "/v1/pipeline/webhooks",
        json={
            "space_id": SPACE,
            "url": _URL,
            "secret_ref": _REF,
            "events": ["answer.served"],
            "secret": marker,
        },
    )
    assert res.status_code == 422
    assert marker not in res.text
    bad = client.post(
        "/v1/pipeline/webhooks",
        json={
            "space_id": SPACE,
            "url": _URL,
            "secret_ref": "https://user:" + marker + "@vault.example/key",
            "events": ["answer.served"],
        },
    )
    assert bad.status_code == 400
    assert bad.json()["detail"] == "bad_secret_ref"
    assert marker not in bad.text


def test_gate_unavailable_does_not_register(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag(monkeypatch, True)
    client = TestClient(create_app())
    res = client.post(
        "/v1/pipeline/webhooks",
        json={
            "space_id": SPACE,
            "url": _URL,
            "secret_ref": _REF,
            "events": ["answer.served"],
        },
    )
    assert res.status_code == 403
    assert res.json()["detail"] == "gate_unavailable"


def test_semantic_import_is_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, on=True)
    dry = client.post(
        "/v1/pipeline/semantic/import",
        json={"space_id": SPACE, "dry_run": True},
    )
    assert dry.status_code == 200
    assert dry.json()["dry_run"] is True
    assert dry.json()["outcome"] == "served"
    live = client.post(
        "/v1/pipeline/semantic/import",
        json={"space_id": SPACE, "dry_run": False, "document": {"models": []}},
    )
    assert live.status_code == 501
    assert live.json()["detail"]["code"] == "semantic_import_not_implemented"


def test_migration_stores_a_reference_and_not_a_secret() -> None:
    text = (ROOT / "alembic" / "versions" / "0011_webhooks.py").read_text(encoding="utf-8")
    assert 'revision: str = "0011_webhooks"' in text
    assert 'down_revision: str | None = "0010_connections"' in text
    assert "secret_ref" in text
    assert "include_values" in text
    folded = text.lower()
    assert "secret text" not in folded
    assert "secret bytea" not in folded
    assert "row_values" not in folded


def test_new_packages_do_not_name_a_provider() -> None:
    from dms_sdk.client import _FORBIDDEN

    assert {"model", "provider", "model_id", "provider_id"} <= _FORBIDDEN
    banned = ("gpt-", "claude-", "openai", "anthropic", "cortexos")
    roots = (ROOT / "packages" / "dms_sdk", ROOT / "packages" / "dms_airflow")
    for root in roots:
        assert root.is_dir()
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8").lower()
            for token in banned:
                assert token not in source
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
            for name in modules:
                assert not name.lower().startswith("cortexos")


def test_outcome_maps_agree() -> None:
    samples = (
        {"route": "needs_clarification", "clarify": {"id": "c"}},
        {"reconfirm": {"why": "held"}},
        {"badge": "ABSTAIN", "abstained": True, "route": "abstain"},
        {"badge": "L2_VALIDATED", "route": "rule", "values": [1]},
        {"outcome": "error", "route": None},
        {},
    )
    for sample in samples:
        assert typed_outcome(sample) == outcome_for_envelope(sample)


def test_sdk_ask_matches_http_and_sends_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    http = _client(monkeypatch, on=False, demo=True)
    recorded: list[dict | None] = []
    original = http.request

    def wrapped(method: str, url: str, **kwargs: object) -> httpx.Response:
        body = kwargs.get("json")
        recorded.append(body if isinstance(body, dict) else None)
        return original(method, url, **kwargs)

    http.request = wrapped  # type: ignore[method-assign]
    sdk = DmsClient(base_url="http://testserver", client=http)
    got = sdk.ask(space_id=SPACE, question=QUESTION)
    direct = http.post("/v1/chat/ask", json={"question": QUESTION, "space_id": SPACE})
    assert recorded and recorded[0] is not None
    assert set(recorded[0]) <= {"question", "space_id", "session_id"}
    assert "model" not in recorded[0]
    assert "provider" not in recorded[0]
    raw = direct.json()
    assert direct.status_code == 200
    stamped = _stable(dict(raw))
    stamped["outcome"] = typed_outcome(raw)
    assert _stable(got) == stamped
    assert got["outcome"] == "served"
    assert "route" in got
    assert got["route"] == raw.get("route")


def test_sdk_post_is_not_retried_and_get_is() -> None:
    calls = {"post": 0, "get": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["post"] += 1
            raise httpx.ConnectError("down")
        calls["get"] += 1
        if calls["get"] < 3:
            return httpx.Response(503, json={"detail": "busy"})
        return httpx.Response(
            200,
            json={"outcome": "served", "route": "pipeline.proof", "space_id": SPACE},
        )

    http = httpx.Client(transport=httpx.MockTransport(handle), base_url="http://dms.test")
    sdk = DmsClient(base_url="http://dms.test", client=http, sleeper=lambda _delay: None)
    ask = sdk.ask(space_id=SPACE, question=QUESTION)
    assert calls["post"] == 1
    assert ask["outcome"] == "error"
    assert ask["code"] == "transport"
    proof = sdk.proof_fetch(space_id=SPACE)
    assert calls["get"] == 3
    assert proof["outcome"] == "served"
    assert proof["route"] == "pipeline.proof"


def test_sdk_dry_run_does_not_post() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("dry run posted")

    http = httpx.Client(transport=httpx.MockTransport(handle), base_url="http://dms.test")
    sdk = DmsClient(base_url="http://dms.test", client=http)
    body = sdk.semantic_import(space_id=SPACE, document={"models": []}, dry_run=True)
    assert body == {
        "outcome": "served",
        "route": "pipeline.semantic_import",
        "dry_run": True,
        "space_id": SPACE,
    }


class _Scripted:
    def __init__(self, body: dict) -> None:
        self.body = body

    def ask(self, *, space_id: str, question: str, session_id: str | None = None) -> dict:
        del space_id, question, session_id
        return dict(self.body)

    def benchmark_run(self, *, space_id: str) -> dict:
        del space_id
        return dict(self.body)

    def semantic_import(
        self,
        *,
        space_id: str,
        document: dict | None = None,
        dry_run: bool = True,
    ) -> dict:
        del space_id, document
        out = dict(self.body)
        out["dry_run"] = dry_run
        return out

    def close(self) -> None:
        return None


def test_cli_json_schema_and_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    cases = {
        "served": 0,
        "clarify": 2,
        "reconfirm": 3,
        "refused": 4,
        "error": 1,
    }
    for outcome, code in cases.items():
        body = {"outcome": outcome, "route": "rule", "wrong": 1}

        def factory(base_url: str, *, _body: dict = body) -> _Scripted:
            del base_url
            return _Scripted(_body)

        status = cli_main(
            ["ask", "--space", SPACE, "--question", QUESTION, "--json"],
            client_factory=factory,
        )
        printed = json.loads(capsys.readouterr().out)
        assert status == code
        assert printed["outcome"] == outcome
        assert printed["route"] == "rule"
        assert set(OUTCOMES) >= {printed["outcome"]}


def test_cli_exit_codes_ignore_wrong_count(capsys: pytest.CaptureFixture[str]) -> None:
    """A served envelope with wrong=1 still exits 0. The count is not a verdict."""

    def factory(base_url: str) -> _Scripted:
        del base_url
        return _Scripted({"outcome": "served", "route": "rule", "wrong": 1})

    status = cli_main(
        ["bench", "run", "--space", SPACE, "--json"],
        client_factory=factory,
    )
    printed = json.loads(capsys.readouterr().out)
    assert status == 0
    assert printed["wrong"] == 1
    assert printed["outcome"] == "served"


def test_cli_import_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "osi_document.json"
    path.write_text('{"models":[]}', encoding="utf-8")
    seen: dict[str, bool] = {}

    def factory(base_url: str) -> _Scripted:
        del base_url

        class _Import(_Scripted):
            def semantic_import(
                self,
                *,
                space_id: str,
                document: dict | None = None,
                dry_run: bool = True,
            ) -> dict:
                del space_id, document
                seen["dry_run"] = dry_run
                return {
                    "outcome": "served",
                    "route": "pipeline.semantic_import",
                    "dry_run": dry_run,
                }

        return _Import({})

    status = cli_main(
        ["import", "--space", SPACE, "--file", str(path), "--dry-run", "--json"],
        client_factory=factory,
    )
    printed = json.loads(capsys.readouterr().out)
    assert status == 0
    assert seen["dry_run"] is True
    assert printed["route"] == "pipeline.semantic_import"
    assert "models" not in printed


def test_benchmark_operator_wrong_count(monkeypatch: pytest.MonkeyPatch) -> None:
    def install(wrong: int) -> None:
        class _Client:
            def benchmark_run(self, *, space_id: str) -> dict:
                del space_id
                return {"wrong": wrong, "outcome": "served", "route": "pipeline.bench"}

            def close(self) -> None:
                return None

        monkeypatch.setattr(
            "dms_airflow.operators.DmsHook.get_client",
            lambda self: _Client(),
        )

    install(1)
    op = DmsBenchmarkOperator(task_id="bench", conn_id="dms_default", space_id=SPACE)
    with pytest.raises(DmsBenchmarkError) as raised:
        op.execute({})
    assert raised.value.code == "benchmark_wrong"
    install(0)
    assert op.execute({})["wrong"] == 0


def test_semantic_sync_operator_is_not_implemented() -> None:
    op = DmsSemanticSyncOperator(task_id="sync", conn_id="dms_default", space_id=SPACE)
    with pytest.raises(NotImplementedError, match="FEAT-SEMANTIC-IMPORT-01"):
        op.execute({})


def test_hook_reads_the_connection_password(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Conn:
        host = "dms.example.test"
        schema = "https"
        port = None
        password = "from-the-connection"

    monkeypatch.setattr(DmsHook, "get_connection", lambda self: _Conn())
    client = DmsHook("dms_default").get_client()
    try:
        assert client._client.headers["Authorization"] == "Bearer from-the-connection"
    finally:
        client.close()
    source = (ROOT / "packages" / "dms_airflow" / "dms_airflow" / "hook.py").read_text(
        encoding="utf-8"
    )
    assert "from-the-connection" not in source


def test_synthetic_dag_seconds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    http = _client(monkeypatch, on=True, demo=True)
    sdk = DmsClient(base_url="http://testserver", client=http)
    result = run_ask_then_benchmark(sdk, space_id=SPACE, question=QUESTION)
    assert result["ask_outcome"] == "served"
    assert "ask_route" in result
    assert result["bench_code"] == "benchmark_not_configured"
    assert result["benchmark_failed"] is True
    assert result["seconds"] >= 0
    print(f"pipeline_dag_seconds={result['seconds']:.3f}")
    captured = capsys.readouterr()
    assert "pipeline_dag_seconds=" in captured.out
