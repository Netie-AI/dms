"""GRANT-STRUCT-01: the ask path grants parsed objects, not question wording.

Synthetic names only (qwest_box, qwest_hidden, alpha_metric, beta_secret).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import DEMO_TABLES, Executor
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.grant_struct import serve_gap, sqlglot_dialect
from dms_executor.manifest import ManifestMinter, SessionAcl

OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
HIDDEN = "qwest_hidden.csv"
FILE_SQL = f"SELECT count(*) AS n FROM '{HIDDEN}'"

# Word order, spelling, and a question that never names the file.
PHRASINGS = (
    "In qwest_box.xlsx on the Ledger sheet, what are the top 3 categories?",
    "what are the top 3 categories in qwest_box.xlsx on the Ledger sheet",
    "row count for workbook qwest_box.xlsx",
    "in qwest_box.xlsx on the ledger sheet how many rows",
    "Inn qwest_box.xlsx on the Ledger sheeet, how many rows",
    "qwest_box.xlsx fail Ledger helaian berapa jumlah",
    "Combien de lignes dans le classeur qwest_box.xlsx",
    "文件 qwest_box.xlsx 的行数是多少",
    "rows please from the ledger tab inside qwest_box.xlsx",
    "tell me about the extract, the workbook is qwest_box.xlsx",
    "por favor filas del libro qwest_box.xlsx hoja Ledger",
    "How many rows are there?",
)


class _Model:
    """Insights returns one statement. Submit records anything that executes."""

    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.submits: list[Any] = []
        self.asks: list[Any] = []

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
        return {"query_sql": self.sql, "plan_source": "ontology_plan"}

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            return QueryResult(
                ok=True,
                status="ok",
                run_id="run_grant_struct",
                output={"rows": [{"n": 1}]},
            )
        return QueryResult(ok=True, status="bound", run_id="run_grant_struct_bind")

    def ledger_append(self, req: Any) -> Any:
        from cortex_client.models import LedgerAppendResponse

        return LedgerAppendResponse(entry_id="led_grant_struct", hash="hash_grant_struct_x")

    def ask(self, req: Any) -> Any:
        from cortex_client.models import AskResponse

        self.asks.append(req)
        return AskResponse(answer="fallback", abstained=True, badge="abstain", rows=[])


def _minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="grant-struct-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)
    return minter


def _live(
    question: str, sql: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], _Model]:
    model = _Model(sql)
    exe = Executor(
        cortex=model, minter=_minter(monkeypatch), warehouse_path=None
    )  # type: ignore[arg-type]
    env = exe.live_ask(question, space_id=OPS, session_id="ses_grant_struct")
    assert_envelope_valid(env)
    return env, model


def _reason(env: dict[str, Any]) -> str:
    return " ".join(str(item) for item in (env.get("assumptions") or []))


@pytest.mark.parametrize("question", PHRASINGS)
def test_phrasings_refuse_the_file_the_sql_reads(
    question: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, model = _live(question, FILE_SQL, monkeypatch)
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["sql_used"] is None
    assert model.submits == []
    assert HIDDEN not in json.dumps(env)
    # A named ungranted sheet refuses before the model (dms#284).
    # Any other wording is the SQL the model wrote.
    from dms_executor.bronze_sheet_ask import bronze_lane_table

    if bronze_lane_table(question):
        assert "ungranted_table:" in _reason(env)
        assert model.asks == []
    else:
        assert "sql_relation_not_granted" in _reason(env)


def test_sql_shapes_refuse_before_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    """File readers, a CTE, a subquery, a quoted name, a schema name."""
    shapes = {
        "read_csv": "SELECT * FROM read_csv('qwest_hidden.csv')",
        "read_parquet": "SELECT * FROM read_parquet('qwest_pack.parquet')",
        "glob": "SELECT * FROM glob('qwest_pack.parquet')",
        "attach": "ATTACH 'qwest_side.duckdb' AS side",
        "cte_file": "WITH src AS (SELECT * FROM 'qwest_hidden.csv') SELECT count(*) AS n FROM src",
        "cte_table": "WITH src AS (SELECT * FROM beta_secret) SELECT count(*) AS n FROM src",
        "subquery": "SELECT count(*) AS n FROM (SELECT * FROM beta_secret) s",
        "quoted": 'SELECT count(*) AS n FROM "beta_secret"',
        "qualified": "SELECT count(*) AS n FROM hidden.beta_secret",
        "read_xlsx": "SELECT * FROM read_xlsx('qwest_hidden.xlsx')",
    }
    for name, sql in shapes.items():
        classified = serve_gap(sql, grantable=set(), dialect="duckdb")
        env, model = _live("How many rows are there?", sql, monkeypatch)
        assert env["abstained"] is True, name
        assert env["rows"] == [], name
        assert model.submits == [], name
        blob = json.dumps(env)
        assert "qwest_hidden" not in blob, name
        assert "qwest_pack" not in blob, name
        assert "qwest_side" not in blob, name
        named = name in {"cte_table", "subquery", "quoted", "qualified"}
        assert named or "beta_secret" not in blob, name
        if name in {"glob", "cte_file", "read_xlsx"}:
            assert classified == "sql_relation_not_granted"
            assert "sql_relation_not_granted" in _reason(env)
        elif name == "qualified":
            assert classified == "sql_relation_not_granted"
            assert "sql_relation_not_granted" in _reason(env)
        elif name in {"cte_table", "subquery", "quoted"}:
            assert classified == "ungranted:beta_secret"
            assert "ungranted:beta_secret" in _reason(env)
        else:
            # read_csv / read_parquet / ATTACH: the existing scanner refuses
            # first. The allow-list would also refuse them.
            assert classified is not None and classified.startswith("hostile_sql:")
            assert "hostile_sql:" in _reason(env)


def test_harmless_wording_serves_granted_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A question that is not the sheet lane. The statement reads a granted table.

    An ungranted sheet refuses before the model. That case is the bronze
    grant test, not this one.
    """
    import duckdb
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    db = tmp_path / "granted.duckdb"
    ensure_demo_warehouse(db)
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE TABLE alpha_metric (category VARCHAR, n DOUBLE)")
        con.execute("INSERT INTO alpha_metric VALUES ('north', 3), ('south', 1), ('east', 2)")
    finally:
        con.close()
    monkeypatch.setattr(
        "dms_executor.DEMO_TABLES",
        (*DEMO_TABLES, "alpha_metric"),
    )

    import dms_executor as pkg

    real = pkg.Executor.grantable_tables

    def _grants(self: Executor, *, space_id: str | None) -> list[str]:
        base = list(real(self, space_id=space_id))
        if "alpha_metric" not in base:
            base.append("alpha_metric")
        return base

    monkeypatch.setattr(pkg.Executor, "grantable_tables", _grants)
    sql = "SELECT category, SUM(n) AS n FROM alpha_metric GROUP BY category LIMIT 3"
    model = _Model(sql)
    exe = Executor(cortex=model, minter=_minter(monkeypatch), warehouse_path=db)  # type: ignore[arg-type]
    env = exe.live_ask(
        "How many rows are there?",
        space_id=OPS,
        session_id="ses_grant_struct_ok",
    )
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["rows"]
    assert "ungranted" not in _reason(env)
    assert any(
        "alpha_metric" in str(getattr(req, "body", {}) or {})
        or "alpha_metric" in json.dumps(req, default=str)
        for req in model.submits
    )


def test_dialect_is_an_input() -> None:
    sql = "SELECT * FROM `secret_tbl`"
    assert sqlglot_dialect("postgresql") == "postgres"
    assert sqlglot_dialect("sqlserver") == "tsql"
    assert sqlglot_dialect("mysql") == "mysql"
    assert sqlglot_dialect("no-such-engine") is None
    assert serve_gap(sql, grantable=set(), dialect="mysql") == "ungranted:secret_tbl"
    assert serve_gap(sql, grantable=set(), dialect="duckdb") == "ungranted:unparsed"
    assert serve_gap(sql, grantable=set(), dialect="no-such-engine") == "sql_dialect_unknown"
    assert serve_gap("", grantable={"alpha_metric"}, dialect="duckdb") == "ungranted:unparsed"
    assert (
        serve_gap("SELECT n FROM alpha_metric", grantable={"alpha_metric"}, dialect="duckdb")
        is None
    )


def test_parse_failure_does_not_retry_or_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    submits: list[str] = []

    def _submit(sql: str) -> Any:
        submits.append(sql)
        return QueryResult(ok=True, status="ok", run_id="run_x", output={"rows": [{"n": 1}]})

    def _ledger(_payload: dict[str, Any]) -> Any:
        from cortex_client.models import LedgerAppendResponse

        return LedgerAppendResponse(entry_id="led_x", hash="hash_x_not_led")

    env = maybe_generative_ask(
        "How many rows are there?",
        grantable={"alpha_metric"},
        compute=lambda _c: {
            "query_sql": "SELECT * FROM `secret_tbl`",
            "plan_source": "ontology_plan",
            "ontology": {"ok": True, "metrics": [{"id": "sku_count"}]},
        },
        submit=_submit,
        ledger_append=_ledger,
        dialect="duckdb",
    )
    assert env is not None
    assert env["abstained"] is True
    assert submits == []
    assert "secret_tbl" not in json.dumps(env)
    assert "ungranted:unparsed" in _reason(env)


def test_qualified_bronze_name_is_not_a_bare_grant() -> None:
    """A bare sheet name is not ``bronze."<sheet>"``. One checker, serve_gap."""
    sql = 'SELECT 1 FROM bronze."qwest_box_Sales"'
    assert serve_gap(sql, grantable=set(), dialect="duckdb") == "sql_relation_not_granted"
    assert (
        serve_gap(sql, grantable={"qwest_box_Sales"}, dialect="duckdb")
        == "sql_relation_not_granted"
    )
