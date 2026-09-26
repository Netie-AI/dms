"""SPACE-GEN-01 round 3: scope-analysed CTEs, full Space catalog, named refusals.

Root cause of the CTE bypasses: "is this name a CTE" was a hand-rolled walk up
the tree with no notion of scope. It exempted a real table that shared a name
with a CTE declared in *another* scope (inside a derived table or a
subquery), and a forward reference to a later CTE in the same WITH list,
which DuckDB binds to the real table. ``sql_grain._scope_real_tables`` now
asks sqlglot scope analysis; anything it cannot place (WITH RECURSIVE, a table
function, LATERAL) is a named refusal.

Every case posts ``POST /v1/chat/ask`` and asserts on the customer envelope
(``assert_envelope_valid``, badge, rendered text, rows). The rig, fake Cortex
and manifest-enforcing submit are the round-2/3 ones from ``test_space_gen_01``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
import sqlglot
from cortex_client.models import LedgerAppendRequest, LedgerAppendResponse
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dms_executor.demo_warehouse import DEMO_TABLES
from dms_executor.gen_path_refuse import (
    cortex_refusal_gap,
    customer_abstain_text,
    customer_gap_label,
)
from dms_executor.manifest import IntermediateKey, ManifestMinter
from dms_executor.semantic_retrieve import (
    SPACE_MAX_BYTES,
    SPACE_MAX_COLS,
    retrieve_space_context,
)
from dms_executor.sql_grain import real_table_labels, resolve_declared_relations
from sqlglot import exp
from test_space_gen_01 import (
    ACCOUNT,
    DISTRICT,
    SPACE_KEYS,
    _multiset,
    _oracle,
    _reasons,
    _RecordingCortex,
    _Rig,
    _rig,
)

SECRET = "secret_salary"
SECRET_ROWS = 7
Q = "How many accounts are there?"

#: Each reads the real, ungranted ``secret_salary`` in DuckDB while the old
#: hand-rolled check called that reference a CTE.
CTE_BYPASSES = {
    # A CTE declared inside a derived table does not cover the outer FROM.
    "derived_table_shadow": (
        "SELECT COUNT(*) AS n FROM (WITH secret_salary AS "
        "(SELECT account_id FROM bronze.financial_account) "
        "SELECT * FROM secret_salary) d, secret_salary"
    ),
    # The same forward reference inside a derived table: the hand-rolled walk
    # found ``secret_salary`` in the derived table's WITH and exempted it.
    "derived_table_forward_reference": (
        "SELECT COUNT(*) AS n FROM (WITH x AS (SELECT * FROM secret_salary), "
        "secret_salary AS (SELECT 1 AS y) SELECT * FROM x) d"
    ),
    # A reference to a CTE defined LATER in the same WITH list is the table.
    "forward_reference": (
        "WITH a AS (SELECT * FROM secret_salary), secret_salary AS "
        "(SELECT account_id FROM bronze.financial_account) SELECT COUNT(*) AS n FROM a"
    ),
    # Same name as a CTE in a subquery scope; the outer FROM is the table.
    "different_scopes": (
        "SELECT COUNT(*) AS n FROM secret_salary WHERE 1 IN "
        "(WITH secret_salary AS (SELECT 1 AS x) SELECT x FROM secret_salary)"
    ),
}
RECURSIVE_SQL = (
    "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r WHERE n < 3) "
    "SELECT COUNT(*) AS n FROM r"
)


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    """The real ``mint_manifest`` on a local key (as ``test_space_gen_01``)."""
    m = ManifestMinter()
    key = IntermediateKey(
        kid="test-kid",
        private_key=Ed25519PrivateKey.generate(),
        not_after=datetime.now(UTC) + timedelta(hours=1),
    )
    monkeypatch.setattr(m, "_ensure_key", lambda: key)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: key)
    monkeypatch.setattr(m, "close", lambda: None)
    return m


def _plant_secret(lake: Path) -> None:
    con = duckdb.connect(str(lake))
    try:
        con.execute(f"CREATE OR REPLACE TABLE main.{SECRET} (account_id VARCHAR, pay INT)")
        con.execute(
            f"INSERT INTO main.{SECRET} SELECT 's' || i::VARCHAR, i FROM range({SECRET_ROWS}) t(i)"
        )
    finally:
        con.close()


def _ask(rig: _Rig, question: str, **extra: Any) -> dict[str, Any]:
    from dms_executor.envelope import assert_envelope_valid

    r = rig.client.post(
        "/v1/chat/ask",
        json={
            "question": question,
            "session_id": "ses_space_gen_01_r3",
            "space_id": rig.space_id,
            **extra,
        },
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    return env


def _assert_named_abstain(env: dict[str, Any], shown: str) -> str:
    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    assert not env.get("drillthrough_token")
    text = str(env.get("text") or "")
    assert f"gap: {shown}" in text, text
    receipt = env["audit_receipt"]["unsure"]
    assert receipt["status"] == "abstain" and receipt["abstained"] is True
    return text


# --- 1. CTE bypasses: scope analysis, fail closed ------------------------------


@pytest.mark.parametrize("case", sorted(CTE_BYPASSES))
def test_cte_bypass_is_named_abstain_with_no_rows(
    tmp_path: Path, minter: ManifestMinter, case: str
) -> None:
    sql = CTE_BYPASSES[case]
    rig = _rig(tmp_path, minter, {"query_sql": sql, "plan_source": "ontology_plan"})
    _plant_secret(rig.lake)
    # The bypass is real: DuckDB reads the ungranted table through this SQL.
    con = duckdb.connect(str(rig.lake), read_only=True)
    try:
        con.execute(sql).fetchall()
        tree = sqlglot.parse_one(sql, read="duckdb")
        named = {t.name for t in tree.find_all(exp.Table)}
        assert SECRET in named
    finally:
        con.close()

    env = _ask(rig, Q)

    text = _assert_named_abstain(env, "validate:ungranted_table")
    assert SECRET not in text, text
    assert f"validate:ungranted:{SECRET}" in _reasons(env), env.get("assumptions")
    assert f"ungranted:{SECRET}" in env["audit_receipt"]["unsure"]["why"]
    assert rig.cortex.executed == [], "a bypass reached Cortex submit"
    assert rig.cortex.refused == []
    assert rig.cortex.asks == []


def test_recursive_cte_is_refused_by_name(tmp_path: Path, minter: ManifestMinter) -> None:
    rig = _rig(tmp_path, minter, {"query_sql": RECURSIVE_SQL, "plan_source": "ontology_plan"})
    env = _ask(rig, Q)

    text = _assert_named_abstain(env, "validate:sql_unanalysable:recursive_cte")
    assert "validate:sql_unanalysable:recursive_cte" in _reasons(env)
    assert rig.cortex.executed == []
    assert rig.cortex.asks == []
    assert "certify" in text


def test_scope_analysis_unit_cases() -> None:
    declared = SPACE_KEYS
    for sql in CTE_BYPASSES.values():
        assert resolve_declared_relations(sql, declared)[1] == f"ungranted:{SECRET}", sql
        assert SECRET in (real_table_labels(sql) or []), sql
    assert resolve_declared_relations(RECURSIVE_SQL, declared)[1] == (
        "sql_unanalysable:recursive_cte"
    )
    refused = {
        "SELECT * FROM read_csv('x.csv')": "sql_unanalysable:table_function",
        "SELECT * FROM bronze.financial_account a, LATERAL (SELECT 1 AS y) l": (
            "sql_unanalysable:lateral"
        ),
        "SELECT * FROM unnest([1, 2])": "sql_unanalysable:table_function",
        # DuckDB binds ``p`` to the CTE ``P``; sqlglot does not. Not provable.
        "WITH P AS (SELECT 1 AS x) SELECT * FROM p": "sql_unanalysable:cte_name_case",
        "SELECT FROM WHERE (": "sql_unanalysable:parse",
    }
    for sql, why in refused.items():
        assert resolve_declared_relations(sql, declared)[1] == why, sql
        assert real_table_labels(sql) is None, sql
    # A legitimate CTE still resolves: in-scope names only, and a
    # non-recursive CTE's own body reads the real table.
    ok = (
        "WITH p AS (SELECT district_id FROM bronze.financial_district), "
        "q AS (SELECT a.account_id FROM bronze.financial_account a JOIN p USING (district_id)) "
        "SELECT COUNT(*) AS n FROM q"
    )
    assert resolve_declared_relations(ok, declared) == (ok, None)
    assert set(real_table_labels(ok) or []) == SPACE_KEYS
    assert resolve_declared_relations(
        "WITH secret_salary AS (SELECT * FROM secret_salary) SELECT 1 FROM secret_salary",
        declared,
    )[1] == f"ungranted:{SECRET}"
    # Customer label: the scope tails are DMS-written, shown whole.
    assert customer_gap_label("validate:sql_unanalysable:recursive_cte") == (
        "validate:sql_unanalysable:recursive_cte"
    )
    assert customer_gap_label("validate:sql_unanalysable:made_up") == (
        "validate:sql_unanalysable"
    )


# --- 2. Full Space catalog on the wire -----------------------------------------


def _cortex_catalog(onto: dict[str, Any]) -> dict[str, list[str]]:
    """What Cortex ``parse_caller_ontology`` builds: the column allowlist."""
    assert onto.get("source") == "space", onto.get("source")
    raw = json.dumps(onto)
    assert len(raw.encode()) <= 64 * 1024
    ident = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    out: dict[str, list[str]] = {}
    for row in onto.get("schema") or []:
        name = str(row["table"])
        assert all(ident.fullmatch(p) for p in name.split(".")), name
        cols = list(row.get("columns") or [])
        assert len(cols) <= 256
        assert all(ident.fullmatch(c) for c in cols), cols
        out[name.lower()] = [c.lower() for c in cols]
    return out


@dataclass
class _AllowlistCortex(_RecordingCortex):
    """Insights that refuses SQL naming a column the caller did not declare.

    Mirrors Cortex ``validate_caller_sql``: the columns DMS sends are the
    allowlist. Records ledger appends so a post-row abstain can prove none.
    """

    ledger: list[Any] = field(default_factory=list)

    def compute_insights(self, question: str, **kw: Any) -> dict[str, Any]:
        out = super().compute_insights(question, **kw)
        sql = str(out.get("query_sql") or "")
        if not sql:
            return out
        catalog = _cortex_catalog(kw.get("ontology") or {})
        tree = sqlglot.parse_one(sql, read="duckdb")
        tables = [
            ".".join(p for p in (t.db, t.name) if p).lower() for t in tree.find_all(exp.Table)
        ]
        union = {c for t in tables for c in catalog.get(t, [])}
        outputs = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
        for col in tree.find_all(exp.Column):
            name = col.name.lower()
            if name and name != "*" and name not in outputs and name not in union:
                return {
                    "ok": True,
                    "status": "ABSTAIN",
                    "phase": "generate",
                    "refuse_reason": f"column {name} is not declared for {','.join(tables)}",
                    "values": [],
                }
        return out

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        self.ledger.append(req)
        return super().ledger_append(req)


def _allowlist_rig(tmp_path: Path, minter: ManifestMinter, payload: dict[str, Any]) -> _Rig:
    rig = _rig(tmp_path, minter, payload)
    cortex = _AllowlistCortex(warehouse=rig.lake, payload=payload)
    app = rig.client.app
    app.state.ask_service._cortex = cortex  # type: ignore[attr-defined]
    app.state.cortex = cortex  # type: ignore[attr-defined]
    rig.cortex = cortex
    return rig


def test_space_payload_sends_every_column_no_demo_aliases(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    sql = "SELECT COUNT(*) AS account_count FROM bronze.financial_account"
    rig = _allowlist_rig(tmp_path, minter, {"query_sql": sql, "plan_source": "ontology_plan"})
    env = _ask(rig, Q)

    sent = rig.cortex.insights[-1]["ontology"]
    catalog = _cortex_catalog(sent)
    # Every column of each granted table, though the question names none
    # (ingest lineage columns included: they are columns of the table).
    lineage = ["_src", "_ingest_id"]
    assert catalog == {
        ACCOUNT: ["account_id", "district_id", "frequency", *lineage],
        DISTRICT: ["district_id", "a2", "a3", *lineage],
    }, catalog
    assert set(sent["tables"]) == SPACE_KEYS
    assert sent["schema_truncated"] is False
    assert all(r["columns_truncated"] is False for r in sent["schema"])
    assert {r["table"]: r["column_count"] for r in sent["schema"]} == {ACCOUNT: 5, DISTRICT: 5}
    # Nothing of the demo pack rides along as this Space's ontology.
    assert not sent.get("measure_aliases"), sent.get("measure_aliases")
    assert not sent.get("measures") and not sent.get("objects")
    assert not sent.get("bound_values")
    assert "measure" not in (sent.get("intent_slots") or {})
    assert not set(catalog) & set(DEMO_TABLES)
    oracle = _oracle(rig.lake, sql)
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["rows"] == oracle == [{"account_count": 4}]
    assert "account_count=4" in str(env.get("text") or "")


def test_unmentioned_column_group_by_answers_l2_with_oracle_rows(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    # "frequencies" does not token-match the column ``frequency``; the old
    # question-filtered retrieve never sent it, and Cortex's allowlist then
    # refused the GROUP BY. The full catalog lets it answer.
    sql = (
        "SELECT frequency, COUNT(*) AS n FROM bronze.financial_account "
        "GROUP BY frequency ORDER BY frequency"
    )
    rig = _allowlist_rig(tmp_path, minter, {"query_sql": sql, "plan_source": "ontology_plan"})
    env = _ask(rig, "How many accounts are there for each of the frequencies?")

    assert "frequency" in _cortex_catalog(rig.cortex.insights[-1]["ontology"])[ACCOUNT]
    oracle = _oracle(rig.lake, sql)
    assert oracle == [
        {"frequency": "POPLATEK MESICNE", "n": 3},
        {"frequency": "POPLATEK TYDNE", "n": 1},
    ]
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert _multiset(env["rows"]) == _multiset(oracle)
    text = str(env.get("text") or "")
    assert "frequency=POPLATEK MESICNE, n=3" in text, text
    assert "frequency=POPLATEK TYDNE, n=1" in text, text
    assert rig.cortex.executed == [sql]
    assert len(rig.cortex.ledger) == 1


def test_space_context_caps_columns_with_an_explicit_flag(tmp_path: Path) -> None:
    lake = tmp_path / "wide.duckdb"
    con = duckdb.connect(str(lake))
    try:
        con.execute("CREATE SCHEMA bronze")
        wide = ", ".join(f"c{i:03d}_long_column_name_for_width INT" for i in range(400))
        con.execute(f"CREATE TABLE bronze.wide ({wide}, amount INT)")
    finally:
        con.close()
    ctx = retrieve_space_context(
        "total amount", warehouse=lake, grantable={"bronze.wide"}
    )
    (row,) = ctx["schema"]
    assert row["table"] == "bronze.wide"
    assert row["column_count"] == 401
    assert row["columns_truncated"] is True
    assert ctx["schema_truncated"] is True
    assert len(row["columns"]) <= SPACE_MAX_COLS
    # The question's column survives the cut.
    assert "amount" in row["columns"]
    assert len(json.dumps(ctx, sort_keys=True)) <= SPACE_MAX_BYTES
    assert "measure_aliases" not in ctx


# --- 3. GRAIN-GUARD abstain carries its reason on the receipt ------------------


def test_grain_abstain_receipt_names_reason_before_submit(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    sql = (
        "SELECT district_id, COUNT(*) AS n FROM bronze.financial_account GROUP BY district_id"
    )
    rig = _allowlist_rig(tmp_path, minter, {"query_sql": sql, "plan_source": "ontology_plan"})
    env = _ask(rig, "How many accounts are there in total?")

    text = _assert_named_abstain(env, "grain_mismatch:scalar_expected")
    assert "grain_mismatch:scalar_expected" in env["audit_receipt"]["unsure"]["why"]
    assert "3" not in text and "4" not in text, text
    # Pre-submit check: nothing executed, nothing appended to the ledger.
    assert rig.cortex.executed == []
    assert rig.cortex.ledger == []


def test_post_row_grain_abstain_names_reason_and_skips_ledger(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    sql = "SELECT account_id FROM bronze.financial_account ORDER BY account_id LIMIT 2"
    rig = _allowlist_rig(tmp_path, minter, {"query_sql": sql, "plan_source": "ontology_plan"})
    env = _ask(rig, "Show all account ids")

    text = _assert_named_abstain(env, "grain_mismatch:truncated")
    assert "grain_mismatch:truncated" in env["audit_receipt"]["unsure"]["why"]
    assert "account_id=1" not in text
    # The row check needs rows, so the query ran; the answer was never
    # certified, so nothing reached the ledger.
    assert rig.cortex.executed == [sql]
    assert rig.cortex.ledger == []


# --- 4. Cortex's named refusal is the answer, not a contract-ask fall-through ---


@pytest.mark.parametrize("with_ranking", [True, False])
def test_cortex_refusal_reason_is_a_named_gap(
    tmp_path: Path, minter: ManifestMinter, with_ranking: bool
) -> None:
    raw = (
        "sql reads a table outside the caller catalog: "
        f"table {SECRET} is not in the caller catalog"
    )
    payload: dict[str, Any] = {
        "ok": True,
        "status": "ABSTAIN",
        "phase": "generate",
        "refuse_reason": raw,
        "generative": {"ok": False, "sql": None, "refuse_reason": raw, "climb": {}},
        "values": [],
    }
    if with_ranking:
        # A ranking alongside the refusal used to read as a plain miss and
        # fall through to /v1/contract/ask's generic text.
        payload["ontology"] = {"ok": True, "metrics": [{"id": "cq_revenue_total"}]}
    rig = _rig(tmp_path, minter, payload)
    env = _ask(rig, "What is the total salary?")

    text = _assert_named_abstain(env, "cortex_refused:table_not_in_catalog")
    assert SECRET not in text, text
    assert "Cortex refused: no governed answer" not in text
    assert "cortex_refused:table_not_in_catalog" in env["audit_receipt"]["unsure"]["why"]
    assert f"Cortex refuse_reason: {raw}" in _reasons(env)
    assert rig.cortex.asks == [], "fell through to the contract ask"
    assert rig.cortex.executed == []


def test_cortex_refusal_codes_are_closed() -> None:
    cases = {
        "table x is not in the caller catalog": "cortex_refused:table_not_in_catalog",
        "bare table t is ambiguous in the caller catalog: a.t,b.t": (
            "cortex_refused:ambiguous_table"
        ),
        "column zz is not declared by the caller": "cortex_refused:column_not_declared",
        "table function refused: read_csv('x')": "cortex_refused:table_function",
        "something the model said about secret_salary": "cortex_refused:unclassified",
    }
    for raw, gap in cases.items():
        got = cortex_refusal_gap({"refuse_reason": raw})
        assert got is not None and got[0] == gap, (raw, got)
        text = customer_abstain_text(got[0])
        assert f"gap: {gap}" in text, text
        for name in ("secret_salary", "read_csv", "zz", "a.t"):
            assert name not in text, (name, text)
    assert cortex_refusal_gap({}) is None
    assert customer_gap_label("cortex_refused:invented_code") == "cortex_refused"


def test_named_gap_tails_do_not_echo_model_names() -> None:
    for reason, shown, hidden in (
        ("unknown_measure: no measure named 'secret_pay'", "unknown_measure", "secret_pay"),
        ("no_path: no chain from secret_obj", "no_path", "secret_obj"),
        ("unknown_column: secret_col", "unknown_column", "secret_col"),
        ("unrequested_grain:secret_col", "unrequested_grain", "secret_col"),
        ("unrequested_measure:secret_alias", "unrequested_measure", "secret_alias"),
        ("unrequested_column:secret_col", "unrequested_column", "secret_col"),
    ):
        text = customer_abstain_text(reason)
        assert f"gap: {shown}" in text, text
        assert hidden not in text, text
    # DMS-derived tails still read whole.
    assert "gap: grain_mismatch:scalar_expected" in customer_abstain_text(
        "grain_mismatch:scalar_expected"
    )
    assert "gap: grain_unanalysable:window_function" in customer_abstain_text(
        "grain_unanalysable:window_function"
    )
