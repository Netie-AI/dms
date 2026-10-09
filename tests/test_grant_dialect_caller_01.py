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
_PACKAGE = ROOT / "packages" / "executor" / "dms_executor"

# Legal shape: the declaration, plus a dialect lookup table. No file list.
_CLEAN = """
SERVING_DIALECT = "duckdb"
_DIALECTS = {"duckdb": "duckdb", "postgres": "postgres"}
_DEFAULT_SCHEMA = {"duckdb": "main"}
"""
_PLANT = _CLEAN + """
def read(sql):
    return parse(sql, read="duckdb")
"""


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


def _assigned_name(node: ast.AST) -> tuple[str | None, ast.AST | None]:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return target.id, node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    return None, None


def _lookup_ids(tree: ast.AST) -> set[int]:
    """``"duckdb"`` keys, and a value only when its key is the same name."""
    found: set[int] = set()
    for node in ast.walk(tree):
        _name, value = _assigned_name(node)
        if not isinstance(value, ast.Dict):
            continue
        for key, val in zip(value.keys, value.values, strict=False):
            key_is = isinstance(key, ast.Constant) and key.value == "duckdb"
            if key_is:
                found.add(id(key))
            if key_is and isinstance(val, ast.Constant) and val.value == "duckdb":
                found.add(id(val))
    return found


def _declarations(tree: ast.AST) -> list[ast.Constant]:
    found: list[ast.Constant] = []
    for node in ast.walk(tree):
        name, value = _assigned_name(node)
        if (
            name == "SERVING_DIALECT"
            and isinstance(value, ast.Constant)
            and value.value == "duckdb"
        ):
            found.append(value)
    return found


def duckdb_literal_hits(src: str) -> list[int]:
    """Line numbers of ``"duckdb"`` that are not the declaration or a lookup entry."""
    tree = ast.parse(src)
    allowed = _lookup_ids(tree)
    declarations = _declarations(tree)
    if len(declarations) == 1:
        allowed.add(id(declarations[0]))
    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == "duckdb" and id(node) not in allowed:
            hits.append(node.lineno)
    return hits


def serve_package_duckdb_hits(root: Path) -> list[str]:
    """Every ``"duckdb"`` literal under ``root``. One declaration across the tree."""
    parsed: list[tuple[Path, ast.AST]] = []
    declarations: list[tuple[Path, ast.Constant]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parsed.append((path, tree))
        declarations.extend((path, node) for node in _declarations(tree))
    allowed: set[int] = set()
    if len(declarations) == 1:
        allowed.add(id(declarations[0][1]))
    hits: list[str] = []
    if len(declarations) != 1:
        rendered = ", ".join(f"{path}:{node.lineno}" for path, node in declarations) or "none"
        hits.append(f"declarations:{len(declarations)}:{rendered}")
    for path, tree in parsed:
        allowed_here = _lookup_ids(tree) | allowed
        rel = path.relative_to(root.parents[2])
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and node.value == "duckdb"
                and id(node) not in allowed_here
            ):
                hits.append(f"{rel}:{node.lineno}")
    return hits


def test_planted_duckdb_literal_is_red_and_the_package_is_green() -> None:
    """A second literal is red. The declaration and lookup tables are not a file list."""
    assert duckdb_literal_hits(_CLEAN) == []
    planted = duckdb_literal_hits(_PLANT)
    assert planted, "a planted read=\"duckdb\" in serve code must fail the scan"
    assert duckdb_literal_hits(_PLANT.replace('read="duckdb"', "read=SERVING_DIALECT")) == []
    package = serve_package_duckdb_hits(_PACKAGE)
    assert package == [], package
    assert SERVING_DIALECT == "duckdb"
