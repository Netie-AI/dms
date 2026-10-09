"""Callers pass the engine dialect. They do not write ``duckdb``.

On 1d50b970 the serve and grant call sites passed that literal, so a
Postgres connection was folded as DuckDB and a missing dialect could
not refuse.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskResponse
from dms_executor import Executor
from dms_executor.demo_warehouse import SERVING_DIALECT
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.manifest import SecurityEvent
from dms_executor.ontology import ObjectType, Ontology, WherePath

ROOT = Path(__file__).resolve().parents[1]

# Modules that choose a dialect for the checker, plus the connector that
# declares one. The registry in grant_struct.py is not a caller.
_SERVE = (
    "packages/executor/dms_executor/__init__.py",
    "packages/executor/dms_executor/ontology.py",
    "packages/executor/dms_executor/demo_pack.py",
    "packages/executor/dms_executor/generative_ask.py",
    "packages/executor/dms_executor/session_followup.py",
    "packages/executor/dms_executor/verified_queries.py",
    "packages/executor/dms_executor/demo_warehouse.py",
    "packages/executor/dms_executor/schema_context.py",
    "packages/executor/dms_executor/bronze_sheet_ask.py",
    "apps/api/dms_api/wiring.py",
)


def _quoted_orders_path() -> tuple[Ontology, WherePath]:
    onto = Ontology(
        objects={
            "shipment": ObjectType("shipment", '"Orders"', ("id",)),
            "product": ObjectType("product", '"Orders"', ("id",)),
        }
    )
    path = WherePath(
        grain="sku",
        target="product",
        hops=("ship_to_product",),
        steps=("shipment", "product"),
        importance=1,
        cardinality="many_to_one",
    )
    return onto, path


def test_postgres_connection_does_not_fold_quoted_names() -> None:
    """Postgres normalization at the serve_gap and ungranted_tables call sites.

    ``"SRC_A".orders`` is not the granted ``src_a.orders``. A quoted
    ``"Orders"`` is not the bare table the compiler cites. DuckDB folds
    both. The connection's dialect is what the call site passes.
    """
    exe = Executor(dialect="postgres")
    with pytest.raises(SecurityEvent) as caught:
        exe.execute(
            'SELECT 1 FROM "SRC_A".orders',
            grantable={"src_a.orders"},
        )
    assert caught.value.code == "sql_relation_not_granted"

    onto, path = _quoted_orders_path()
    assert (
        onto.granted_where_paths(
            [path], "sku", {'"Orders"'}, dialect=exe.dialect
        )
        == []
    )
    assert onto.granted_where_paths(
        [path], "sku", {'"Orders"'}, dialect=SERVING_DIALECT
    ) == [path]


def test_missing_dialect_refuses_serve_and_generative() -> None:
    """No declared dialect is sql_dialect_unknown. There is no DuckDB fallback."""
    env = Executor(dialect=None).answer_user_sql("SELECT 1 FROM orders")
    assert_envelope_valid(env)
    assert env["abstained"] is True
    assert env["sql_used"] is None
    assert env["rows"] == []
    assert "validate:sql_dialect_unknown" in env["assumptions"]

    with pytest.raises(SecurityEvent) as caught:
        Executor(dialect="").execute("SELECT 1", grantable={"orders"})
    assert caught.value.code == "sql_dialect_unknown"

    with pytest.raises(SecurityEvent) as unknown:
        Executor(dialect="not-a-dialect").execute(
            "SELECT 1 FROM orders", grantable={"orders"}
        )
    assert unknown.value.code == "sql_dialect_unknown"

    submits: list[str] = []

    def _submit(sql: str) -> Any:
        submits.append(sql)
        raise AssertionError("submit")

    gen = maybe_generative_ask(
        "How many rows are there?",
        grantable={"orders"},
        compute=lambda _ctx: {
            "query_sql": "SELECT 1 AS n FROM orders",
            "plan_source": "ontology_plan",
        },
        submit=_submit,
        ledger_append=lambda _payload: None,
    )
    assert gen is not None
    assert_envelope_valid(gen)
    assert gen["abstained"] is True
    assert gen["sql_used"] is None
    assert "validate:sql_dialect_unknown" in gen["assumptions"]
    assert submits == []


def test_missing_dialect_refuses_the_sheet_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sheet lane names sql_dialect_unknown before any model call."""
    from test_bronze_grant_01 import OPS, _point_warehouse, _question, _seed

    db = tmp_path / "nodialect.duckdb"
    filename = "grantgap.xlsx"
    _seed(db, filename, space_id=None, amount=1545366.40)
    monkeypatch.delenv("DMS_LANE_BRONZE_SHEET", raising=False)
    monkeypatch.delenv("DMS_SCHEMA_CONTEXT", raising=False)

    class _Count:
        def __init__(self) -> None:
            self.computes = 0
            self.submits = 0
            self.asks = 0

        def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
            self.computes += 1
            return {"query_sql": "SELECT 1", "plan_source": "ontology_plan"}

        def submit(self, _req: Any) -> Any:
            self.submits += 1
            raise AssertionError("submit")

        def ask(self, _req: Any) -> AskResponse:
            self.asks += 1
            raise AssertionError("ask")

        def ledger_append(self, _req: Any) -> Any:
            raise AssertionError("ledger")

    stub = _Count()
    _point_warehouse(monkeypatch, db)
    env = Executor(
        cortex=stub, warehouse_path=db, dialect=None
    ).live_ask(  # type: ignore[arg-type]
        _question(filename),
        space_id=OPS,
        session_id="ses_nodialect",
    )
    assert_envelope_valid(env)
    assert stub.computes == 0
    assert stub.submits == 0
    assert stub.asks == 0
    assert env["badge"] == "ABSTAIN" and env["abstained"] is True
    assert env["sql_used"] is None
    assert env["rows"] == []
    assert "validate:sql_dialect_unknown" in env["assumptions"]
    assert "ungranted_table:" not in env["text"]
    assert "1545366.4" not in env["text"]


def test_serve_code_declares_duckdb_once() -> None:
    """One ``SERVING_DIALECT`` assignment. Callers do not write the literal.

    The allow-list is that symbol in the connector module, not a line number.
    """
    extras: list[str] = []
    declarations = 0
    for rel in _SERVE:
        tree = ast.parse((ROOT / rel).read_text())
        allowed: set[int] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "SERVING_DIALECT"
                and isinstance(node.value, ast.Constant)
                and node.value.value == "duckdb"
                and rel.endswith("demo_warehouse.py")
            ):
                allowed.add(id(node.value))
                declarations += 1
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and node.value == "duckdb"
                and id(node) not in allowed
            ):
                extras.append(f"{rel}:{node.lineno}")
    assert declarations == 1
    assert extras == []
    assert SERVING_DIALECT == "duckdb"
