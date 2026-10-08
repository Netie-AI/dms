"""GEN-INTENT-01 check bar: oracle list values, and no model credit.

The run-2 oracle file is not in this repo. Its sha256 is recorded on the
fixture. This test does not open oracles.yaml or the scorer.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dms_executor.demo_grants import DEMO_SPACE_GRANTS
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.ontology import demo_ontology, sql_is_aggregate

_SOURCE_SHA = "008f5bb1b8ac775a677501937a9cd8c444682986cf8008a04beb9fd27a162a12"
_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "gen_intent_01"
    / "chemicals_list_oracle.json"
)
_FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_DISCARDED_MODEL = "discarded-model-not-served"
_QUESTION = "List chemicals in inventory"


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_check", hash="hash_check_not_entry")


def _submit(db: Path):
    def _run(sql: str) -> Any:
        con = connect_file(db)
        try:
            rel = con.execute(sql)
            cols = [d[0] for d in rel.description] if rel.description else []
            rows = [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
        finally:
            con.close()
        return SimpleNamespace(
            ok=True, status="ok", run_id="run_check", output={"rows": rows}
        )

    return _run


def _scalars(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        assert len(row) == 1, row
        out.append(str(next(iter(row.values()))))
    return out


def _oracle_rows(db: Path, sql: str) -> list[dict[str, Any]]:
    con = connect_file(db)
    try:
        rel = con.execute(sql)
        cols = [d[0] for d in rel.description] if rel.description else []
        return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
    finally:
        con.close()


def test_chemicals_lists_match_run2_oracle_values(tmp_path: Path) -> None:
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert raw["source_sha256"] == _SOURCE_SHA
    db = tmp_path / "oracle.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None and onto.verified
    spaces = {"cq_chemicals_list": _FINANCE, "ops_chemicals_list": _OPS}
    grants = {
        _FINANCE: set(DEMO_SPACE_GRANTS[_FINANCE][1]),
        _OPS: set(DEMO_SPACE_GRANTS[_OPS][1]),
    }
    for case_id, spec in raw["cases"].items():
        sql = str(spec["sql"])
        pinned = [str(v) for v in spec["values"]]
        oracle_vals = _scalars(_oracle_rows(db, sql))
        assert oracle_vals == pinned, case_id
        payload = {
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
        space = spaces[case_id]
        env = maybe_generative_ask(
            _QUESTION,
            space_id=space,
            warehouse=db,
            grantable=grants[space],
            compute=lambda _c, payload=payload: payload,
            submit=_submit(db),
            ledger_append=_ledger,
            ontology=onto,
            bind_on_miss=False,
        )
        assert env is not None, case_id
        assert_envelope_valid(env)
        ask_vals = _scalars(list(env.get("rows") or []))
        if ask_vals != oracle_vals:
            assert env["badge"] == "ABSTAIN", case_id
            blob = str(env.get("text") or "") + " " + " ".join(
                str(a) for a in (env.get("assumptions") or [])
            )
            assert "generate_empty_no_ranking_answer" in blob or (
                "unrequested_measure:" in blob
            ), case_id
            continue
        assert env["badge"] == "L2_VALIDATED", case_id
        assert env.get("plan_source") == "ontology_plan", case_id
        assert not sql_is_aggregate(str(env.get("sql_used") or "")), case_id
        assert ask_vals == ["SKU-GAMMA"], case_id


def test_grammar_list_does_not_credit_the_discarded_model(tmp_path: Path) -> None:
    db = tmp_path / "credit.duckdb"
    ensure_demo_warehouse(db)
    onto = load_verified_ontology(db, demo_ontology(db))
    assert onto is not None
    sql = (
        "SELECT ROUND(SUM(quantity_kg * unit_cost_myr), 2) "
        "AS stock_value_myr FROM inventory"
    )
    payload = {
        "query_sql": sql,
        "plan_source": "ontology_plan",
        "served_provider": "discarded-provider",
        "served_model": _DISCARDED_MODEL,
        "generative": {"ok": True, "sql": sql},
        "generate_legs": {
            "count": 1,
            "legs": [
                {
                    "returned": "sql",
                    "served_provider": "discarded-provider",
                    "served_model": _DISCARDED_MODEL,
                }
            ],
        },
        "ontology": {
            "ok": True,
            "metrics": [{"id": "cq_chemicals_list", "importance": {"rank": 1}}],
        },
    }
    env = maybe_generative_ask(
        _QUESTION,
        space_id=_FINANCE,
        warehouse=db,
        grantable=set(DEMO_SPACE_GRANTS[_FINANCE][1]),
        compute=lambda _c: payload,
        submit=_submit(db),
        ledger_append=_ledger,
        ontology=onto,
        bind_on_miss=False,
    )
    assert env is not None
    assert_envelope_valid(env)
    assert env["plan_source"] == "ontology_plan"
    assert env.get("served_attribution") == "none"
    assert env.get("served_model") == "none"
    assert env.get("served_provider") == "none"
    legs = (env.get("generate_legs") or {}).get("legs") or []
    assert legs
    for leg in legs:
        assert leg.get("served_model") == "none"
        assert leg.get("served_provider") == "none"
    assert _DISCARDED_MODEL not in json.dumps(env)
    assert "discarded-provider" not in json.dumps(env)
    assert not sql_is_aggregate(str(env.get("sql_used") or ""))
