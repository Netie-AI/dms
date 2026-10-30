"""GRAIN-AI-OV-AUTH-01 (proposed F111, sibling of #481). The grain reader's bearer.

``DMS_GRAIN_AI=1`` maps a question to approved grains with two OpenVault
readings (``role=grain``). OpenVault's ``POST /v1/chat/completions`` accepts
only an issued ``ov_`` API key (``vault/auth.py resolve_caller``). The DMS
signing-service token is not one, so it is 401. Before this fix the reader
sent that token, and ``_one_reading`` turned the 401 into "not confident
enough": an auth failure reached the customer as model doubt.

The fake OpenVault below is a real HTTP server on loopback that enforces the
issued-key rule, so the real ``default_transport`` runs (no bound transport).
The pattern is ``tests/test_ao_ov_auth_01.py`` from #493.

Every answer-path assertion is on the customer envelope from
``POST /v1/chat/ask`` (rendered text, rows, ``assert_envelope_valid``).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_core.control_plane.onto_data import (
    ontology_data_store,
    reset_ontology_data_store,
    store_proposal,
)
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl

ISSUED_KEY = "ov_issuedgrainkey0001"
SERVICE_TOKEN = "dms-signing-service-token-grain-0001"
QUESTION = "What is the shipping cost by product and location?"
GRAINS = ("product", "location")
WHY_LOW = "the grain reading is not confident enough to choose"


class FakeOpenVault:
    """Loopback chat endpoint. ``mode`` is ``ok`` or ``forbid``."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.requests: list[dict[str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                auth = self.headers.get("Authorization") or ""
                outer.requests.append({"path": self.path, "auth": auth, "body": body})
                token = auth[len("Bearer ") :] if auth.startswith("Bearer ") else ""
                if outer.mode == "forbid":
                    self._send(403, {"error": {"type": "forbidden"}})
                elif token != ISSUED_KEY:
                    self._send(401, {"error": {"type": "openvault_unauthenticated"}})
                else:
                    content = json.dumps(_reading(body))
                    self._send(200, {"choices": [{"message": {"content": content}}]})

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _reading(body: dict[str, Any]) -> dict[str, Any]:
    """What a model would say: both grains, cited from the question it was sent."""
    user = json.loads(body["messages"][1]["content"])
    question = str(user["question"])
    evidence = []
    for grain in GRAINS:
        start = question.find(grain)
        assert start >= 0, grain
        evidence.append({"grain": grain, "start": start, "end": start + len(grain), "text": grain})
    return {
        "grains": list(GRAINS),
        "evidence": evidence,
        "confidence": 0.95,
        "reading": "shipping cost for each product at each location",
    }


class _Cortex:
    """Contract fake. Insights returns no plan; the contract ask is never wanted.

    ``submit`` runs the submitted SQL read-only on the test warehouse, as
    Cortex's execution would, so the rows on the envelope are real rows.
    """

    def __init__(self, warehouse: Path) -> None:
        self.warehouse = warehouse
        self.asks: list[AskRequest] = []
        self.submitted: list[str] = []

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any] | None:
        return {"answer": "not a plan"}

    def submit(self, req: Any) -> QueryResult:
        body = req.body if isinstance(req.body, dict) else {}
        if "sql" not in body:
            # bind_session: the session bind carries no SQL.
            return QueryResult(ok=True, status="bound", run_id="run_grain_auth_bind")
        sql = str(body["sql"])
        self.submitted.append(sql)
        con = connect_file(self.warehouse)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_grain_auth", output={"rows": rows})

    def ledger_append(self, _req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_grain_auth", hash="hash_grain_auth")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        raise AssertionError("the grain lane must not fall through to the contract ask")


@pytest.fixture()
def minter() -> ManifestMinter:
    m = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-30T00:00:00+00:00",
            expires_at="2026-10-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    setattr(m, "mint_manifest", _mint)
    setattr(m, "fetch_intermediate", lambda: None)
    setattr(m, "close", lambda: None)
    setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


@pytest.fixture()
def ov() -> Iterator[FakeOpenVault]:
    fake = FakeOpenVault()
    fake.start()
    try:
        yield fake
    finally:
        fake.stop()


@pytest.fixture(autouse=True)
def _approved_grains() -> Iterator[None]:
    """Approve two grains that name demo-ontology objects. Nothing else."""
    reset_ontology_data_store()
    stored = store_proposal(
        {
            "status": "proposed",
            "served": False,
            "vendor_sha": "grain-auth-fixture",
            "terms": [],
            "grains": [{"name": name, "object": name} for name in GRAINS],
        },
        created_by="steward",
    )
    store = ontology_data_store()
    for item in stored["items"]:
        store.approve(item["id"], actor="steward")
    yield
    reset_ontology_data_store()


def _env(
    monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault, *, cortex_key: str, ov_key: str | None
) -> None:
    # The app's startup probe would find a real OpenVault on a default port
    # and fetch a signing key there. This test talks only to the loopback fake.
    monkeypatch.setattr("dms_executor.probe_openvault", lambda **_k: (None, "offline in test"))
    monkeypatch.setenv("DMS_GRAIN_AI", "1")
    monkeypatch.setenv("DMS_ASK_MODE", "live")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    monkeypatch.setenv("OPENVAULT_URL", ov.url)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setenv("CORTEX_API_KEY", cortex_key)
    monkeypatch.delenv("OPENVAULT_API_KEY", raising=False)
    # The credential the defect sent. Chat does not accept it.
    monkeypatch.setenv("DMS_OV_SERVICE_TOKEN", SERVICE_TOKEN)
    if ov_key is not None:
        monkeypatch.setenv("OPENVAULT_API_KEY", ov_key)


def _chat_ask(minter: ManifestMinter, tmp_path: Path) -> tuple[dict[str, Any], _Cortex]:
    from dms_api import settings as settings_mod
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    warehouse = tmp_path / "grain_auth.duckdb"
    ensure_demo_warehouse(warehouse)
    cortex = _Cortex(warehouse)
    settings_mod.get_settings.cache_clear()
    try:
        app = create_app()
        app.state.ask_service = Executor(  # type: ignore[arg-type]
            cortex=cortex, minter=minter, warehouse_path=warehouse
        )
        app.state.cortex = cortex
        res = TestClient(app).post(
            "/v1/chat/ask", json={"question": QUESTION, "session_id": "ses_grain_auth"}
        )
    finally:
        settings_mod.get_settings.cache_clear()
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body, dict)
    return body, cortex


def _assert_named_refusal(body: dict[str, Any], code: str) -> None:
    assert_envelope_valid(body)
    assert body["badge"] == "ABSTAIN"
    assert body["abstained"] is True
    assert body["rows"] == []
    assert body["values"] == []
    assert not body.get("sql_used")
    assert body.get("abstain_reason") == code
    text = str(body.get("text") or "")
    assert "OpenVault" in text
    assert WHY_LOW not in text
    # Not the grain reconfirm (#464): no Yes, no "not confident" offer.
    assert "reconfirm_yes" not in body
    offer = body.get("reconfirm")
    if isinstance(offer, dict):
        assert offer.get("reason_code") == code
        assert WHY_LOW not in json.dumps(offer)
    blob = json.dumps(body)
    assert SERVICE_TOKEN not in blob
    assert ISSUED_KEY not in blob


def test_issued_key_answers_grouped_by_the_model_named_grains(
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    ov: FakeOpenVault,
    tmp_path: Path,
) -> None:
    _env(monkeypatch, ov, cortex_key="fake-steward-key-grain-auth", ov_key=ISSUED_KEY)
    body, cortex = _chat_ask(minter, tmp_path)
    assert cortex.asks == []
    assert_envelope_valid(body)
    assert body["badge"] == "L2_VALIDATED"
    assert body["abstained"] is False
    assert body.get("abstain_reason") is None
    rows = body["rows"]
    assert rows
    # Grouped by both grains the model named: one row per location x product.
    keys = {(row["location_location_id"], row["product_sku"]) for row in rows}
    assert len(keys) == len(rows)
    # The model named product and location; the compile walked to exactly those.
    targets = {p.get("target") for p in (body.get("where_paths") or [])}
    assert targets == set(GRAINS)
    assert {key for row in rows for key in row} == {
        "product_sku",
        "location_location_id",
        "shipping_cost_myr",
    }
    # Same numbers as an independent read of the warehouse.
    con = connect_file(tmp_path / "grain_auth.duckdb")
    try:
        expected = {
            (loc, sku, float(cost))
            for loc, sku, cost in con.execute(
                "SELECT destination_location_id, sku, ROUND(SUM(cost_myr), 2) "
                "FROM shipments GROUP BY 1, 2"
            ).fetchall()
        }
    finally:
        con.close()
    got = {
        (r["location_location_id"], r["product_sku"], float(r["shipping_cost_myr"])) for r in rows
    }
    assert got == expected
    # The rendered answer names every group and its figure.
    text = str(body.get("text") or "")
    for row in rows:
        assert f"location_location_id={row['location_location_id']}" in text
        assert f"product_sku={row['product_sku']}" in text
        assert f"shipping_cost_myr={row['shipping_cost_myr']}" in text
    # Exactly the two readings, each with the issued key and role=grain.
    assert len(ov.requests) == 2
    for call in ov.requests:
        assert call["path"] == "/v1/chat/completions"
        assert call["auth"] == f"Bearer {ISSUED_KEY}"
        assert SERVICE_TOKEN not in call["auth"]
        assert call["body"]["role"] == "grain"
        assert "model" not in call["body"]
    assert [call["body"]["reading"] for call in ov.requests] == [1, 2]


def test_cortex_api_key_is_the_fallback_credential(
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    ov: FakeOpenVault,
    tmp_path: Path,
) -> None:
    _env(monkeypatch, ov, cortex_key=ISSUED_KEY, ov_key=None)
    body, _cortex = _chat_ask(minter, tmp_path)
    assert_envelope_valid(body)
    assert body["abstained"] is False
    assert body["rows"]
    assert {call["auth"] for call in ov.requests} == {f"Bearer {ISSUED_KEY}"}


def test_service_token_alone_makes_no_call_and_is_a_named_refusal(
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    ov: FakeOpenVault,
    tmp_path: Path,
) -> None:
    _env(monkeypatch, ov, cortex_key="fake-steward-key-grain-auth", ov_key=None)
    body, cortex = _chat_ask(minter, tmp_path)
    assert ov.requests == []
    assert cortex.asks == []
    _assert_named_refusal(body, "ov_api_key_missing")


def test_unissued_key_gets_401_one_call_and_a_named_refusal(
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    ov: FakeOpenVault,
    tmp_path: Path,
) -> None:
    _env(monkeypatch, ov, cortex_key="fake-steward-key-grain-auth", ov_key="ov_notissued")
    body, _cortex = _chat_ask(minter, tmp_path)
    # One 401 stops the readings. The second is not sent.
    assert len(ov.requests) == 1
    assert ov.requests[0]["auth"] == "Bearer ov_notissued"
    _assert_named_refusal(body, "ov_unauthorized")
    assert "ov_notissued" not in json.dumps(body)


def test_403_is_a_named_refusal(
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    ov: FakeOpenVault,
    tmp_path: Path,
) -> None:
    ov.mode = "forbid"
    _env(monkeypatch, ov, cortex_key="fake-steward-key-grain-auth", ov_key=ISSUED_KEY)
    body, _cortex = _chat_ask(minter, tmp_path)
    assert len(ov.requests) == 1
    _assert_named_refusal(body, "ov_unauthorized")


def test_unreachable_openvault_is_a_named_refusal(
    minter: ManifestMinter,
    monkeypatch: pytest.MonkeyPatch,
    ov: FakeOpenVault,
    tmp_path: Path,
) -> None:
    _env(monkeypatch, ov, cortex_key="fake-steward-key-grain-auth", ov_key=ISSUED_KEY)
    ov.stop()
    body, _cortex = _chat_ask(minter, tmp_path)
    _assert_named_refusal(body, "ov_unreachable")


@pytest.mark.parametrize(
    "value",
    ["dms-demo-viewer-key", SERVICE_TOKEN, "", "  ", "OV_upper"],
)
def test_load_api_key_skips_anything_that_is_not_an_ov_key(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    from dms_executor.grain_ai import load_api_key

    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.setenv("DMS_OV_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("OPENVAULT_API_KEY", value)
    assert load_api_key() == ""
    monkeypatch.setenv("CORTEX_API_KEY", value)
    assert load_api_key() == ""
    monkeypatch.setenv("CORTEX_API_KEY", "ov_real")
    assert load_api_key() == "ov_real"
    monkeypatch.setenv("OPENVAULT_API_KEY", "ov_first")
    assert load_api_key() == "ov_first"
