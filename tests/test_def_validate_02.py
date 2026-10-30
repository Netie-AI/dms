"""DEF-VALIDATE-02. No skip, no xfail. CI runs this file inside ``pytest tests/``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from dms_core.control_plane.onto_data import ontology_data_store, reset_ontology_data_store
from dms_executor.demo_warehouse import SERVING_DIALECT
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ontology import Ontology
from dms_executor.pipeline_failure import _reason_code, reconfirm_disposition

_QUESTION = "What is the order amount?"
_COUNT = "SELECT COUNT(*) AS n FROM orders"
_JOIN = (
    "SELECT c.name, SUM(o.qty) AS n FROM orders o "
    "JOIN customers c ON o.customer_id = c.id GROUP BY c.name"
)
_GRANTS = {"orders", "customers"}


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    from dms_executor.reconfirm import _reset_reconfirm

    reset_ontology_data_store()
    _reset_reconfirm()
    yield
    reset_ontology_data_store()
    _reset_reconfirm()


def test_count_star_on_an_approved_object_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seeded = _approve()
    env, submitted = _ask(monkeypatch, tmp_path, _COUNT)
    assert env is not None
    assert submitted == [_COUNT]
    assert env["abstained"] is False
    assert env["ontology_provenance"]["derived"] == ["derived:count(orders.*)"]
    assert env["ontology_provenance"]["version_id"] == seeded["version_id"]
    assert seeded["Order"] in env["ontology_provenance"]["item_ids"]


def test_sum_through_an_approved_link_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _approve()
    env, submitted = _ask(monkeypatch, tmp_path, _JOIN)
    assert env is not None
    assert submitted == [_JOIN]
    assert env["abstained"] is False
    assert env["ontology_provenance"]["derived"] == ["derived:sum(orders.qty)"]
    assert env.get("reconfirm_yes") is None


@pytest.mark.parametrize(
    ("sql", "provenance"),
    [
        ("SELECT COUNT(id) AS n FROM orders", "derived:count(orders.id)"),
        ("SELECT SUM(qty) AS n FROM orders", "derived:sum(orders.qty)"),
        ("SELECT AVG(qty) AS n FROM orders", "derived:avg(orders.qty)"),
        ("SELECT MIN(qty) AS n FROM orders", "derived:min(orders.qty)"),
        ("SELECT MAX(qty) AS n FROM orders", "derived:max(orders.qty)"),
    ],
)
def test_plain_aggregate_of_an_approved_column_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sql: str, provenance: str
) -> None:
    _approve()
    env, submitted = _ask(monkeypatch, tmp_path, sql)
    assert env is not None
    assert submitted == [sql]
    assert env["abstained"] is False
    assert env["ontology_provenance"]["derived"] == [provenance]


def test_empty_store_join_reconfirms_not_in_ontology(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env, submitted = _ask(monkeypatch, tmp_path, _JOIN)
    assert env is not None
    assert submitted == []
    assert env["abstained"] is True
    assert env["reconfirm_yes"] == "yes"
    assert env["reconfirm"]["code"] == "not_in_ontology"
    assert env["text"].startswith("not_in_ontology:")
    assert "." in env["reconfirm"]["definition"]
    assert "ungranted" not in env["text"]
    assert "ungranted" not in env.get("assumptions", [])


def test_sum_of_a_column_outside_the_ontology_is_measure_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _approve(qty=False)
    sql = "SELECT SUM(qty) AS n FROM orders"
    env, submitted = _ask(monkeypatch, tmp_path, sql)
    assert env is not None
    assert submitted == []
    assert env["reconfirm_yes"] == "yes"
    assert env["reconfirm"]["code"] == "measure_drift"
    assert env["text"].startswith("measure_drift:")
    assert "qty" in env["reconfirm"]["definition"]
    assert "not_in_ontology" not in env["text"]


def test_redefined_measure_name_is_measure_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _approve(
        measures=[
            {
                "name": "revenue",
                "grain": "Order",
                "expression": "SUM(amount)",
                "additive": True,
                "description": "revenue",
            }
        ]
    )
    sql = "SELECT SUM(qty) AS revenue FROM orders"
    env, submitted = _ask(monkeypatch, tmp_path, sql)
    assert env is not None
    assert submitted == []
    assert env["reconfirm_yes"] == "yes"
    assert env["text"].startswith("measure_drift:")
    definition = env["reconfirm"]["definition"]
    assert "revenue" in definition
    assert "SUM(amount)" in definition


def test_parent_aggregate_at_the_child_grain_is_measure_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _approve(points=True)
    sql = (
        "SELECT o.id, SUM(c.points) AS n FROM orders o "
        "JOIN customers c ON o.customer_id = c.id GROUP BY o.id"
    )
    env, submitted = _ask(monkeypatch, tmp_path, sql)
    assert env is not None
    assert submitted == []
    assert env["reconfirm_yes"] == "yes"
    assert env["text"].startswith("measure_drift:")


def test_nested_aggregate_is_measure_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _approve()
    sql = "SELECT SUM(n) AS n FROM (SELECT COUNT(*) AS n FROM orders) s"
    env, submitted = _ask(monkeypatch, tmp_path, sql)
    assert env is not None
    assert submitted == []
    assert env["reconfirm_yes"] == "yes"
    assert env["text"].startswith("measure_drift:")


def test_table_outside_the_grant_abstains_ungranted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _approve()
    env, submitted = _ask(monkeypatch, tmp_path, "SELECT COUNT(*) AS n FROM alerts")
    assert env is not None
    assert submitted == []
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert "reconfirm" not in env
    assert env.get("reconfirm_yes") is None
    assert "ungranted" in env["assumptions"]


def test_not_in_ontology_disposition_is_offer() -> None:
    assert reconfirm_disposition("not_in_ontology:orders.qty") == "offer"
    assert _reason_code("not_in_ontology:orders.qty") == "not_in_ontology"
    assert reconfirm_disposition("ungranted") == "direct"
    assert reconfirm_disposition("ungranted:alerts") == "direct"


@pytest.mark.parametrize(
    ("sql", "code", "kind"),
    [
        (_JOIN, "not_in_ontology", "empty"),
        ("SELECT SUM(qty) AS n FROM orders", "measure_drift", "no_qty"),
    ],
)
def test_held_yes_runs_the_reconfirm_sql(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sql: str,
    code: str,
    kind: str,
) -> None:
    """#477 parks SQL only when code and definition are non-empty strings.

    Provenance ``yes:definition:<code>`` is stored only when code is an identifier.
    """
    from dms_executor.reconfirm import apply_reconfirm, peek_pending
    from dms_executor.verified_queries import _provenance_ok
    from test_reconfirm_yes_01 import _executor, _patch_run, _patch_save, _refuse_model

    monkeypatch.setenv("DMS_RECONFIRM_RUN_HELD", "1")
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    if kind == "no_qty":
        _approve(qty=False)
    env, submitted = _ask(monkeypatch, tmp_path, sql)
    assert env is not None
    assert submitted == []
    block = env["reconfirm"]
    assert block["code"] == code
    assert block["code"].isidentifier()
    assert isinstance(block["definition"], str) and block["definition"].strip()
    assert isinstance(block.get("hold_id"), str) and block["hold_id"]
    offered = apply_reconfirm(
        env,
        question=_QUESTION,
        space_id=None,
        session_id=None,
        tables=None,
        ask_path=None,
        ontology="",
        schema="",
        complete=_refuse_model,
    )
    assert "hold_id" not in offered["reconfirm"]
    assert sql not in str(offered["reconfirm"])
    token = offered["reconfirm"]["token"]
    pending = peek_pending(token)
    assert pending is not None
    assert pending["sql"] == sql
    assert pending["reason_code"] == code
    assert pending["definition"] == block["definition"].strip()
    assert "rows" not in pending
    ran: list[str] = []
    saved: list[dict[str, Any]] = []
    _patch_run(monkeypatch, ran)
    _patch_save(monkeypatch, saved)
    answered = _executor(tmp_path / "def_validate_02.duckdb").confirm_reconfirm(token, True)
    provenance = f"yes:definition:{code}"
    assert ran == [sql]
    assert "reconfirm" not in answered
    assert answered["reconfirm_accepted"] is True
    assert answered["provenance"] == provenance
    assert answered["abstained"] is False
    assert answered["rows"]
    assert _provenance_ok(provenance)
    assert saved and saved[0]["provenance"] == provenance


def _approve(
    *,
    qty: bool = True,
    points: bool = False,
    measures: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    stored = ontology_data_store().store_proposal(
        _body(qty=qty, points=points, measures=measures), created_by="steward"
    )
    ids = {"version_id": str(stored["version_id"])}
    for item in stored["items"]:
        assert ontology_data_store().approve(item["id"], actor="steward") is not None
        if item["kind"] == "object":
            ids[str(item["payload"]["name"])] = item["id"]
    return ids


def _body(
    *,
    qty: bool,
    points: bool,
    measures: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    order_attrs: list[dict[str, str]] = [
        {"source_column": "id"},
        {"source_column": "customer_id"},
        {"source_column": "amount"},
    ]
    if qty:
        order_attrs.append({"source_column": "qty"})
    customer_attrs: list[dict[str, str]] = [
        {"source_column": "id"},
        {"source_column": "name"},
    ]
    if points:
        customer_attrs.append({"source_column": "points"})
    return {
        "status": "proposed",
        "served": False,
        "vendor_sha": "",
        "terms": [
            {
                "name": "Order",
                "table": "orders",
                "description": "order",
                "key": ["id"],
                "attributes": order_attrs,
            },
            {
                "name": "Customer",
                "table": "customers",
                "description": "customer",
                "key": ["id"],
                "attributes": customer_attrs,
            },
        ],
        "measures": measures or [],
        "pii": [],
        "inferred_fks": [],
        "declared_fks": [
            {
                "source_table": "orders",
                "source_column": "customer_id",
                "target_table": "customers",
                "target_column": "id",
            }
        ],
        "joins": [],
    }


def _ask(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sql: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    monkeypatch.setenv("DMS_DEF_VALIDATE", "1")
    path = tmp_path / "def_validate_02.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE orders (id INTEGER, customer_id INTEGER, qty INTEGER, amount INTEGER)"
    )
    con.execute("INSERT INTO orders VALUES (1, 2, 5, 9)")
    con.execute("CREATE TABLE customers (id INTEGER, name VARCHAR, points INTEGER)")
    con.execute("INSERT INTO customers VALUES (2, 'ada', 3)")
    con.close()
    submitted: list[str] = []

    def submit(statement: str) -> Any:
        submitted.append(statement)
        handle = duckdb.connect(str(path))
        try:
            cur = handle.execute(statement)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            handle.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_def", output={"rows": rows})

    onto = Ontology()
    onto.verified = True
    env = maybe_generative_ask(
        _QUESTION,
        warehouse=path,
        grantable=set(_GRANTS),
        compute=lambda _ctx: {"query_sql": sql},
        submit=submit,
        ledger_append=lambda _payload: SimpleNamespace(entry_id="led", hash="h"),
        ontology=onto,
        dialect=SERVING_DIALECT,
        demo_gate=False,
    )
    return env, submitted
