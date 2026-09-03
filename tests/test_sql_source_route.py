"""EPIC-020 ticket 4: POST /v1/studio/sources/sql. Credential never leaves the request."""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pytest
from dms_api.app import create_app
from dms_api.settings import get_settings
from fastapi.testclient import TestClient
from test_db_connector import _FakeConnection, _install

SECRET = "p;w}d"
ORDERS_SOURCE = "sqlserver://db.example.net:1433/sales#dbo.orders"


def _body(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": "sqlserver",
        "host": "db.example.net",
        "database": "sales",
        "user": "reader",
        "password": SECRET,
    }
    base.update(over)
    return base


def _gate_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_api.routes.studio as studio_routes
    from cortex_client.gate import ComplianceDecision

    monkeypatch.setattr(
        studio_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(
            allowed=True, reason="test_allow", action=action
        ),
    )


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    path = tmp_path / "sql_source.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    get_settings.cache_clear()
    return path


def _secret_leaked(text: str, caplog: pytest.LogCaptureFixture, warehouse: Path | None) -> str:
    if SECRET in text:
        return "response body"
    for rec in caplog.records:
        if SECRET in rec.getMessage():
            return f"log:{rec.name}"
    if warehouse is not None and warehouse.is_file():
        con = duckdb.connect(str(warehouse))
        try:
            rows = con.execute(
                "SELECT filename, sha256 FROM bronze._ingest_registry"
            ).fetchall()
        except Exception:  # noqa: BLE001 - registry may not exist
            rows = []
        finally:
            con.close()
        blob = " ".join(str(c) for row in rows for c in row)
        if SECRET in blob:
            return "registry"
    return ""


def test_422_does_not_echo_the_password(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    client = TestClient(create_app())
    probes = [
        _body(password=SECRET + "x" * 260),
        [_body()],
        _body(password=1),  # type: ignore[arg-type]
    ]
    for payload in probes:
        r = client.post("/v1/studio/sources/sql", json=payload)
        assert r.status_code == 422, r.text
        assert SECRET not in r.text
        assert not _secret_leaked(r.text, caplog, None)


def test_sql_source_lands_and_preview_names_it(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    con = _FakeConnection([("dbo", "orders")], (["order_id", "amount"], [["A-1", "10.50"]]))
    _install(monkeypatch, con)
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post("/v1/studio/sources/sql", json=_body(tables=["orders"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert SECRET not in r.text
    assert body["source"] == "sqlserver://db.example.net:1433/sales"
    assert len(body["tables"]) == 1
    landed = body["tables"][0]
    assert landed["row_count"] == 1
    assert landed["truncated"] is False
    table = landed["bronze_table"]
    prev = client.get(f"/v1/library/bronze/{table}/preview")
    assert prev.status_code == 200, prev.text
    assert prev.json()["source"] == ORDERS_SOURCE
    assert not _secret_leaked(r.text + prev.text, caplog, warehouse)


def test_gate_refused_opens_nothing(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    import dms_executor.db_connector as dbc
    from dms_executor.bronze import list_bronze_tables

    before = list_bronze_tables()
    opened = {"n": 0}

    def _no_connect(cfg: object):  # noqa: ARG001
        opened["n"] += 1
        raise AssertionError("connect must not run when the gate refuses")

    monkeypatch.setattr(dbc, "connect", _no_connect)
    client = TestClient(create_app())
    r = client.post("/v1/studio/sources/sql", json=_body())
    assert r.status_code == 403, r.text
    assert r.json()["detail"] in {"gate_unavailable", "gate_task_unknown"}
    assert opened["n"] == 0
    assert list_bronze_tables() == before
    assert not _secret_leaked(r.text, caplog, warehouse)


def test_truncated_and_skipped(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    con = _FakeConnection(
        [("dbo", "orders")],
        (["order_id"], [["1"], ["2"]]),
    )
    _install(monkeypatch, con)
    _gate_allows(monkeypatch)
    client = TestClient(create_app())
    r = client.post(
        "/v1/studio/sources/sql",
        json=_body(tables=["orders", "secret"], max_rows=1),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skipped"] == ["secret"]
    assert body["tables"][0]["truncated"] is True
    assert body["tables"][0]["row_count"] == 1
    assert not _secret_leaked(r.text, caplog, warehouse)


def test_connect_failure_is_502_without_driver_text(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    import dms_executor as exe

    _gate_allows(monkeypatch)

    def _boom(*_a: object, **_k: object):
        err = exe.SourceConnectionError("could not connect to sqlserver://db.example.net:1433/sales")
        err.__cause__ = RuntimeError(f"user 'reader'@'host' with {SECRET}")
        raise err

    monkeypatch.setattr(exe, "ingest_source_database", _boom)
    client = TestClient(create_app())
    r = client.post("/v1/studio/sources/sql", json=_body())
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["code"] == "source_unreachable"
    assert SECRET not in r.text
    assert "reader@" not in r.text
    assert not _secret_leaked(r.text, caplog, warehouse)
