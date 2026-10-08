"""GEN-INTENT-01 sql-path grounding through Executor.live_ask.

Correct model SQL is served with that model's credit. The same chemicals
list, with a question word the SQL does not ground, abstains
``ungrounded_qualifier``. Must fail on 10c57961 (every row abstains before
the SQL is read).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.ontology import is_list_intent

_MODEL = "b1-stub-model"
_PROVIDER = "b1-stub-provider"
_SUPPLIERS = (
    "SELECT DISTINCT s.supplier_name "
    "FROM inventory AS i "
    "JOIN suppliers AS s ON i.supplier_id = s.supplier_id "
    "WHERE lower(CAST(i.category AS VARCHAR)) LIKE '%chemical%'"
)
_VALUE = (
    "SELECT ROUND(SUM(i.quantity_kg * i.unit_cost_myr), 2) AS total_value "
    "FROM inventory AS i "
    "WHERE lower(CAST(i.category AS VARCHAR)) LIKE '%chemical%'"
)
_EXPIRE = (
    "SELECT i.sku, i.expiry_date "
    "FROM inventory AS i "
    "WHERE lower(CAST(i.category AS VARCHAR)) LIKE '%chemical%'"
)
_LIST = (
    "SELECT DISTINCT i.sku "
    "FROM inventory AS i "
    "WHERE lower(CAST(i.category AS VARCHAR)) LIKE '%chemical%'"
)


class _Insights:
    def __init__(self, warehouse: Path, sql: str) -> None:
        self.warehouse = warehouse
        self.sql = sql
        self.sqls: list[str] = []

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        return {
            "query_sql": self.sql,
            "plan_source": "ontology_plan",
            "served_provider": _PROVIDER,
            "served_model": _MODEL,
            "generative": {"ok": True, "sql": self.sql},
            "generate_legs": {
                "count": 1,
                "legs": [
                    {
                        "returned": "sql",
                        "served_provider": _PROVIDER,
                        "served_model": _MODEL,
                    }
                ],
            },
        }

    def submit(self, req: Any) -> QueryResult:
        kind = str((getattr(req, "plan", None) or {}).get("kind") or "")
        if kind != "sql":
            return QueryResult(ok=True, status="bound", run_id="run_b1_bind")
        sql = str((getattr(req, "body", None) or {}).get("sql") or "")
        self.sqls.append(sql)
        con = connect_file(self.warehouse)
        try:
            rel = con.execute(sql)
            cols = [d[0] for d in rel.description] if rel.description else []
            rows = [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
        finally:
            con.close()
        return QueryResult(
            ok=True, status="ok", run_id="run_b1_sql", output={"rows": rows}
        )

    def ledger_append(self, _req: Any) -> Any:
        return type("Led", (), {"entry_id": "led_b1", "hash": "hash_b1_not_entry"})()

    def ask(self, _req: Any) -> Any:
        raise AssertionError("contract ask ran")


def _minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-08T00:00:00+00:00",
            expires_at="2026-10-08T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)
    return minter


def _ask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    sql: str,
) -> dict[str, Any]:
    db = ensure_demo_warehouse(tmp_path / "b1.duckdb")
    fake = _Insights(db, sql)
    exe = Executor(
        cortex=fake,  # type: ignore[arg-type]
        minter=_minter(monkeypatch),
        warehouse_path=db,
    )
    env = exe.live_ask(question, session_id="ses_b1", ask_path="generative")
    assert isinstance(env, dict)
    return env


def _cells(env: dict[str, Any]) -> list[Any]:
    out: list[Any] = []
    for row in env.get("rows") or []:
        out.extend(row.values())
    return out


def _norm_sql(sql: str) -> str:
    return " ".join(sql.split())


def _assert_model_credit(env: dict[str, Any], sql: str) -> None:
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["route"] == "generated"
    assert env["lane"] == "generative"
    assert env["served_model"] == _MODEL
    assert env["served_provider"] == _PROVIDER
    assert env["served_attribution"] == "reported"
    assert env["served_attribution"] != "none"
    assert _norm_sql(str(env.get("sql_used") or "")) == _norm_sql(sql)
    blob = json.dumps(env)
    assert "grammar list:" not in blob
    assert "list_unhandled_terms:" not in blob
    assert "ungrounded_qualifier:" not in blob
    legs = (env.get("generate_legs") or {}).get("legs") or []
    assert legs
    for leg in legs:
        assert leg.get("served_model") == _MODEL
        assert leg.get("served_provider") == _PROVIDER


def _assert_named_abstain(env: dict[str, Any], word: str) -> None:
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["route"] == "generated"
    assert env["lane"] == "generative"
    assert env.get("rows") == []
    reason = f"ungrounded_qualifier:{word}"
    text = str(env.get("text") or "")
    assert reason in text
    blob = text + " " + " ".join(str(a) for a in (env.get("assumptions") or []))
    assert reason in blob
    assert "SKU-GAMMA" not in json.dumps(env.get("rows"))


def test_total_value_of_chemicals_is_not_list_intent() -> None:
    assert not is_list_intent("total value of chemicals")


def test_which_suppliers_sell_chemicals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _ask(tmp_path, monkeypatch, "which suppliers sell chemicals", _SUPPLIERS)
    _assert_model_credit(env, _SUPPLIERS)
    assert _cells(env) == ["Orbit Packing"]


def test_total_value_of_chemicals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _ask(tmp_path, monkeypatch, "total value of chemicals", _VALUE)
    _assert_model_credit(env, _VALUE)
    assert [float(v) for v in _cells(env)] == [1800.0]


def test_when_chemicals_expire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _ask(tmp_path, monkeypatch, "when do the chemicals expire", _EXPIRE)
    _assert_model_credit(env, _EXPIRE)
    assert len(env["rows"]) == 1
    row = env["rows"][0]
    assert "GAMMA" in str(row.get("sku"))
    assert str(row.get("expiry_date")).startswith("2020-01-15")


def test_list_chemicals_in_stock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _ask(tmp_path, monkeypatch, "list chemicals in stock", _LIST)
    _assert_model_credit(env, _LIST)
    assert set(_cells(env)) == {"SKU-GAMMA"}


@pytest.mark.parametrize(
    ("question", "word"),
    [
        ("list chemicals at WH-B", "wh-b"),
        ("chemicals from supplier X", "x"),
        ("list all chemicals except SKU-GAMMA", "except"),
    ],
)
def test_lying_sql_abstains_ungrounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    word: str,
) -> None:
    """Stub SQL is the chemicals list: no warehouse, no supplier, no exclusion."""
    env = _ask(tmp_path, monkeypatch, question, _LIST)
    _assert_named_abstain(env, word)
