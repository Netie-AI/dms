"""GEN-INTENT-01 through Executor.live_ask.

Credit cleared on a grammar list must survive the live_ask restamp, on the
aggregate-replaced path and the empty-generate path. A category list serves
only when every token is allowed. Anything else abstains
``list_unhandled_terms``. ``which skus are chemicals`` is a list, not a
stock-value aggregate.
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
from dms_executor.ontology import sql_is_aggregate

_DISCARDED_MODEL = "discarded-live-ask-model"
_DISCARDED_PROVIDER = "discarded-live-ask-provider"
_AGG_SQL = (
    "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) "
    "AS stock_value_myr FROM inventory"
)
_EMPTY: dict[str, Any] = {
    "ok": False,
    "status": "REFUSE",
    "phase": "generate",
    "generative": {"ok": False, "sql": None, "climb": {"final": "EMPTY"}},
    "ontology": {
        "ok": True,
        "metrics": [{"id": "cq_chemicals_list", "importance": {"rank": 1}}],
    },
    "values": [],
}


class _Insights:
    def __init__(self, warehouse: Path, payload: dict[str, Any]) -> None:
        self.warehouse = warehouse
        self.payload = payload
        self.questions: list[str] = []
        self.sqls: list[str] = []

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.questions.append(question)
        return self.payload

    def submit(self, req: Any) -> QueryResult:
        kind = str((getattr(req, "plan", None) or {}).get("kind") or "")
        if kind != "sql":
            return QueryResult(ok=True, status="bound", run_id="run_live_bind")
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
            ok=True, status="ok", run_id="run_live_list", output={"rows": rows}
        )

    def ledger_append(self, _req: Any) -> Any:
        return type(
            "Led",
            (),
            {"entry_id": "led_live_list", "hash": "hash_live_list_not_entry"},
        )()

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
    payload: dict[str, Any],
) -> dict[str, Any]:
    db = ensure_demo_warehouse(tmp_path / "live_credit.duckdb")
    fake = _Insights(db, payload)
    exe = Executor(
        cortex=fake,  # type: ignore[arg-type]
        minter=_minter(monkeypatch),
        warehouse_path=db,
    )
    env = exe.live_ask(question, session_id="ses_live_credit", ask_path="generative")
    assert isinstance(env, dict)
    return env


def _agg_payload() -> dict[str, Any]:
    return {
        "query_sql": _AGG_SQL,
        "plan_source": "ontology_plan",
        "served_provider": _DISCARDED_PROVIDER,
        "served_model": _DISCARDED_MODEL,
        "generative": {"ok": True, "sql": _AGG_SQL},
        "generate_legs": {
            "count": 1,
            "legs": [
                {
                    "returned": "sql",
                    "served_provider": _DISCARDED_PROVIDER,
                    "served_model": _DISCARDED_MODEL,
                }
            ],
        },
        "ontology": {
            "ok": True,
            "metrics": [{"id": "cq_chemicals_list", "importance": {"rank": 1}}],
        },
    }


def _assert_credit_none(env: dict[str, Any]) -> None:
    assert env.get("served_model") == "none"
    assert env.get("served_provider") == "none"
    assert env.get("served_attribution") == "none"
    legs = (env.get("generate_legs") or {}).get("legs") or []
    assert legs
    for leg in legs:
        assert leg.get("served_model") == "none"
        assert leg.get("served_provider") == "none"
        assert leg.get("served_attribution") == "none"


def _assert_distinct_list(env: dict[str, Any]) -> None:
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    sql = str(env.get("sql_used") or "")
    assert not sql_is_aggregate(sql)
    assert "DISTINCT" in sql.upper()
    assert "stock_value_myr" not in sql
    assert "stock_value_myr" not in json.dumps(env.get("rows"))
    _assert_credit_none(env)


def test_live_ask_aggregate_list_keeps_cleared_model_credit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model SUM is replaced by the distinct list. Credit stays none."""
    env = _ask(tmp_path, monkeypatch, "List chemicals in inventory", _agg_payload())
    _assert_distinct_list(env)
    blob = json.dumps(env)
    assert _DISCARDED_MODEL not in blob
    assert _DISCARDED_PROVIDER not in blob


def test_live_ask_empty_generate_list_credit_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty generate is not a model credit. All three served fields are none."""
    env = _ask(tmp_path, monkeypatch, "List chemicals in inventory", _EMPTY)
    _assert_distinct_list(env)


@pytest.mark.parametrize(
    "question",
    [
        "which skus are chemicals",
        "what skus are chemicals",
        "which items are in chemicals",
    ],
)
def test_live_ask_sku_list_phrasings_are_not_stock_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, question: str
) -> None:
    env = _ask(tmp_path, monkeypatch, question, _EMPTY)
    _assert_distinct_list(env)


@pytest.mark.parametrize(
    ("question", "tokens"),
    [
        ("List chemicals at WH-B", ("at", "wh-b")),
        ("chemicals in warehouse A", ("warehouse", "a")),
        ("list chemical skus from supplier X", ("from", "supplier", "x")),
    ],
)
def test_live_ask_unhandled_list_token_abstains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    tokens: tuple[str, ...],
) -> None:
    env = _ask(tmp_path, monkeypatch, question, _EMPTY)
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env.get("rows") == []
    sql = str(env.get("sql_used") or "")
    assert "stock_value_myr" not in sql
    assert not sql_is_aggregate(sql)
    blob = str(env.get("text") or "") + " " + " ".join(
        str(a) for a in (env.get("assumptions") or [])
    )
    assert "list_unhandled_terms:" in blob
    for tok in tokens:
        assert tok in blob
    assert "SKU-GAMMA" not in json.dumps(env.get("rows"))
