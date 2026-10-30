"""AO-OV-AUTH-01 (#481). onto_compile refuses on a model-call failure and stores nothing.

The fake OpenVault below is a real HTTP server on loopback. It enforces what
OpenVault main enforces on ``POST /v1/chat/completions`` with
``OPENVAULT_REQUIRE_API_KEY=1`` (``vault/auth.py resolve_caller``): the bearer
must be an issued ``ov_`` key. A signing-service token is not one, so it is
401. That exercises the real ``default_transport``, not an injected one.
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_core.control_plane.onto_data import OntologyDataStore
from test_ao_vendor_01 import _recorded

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

import onto_compile  # noqa: E402

ISSUED_KEY = "ov_issuedkey0001"
SERVICE_TOKEN = "dms-signing-service-token-0001"


class FakeOpenVault:
    """Loopback chat endpoint. ``mode`` is ``ok``, ``forbid``, or ``garbage``."""

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
                elif outer.mode == "garbage":
                    self._send(200, {"choices": [{"message": {"content": '{"nope": 1}'}}]})
                else:
                    content = json.dumps(_recorded(body))
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


class SpyStore(OntologyDataStore):
    def __init__(self) -> None:
        super().__init__()
        self.stored = 0

    def store_proposal(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.stored += 1
        return super().store_proposal(*args, **kwargs)


@pytest.fixture
def ov() -> Iterator[FakeOpenVault]:
    fake = FakeOpenVault()
    fake.start()
    try:
        yield fake
    finally:
        fake.stop()


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "prove.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name VARCHAR)")
    conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, sku VARCHAR)")
    conn.execute(
        "CREATE TABLE orders ("
        "id INTEGER PRIMARY KEY, "
        "customer_id INTEGER, "
        "product_id INTEGER, "
        "qty INTEGER, "
        "FOREIGN KEY (product_id) REFERENCES products(id))"
    )
    conn.execute("INSERT INTO customers VALUES (1, 'ada'), (2, 'bea')")
    conn.execute("INSERT INTO products VALUES (10, 'sku-a')")
    conn.execute("INSERT INTO orders VALUES (100, 1, 10, 3), (101, 1, 10, 1)")
    conn.close()
    return path


def _env(
    monkeypatch: pytest.MonkeyPatch,
    ov: FakeOpenVault,
    *,
    key: str | None,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DMS_AO_COMPILE", "1")
    monkeypatch.setenv("OPENVAULT_URL", ov.url)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.delenv("OPENVAULT_API_KEY", raising=False)
    # The credential the issue's run had. Chat does not accept it.
    token_file = tmp_path / "ov_service_token"
    token_file.write_text(SERVICE_TOKEN, encoding="utf-8")
    monkeypatch.setenv("DMS_OV_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("DMS_OV_SERVICE_TOKEN_FILE", str(token_file))
    if key is not None:
        monkeypatch.setenv("OPENVAULT_API_KEY", key)


def _refused(warehouse: Path, store: SpyStore) -> str:
    with pytest.raises(SystemExit) as info:
        onto_compile.compile_warehouse(warehouse, str(uuid.uuid4()), store=store)
    return str(info.value.code)


def _assert_nothing_stored(store: SpyStore) -> None:
    assert store.stored == 0
    assert store._versions == {}
    assert store._items == {}


def test_service_token_alone_is_refused_by_name_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault, warehouse: Path, tmp_path: Path
) -> None:
    _env(monkeypatch, ov, key=None, tmp_path=tmp_path)
    store = SpyStore()
    message = _refused(warehouse, store)
    assert message == "onto_compile: REFUSED ao_ov_api_key_missing"
    assert ov.requests == []
    _assert_nothing_stored(store)


def test_unissued_key_gets_401_refuses_and_stores_nothing(
    monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault, warehouse: Path, tmp_path: Path
) -> None:
    _env(monkeypatch, ov, key="ov_notissued", tmp_path=tmp_path)
    store = SpyStore()
    message = _refused(warehouse, store)
    assert message == "onto_compile: REFUSED ao_ov_unauthorized"
    assert "ov_notissued" not in message
    # One 401 stops the sends. The issue's run made 42 failing calls.
    assert len(ov.requests) == 1
    _assert_nothing_stored(store)


def test_403_refuses_as_unauthorized_and_stores_nothing(
    monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault, warehouse: Path, tmp_path: Path
) -> None:
    ov.mode = "forbid"
    _env(monkeypatch, ov, key=ISSUED_KEY, tmp_path=tmp_path)
    store = SpyStore()
    assert _refused(warehouse, store) == "onto_compile: REFUSED ao_ov_unauthorized"
    _assert_nothing_stored(store)


def test_unreachable_openvault_refuses_and_stores_nothing(
    monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault, warehouse: Path, tmp_path: Path
) -> None:
    _env(monkeypatch, ov, key=ISSUED_KEY, tmp_path=tmp_path)
    ov.stop()
    store = SpyStore()
    assert _refused(warehouse, store) == "onto_compile: REFUSED ao_ov_unreachable"
    _assert_nothing_stored(store)


def test_a_200_whose_output_does_not_parse_refuses_and_stores_nothing(
    monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault, warehouse: Path, tmp_path: Path
) -> None:
    ov.mode = "garbage"
    _env(monkeypatch, ov, key=ISSUED_KEY, tmp_path=tmp_path)
    store = SpyStore()
    assert _refused(warehouse, store) == "onto_compile: REFUSED ao_model_output_invalid"
    assert ov.requests
    _assert_nothing_stored(store)


def test_issued_key_compiles_and_stores_an_ai_proposal(
    monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault, warehouse: Path, tmp_path: Path
) -> None:
    _env(monkeypatch, ov, key=ISSUED_KEY, tmp_path=tmp_path)
    store = SpyStore()
    result = onto_compile.compile_warehouse(
        warehouse, str(uuid.uuid4()), store=store, dest=tmp_path / "onto_proposal.json"
    )
    assert store.stored == 1
    assert result["status"] == "proposed"
    assert result["served"] is False
    assert result["counts"]["object"] == 3
    assert result["counts"]["link"] >= 1
    assert {row["name"] for row in result["proposal"]["terms"]} == {
        "Customer",
        "Order",
        "Product",
    }
    # The AI output is in the proposal, and the count is the calls that made it.
    assert result["model_calls"] == len(ov.requests) > 0
    written = json.loads((tmp_path / "onto_proposal.json").read_text(encoding="utf-8"))
    assert written["model_calls"] == result["model_calls"]
    for call in ov.requests:
        assert call["path"] == "/v1/chat/completions"
        assert call["auth"] == f"Bearer {ISSUED_KEY}"
        assert SERVICE_TOKEN not in call["auth"]
        assert call["body"]["role"] == "ontology"
        assert "model" not in call["body"]


def test_cortex_api_key_is_the_fallback_credential(
    monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault, warehouse: Path, tmp_path: Path
) -> None:
    _env(monkeypatch, ov, key=None, tmp_path=tmp_path)
    monkeypatch.setenv("CORTEX_API_KEY", ISSUED_KEY)
    store = SpyStore()
    result = onto_compile.compile_warehouse(warehouse, str(uuid.uuid4()), store=store)
    assert store.stored == 1
    assert result["model_calls"] > 0
    assert {call["auth"] for call in ov.requests} == {f"Bearer {ISSUED_KEY}"}


def test_a_caller_that_swallows_the_error_cannot_hide_a_failure(
    monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault, warehouse: Path, tmp_path: Path
) -> None:
    """``deterministic.py`` wraps its call in ``except Exception``. The compile still refuses."""
    from ao_vendor.llm.ov_client import AoLlmError

    _env(monkeypatch, ov, key=ISSUED_KEY, tmp_path=tmp_path)
    calls: list[str] = []

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        calls.append(body["schema"])
        if body["schema"] == "ColumnDescriptionResult":
            raise AoLlmError("ao_ov_unauthorized")
        return _recorded(body)

    store = SpyStore()
    with pytest.raises(SystemExit) as info:
        onto_compile.compile_warehouse(
            warehouse, str(uuid.uuid4()), transport=transport, store=store
        )
    assert str(info.value.code) == "onto_compile: REFUSED ao_ov_unauthorized"
    assert calls.count("ColumnDescriptionResult") == 1
    _assert_nothing_stored(store)


def test_a_compile_with_tables_and_no_model_call_is_refused(
    monkeypatch: pytest.MonkeyPatch, ov: FakeOpenVault, warehouse: Path, tmp_path: Path
) -> None:
    from ao_vendor import compile_run
    from ao_vendor.semantic.visit_enter import CompiledTable

    _env(monkeypatch, ov, key=ISSUED_KEY, tmp_path=tmp_path)
    monkeypatch.setattr(compile_run, "process_table", lambda table, ctx, conn=None: CompiledTable())
    store = SpyStore()
    assert _refused(warehouse, store) == "onto_compile: REFUSED ao_no_model_calls"
    _assert_nothing_stored(store)


def test_cli_exits_non_zero_names_the_refusal_and_writes_no_proposal(
    monkeypatch: pytest.MonkeyPatch,
    ov: FakeOpenVault,
    warehouse: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _env(monkeypatch, ov, key="ov_notissued", tmp_path=tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused:unused@127.0.0.1:1/unused")
    monkeypatch.setenv("DMS_ONTO_PG", "1")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "run"))
    with pytest.raises(SystemExit) as info:
        onto_compile.main(["--warehouse", str(warehouse), "--tenant", str(uuid.uuid4())])
    assert info.value.code == "onto_compile: REFUSED ao_ov_unauthorized"
    assert not (tmp_path / "run" / "grade52" / "onto_proposal.json").exists()
    out = capsys.readouterr()
    assert "version_id" not in out.out
    assert "ov_notissued" not in out.out + out.err
    assert SERVICE_TOKEN not in out.out + out.err


@pytest.mark.parametrize(
    "value",
    ["dms-demo-viewer-key", SERVICE_TOKEN, "", "  ", "OV_upper"],
)
def test_load_api_key_skips_anything_that_is_not_an_ov_key(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    from ao_vendor.llm.ov_client import load_api_key

    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.setenv("OPENVAULT_API_KEY", value)
    assert load_api_key() == ""
    monkeypatch.setenv("CORTEX_API_KEY", value)
    assert load_api_key() == ""
    monkeypatch.setenv("CORTEX_API_KEY", "ov_real")
    assert load_api_key() == "ov_real"
