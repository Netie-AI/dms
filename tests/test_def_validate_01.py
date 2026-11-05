"""DEF-VALIDATE-01. No skip, no xfail. CI runs this file inside ``pytest tests/``."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from dms_core.control_plane.onto_data import ontology_data_store, reset_ontology_data_store
from dms_executor.def_validate import def_validate_enabled
from dms_executor.demo_warehouse import SERVING_DIALECT
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ontology import Ontology

ROOT = Path(__file__).resolve().parents[1]
_GATE = ROOT / "packages" / "executor" / "dms_executor" / "def_validate.py"
_QUESTION = "What is the order amount?"
_JOIN = "SELECT orders.id FROM orders JOIN customers ON orders.customer_id = customers.id"
_MEASURE = "SELECT SUM(amount) AS n FROM orders"
_PII = "SELECT email FROM customers"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    reset_ontology_data_store()
    yield
    reset_ontology_data_store()


def test_gate_source_has_no_phrase_rule() -> None:
    tree = ast.parse(_GATE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "re" for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "re"


def test_undeclared_join_reconfirms(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seeded = _approve(measure=True, pii=False)
    env, submitted = _ask(monkeypatch, tmp_path, _JOIN, flag="1")
    assert env is not None
    assert submitted == []
    assert env["abstained"] is True
    assert env["reconfirm_yes"] == "yes"
    definition = env["reconfirm"]["definition"]
    assert "orders.customer_id" in definition
    assert "customers.id" in definition
    assert definition in env["text"]
    assert seeded["version_id"] == env["ontology_provenance"]["version_id"]
    assert seeded["orders"] in env["ontology_provenance"]["item_ids"]


def test_revoked_measure_reconfirms(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seeded = _approve(measure=True, pii=False)
    assert ontology_data_store().revoke(seeded["measure"], actor="steward") is not None
    env, submitted = _ask(monkeypatch, tmp_path, _MEASURE, flag="1")
    assert env is not None
    assert submitted == []
    assert env["reconfirm_yes"] == "yes"
    definition = env["reconfirm"]["definition"]
    assert "revoked measure order_total" in definition
    assert "SUM(amount)" in definition
    assert definition in env["text"]
    assert seeded["measure"] in env["ontology_provenance"]["item_ids"]


def test_pii_column_abstains(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _approve(measure=False, pii=True)
    env, submitted = _ask(monkeypatch, tmp_path, _PII, flag="1")
    assert env is not None
    assert submitted == []
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert "reconfirm" not in env
    assert env.get("reconfirm_yes") is None
    assert env["sql_used"] in (None, "")


def test_provenance_on_a_passing_answer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seeded = _approve(measure=True, pii=False)
    env, submitted = _ask(monkeypatch, tmp_path, _MEASURE, flag="1")
    assert env is not None
    assert submitted == [_MEASURE]
    assert env["abstained"] is False
    prov = env["ontology_provenance"]
    assert prov["version_id"] == seeded["version_id"]
    assert seeded["orders"] in prov["item_ids"]
    assert seeded["measure"] in prov["item_ids"]


def test_flag_off_leaves_the_ask_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DMS_DEF_VALIDATE", raising=False)
    assert def_validate_enabled() is False
    _approve(measure=True, pii=False)
    env, submitted = _ask(monkeypatch, tmp_path, _JOIN, flag=None)
    assert env is not None
    assert submitted == [_JOIN]
    assert env["abstained"] is False
    assert "ontology_provenance" not in env
    assert env.get("reconfirm_yes") is None
    assert "reconfirm" not in env


def _approve(*, measure: bool, pii: bool) -> dict[str, str]:
    stored = ontology_data_store().store_proposal(
        _body(measure=measure, pii=pii), created_by="steward"
    )
    ids = {"version_id": str(stored["version_id"])}
    for item in stored["items"]:
        assert ontology_data_store().approve(item["id"], actor="steward") is not None
        if item["kind"] == "object" and item["payload"]["name"] == "Order":
            ids["orders"] = item["id"]
        if item["kind"] == "object" and item["payload"]["name"] == "Customer":
            ids["customers"] = item["id"]
        if item["kind"] == "measure":
            ids["measure"] = item["id"]
        if item["kind"] == "pii":
            ids["pii"] = item["id"]
    return ids


def _body(*, measure: bool, pii: bool) -> dict[str, Any]:
    measures: list[dict[str, Any]] = []
    if measure:
        measures.append(
            {
                "name": "order_total",
                "grain": "Order",
                "expression": "SUM(amount)",
                "additive": True,
                "description": "order amount",
            }
        )
    pii_rows: list[dict[str, Any]] = []
    if pii:
        pii_rows.append(
            {
                "name": "customer_email",
                "relation": "customers",
                "column": "email",
            }
        )
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
                "attributes": [
                    {"source_column": "id"},
                    {"source_column": "customer_id"},
                    {"source_column": "amount"},
                ],
            },
            {
                "name": "Customer",
                "table": "customers",
                "description": "customer",
                "key": ["id"],
                "attributes": [
                    {"source_column": "id"},
                    {"source_column": "email"},
                ],
            },
        ],
        "measures": measures,
        "pii": pii_rows,
        "inferred_fks": [],
        "declared_fks": [],
        "joins": [],
    }


def _ask(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sql: str,
    *,
    flag: str | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    if flag is None:
        monkeypatch.delenv("DMS_DEF_VALIDATE", raising=False)
    else:
        monkeypatch.setenv("DMS_DEF_VALIDATE", flag)
    path = tmp_path / "def_validate.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE orders (id INTEGER, customer_id INTEGER, amount INTEGER)")
    con.execute("INSERT INTO orders VALUES (1, 2, 5)")
    con.execute("CREATE TABLE customers (id INTEGER, email VARCHAR)")
    con.execute("INSERT INTO customers VALUES (2, 'a@b.c')")
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
        grantable={"orders", "customers"},
        compute=lambda _ctx: {"query_sql": sql},
        submit=submit,
        ledger_append=lambda _payload: SimpleNamespace(entry_id="led", hash="h"),
        ontology=onto,
        dialect=SERVING_DIALECT,
        demo_gate=False,
    )
    return env, submitted
