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


# --- SQLSRC-08 (#156): the refusal reaches the receipt ----------------------


def test_receipt_reports_the_measured_cardinality_of_every_declared_link(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-0001, on the artifact the customer receives.

    EPIC-020 clause 2 promises the system will "measure every link's cardinality
    against the landed rows, and refuse - naming the link - any link the source
    declares that the data violates". Until #156 the manifest the ontology
    consumes was built, put in this receipt, and dropped: ``verify()`` ran on no
    path a customer could reach, so the promise was underivable rather than
    merely undelivered.

    The source here declares ``orders.cust_ref -> customers.cust_ref`` while
    ``customers`` is keyed on ``(cust_ref, version)`` and carries two versions of
    C1. The declaration is not a lie about the schema - it is a lie about what
    the join does to a measure, inflating C1's revenue from 30 to 45. The receipt
    has to say so at the link, by name.
    """
    from test_db_connector import _fanout_source

    _gate_allows(monkeypatch)
    _install(monkeypatch, _fanout_source())

    r = TestClient(create_app()).post("/v1/studio/sources/sql", json=_body())
    assert r.status_code == 200, r.text
    body = r.json()

    links = body["links"]
    assert links["measured"] is True, links
    by_name = {link["name"]: link for link in links["links"]}
    assert "FK_Orders_Customers" in by_name, by_name

    fk = by_name["FK_Orders_Customers"]
    # The whole point. "many_to_one" here would be the layer certifying a join
    # that inflates the measure, which is exactly what a declared-but-unmeasured
    # foreign key buys you.
    assert fk["cardinality"] == "many_to_many", fk
    assert fk["max_fanout"] == 2, fk
    assert fk["from"] == "dbo.orders"
    assert fk["to"] == "dbo.customers"
    assert fk["from_columns"] == ["cust_ref"]
    assert fk["to_columns"] == ["cust_ref"]

    # The rows still landed. A join we refuse to group through is not a reason to
    # throw away an extract whose provenance is real.
    assert {t["bronze_table"] for t in body["tables"]} == {
        "bronze.dbo_orders",
        "bronze.dbo_customers",
    }
    assert body["declared_foreign_keys"] == 1


def test_a_source_declaring_no_foreign_keys_is_not_reported_as_verified(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-0011: nothing to measure must not read as measured and clean.

    A source with no declared foreign keys has told us nothing about its joins.
    Returning ``verified: true`` over an empty claim set is the silent fallback
    that rule names - a green result standing in for an absent measurement - and
    it is the shape a steward would read as "the joins are safe".
    """
    _gate_allows(monkeypatch)
    _install(
        monkeypatch,
        _FakeConnection([("dbo", "orders")], (["order_id", "amount"], [["A-1", "10.50"]])),
    )

    r = TestClient(create_app()).post("/v1/studio/sources/sql", json=_body())
    assert r.status_code == 200, r.text
    links = r.json()["links"]

    assert links["measured"] is False, links
    assert links["verified"] is False, links
    assert "no foreign keys" in links["reason"], links
    assert links["links"] == []
    assert links["violations"] == []


def test_a_capped_parent_refuses_the_link_it_invented_orphans_in(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQLSRC-07 (#157) on the customer artifact.

    The extractor caps every table independently, so capping a parent orphans
    the children that pointed at the rows it cut - orphans the source never had.
    The compiler's LEFT JOIN then keeps the grand total right and puts every one
    of them in a NULL group, so each named group is understated while the total
    reconciles. The total is the first number anyone checks, which is what makes
    this the worst shape rather than merely a wrong one.

    ``verify()`` reads a DuckDB connection and cannot tell this from a source
    that was always dirty. This asserts the fact is actually threaded through on
    the real path - not passing it is a fail-open, because a capped parent would
    then read as a whole one and its orphans would be disclosed instead of
    refused.
    """
    con = _FakeConnection(
        [("dbo", "orders"), ("dbo", "customers")],
        {
            "[dbo].[orders]": (
                ["order_id", "cust_ref", "amount"],
                [["O1", "C1", "10"], ["O2", "C3", "20"]],
            ),
            "[dbo].[customers]": (
                ["cust_ref", "region"],
                [["C1", "North"], ["C2", "South"], ["C3", "East"]],
            ),
        },
        pks=[
            ("dbo", "orders", "order_id", 1),
            ("dbo", "customers", "cust_ref", 1),
        ],
        fks=[
            ("FK_Orders_Customers", "dbo", "orders", "cust_ref", "dbo", "customers", "cust_ref", 1),
        ],
    )
    _gate_allows(monkeypatch)
    _install(monkeypatch, con)

    r = TestClient(create_app()).post("/v1/studio/sources/sql", json=_body(max_rows=2))
    assert r.status_code == 200, r.text
    body = r.json()

    # The cap did land a partial parent, which is the precondition.
    capped = {t["bronze_table"] for t in body["tables"] if t["truncated"]}
    assert "bronze.dbo_customers" in capped, body["tables"]

    links = body["links"]
    assert links["measured"] is True, links
    assert "bronze.dbo_customers" in links["capped_tables"], links

    # Counted, and reported whether or not it was a refusal.
    orphans = links["orphans"]["FK_Orders_Customers"]
    assert orphans["rows"] == 1, orphans
    assert orphans["distinct_keys"] == 1, orphans
    assert orphans["parent_capped"] is True, orphans

    # Named, and attributed to our cap rather than to the customer's data.
    intact = [v for v in links["violations"] if v["check"] == "link_intact"]
    assert len(intact) == 1, links["violations"]
    assert intact[0]["subject"] == "FK_Orders_Customers"
    assert "our own row cap invented" in intact[0]["detail"]
    assert "bronze.dbo_customers" in intact[0]["detail"]

    # And refused: no cardinality, so nothing may be grouped through it.
    fk = {link["name"]: link for link in links["links"]}["FK_Orders_Customers"]
    assert fk["cardinality"] == "unverified", fk
    assert links["verified"] is False, links

    # The rows still landed. A refused join is not a reason to discard an
    # extract whose provenance is real.
    assert {t["bronze_table"] for t in body["tables"]} == {
        "bronze.dbo_orders",
        "bronze.dbo_customers",
    }
