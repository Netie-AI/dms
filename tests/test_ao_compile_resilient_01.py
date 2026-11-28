"""FDE-AO-COMPILE-01: one bad ontology call must not take the compile down.

Synthetic schemas only. ``DMS_AO_COMPILE_RESILIENT`` defaults off. Off, a 429
is one refusal and a deadline is ignored. On, a 429 is retried, a validation
miss is sent back once, and a transport that sleeps past the caller deadline
is ``ao_ov_timeout``.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import duckdb
import pytest
from ao_vendor.llm.messages import HumanMessage
from ao_vendor.llm.ov_client import (
    REFUSE_OUTPUT_INVALID,
    REFUSE_TIMEOUT,
    RESILIENT_FLAG,
    AoLlmError,
    OpenVaultOntologyClient,
    bind_deadline,
    reset_deadline,
)
from pydantic import BaseModel, ConfigDict, ValidationError


class SyntheticNote(BaseModel):
    """Synthetic structured output. Not a catalog table."""

    model_config = ConfigDict(extra="forbid")

    label: str
    count: int


_GOOD = {"label": "alpha", "count": 2}
_BAD_VALUE = "NOT_A_NUMBER_SENTINEL"
_BAD = {"label": "alpha", "count": _BAD_VALUE}


def _client(monkeypatch: pytest.MonkeyPatch, transport: Any) -> OpenVaultOntologyClient:
    monkeypatch.setenv("OPENVAULT_API_KEY", "ov_synthetickey")
    monkeypatch.setenv("OPENVAULT_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    return OpenVaultOntologyClient(transport=transport)


def _ask(client: OpenVaultOntologyClient) -> SyntheticNote:
    return client.complete_structured([HumanMessage(content="ping")], SyntheticNote)


def test_flag_off_429_is_one_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RESILIENT_FLAG, raising=False)
    seen = {"n": 0}

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        seen["n"] += 1
        raise AoLlmError("ao_ov_http_429", retry_after=0)

    client = _client(monkeypatch, transport)
    with pytest.raises(AoLlmError) as info:
        _ask(client)
    assert info.value.code == "ao_ov_http_429"
    assert seen["n"] == 1
    assert client.failures == ["ao_ov_http_429"]
    assert client.calls_ok == 0


def test_flag_on_429_then_200_returns_the_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RESILIENT_FLAG, "1")
    waits: list[float] = []
    monkeypatch.setattr(
        "ao_vendor.llm.ov_client._pause", lambda seconds: waits.append(seconds)
    )
    seen = {"n": 0}

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        seen["n"] += 1
        if seen["n"] == 1:
            raise AoLlmError("ao_ov_http_429", retry_after=0)
        return dict(_GOOD)

    client = _client(monkeypatch, transport)
    parsed = _ask(client)
    assert parsed.label == "alpha"
    assert parsed.count == 2
    assert seen["n"] == 2
    assert waits == [0.0]
    assert client.failures == []
    assert client.calls_ok == 1


def test_flag_on_validation_repair_uses_field_names_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(RESILIENT_FLAG, "1")
    seen: list[dict[str, Any]] = []

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        seen.append(body)
        if len(seen) == 1:
            return dict(_BAD)
        return dict(_GOOD)

    client = _client(monkeypatch, transport)
    with caplog.at_level(logging.ERROR):
        parsed = _ask(client)
    assert parsed.count == 2
    assert len(seen) == 2
    repair = seen[1]["messages"][-1]["content"]
    assert "count" in repair
    assert _BAD_VALUE not in repair
    assert _BAD_VALUE not in caplog.text
    assert "fields=count" in caplog.text
    assert client.failures == []


def test_flag_on_second_validation_miss_is_invalid(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(RESILIENT_FLAG, "1")
    seen = {"n": 0}

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        seen["n"] += 1
        return dict(_BAD)

    client = _client(monkeypatch, transport)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValidationError):
            _ask(client)
    assert seen["n"] == 2
    assert client.failures == [REFUSE_OUTPUT_INVALID]
    assert client.failure_steps == ["SyntheticNote"]
    assert client.invalid_fields == ["count"]
    assert _BAD_VALUE not in caplog.text


def test_flag_off_ignores_a_passed_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RESILIENT_FLAG, raising=False)

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        return dict(_GOOD)

    client = _client(monkeypatch, transport)
    token = bind_deadline(time.monotonic() - 1)
    try:
        parsed = _ask(client)
    finally:
        reset_deadline(token)
    assert parsed.count == 2
    assert client.failures == []


def test_transport_sleeping_past_the_deadline_is_ao_ov_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RESILIENT_FLAG, "1")

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        time.sleep(0.12)
        return dict(_GOOD)

    client = _client(monkeypatch, transport)
    token = bind_deadline(time.monotonic() + 0.04)
    try:
        with pytest.raises(AoLlmError) as info:
            _ask(client)
    finally:
        reset_deadline(token)
    assert info.value.code == REFUSE_TIMEOUT
    assert client.failures == [REFUSE_TIMEOUT]
    assert "ao_ov_unreachable" not in client.failures


def _pharmacy(path: Path, *, extra: bool) -> None:
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name VARCHAR)")
    conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, sku VARCHAR)")
    conn.execute("INSERT INTO customers VALUES (1, 'ada')")
    conn.execute("INSERT INTO products VALUES (10, 'sku-a')")
    if extra:
        conn.execute("CREATE TABLE secret_audit (id INTEGER PRIMARY KEY, note VARCHAR)")
        conn.execute("INSERT INTO secret_audit VALUES (1, 'hidden')")
    conn.close()


def _env(monkeypatch: pytest.MonkeyPatch, *, resilient: str | None) -> None:
    monkeypatch.setenv("DMS_AO_COMPILE", "1")
    monkeypatch.setenv("OPENVAULT_API_KEY", "ov_synthetickey")
    monkeypatch.setenv("OPENVAULT_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.delenv("DMS_AO_RESPONSE_FORMAT", raising=False)
    if resilient is None:
        monkeypatch.delenv(RESILIENT_FLAG, raising=False)
    else:
        monkeypatch.setenv(RESILIENT_FLAG, resilient)


def _compile(monkeypatch: pytest.MonkeyPatch, path: Path, transport: Any) -> Any:
    from dms_executor.ao_compile import compile_source

    conn = duckdb.connect(str(path), read_only=True)
    try:
        return compile_source(conn, transport=transport)
    finally:
        conn.close()


def test_flag_off_proposal_bytes_match_flag_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from test_ao_vendor_01 import _recorded

    path = tmp_path / "bytes.duckdb"
    _pharmacy(path, extra=False)

    def _run(flag: str | None) -> tuple[str, list[str]]:
        _env(monkeypatch, resilient=flag)
        seen: list[str] = []

        def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
            seen.append(json.dumps(body, sort_keys=True))
            assert list(body) == ["role", "messages", "schema"]
            return _recorded(body)

        proposal = _compile(monkeypatch, path, transport)
        return json.dumps(proposal.to_json(), sort_keys=True), seen

    unset_body, unset_calls = _run(None)
    off_body, off_calls = _run("0")
    assert unset_body == off_body
    assert unset_calls == off_calls
    assert "gaps" not in json.loads(unset_body)


def test_flag_on_clean_compile_matches_flag_off_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from test_ao_vendor_01 import _recorded

    path = tmp_path / "clean.duckdb"
    _pharmacy(path, extra=False)
    _env(monkeypatch, resilient=None)

    def transport_off(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        return _recorded(body)

    off = _compile(monkeypatch, path, transport_off).to_json()
    _env(monkeypatch, resilient="1")

    def transport_on(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        return _recorded(body)

    on = _compile(monkeypatch, path, transport_on).to_json()
    assert json.dumps(on, sort_keys=True) == json.dumps(off, sort_keys=True)
    assert on["status"] == "proposed"
    assert "gaps" not in on


def test_429_then_200_compiles_the_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from test_ao_vendor_01 import _recorded

    path = tmp_path / "retry.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name VARCHAR)")
    conn.execute("INSERT INTO customers VALUES (1, 'ada')")
    conn.close()
    _env(monkeypatch, resilient="1")
    monkeypatch.setattr("ao_vendor.llm.ov_client._pause", lambda _seconds: None)
    seen = {"n": 0}

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        seen["n"] += 1
        if seen["n"] == 1:
            raise AoLlmError("ao_ov_http_429", retry_after=0)
        return _recorded(body)

    proposal = _compile(monkeypatch, path, transport)
    body = proposal.to_json()
    assert body["status"] == "proposed"
    assert {row["table"] for row in body["terms"]} == {"customers"}
    assert "gaps" not in body
    assert seen["n"] >= 2


def test_one_table_validation_error_is_a_partial_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from test_ao_vendor_01 import _recorded, _table_name

    path = tmp_path / "partial.duckdb"
    _pharmacy(path, extra=False)
    _env(monkeypatch, resilient="1")

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        if _table_name(body) == "products" and body["schema"] == "FkAndPkResult":
            return {"fk_suggestions": _BAD_VALUE}
        encoded = json.dumps(body)
        assert _BAD_VALUE not in encoded
        return _recorded(body)

    with caplog.at_level(logging.ERROR):
        proposal = _compile(monkeypatch, path, transport)
    body = proposal.to_json()
    assert body["status"] == "partial"
    assert {row["table"] for row in body["terms"]} == {"customers"}
    assert body["gaps"] == [
        {
            "table": "products",
            "code": REFUSE_OUTPUT_INVALID,
            "step": "FkAndPkResult",
            "fields": ["fk_suggestions"],
        }
    ]
    assert _BAD_VALUE not in caplog.text
    assert _BAD_VALUE not in json.dumps(body)


def test_sleep_past_deadline_gaps_the_table_as_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dms_executor.ao_compile import compile_source
    from test_ao_vendor_01 import _recorded

    path = tmp_path / "slow.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name VARCHAR)")
    conn.execute("INSERT INTO customers VALUES (1, 'ada')")
    conn.close()
    _env(monkeypatch, resilient="1")

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        time.sleep(0.12)
        return _recorded(body)

    held = duckdb.connect(str(path), read_only=True)
    try:
        proposal = compile_source(held, transport=transport, deadline=time.monotonic() + 0.04)
    finally:
        held.close()
    body = proposal.to_json()
    assert body["status"] == "refused"
    assert body["terms"] == []
    assert body["gaps"][0]["code"] == REFUSE_TIMEOUT
    assert body["gaps"][0]["table"] == "customers"


def test_compile_proposal_reads_only_granted_tables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dms_core.control_plane.connect_grants import grant_tables, register_source, reset
    from dms_executor.ao_compile import compile_source
    from test_ao_vendor_01 import _recorded, _table_name

    path = tmp_path / "scoped.duckdb"
    _pharmacy(path, extra=True)
    _env(monkeypatch, resilient="1")
    space = "11111111-1111-1111-1111-111111111111"
    reset()
    register_source(
        source_id="src-pharm",
        space_id=space,
        connector_id="pharmacy",
        exposed=["customers", "products", "secret_audit"],
        actor="steward",
        token_id="tok",
    )
    grant_tables(
        source_id="src-pharm",
        tables=["customers", "products"],
        actor="steward",
        token_id="tok",
    )
    seen: list[str] = []

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        name = _table_name(body)
        seen.append(name)
        assert name != "secret_audit"
        return _recorded(body)

    conn = duckdb.connect(str(path), read_only=True)
    try:
        from dms_executor.ao_compile import space_compile_tables

        proposal = compile_source(
            conn, transport=transport, tables=space_compile_tables(space)
        )
    finally:
        conn.close()
        reset()
    tables = {row["table"] for row in proposal.to_json()["terms"]}
    assert tables == {"customers", "products"}
    assert "secret_audit" not in seen


def _allow_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex_client.gate import ComplianceDecision

    def _allow(*, action: str, **_: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr("dms_api.routes.ontology.compliance_gate", _allow)


def test_route_partial_is_200_and_a_boom_is_not_500(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dms_api.app import create_app
    from dms_core.control_plane.connect_grants import grant_tables, register_source, reset
    from dms_core.control_plane.onto_data import reset_ontology_data_store
    from fastapi.testclient import TestClient
    from test_ao_vendor_01 import _recorded, _table_name

    path = tmp_path / "route.duckdb"
    _pharmacy(path, extra=True)
    _env(monkeypatch, resilient="1")
    monkeypatch.setattr("dms_executor.warehouse_path", lambda: path)
    space = "22222222-2222-2222-2222-222222222222"
    reset()
    reset_ontology_data_store()
    register_source(
        source_id="src-route",
        space_id=space,
        connector_id="pharmacy",
        exposed=["customers", "products", "secret_audit"],
        actor="steward",
        token_id="tok",
    )
    grant_tables(
        source_id="src-route",
        tables=["customers", "products"],
        actor="steward",
        token_id="tok",
    )
    _allow_gate(monkeypatch)

    def transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        name = _table_name(body)
        assert name != "secret_audit"
        if name == "products" and body["schema"] == "FkAndPkResult":
            return {"fk_suggestions": _BAD_VALUE}
        return _recorded(body)

    monkeypatch.setattr("ao_vendor.llm.ov_client.default_transport", transport)
    client = TestClient(create_app(), raise_server_exceptions=False)
    try:
        res = client.post("/v1/ontology/data/compile", params={"space_id": space})
        assert res.status_code == 200, res.text
        payload = res.json()
        assert payload["status"] == "partial"
        assert payload["served"] is False
        assert payload["gaps"][0]["table"] == "products"
        assert payload["gaps"][0]["code"] == REFUSE_OUTPUT_INVALID
        assert _BAD_VALUE not in res.text
        names = {
            item["payload"].get("relation")
            for item in payload["items"]
            if item["kind"] == "object"
        }
        assert names == {"customers"}

        def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("extract-broke")

        monkeypatch.setattr("ao_vendor.compile_run.extract_duckdb", _boom)
        blown = client.post("/v1/ontology/data/compile", params={"space_id": space})
        assert blown.status_code == 503, blown.text
        assert blown.json()["detail"] == "ao_compile_failed"
        assert blown.status_code != 500

        missing = client.post("/v1/ontology/data/compile")
        assert missing.status_code == 400
        assert missing.json()["detail"] == "space_id_required"
    finally:
        reset()
        reset_ontology_data_store()


def test_flag_off_route_still_returns_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    _env(monkeypatch, resilient=None)
    _allow_gate(monkeypatch)

    def _boom(conn: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("old-path")

    monkeypatch.setattr("dms_executor.compile_source", _boom)
    client = TestClient(create_app(), raise_server_exceptions=False)
    res = client.post("/v1/ontology/data/compile")
    assert res.status_code == 500
