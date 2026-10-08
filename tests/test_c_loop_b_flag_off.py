"""Flag off matches main f9ffc3e except attribution and credit fields.

The word list below is the review stub only. Product code has none.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.manifest import ManifestMinter, SessionAcl

_FIXTURE = Path(__file__).resolve().parents[0] / "fixtures" / "c_loop_b"
_COLD = "SELECT location_code FROM locations WHERE is_cold_storage = TRUE"
_DESC = (
    "SELECT category, ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS stock_value_myr "
    "FROM inventory GROUP BY category ORDER BY stock_value_myr DESC LIMIT 3"
)
_CHEM = "SELECT sku FROM inventory WHERE category = 'CHEMICALS'"
_TOP = (
    "SELECT sku, ROUND(SUM(quantity_kg * unit_cost_myr), 2) AS sales_value_myr "
    "FROM transactions GROUP BY sku ORDER BY sales_value_myr DESC LIMIT 5"
)
_AUDIT = (
    "SELECT supplier_name FROM suppliers "
    "WHERE last_audit_date < DATE '2026-07-01'"
)
_FREIGHT = (
    "SELECT destination_location_id, ROUND(SUM(freight_cost_myr), 2) AS freight_myr "
    "FROM shipments GROUP BY destination_location_id"
)
_DROP = {
    "served_attribution",
    "served_model",
    "served_provider",
    "ov_key_id",
    "as_of",
    "engine_as_of",
    "engine_as_of_after",
    "engine_timezone",
    "engine_timezone_after",
}
_CREDIT = (
    "served_attribution",
    "served_model",
    "served_provider",
    "ov_key_id",
)


def _sql_for(question: str) -> str:
    low = question.casefold()
    if any(word in low for word in ("lowest", "least", "bottom", "cheapest")):
        return _DESC
    if "chemical" in low:
        return _CHEM
    if "sku" in low or "categoty" in low or "categor" in low:
        return _TOP if "sku" in low else _DESC
    if "audit" in low:
        return _AUDIT
    if "freight" in low or "destination" in low:
        return _FREIGHT
    return _COLD


class _Cortex:
    def __init__(self, db: Path) -> None:
        self._db = db

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        sql = _sql_for(question)
        return {
            "phase": "generate",
            "query_sql": sql,
            "served_model": "stub-model",
            "served_provider": "stub-provider",
            "ov_key_id": "ovk-stub",
            "generative": {"sql": sql, "ok": True, "stamp": {"impl": "stub"}},
            "ontology": {"metrics": [{"id": "cq_cold_storage"}]},
        }

    def submit(self, req: Any) -> QueryResult:
        body = getattr(req, "body", None)
        sql = str(body.get("sql") or "") if isinstance(body, dict) else ""
        con = connect_file(self._db)
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_flag", output={"rows": rows})

    def ledger_append(self, _req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_flag", hash="hash_flag")

    def ask(self, req: AskRequest) -> AskResponse:
        del req
        return AskResponse(
            answer="There are 5 locations.",
            badge="certified",
            sql_used="SELECT COUNT(*) AS location_count FROM locations",
            rows=[{"location_count": 5}],
            audit_id="aud_flag",
            route="sql",
        )


def _questions() -> list[str]:
    root = Path(__file__).resolve().parents[1]
    questions_path = root / "tests/fixtures/curated_ceo/questions.yaml"
    pack = yaml.safe_load(questions_path.read_text(encoding="utf-8"))
    out = [str(row["question"]) for row in pack["questions"]]
    out.extend(
        [
            "List chemicals in inventory",
            "What chemicals do we have in stock?",
            "top 5 SKUs by sales",
            "show the five best-selling SKUs",
            "top 3 categories by value",
            "show top 3 categoty sales",
            "which supplier audits are overdue",
            "what's the freight cost by destination",
            "what's the bottom three categories by stock value",
            "which three categories are sitting on the least inventory value",
            "give me the lowest five categories by stock value",
            "the three cheapest categories by stock",
            "who is the third-lowest category by stock value",
            "are there any chemicals expiring in November 2026",
            "can you list the chemicals we carry",
            "show me our top five SKUs by sales",
            "top 3 categoty by value",
            "which supplier audits are overdue",
            "what's freight looking like by destination",
            "how many florbs did wibble sell last quarter",
            "what is the meaning of life for this warehouse",
        ]
    )
    return out


def _norm(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _norm(v) for k, v in obj.items() if k not in _DROP}
    if isinstance(obj, list):
        return [_norm(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, 6)
    return obj


def test_flag_off_envelopes_match_main_except_credit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    monkeypatch.delenv("DMS_LANE_ONTOLOGY_RANKED", raising=False)
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    db = tmp_path / "flag.duckdb"
    ensure_demo_warehouse(db)
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
            issued_at="2026-09-26T00:00:00+00:00",
            expires_at="2026-09-26T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    exe = Executor(cortex=_Cortex(db), minter=minter, warehouse_path=db)  # type: ignore[arg-type]
    golden = json.loads((_FIXTURE / "flag_off_main.json").read_text(encoding="utf-8"))
    credit = json.loads((_FIXTURE / "flag_off_credit.json").read_text(encoding="utf-8"))
    questions = _questions()
    assert len(questions) == len(golden) == len(credit) == 73
    assert os.environ.get("DMS_CLOOP_B") is None
    for index, question in enumerate(questions):
        env = exe.live_ask(question, session_id="ses_flag")
        got = json.loads(json.dumps(_norm(env), sort_keys=True, default=str))
        assert got == golden[index]["envelope"], question
        for name in _CREDIT:
            assert env.get(name) == credit[index][name], (question, name)
        if env.get("served_attribution") == "none":
            assert env.get("served_model") is None
            assert env.get("served_provider") is None
            assert env.get("ov_key_id") is None
