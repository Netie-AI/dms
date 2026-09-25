"""ORACLE-FIX-01 / dms#301: curated oracle txn_type matches the DMS demo seed.

Harness-only. Seeded tmp lake via ensure_demo_warehouse. No network, no Cortex,
no keys. Allowed txn_type values are parsed from demo_warehouse.py source, never
from a query result or an answer. CI fixtures, not live. Not COMPLETE.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).resolve().parents[1]
ORACLES_PATH = ROOT / "tests" / "fixtures" / "curated_ceo" / "oracles.yaml"
SEED_PATH = ROOT / "packages" / "executor" / "dms_executor" / "demo_warehouse.py"

# dms#301: last_audit_date / INTERVAL stays its own ORACLE_ERROR category.
_OWN_ORACLE_ERROR = frozenset({"cq_audit_overdue"})

_EQ_SINGLE = re.compile(r"\btxn_type\s*=\s*'([^']*)'", re.IGNORECASE)
_EQ_DOUBLE = re.compile(r'\btxn_type\s*=\s*"([^"]*)"', re.IGNORECASE)
_IN_LIST = re.compile(r"\btxn_type\s+IN\s*\((.*?)\)", re.IGNORECASE | re.DOTALL)
_STR_LIT = re.compile(r"'([^']*)'|\"([^\"]*)\"")


def _load_demo_warehouse() -> ModuleType:
    """Load demo_warehouse.py as a file. Package __init__ imports Cortex HTTP."""
    spec = importlib.util.spec_from_file_location("dms_demo_warehouse_harness", SEED_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _txn_types_written_by_seed() -> frozenset[str]:
    """Fourth field of the `_seed` `rows = [...]` list in demo_warehouse.py."""
    tree = ast.parse(SEED_PATH.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "rows" for t in node.targets):
            continue
        if not isinstance(node.value, ast.List):
            continue
        for elt in node.value.elts:
            if not isinstance(elt, ast.Tuple) or len(elt.elts) < 4:
                continue
            lit = elt.elts[3]
            if isinstance(lit, ast.Constant) and isinstance(lit.value, str):
                found.add(lit.value)
    assert found, f"no txn_type literals in seed rows list ({SEED_PATH})"
    return frozenset(found)


def _txn_type_literals(sql: str) -> list[str]:
    out = [m.group(1) for m in _EQ_SINGLE.finditer(sql)]
    out.extend(m.group(1) for m in _EQ_DOUBLE.finditer(sql))
    for block in _IN_LIST.finditer(sql):
        for lit in _STR_LIT.finditer(block.group(1)):
            out.append(lit.group(1) or lit.group(2))
    return out


def test_curated_oracles_match_demo_seed_txn_type(tmp_path: Path) -> None:
    seed_types = _txn_types_written_by_seed()
    raw = yaml.safe_load(ORACLES_PATH.read_text(encoding="utf-8")) or {}
    oracles = raw.get("oracles") or {}
    assert isinstance(oracles, dict) and oracles, f"no oracles in {ORACLES_PATH}"

    warehouse = _load_demo_warehouse()
    db = tmp_path / "dms_demo.duckdb"
    warehouse.ensure_demo_warehouse(db)

    bad_literals: list[str] = []
    txn_oracle_ids: list[str] = []
    errors: list[str] = []
    zero_rows: list[str] = []

    for oid, spec in oracles.items():
        body = spec or {}
        sql = str(body.get("sql") or "").strip()
        if not sql:
            continue
        lits = _txn_type_literals(sql)
        if lits:
            txn_oracle_ids.append(str(oid))
        for lit in lits:
            if lit not in seed_types:
                bad_literals.append(f"{oid}:{lit!r}")

        expect = str(body.get("expect") or "").lower()
        # Refuse SQL is Cortex-shaped documentation, not a DMS-lake PASS oracle.
        # cq_audit_overdue keeps its own ORACLE_ERROR category (last_audit_date).
        own_error = oid in _OWN_ORACLE_ERROR or expect in {"refuse", "abstain"}
        try:
            rows = warehouse.execute_sql(sql, path=db)
        except Exception as exc:  # surface oracle id; do not skip
            if own_error:
                continue
            errors.append(f"{oid}:{type(exc).__name__}:{exc}")
            continue
        if own_error:
            continue
        if lits and not rows:
            zero_rows.append(str(oid))

    assert len(txn_oracle_ids) >= 9, (
        f"expected >=9 oracles that filter txn_type, got {txn_oracle_ids}"
    )
    assert not bad_literals and not errors and not zero_rows, (
        f"txn_type literals not in seed {sorted(seed_types)}: {bad_literals}; "
        f"oracle errors: {errors}; "
        f"zero-row txn_type oracles: {zero_rows}"
    )
