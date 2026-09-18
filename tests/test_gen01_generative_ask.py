"""GEN-01: ontology-grounded generative ask + execute-validate.

Abstain when unsure. Do not execute until compile+validate pass.
Planted refuse traps must not ship a confident badge. Not pack expansion.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from cortex_client.compute import compute_query
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import QueryResult
from dms_executor import Executor
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import maybe_generative_ask, ontology_catalog
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.ontology import Ontology
from dms_executor.semantic_retrieve import bind_plan, retrieve_short_context

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from score_curated import judge  # noqa: E402

PLANTED_TRAPS_GREEDY = (
    "Just give me last month's number",
    "Are we short on anything the warehouse should worry about?",
    "how full is each warehouse",
    "How many delayed incoming shipments per warehouse?",
)
PLANTED_TRAPS_MISS = (
    "Show stock by storage bin",
    "List active alerts across the warehouse network",
)


@dataclass
class _GenCortex:
    asks: list[Any] = field(default_factory=list)
    submits: list[Any] = field(default_factory=list)
    appends: list[Any] = field(default_factory=list)
    compute_payload: dict[str, Any] | None = None
    sql_output: dict[str, Any] | None = field(
        default_factory=lambda: {"rows": [{"product_category": "ALPHA", "revenue": 100.0}]}
    )

    def compute_query(self, question: str, **_: Any) -> dict[str, Any] | None:
        return self.compute_payload

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql":
            return QueryResult(
                ok=True, status="ok", run_id="run_gen01", output=self.sql_output
            )
        return QueryResult(ok=True, status="bound", run_id="run_gen01_bind")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        self.appends.append(req)
        return LedgerAppendResponse(entry_id="led_gen01", hash="hash_gen01_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Cortex fallback.",
            badge="certified",
            sql_used="SELECT 1 AS cortex_marker",
            rows=[{"cortex_marker": 1}],
            audit_id="aud_gen01_cortex",
            route="sql",
        )


def _ontology() -> Ontology:
    o = Ontology()
    o.add_object("sale", "sales", ["txn_id"])
    o.add_object("lot", "lots", ["lot_id"])
    o.add_object("region", "regions", ["region"])
    o.add_object(
        "product",
        "(SELECT sku, ANY_VALUE(category) AS category FROM lots GROUP BY sku)",
        ["sku"],
    )
    o.add_link("sale_of_lot", "sale", ["sku"], "lot", ["sku"])
    o.add_link("sale_of_product", "sale", ["sku"], "product", ["sku"])
    o.add_link("sale_in_region", "sale", ["region"], "region", ["region"])
    o.add_measure("revenue", "sale", "SUM(f.amount)")
    return o


def _seed(path: Path) -> None:
    import duckdb

    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE lots (lot_id VARCHAR, sku VARCHAR, category VARCHAR, qty DOUBLE)")
        con.execute(
            "INSERT INTO lots VALUES "
            "('L1','SKU-1','ALPHA',10),('L2','SKU-1','ALPHA',20),"
            "('L3','SKU-1','ALPHA',30),('L4','SKU-2','BETA',40)"
        )
        con.execute(
            "CREATE TABLE sales (txn_id VARCHAR, sku VARCHAR, region VARCHAR, amount DOUBLE)"
        )
        con.execute("INSERT INTO sales VALUES ('T1','SKU-1','North',100),('T2','SKU-2','South',50)")
        con.execute("CREATE TABLE regions (region VARCHAR, country VARCHAR)")
        con.execute("INSERT INTO regions VALUES ('North','MY'),('South','MY')")
    finally:
        con.close()


@pytest.fixture()
def warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "gen01.duckdb"
    _seed(path)
    return path


@pytest.fixture()
def onto(warehouse: Path) -> Ontology:
    from dms_executor.generative_ask import load_verified_ontology

    loaded = load_verified_ontology(warehouse, _ontology())
    assert loaded is not None
    return loaded


def _submit_ok(sql: str) -> QueryResult:
    return QueryResult(
        ok=True,
        status="ok",
        run_id="run_gen01",
        output={"rows": [{"product_category": "ALPHA", "revenue": 100.0}]},
    )


def _ledger_ok(_: dict[str, Any]) -> LedgerAppendResponse:
    return LedgerAppendResponse(entry_id="led_gen01", hash="hash_gen01_not_entry")


def test_abstain_when_question_is_unsure(onto: Ontology, warehouse: Path) -> None:
    submits: list[str] = []

    def submit(sql: str) -> QueryResult:
        submits.append(sql)
        return _submit_ok(sql)

    env = maybe_generative_ask(
        "Just give me last month's number",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            "query_plan": {"measure": "revenue", "group_by": [["product", "category"]]}
        },
        submit=submit,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    assert "cannot certify" in env["text"].lower()
    assert submits == []
    assert_envelope_valid(env)


def test_abstain_when_compute_is_unsure(onto: Ontology, warehouse: Path) -> None:
    submits: list[str] = []
    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {"unsure": True},
        submit=lambda sql: submits.append(sql) or _submit_ok(sql),
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert submits == []


def test_abstain_when_compile_refuses(onto: Ontology, warehouse: Path) -> None:
    env = maybe_generative_ask(
        "What is revenue by lot category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {
            "query_plan": {"measure": "revenue", "group_by": [["lot", "category"]]}
        },
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["sql_used"] is None


def test_validate_skips_execute_on_ungranted_tables(onto: Ontology, warehouse: Path) -> None:
    submits: list[str] = []
    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable=set(),
        compute=lambda _c: {
            "query_plan": {"measure": "revenue", "group_by": [["product", "category"]]}
        },
        submit=lambda sql: submits.append(sql) or _submit_ok(sql),
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert submits == []


def test_l2_when_plan_compiles_and_validate_passes(onto: Ontology, warehouse: Path) -> None:
    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda catalog: {
            "query_plan": {"measure": "revenue", "group_by": [["product", "category"]]},
            "catalog_measures": list((catalog.get("measures") or {}).keys()),
        },
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert env["sql_used"]
    assert "SELECT" in env["sql_used"].upper()
    assert env["rows"]
    assert "100" in env["text"] or "ALPHA" in env["text"]
    assert_envelope_valid(env)


def test_miss_when_compute_returns_no_plan(onto: Ontology, warehouse: Path) -> None:
    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=lambda _c: {"answer": "not a plan"},
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert any("compute_fallback:bind_plan" in str(a) for a in (env.get("assumptions") or []))
    assert env.get("plan_source") == "bind_plan"


def test_planted_traps_are_not_confident(onto: Ontology, warehouse: Path) -> None:
    """A compiling plan must not green planted refuse/vague traps."""

    def greedy(_catalog: dict[str, Any]) -> dict[str, Any]:
        return {"query_plan": {"measure": "revenue", "group_by": [["product", "category"]]}}

    for question in PLANTED_TRAPS_GREEDY:
        env = maybe_generative_ask(
            question,
            warehouse=warehouse,
            grantable={"sales", "lots", "regions"},
            compute=greedy,
            submit=_submit_ok,
            ledger_append=_ledger_ok,
            ontology=onto,
        )
        if env is None:
            continue
        assert env["badge"] == "ABSTAIN", question
        assert env["abstained"] is True, question
        assert judge({"expect": "refuse"}, env) != "WRONG", question


def test_planted_traps_without_a_plan_miss_not_green(
    onto: Ontology, warehouse: Path
) -> None:
    for question in PLANTED_TRAPS_MISS:
        env = maybe_generative_ask(
            question,
            warehouse=warehouse,
            grantable={"sales", "lots", "regions"},
            compute=lambda _c: {"answer": "no plan"},
            submit=_submit_ok,
            ledger_append=_ledger_ok,
            ontology=onto,
        )
        assert env is None or env["badge"] == "ABSTAIN", question
        if env is not None:
            assert env["abstained"] is True, question
            assert judge({"expect": "refuse"}, env) != "WRONG", question


def test_judge_green_trap_still_wrong() -> None:
    assert (
        judge(
            {"expect": "refuse"},
            {"badge": "L2_VALIDATED", "abstained": False, "rows": [{"v": 1}]},
        )
        == "WRONG"
    )


def test_catalog_has_no_secrets(onto: Ontology) -> None:
    cat = ontology_catalog(onto)
    blob = str(cat).lower()
    assert "sk-" not in blob
    assert "api_key" not in blob
    assert "password" not in blob
    assert "revenue" in cat["measures"]


def test_retrieve_short_context_is_filtered(onto: Ontology, warehouse: Path) -> None:
    seen: dict[str, Any] = {}

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen.update(ctx)
        return {
            "query_plan": {"measure": "revenue", "group_by": [["product", "category"]]}
        }

    env = maybe_generative_ask(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        compute=compute,
        submit=_submit_ok,
        ledger_append=_ledger_ok,
        ontology=onto,
    )
    assert env is not None
    assert env["badge"] == "L2_VALIDATED"
    blob = str(seen).lower()
    assert "sum(" not in blob
    assert "sk-" not in blob
    assert "api_key" not in blob
    assert "revenue" in (seen.get("measures") or {})
    methods = seen.get("methods") or []
    assert "ontology" in methods
    assert "summarize" in methods
    assert "hybrid_fuse" in methods
    ctx = retrieve_short_context(
        "What is revenue by product category?",
        warehouse=warehouse,
        grantable={"sales", "lots", "regions"},
        ontology=onto,
    )
    assert "schema_sql" in (ctx.get("methods") or [])
    assert "sql_filter_values" in (ctx.get("methods") or [])
    assert any("category" in k for k in (ctx.get("encodings") or {}))


def test_bind_plan_abstains_when_by_has_no_dimension() -> None:
    ctx = {
        "measures": {"stock_value_myr": {"grain": "lot", "description": "carrying value"}},
        "objects": {"lot": {"key": ["lot_id"]}},
        "columns": {"lot": ["lot_id", "qty"]},
    }
    out = bind_plan("Show stock by storage bin", ctx)
    assert out is None


def test_bind_plan_is_not_a_pack_lookup() -> None:
    ctx = {
        "measures": {"revenue": {"grain": "sale", "description": ""}},
        "objects": {"product": {"key": ["sku"]}, "sale": {"key": ["txn_id"]}},
        "columns": {"product": ["sku", "category"], "sale": ["txn_id", "sku", "amount"]},
    }
    out = bind_plan("Top 5 selling SKUs by revenue", ctx)
    assert out is not None
    assert out.get("unsure") is not True
    plan = out["query_plan"]
    assert plan["measure"] == "revenue"
    assert plan["group_by"] == [["product", "sku"]]
    assert plan["limit"] == 5


def test_ab_curated_wrong_zero_both_paths() -> None:
    from score_curated import run_ab_curated

    report = run_ab_curated()
    assert report["exact_match"]["wrong"] == 0
    assert report["generative"]["wrong"] == 0
    assert report["passed"] is True
    assert report["exact_match"]["answered"] >= 1
    assert report["generative"]["n"] == report["exact_match"]["n"]
    planted = {
        "trap_last_month",
        "trap_short_paraphrase",
        "trap_how_full_synonym",
        "trap_delayed_count",
        "trap_stock_by_bin",
        "trap_alerts_ungranted",
    }
    for row in report["cases"]:
        if row["id"] in planted:
            assert row["generative"] != "WRONG", row
            assert row["exact"] != "WRONG", row
            assert row["generative_badge"] == "ABSTAIN", row
    assert report["generative"]["answered"] >= report["baseline_ab"]["generative_answered"]


def test_compute_http_does_not_invent_a_key() -> None:
    seen: dict[str, Any] = {}

    class _Resp:
        status_code = 404
        text = "missing"

        def json(self) -> dict[str, Any]:
            return {}

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def post(
            self, url: str, json: dict[str, Any], headers: dict[str, str] | None = None
        ) -> _Resp:
            seen["url"] = url
            seen["json"] = json
            seen["headers"] = headers
            return _Resp()

    with patch("cortex_client.compute.httpx.Client", _Client):
        out = compute_query("http://127.0.0.1:8010", question="hello")
    assert out is None
    assert seen["headers"] in (None, {})
    assert "Authorization" not in (seen["headers"] or {})
    assert "X-API-Key" not in (seen["headers"] or {})
    body = str(seen["json"])
    assert "sk-" not in body
    assert "api_key" not in body.lower()


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    m = ManifestMinter()
    from cortex_contract.execution import Manifest as M

    def _mint(acl: SessionAcl) -> M:
        return M(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


def test_live_ask_falls_through_without_compute_query(minter: ManifestMinter) -> None:
    from tests.test_live_ask import FakeCortex

    fake = FakeCortex(submits=[], asks=[])
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    env = exe.live_ask("List chemicals in inventory", session_id="ses_gen01_miss")
    assert fake.asks, "contract ask still runs when compute is absent"
    assert env["badge"] == "L0_CERTIFIED"


def test_live_ask_abstain_when_compute_unsure(minter: ManifestMinter) -> None:
    fake = _GenCortex(compute_payload={"unsure": True})
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    env = exe.live_ask("What is revenue by product category?", session_id="ses_gen01_unsure")
    assert fake.asks == []
    assert fake.submits == []
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert_envelope_valid(env)


def test_live_ask_vq04_beats_greedy_compute(minter: ManifestMinter) -> None:
    """VQ-04 planted refuse stays ABSTAIN even if compute would compile a plan."""
    fake = _GenCortex(
        compute_payload={
            "query_plan": {"measure": "revenue", "group_by": [["product", "category"]]}
        }
    )
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    env = exe.live_ask("how full is each warehouse", session_id="ses_gen01_vq04")
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert fake.submits == []
    assert fake.asks == []
    assert judge({"expect": "refuse"}, env) != "WRONG"


def test_live_ask_vague_trap_does_not_execute(minter: ManifestMinter) -> None:
    fake = _GenCortex(
        compute_payload={
            "query_plan": {"measure": "revenue", "group_by": [["product", "category"]]}
        }
    )
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    env = exe.live_ask("Just give me last month's number", session_id="ses_gen01_vague")
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert fake.submits == []
    assert fake.asks == []
    assert judge({"expect": "refuse"}, env) != "WRONG"


def test_ask_path_exact_miss_does_not_call_cortex(minter: ManifestMinter) -> None:
    from tests.test_live_ask import FakeCortex

    fake = FakeCortex(submits=[], asks=[])
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    env = exe.live_ask(
        "Top 5 selling SKUs by revenue",
        session_id="ses_gen02_exact_miss",
        ask_path="exact",
    )
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert fake.asks == []
    assert fake.submits == []


def test_ask_path_generative_skips_certified_pack(minter: ManifestMinter) -> None:
    fake = _GenCortex(compute_payload={"unsure": True})
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    env = exe.live_ask(
        "What is total stock value by category?",
        session_id="ses_gen02_gen_skip_pack",
        ask_path="generative",
    )
    assert env["badge"] == "ABSTAIN"
    assert fake.asks == []
    assert fake.submits == []


def test_ask_path_generative_binds_on_compute_miss(minter: ManifestMinter) -> None:
    fake = _GenCortex(compute_payload=None)
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    env = exe.live_ask(
        "What is total stock value by category?",
        session_id="ses_gen02_gen_bind_miss",
        ask_path="generative",
    )
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    assert fake.asks == []
    assert fake.submits
    assert any("compute_fallback:bind_plan" in str(a) for a in (env.get("assumptions") or []))
    assert env.get("plan_source") == "bind_plan"


def test_ask_path_exact_still_hits_pack(minter: ManifestMinter) -> None:
    fake = _GenCortex()
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    env = exe.live_ask(
        "What is total stock value by category?",
        session_id="ses_gen02_exact_pack",
        ask_path="exact",
    )
    assert env["route"] == "governed_metric"
    assert fake.asks == []
    assert fake.submits
