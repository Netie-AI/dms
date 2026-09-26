"""SPACE-GEN-01: a Space whose data is not the demo lake reaches generation.

BIRD Mini-Dev live run (dms#312): n=500, answered=0, ABSTAIN=500, no provider
call. On a Space holding only SQL-source bronze tables:

* the no-selection path read ``DEMO_TABLES`` only, so the Space's own tables
  were never readable (``__init__.py`` default_readable);
* a table selection skipped generation outright (``if tables ... return None``);
* the Space was never a member of itself in the demo session store, so the
  grant named no sources and Cortex refused "nothing grants Space" (44/500);
* the Insights body carried the supply-chain demo ontology slice, never the
  Space's own tables (``retrieve_schema_sql`` dropped every ``bronze.<t>``);
* 110 abstains rendered "cannot certify ..." with no named reason.

Every case posts ``POST /v1/chat/ask`` and asserts on the customer envelope
(``assert_envelope_valid``, badge, rendered text, rows). The fake Cortex
records what DMS sent and executes submits on the test lake, so rows are real.

Round 2 (verifier): the first fake executed submitted SQL without enforcing
the manifest, which hid that Cortex grants by *bare* table name while DMS
minted ``bronze.<t>`` keys, so every Space query would be refused live. The
fake now runs the Cortex ``enforce_manifest`` grant rule (deny any named table
whose bare lowercased name is not a ``row_predicates`` key, CTEs exempt,
three-part names refused, each granted table wrapped in its predicate) and the
real ``ManifestMinter.mint_manifest`` signs the manifest it sees. DMS may not
import CortexOS, so the rule is mirrored here; the round-2 report ran the same
manifests through the real enforcer.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
import sqlglot
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_executor import Executor
from dms_executor.bronze import record_source_pull, write_bronze_rows
from dms_executor.demo_grants import DemoSessionStore
from dms_executor.demo_warehouse import DEMO_TABLES, ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.gen_path_refuse import customer_abstain_text
from dms_executor.manifest import IntermediateKey, ManifestMinter
from fastapi.testclient import TestClient
from sqlglot import exp

ACCOUNT = "bronze.financial_account"
DISTRICT = "bronze.financial_district"

COUNT_Q = "How many accounts are in the district named Prague?"
COUNT_SQL = (
    "SELECT COUNT(*) AS account_count FROM bronze.financial_account a "
    "JOIN bronze.financial_district d ON a.district_id = d.district_id "
    "WHERE d.a2 = 'Prague'"
)
COUNT_ORACLE = (
    "SELECT COUNT(*) AS account_count FROM bronze.financial_account a "
    "JOIN bronze.financial_district d ON a.district_id = d.district_id "
    "WHERE d.a2 = 'Prague'"
)
#: Cites a demo-lake table this Space is not granted.
UNGRANTED_SQL = "SELECT COUNT(*) AS n FROM transactions"
#: What Cortex's grant check matches ``FROM bronze.<t>`` against.
BARE_KEYS = {"financial_account", "financial_district"}


class _CortexRefusal(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


def _cortex_enforce(sql: str, row_predicates: dict[str, str]) -> str:
    """Mirror of Cortex ``enforce_manifest``'s table grant and predicate wrap.

    ``CortexOS/execution/manifest.py``: ``_refuse_cross_catalog``,
    ``_refuse_ungranted_tables`` (``table.name.lower()`` against the lowercased
    ``row_predicates`` keys, CTE names exempt) and ``_wrap_with_predicate``.
    """
    root = sqlglot.parse_one(sql, read="duckdb")
    for table in root.find_all(exp.Table):
        if isinstance(table.this, exp.Identifier) and table.catalog:
            raise _CortexRefusal("path_not_allowed", "cross-catalog reference")
    predicates = {k.lower(): v for k, v in row_predicates.items()}
    ctes = {c.alias.lower() for c in root.find_all(exp.CTE) if c.alias}
    named = [t for t in root.find_all(exp.Table) if isinstance(t.this, exp.Identifier)]
    for table in named:
        name = table.name.lower()
        if name and name not in ctes and name not in predicates:
            raise _CortexRefusal(
                "path_not_allowed", f"table {table.name!r} is not named by this manifest"
            )
    for table in list(named):
        pred = predicates.get(table.name.lower())
        if pred is None:
            continue
        inner = table.copy()
        inner.set("alias", None)
        alias = table.args.get("alias") or exp.TableAlias(
            this=exp.to_identifier(table.name, quoted=table.this.quoted)
        )
        table.replace(
            exp.Subquery(
                this=exp.select("*").from_(inner).where(pred, dialect="duckdb"), alias=alias
            )
        )
    return root.sql(dialect="duckdb")


@dataclass
class _RecordingCortex:
    """Insights returns a fixed payload and records the request body fields.

    ``submit`` enforces the manifest the way Cortex does before executing.
    """

    warehouse: Path
    payload: dict[str, Any] = field(default_factory=dict)
    insights: list[dict[str, Any]] = field(default_factory=list)
    manifests: list[Manifest] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    asks: list[Any] = field(default_factory=list)

    def compute_insights(self, question: str, **kw: Any) -> dict[str, Any]:
        self.insights.append({"question": question, **kw})
        return dict(self.payload)

    def compute_query(self, question: str, **_kw: Any) -> None:
        raise AssertionError("ask lane called compute_query /dms/query")

    def submit(self, req: Any) -> QueryResult:
        self.manifests.append(req.manifest)
        assert req.manifest.signature, "manifest was not signed"
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind != "sql":
            return QueryResult(ok=True, status="bound", run_id="run_space_bind")
        sql = str((getattr(req, "body", None) or {}).get("sql") or "")
        try:
            enforced = _cortex_enforce(sql, dict(req.manifest.row_predicates or {}))
        except _CortexRefusal:
            self.refused.append(sql)
            raise
        self.executed.append(sql)
        con = duckdb.connect(str(self.warehouse), read_only=True)
        try:
            cur = con.execute(enforced)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run_space_sql", output={"rows": rows})

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        return LedgerAppendResponse(entry_id="led_space_gen_01", hash="hash_space_gen_01_x")

    def ask(self, req: AskRequest) -> AskResponse:
        # The contract ask also serves doc RAG, so a generation miss still
        # reaches it; the manifest bound for it must be this Space's grant.
        self.asks.append(req)
        return AskResponse(
            answer="Cortex refused: no governed answer for this Space.",
            abstained=True,
            badge="ABSTAIN",
        )


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    """The real ``mint_manifest`` (keys, canonical bytes, signature) on a local key."""
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


def _land(
    lake: Path,
    table: str,
    columns: list[str],
    rows: list[list[Any]],
    space: str,
    *,
    truncated: bool = False,
) -> None:
    landed = write_bronze_rows(table=table, columns=columns, rows=rows, path=lake)
    record_source_pull(
        table_name=landed.split(".", 1)[-1],
        source=f"postgresql://bird/financial#{table}",
        ingest_id=f"ing_{table}",
        row_count=len(rows),
        truncated=truncated,
        space_id=space,
        path=lake,
    )


@dataclass
class _Rig:
    client: TestClient
    cortex: _RecordingCortex
    space_id: str
    lake: Path


def _rig(tmp_path: Path, minter: ManifestMinter, payload: dict[str, Any]) -> _Rig:
    lake = tmp_path / "space_gen.duckdb"
    ensure_demo_warehouse(lake)
    cortex = _RecordingCortex(warehouse=lake, payload=payload)
    app = create_app()
    space = app.state.space_store.create("BIRD financial")
    _land(
        lake,
        ACCOUNT,
        ["account_id", "district_id", "frequency"],
        [["1", "10", "POPLATEK MESICNE"], ["2", "10", "POPLATEK TYDNE"],
         ["3", "11", "POPLATEK MESICNE"], ["4", "10", "POPLATEK MESICNE"]],
        space.id,
    )
    _land(
        lake,
        DISTRICT,
        ["district_id", "a2", "a3"],
        [["10", "Prague", "Prague"], ["11", "Brno", "south Moravia"]],
        space.id,
    )
    exe = Executor(cortex=cortex, minter=minter, warehouse_path=lake)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
        dms_harness_ask_paths=True,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return _Rig(client=TestClient(app), cortex=cortex, space_id=space.id, lake=lake)


def _ask(rig: _Rig, question: str, **extra: Any) -> dict[str, Any]:
    r = rig.client.post(
        "/v1/chat/ask",
        json={
            "question": question,
            "session_id": "ses_space_gen_01",
            "space_id": rig.space_id,
            **extra,
        },
    )
    assert r.status_code == 200, r.text
    env = r.json()
    assert_envelope_valid(env)
    return env


def _oracle(lake: Path, sql: str) -> list[dict[str, Any]]:
    con = duckdb.connect(str(lake), read_only=True)
    try:
        cur = con.execute(sql)
        cols = [str(c[0]) for c in (cur.description or [])]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _multiset(rows: list[dict[str, Any]]) -> Counter[tuple[str, ...]]:
    return Counter(tuple(sorted(str(v) for v in row.values())) for row in rows)


def _reasons(env: dict[str, Any]) -> str:
    return " ".join(str(a) for a in env.get("assumptions") or [])


def _sent_tables(rig: _Rig) -> set[str]:
    assert rig.cortex.insights, "generation was never reached"
    onto = rig.cortex.insights[-1].get("ontology") or {}
    return {str(t.get("table")) for t in onto.get("schema") or []}


@pytest.mark.parametrize("ask_path", [None, "generative"])
def test_correct_sql_on_space_tables_is_l2_with_oracle_rows(
    tmp_path: Path, minter: ManifestMinter, ask_path: str | None
) -> None:
    rig = _rig(tmp_path, minter, {"query_sql": COUNT_SQL, "plan_source": "ontology_plan"})
    extra = {"ask_path": ask_path} if ask_path else {}
    env = _ask(rig, COUNT_Q, **extra)

    # Generation was reached with this Space's own tables, not the demo lake.
    sent = rig.cortex.insights[-1]
    assert sent["space_id"] == rig.space_id
    tables = _sent_tables(rig)
    assert {ACCOUNT, DISTRICT} <= tables, tables
    assert not tables & set(DEMO_TABLES), tables
    onto = sent["ontology"]
    # No supply-chain demo measures or objects ride along as this Space's ontology.
    assert not onto.get("measures"), onto.get("measures")
    assert not onto.get("objects"), onto.get("objects")

    # The customer envelope: green, the oracle's rows, the figure in the text.
    oracle = _oracle(rig.lake, COUNT_ORACLE)
    assert oracle == [{"account_count": 3}]
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["abstained"] is False
    assert _multiset(env["rows"]) == _multiset(oracle)
    assert "account_count=3" in str(env.get("text") or ""), env.get("text")
    assert env.get("audit_id") == "led_space_gen_01"
    assert rig.cortex.executed == [COUNT_SQL]
    assert rig.cortex.asks == []

    # The grant names the Space's tables and nothing of the demo lake: the
    # "nothing grants Space" refusal came from an empty grant here. Keys are
    # the bare names Cortex resolves ``FROM bronze.<t>`` to; the SQL passed
    # the Cortex grant rule, so none was refused.
    sql_manifest = rig.cortex.manifests[-1]
    assert set(sql_manifest.row_predicates) == BARE_KEYS
    assert rig.cortex.refused == []


def test_table_selection_narrows_generation_instead_of_skipping_it(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    sql = "SELECT COUNT(*) AS account_count FROM bronze.financial_account"
    rig = _rig(tmp_path, minter, {"query_sql": sql, "plan_source": "ontology_plan"})
    env = _ask(rig, "How many accounts are there?", grounded_tables=[ACCOUNT])

    assert _sent_tables(rig) == {ACCOUNT}
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"), env.get("assumptions"))
    assert env["rows"] == _oracle(rig.lake, sql) == [{"account_count": 4}]
    assert "account_count=4" in str(env.get("text") or "")
    assert set(rig.cortex.manifests[-1].row_predicates) == {"financial_account"}
    assert rig.cortex.refused == []


def test_selection_does_not_widen_generated_sql(tmp_path: Path, minter: ManifestMinter) -> None:
    # Selected one table; generated SQL joins the other. Named ABSTAIN, no rows.
    rig = _rig(tmp_path, minter, {"query_sql": COUNT_SQL, "plan_source": "ontology_plan"})
    env = _ask(rig, COUNT_Q, grounded_tables=[ACCOUNT])

    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    text = str(env.get("text") or "")
    assert "gap: validate:ungranted:financial_district" in text, text
    assert rig.cortex.executed == []


def test_sql_over_ungranted_table_is_named_abstain(tmp_path: Path, minter: ManifestMinter) -> None:
    rig = _rig(tmp_path, minter, {"query_sql": UNGRANTED_SQL, "plan_source": "ontology_plan"})
    env = _ask(rig, "How many transactions are there?")

    assert rig.cortex.insights, "generation was never reached"
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    text = str(env.get("text") or "")
    assert "gap: validate:ungranted:transactions" in text, text
    assert "validate:ungranted:transactions" in _reasons(env)
    assert rig.cortex.executed == []
    assert rig.cortex.asks == []


def test_demo_ranked_plan_is_not_compiled_on_non_demo_space(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    # Cortex ranked a supply-chain demo metric (today's packs/dms ranking).
    # DMS must not compile it against the demo ontology for this Space.
    payload = {
        "plan_source": "ontology_plan",
        "query_plan": {"measure": "stock_value_myr", "group_by": [], "filters": []},
    }
    rig = _rig(tmp_path, minter, payload)
    env = _ask(rig, "How many accounts are there?")

    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    text = str(env.get("text") or "")
    assert "gap: missing_ontology" in text, text
    assert rig.cortex.executed == []
    assert rig.cortex.asks == []


def test_generation_miss_falls_through_under_the_space_grant_only(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    rig = _rig(tmp_path, minter, {})
    env = _ask(rig, "How many accounts are there?")

    assert rig.cortex.insights, "generation was never reached"
    assert len(rig.cortex.asks) == 1
    # The bound manifest names this Space's tables: never empty ("nothing
    # grants Space"), never the demo lake.
    assert set(rig.cortex.manifests[-1].row_predicates) == BARE_KEYS
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []


def test_ungrantable_selection_is_refused_before_generation(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    rig = _rig(tmp_path, minter, {"query_sql": COUNT_SQL, "plan_source": "ontology_plan"})
    r = rig.client.post(
        "/v1/chat/ask",
        json={
            "question": COUNT_Q,
            "session_id": "ses_space_gen_01",
            "space_id": rig.space_id,
            "grounded_tables": ["bronze.never_ingested"],
        },
    )
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "grounding_not_grantable"
    assert rig.cortex.insights == []
    assert rig.cortex.executed == []


def test_every_abstain_text_names_its_reason() -> None:
    for reason, shown in (
        ("query_sql was empty", "query_sql was empty"),
        ("compute abstained (unsure)", "compute abstained (unsure)"),
        ("submit_failed", "submit_failed"),
        # Named, without the engine exception class or the guard's code.
        ("validate:explain:ParserException", "validate:sql_does_not_run"),
        ("validate:hostile_sql:path_not_allowed", "validate:unsafe_sql"),
        ("source_truncated:trans", "source_truncated:trans"),
        ("", "abstain_reason_missing"),
    ):
        text = customer_abstain_text(reason)
        assert f"gap: {shown}" in text, (reason, text)
        assert "ParserException" not in text
        assert "path_not_allowed" not in text


@pytest.mark.parametrize(
    "sql",
    [
        # Comma join: the FROM/JOIN regex saw only the first relation.
        "SELECT COUNT(*) AS n FROM bronze.financial_account a, transactions t",
        # Block comment where the regex wanted whitespace.
        "SELECT COUNT(*) AS n FROM/**/transactions",
        # Bare demo name while only bronze.transactions-like names are granted.
        "SELECT COUNT(*) AS n FROM bronze.financial_account "
        "WHERE account_id IN (SELECT 1 FROM\ninventory)",
    ],
)
def test_ungranted_table_the_regex_missed_is_named_abstain(
    tmp_path: Path, minter: ManifestMinter, sql: str
) -> None:
    rig = _rig(tmp_path, minter, {"query_sql": sql, "plan_source": "ontology_plan"})
    env = _ask(rig, "How many accounts are there?")

    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"))
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    text = str(env.get("text") or "")
    assert "gap: validate:ungranted:" in text, text
    assert "validate:ungranted:" in _reasons(env)
    # Refused by DMS before submit, not left to the engine.
    assert rig.cortex.executed == []
    assert rig.cortex.refused == []


def test_truncated_source_is_named_abstain_not_l2(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    sql = "SELECT COUNT(*) AS trans_count FROM bronze.trans"
    rig = _rig(tmp_path, minter, {"query_sql": sql, "plan_source": "ontology_plan"})
    # The row cap cut the source short: 5 rows landed of many more.
    _land(
        rig.lake,
        "bronze.trans",
        ["trans_id", "account_id"],
        [[str(i), "1"] for i in range(5)],
        rig.space_id,
        truncated=True,
    )
    env = _ask(rig, "How many transactions are there in trans?")

    assert rig.cortex.insights, "generation was never reached"
    assert env["badge"] == "ABSTAIN", (env["badge"], env.get("text"), env.get("rows"))
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    text = str(env.get("text") or "")
    assert "gap: source_truncated:trans" in text, text
    assert "partly loaded" in text
    assert "trans_count=5" not in text
    assert "source_truncated:trans" in _reasons(env)
    assert rig.cortex.executed == []


def test_untruncated_source_still_answers(tmp_path: Path, minter: ManifestMinter) -> None:
    # The truncation gate reads the registry flag; a complete pull is not caught.
    sql = "SELECT COUNT(*) AS account_count FROM bronze.financial_account"
    rig = _rig(tmp_path, minter, {"query_sql": sql, "plan_source": "ontology_plan"})
    env = _ask(rig, "How many accounts are there?")
    assert env["badge"] == "L2_VALIDATED", (env["badge"], env.get("text"))
    assert env["rows"] == [{"account_count": 4}]


def test_hostile_sql_abstain_text_does_not_name_the_guard(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    sql = "SELECT * FROM read_csv_auto('/etc/passwd')"
    rig = _rig(tmp_path, minter, {"query_sql": sql, "plan_source": "ontology_plan"})
    env = _ask(rig, "How many accounts are there?")

    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    text = str(env.get("text") or "")
    assert "gap: validate:unsafe_sql" in text, text
    assert "path_not_allowed" not in text
    # The audit trail keeps the full reason.
    assert "validate:hostile_sql:" in _reasons(env)
    assert rig.cortex.executed == []


def test_generation_miss_fallthrough_abstain_names_its_reason(
    tmp_path: Path, minter: ManifestMinter
) -> None:
    rig = _rig(tmp_path, minter, {})
    env = _ask(rig, "How many accounts are there?")

    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert "Cortex refused" in str(env.get("text") or "")
    assert "ABSTAIN reason: cortex_contract_ask_abstained" in _reasons(env), env.get(
        "assumptions"
    )


def test_ingested_space_membership_is_the_stewards_only(tmp_path: Path) -> None:
    lake = tmp_path / "member.duckdb"
    ensure_demo_warehouse(lake)
    space = "12345678-1234-4234-8234-123456789abc"
    _land(lake, ACCOUNT, ["account_id"], [["1"]], space)
    store = DemoSessionStore(warehouse=lake)
    assert store.is_space_member(space, store.steward_user_id) is True
    assert store.is_space_member(space, "99999999-9999-9999-9999-999999999999") is False
    # Nothing ingested: not a member, steward or not.
    other = "87654321-4321-4321-8321-cba987654321"
    assert store.is_space_member(other, store.steward_user_id) is False
