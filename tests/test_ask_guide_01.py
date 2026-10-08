"""ASK-GUIDE-01. Flag off is byte-identical. Flag on guides; it does not execute.

#392 (rank_window_unhandled_terms) and GEN-INTENT (intent_shape_mismatch) are
not on this main. A confirmed pick still goes through ``_submit_validated``
and ``build_answer_envelope``, so those checks apply once they land there.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
import yaml
from cortex_contract.execution import Manifest, QueryResult
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_core.ask import AskServiceError, ask_clarify_enabled
from dms_executor import Executor
from dms_executor.ask_clarify import (
    apply_ask_guide,
    build_options,
    confirm_reading,
    public_plan,
)
from dms_executor.ask_clarify import (
    compile_plan as compile_plan_fn,
)
from dms_executor.demo_warehouse import DEMO_TABLES
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.manifest import ManifestMinter
from dms_executor.ontology import NO_SILENT_PAD, CompiledQuery, Coverage, Ontology
from fastapi.testclient import TestClient

from tests.test_live_ask import FakeCortex

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "tests" / "fixtures" / "curated_ceo" / "questions.yaml"
GOLDEN = ROOT / "tests" / "fixtures" / "ask_guide" / "flag_off_ask.json"
ASK_CLARIFY = ROOT / "packages" / "executor" / "dms_executor" / "ask_clarify.py"
FIXED = "2026-01-01T00:00:00Z"
GRANTS = set(DEMO_TABLES)
_ON = re.compile(
    r"DMS_ASK_CLARIFY\s*=\s*['\"]?(?:1|true|yes|on)\b",
    re.I,
)
_FORBIDDEN_PATH = ("oracles.yaml", "questions.yaml", "query_skill", "flag_off_ask.json")


def _questions() -> list[dict[str, Any]]:
    raw = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    return list(raw["questions"])


def _question(qid: str) -> str:
    for row in _questions():
        if row["id"] == qid:
            return str(row["question"])
    raise AssertionError(f"missing curated question {qid}")


def _freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dms_executor.datetime_now", lambda: FIXED)
    monkeypatch.setattr("dms_executor.generative_ask._as_of", lambda: FIXED)
    monkeypatch.setattr("dms_executor.demo_pack._as_of", lambda: FIXED)
    monkeypatch.setattr("dms_executor.verified_queries._as_of", lambda: FIXED)
    monkeypatch.setattr("dms_executor.session_followup._as_of", lambda: FIXED)
    monkeypatch.setattr("dms_executor.demo_ask._as_of", lambda: FIXED)
    monkeypatch.setattr("dms_executor.envelope._now", lambda: FIXED)


def _dump(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode() + b"\n"


def _seed(path: Path) -> Ontology:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE transactions (txn_id VARCHAR, sku VARCHAR, "
        "quantity_kg DOUBLE, supplier_id VARCHAR)"
    )
    con.execute(
        "INSERT INTO transactions VALUES "
        "('T1','SKU-1',10,'S1'),('T2','SKU-2',20,'S1')"
    )
    con.execute("CREATE TABLE suppliers (supplier_id VARCHAR, country VARCHAR)")
    con.execute("INSERT INTO suppliers VALUES ('S1','MY')")
    onto = Ontology()
    onto.add_object("transaction", "transactions", ["txn_id"])
    onto.add_object("supplier", "suppliers", ["supplier_id"])
    onto.add_link(
        "txn_supplier", "transaction", ["supplier_id"], "supplier", ["supplier_id"]
    )
    onto.add_measure(
        "spend_kg",
        "transaction",
        "SUM(f.quantity_kg)",
        description="Total quantity moved",
    )
    onto.add_measure(
        "sku_rows",
        "transaction",
        "COUNT(*)",
        description="Transaction rows",
    )
    assert onto.verify(con) == []
    assert onto.verified
    con.close()
    return onto


def _patch_ontology(monkeypatch: pytest.MonkeyPatch, onto: Ontology) -> None:
    monkeypatch.setattr(
        "dms_executor.ask_clarify.load_verified_ontology",
        lambda *_a, **_k: onto,
    )
    monkeypatch.setattr(
        "dms_executor.generative_ask.load_verified_ontology",
        lambda *_a, **_k: onto,
    )


def _abstain(reason: str) -> dict[str, Any]:
    return {
        "badge": "ABSTAIN",
        "abstained": True,
        "text": "I cannot certify an ontology-grounded query for that question.",
        "assumptions": [f"GEN-01: {reason}"],
        "values": [],
        "rows": [],
        "sql_used": None,
    }


def _ledger() -> SimpleNamespace:
    return SimpleNamespace(entry_id="ent-guide", hash="hash-guide")


def _rows_result() -> QueryResult:
    return QueryResult(
        ok=True,
        status="ok",
        run_id="run-guide",
        output={"rows": [{"supplier_id": "S1", "spend_kg": 30}]},
    )


def _stub_minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    minter = ManifestMinter(openvault_url="http://127.0.0.1:9")

    def _mint(acl: Any) -> Manifest:
        return Manifest(
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

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)
    return minter


class _GuideCortex(FakeCortex):
    def __init__(self) -> None:
        super().__init__(submits=[], asks=[], submit_result=_rows_result())
        self.ledgers: list[Any] = []

    def ledger_append(self, req: Any) -> SimpleNamespace:
        self.ledgers.append(req)
        return _ledger()

    def compute_insights(self, _question: str, **_kwargs: Any) -> dict[str, Any]:
        return {"ontology": {"metrics": [{"id": "suppliers_by_risk"}]}}


def _client(
    monkeypatch: pytest.MonkeyPatch,
    cortex: FakeCortex,
    *,
    warehouse: Path,
) -> TestClient:
    # grantable_tables can reseed the lake. Keep the verified fixture file.
    monkeypatch.setattr(
        "dms_executor.ensure_demo_warehouse",
        lambda path=None: path,
    )
    monkeypatch.setattr(
        "dms_executor.demo_warehouse.ensure_demo_warehouse",
        lambda path=None: path,
    )
    app = create_app()
    exe = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=_stub_minter(monkeypatch),
        warehouse_path=warehouse,
    )
    app.state.ask_service = exe
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        dms_ask_mode="live",
        dms_demo_fallback=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _reason(env: dict[str, Any]) -> str:
    notes = env.get("assumptions") or []
    return " ".join(str(n) for n in notes)


@pytest.fixture()
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Ontology]:
    path = tmp_path / "lake.duckdb"
    onto = _seed(path)
    _patch_ontology(monkeypatch, onto)
    return path, onto


def test_flag_reads_only_explicit_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_ASK_CLARIFY", raising=False)
    assert ask_clarify_enabled() is False
    for off in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("DMS_ASK_CLARIFY", off)
        assert ask_clarify_enabled() is False
    for on in ("1", "true", "yes", "on", " TRUE "):
        monkeypatch.setenv("DMS_ASK_CLARIFY", on)
        assert ask_clarify_enabled() is True


def test_flag_off_live_ask_matches_golden_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Byte-identical to main: same object shape, and no clarify key at all."""
    monkeypatch.delenv("DMS_ASK_CLARIFY", raising=False)
    _freeze(monkeypatch)
    fake = FakeCortex(submits=[], asks=[])
    exe = Executor(
        cortex=fake,  # type: ignore[arg-type]
        minter=ManifestMinter(openvault_url="http://127.0.0.1:9"),
    )
    rows = [
        {"id": row["id"], "env": exe.live_ask(str(row["question"]), session_id="ses_golden")}
        for row in _questions()
    ]
    for row in rows:
        assert "clarify" not in row["env"]
        assert row["env"].get("clarify", "absent") == "absent"
    assert _dump(rows) == GOLDEN.read_bytes()


def test_flag_off_apply_keeps_the_same_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_ASK_CLARIFY", raising=False)
    env = _abstain("question is too vague or time-unbounded to ground")
    out = apply_ask_guide(
        env,
        warehouse=None,
        grantable=GRANTS,
        space_id=None,
        session_id=None,
        submit=lambda _sql: (_ for _ in ()).throw(AssertionError("submit")),
        ledger_append=lambda _p: (_ for _ in ()).throw(AssertionError("ledger")),
    )
    assert out is env
    assert "clarify" not in out


def test_ambiguity_prefixes_gain_options_and_others_do_not(
    monkeypatch: pytest.MonkeyPatch,
    lake: tuple[Path, Ontology],
) -> None:
    path, _onto = lake
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    submits: list[str] = []

    def _submit(sql: str) -> None:
        submits.append(sql)
        raise AssertionError("option build executed SQL")

    for reason in (
        "ambiguous_measure:none",
        "rank_window_unhandled_terms:top",
        "intent_shape_mismatch:list",
        "exact-match miss: not a certified VQ/pack hit",
        "unknown_measure: no measure named 'suppliers_by_risk'",
        "question is too vague or time-unbounded to ground",
    ):
        env = _abstain(reason)
        out = apply_ask_guide(
            env,
            warehouse=path,
            grantable=GRANTS,
            space_id=None,
            session_id="ses_opt",
            submit=_submit,
            ledger_append=lambda _p: (_ for _ in ()).throw(AssertionError("ledger")),
        )
        options = out["clarify"]["options"]
        assert 1 <= len(options) <= 4, reason
        assert env["assumptions"] == out["assumptions"]
        assert submits == []
    for reason in (
        "ungranted:secret",
        "insights_timeout",
        "insights_unarmed",
        "unhonored_qualifier:group_by=supplier",
        "budget",
    ):
        env = _abstain(reason)
        out = apply_ask_guide(
            env,
            warehouse=path,
            grantable=GRANTS,
            space_id=None,
            session_id="ses_opt",
            submit=_submit,
            ledger_append=lambda _p: None,
        )
        assert out is env
        assert "clarify" not in out


def test_vague_curated_asks_offer_options_without_executing(
    monkeypatch: pytest.MonkeyPatch,
    lake: tuple[Path, Ontology],
) -> None:
    path, _onto = lake
    found: list[str] = []
    for row in _questions():
        env = maybe_generative_ask(
            str(row["question"]),
            warehouse=path,
            grantable=GRANTS,
            compute=lambda _ctx: {},
            submit=lambda _sql: None,
            ledger_append=lambda _p: None,
        )
        if env and "too vague or time-unbounded" in _reason(env):
            found.append(str(row["question"]))
    assert len(found) >= 2
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    fake = FakeCortex(submits=[], asks=[])
    exe = Executor(
        cortex=fake,  # type: ignore[arg-type]
        minter=ManifestMinter(openvault_url="http://127.0.0.1:9"),
        warehouse_path=path,
    )
    for question in found:
        off = monkeypatch
        off.delenv("DMS_ASK_CLARIFY", raising=False)
        bare = exe.live_ask(question, session_id="ses_vague")
        monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
        guided = exe.live_ask(question, session_id="ses_vague")
        assert "too vague or time-unbounded" in _reason(guided)
        assert bare["assumptions"] == guided["assumptions"]
        options = guided["clarify"]["options"]
        assert 1 <= len(options) <= 4
        assert "clarify" not in bare
    assert fake.submits == []


def test_unknown_measure_suppliers_by_risk_offers_options(
    monkeypatch: pytest.MonkeyPatch,
    lake: tuple[Path, Ontology],
) -> None:
    """The curated supplier-rank question, with Insights naming a missing metric.

    ``live_ask`` only reaches this door after the certified lane misses. The
    pack owns this phrase, so the test calls ``maybe_generative_ask`` (the
    function ``live_ask`` calls) and then the same ``apply_ask_guide`` post-step.
    """
    path, onto = lake
    question = _question("cq_supplier_ranking")
    submits: list[str] = []
    ledgers: list[Any] = []
    env = maybe_generative_ask(
        question,
        warehouse=path,
        grantable=GRANTS,
        ontology=onto,
        compute=lambda _ctx: {"ontology": {"metrics": [{"id": "suppliers_by_risk"}]}},
        submit=lambda sql: submits.append(sql),
        ledger_append=lambda payload: ledgers.append(payload),
    )
    assert env is not None
    assert "unknown_measure:" in _reason(env)
    assert "suppliers_by_risk" in _reason(env)
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    guided = apply_ask_guide(
        env,
        warehouse=path,
        grantable=GRANTS,
        space_id=None,
        session_id="ses_rank",
        submit=lambda sql: submits.append(sql),
        ledger_append=lambda payload: ledgers.append(payload),
    )
    assert 1 <= len(guided["clarify"]["options"]) <= 4
    assert submits == []
    assert ledgers == []


def test_options_do_not_open_oracles_or_import_the_score_pack(
    lake: tuple[Path, Ontology],
) -> None:
    path, onto = lake
    tree = ast.parse(ASK_CLARIFY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "demo_pack" not in (node.module or "")
            assert "score" not in (node.module or "")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "demo_pack" not in alias.name
    source = ASK_CLARIFY.read_text(encoding="utf-8")
    for needle in ("oracles.yaml", "questions.yaml", "query_skill", "score_curated"):
        assert needle not in source
    opened: list[str] = []
    real_open = open

    def _open(file: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkey = pytest.MonkeyPatch()
    monkey.setattr("builtins.open", _open)
    try:
        options = build_options(onto, grantable=GRANTS, warehouse=path)
    finally:
        monkey.undo()
    assert options
    blob = " ".join(opened)
    for needle in _FORBIDDEN_PATH:
        assert needle not in blob


def test_option_sql_is_explained_not_executed(
    monkeypatch: pytest.MonkeyPatch,
    lake: tuple[Path, Ontology],
) -> None:
    path, onto = lake
    sqls: list[str] = []
    real = __import__(
        "dms_executor.generative_ask", fromlist=["connect_file"]
    ).connect_file

    def _connect(warehouse: Path) -> Any:
        con = real(warehouse)
        orig = con.execute

        def _execute(sql: str, *args: Any, **kwargs: Any) -> Any:
            sqls.append(str(sql))
            return orig(sql, *args, **kwargs)

        con.execute = _execute  # type: ignore[method-assign]
        return con

    monkeypatch.setattr("dms_executor.generative_ask.connect_file", _connect)
    options = build_options(onto, grantable=GRANTS, warehouse=path)
    assert options
    assert sqls
    assert all(sql.strip().upper().startswith("EXPLAIN") for sql in sqls)


def test_public_plan_drops_client_sql() -> None:
    plan = public_plan(
        {
            "measure": "spend_kg",
            "sql": "DROP TABLE transactions",
            "entity": None,
            "filter": {"value": "MY"},
            "time_grain": None,
        }
    )
    assert "sql" not in plan
    assert "DROP" not in json.dumps(plan)


def test_clarify_run_is_refused_when_the_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
    lake: tuple[Path, Ontology],
) -> None:
    path, _onto = lake
    monkeypatch.delenv("DMS_ASK_CLARIFY", raising=False)
    fake = _GuideCortex()
    client = _client(monkeypatch, fake, warehouse=path)
    response = client.post(
        "/v1/chat/clarify/run",
        json={
            "option_id": "opt_x",
            "plan": {"measure": "spend_kg", "entity": None, "filter": None, "time_grain": None},
            "sql": "DROP TABLE transactions",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "clarify_disabled"
    assert fake.submits == []


def test_clarify_run_recompiles_and_rejects_a_mismatched_plan(
    monkeypatch: pytest.MonkeyPatch,
    lake: tuple[Path, Ontology],
) -> None:
    path, onto = lake
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    fake = _GuideCortex()
    client = _client(monkeypatch, fake, warehouse=path)
    bad = client.post(
        "/v1/chat/clarify/run",
        json={
            "option_id": "opt_missing",
            "plan": {"measure": "spend_kg", "entity": None, "filter": None, "time_grain": None},
            "sql": "DROP TABLE transactions",
        },
    )
    assert bad.status_code == 409
    assert bad.json()["detail"]["code"] == "clarify_plan_mismatch"
    assert fake.submits == []
    offered = build_options(onto, grantable=GRANTS, warehouse=path)
    grouped = [opt for opt in offered if opt["plan"]["entity"]]
    assert grouped, [(opt["plan"], opt["text"]) for opt in offered]
    pick = grouped[0]
    ok = client.post(
        "/v1/chat/clarify/run",
        json={
            "option_id": pick["id"],
            "plan": pick["plan"],
            "sql": "DROP TABLE transactions",
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    sqls = [
        str((getattr(req, "body", {}) or {}).get("sql") or "")
        for req in fake.submits
    ]
    ran = [sql for sql in sqls if sql]
    assert ran
    assert all("DROP" not in sql.upper() for sql in ran)
    assert body["badge"] != "L2_VALIDATED" or "DROP" not in str(body.get("sql_used") or "").upper()
    assert "DROP" not in str(body.get("sql_used") or "").upper()


def _scalar(compiled: CompiledQuery) -> CompiledQuery:
    return CompiledQuery(
        sql="SELECT SUM(quantity_kg) AS spend_kg FROM transactions",
        measure=compiled.measure,
        grain=compiled.grain,
        group_by=(),
        notes=compiled.notes,
        coverage=compiled.coverage
        or Coverage(
            include=("transactions.quantity_kg",),
            exclude=(NO_SILENT_PAD,),
            unsure=(),
        ),
    )


def _confirm(
    path: Path,
    onto: Ontology,
    *,
    submit: Any,
) -> dict[str, Any]:
    offered = build_options(onto, grantable=GRANTS, warehouse=path)
    pick = next(opt for opt in offered if opt["plan"]["entity"])
    return confirm_reading(
        option_id_value=pick["id"],
        plan=pick["plan"],
        warehouse=path,
        grantable=GRANTS,
        space_id=None,
        session_id="ses_guard",
        submit=submit,
        ledger_append=lambda _p: _ledger(),
    )


def _real_compile(onto: Ontology, plan: dict[str, Any]) -> Any:
    """The function object captured at import. A later monkeypatch does not replace it."""
    return compile_plan_fn(onto, plan)


def _compile_scalar(onto: Ontology, plan: dict[str, Any]) -> Any:
    compiled = _real_compile(onto, plan)
    if not isinstance(compiled, CompiledQuery):
        return compiled
    return _scalar(compiled)


def test_unhonored_qualifier_blocks_l2(
    monkeypatch: pytest.MonkeyPatch,
    lake: tuple[Path, Ontology],
) -> None:
    path, onto = lake
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    monkeypatch.setattr("dms_executor.ask_clarify.compile_plan", _compile_scalar)
    submits: list[str] = []
    env = _confirm(path, onto, submit=lambda sql: submits.append(sql))
    assert env["badge"] != "L2_VALIDATED"
    assert env["abstained"] is True
    assert "unhonored_qualifier" in _reason(env)
    assert submits == []


def test_rank_window_and_intent_shape_use_the_submit_door(
    monkeypatch: pytest.MonkeyPatch,
    lake: tuple[Path, Ontology],
) -> None:
    """Guards not on this main still sit on ``_submit_validated`` once merged."""
    path, onto = lake
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    submits: list[str] = []

    def _refuse(reason: str) -> None:
        monkeypatch.setattr(
            "dms_executor.generative_ask.unhonored_qualifier_reason",
            lambda *_a, **_k: reason,
        )

    _refuse("rank_window_unhandled_terms:top")
    env = _confirm(path, onto, submit=lambda sql: submits.append(sql))
    assert env["badge"] != "L2_VALIDATED"
    assert "rank_window_unhandled_terms" in _reason(env)
    assert submits == []

    _refuse("intent_shape_mismatch:list")
    env = _confirm(path, onto, submit=lambda sql: submits.append(sql))
    assert env["badge"] != "L2_VALIDATED"
    assert "intent_shape_mismatch" in _reason(env)
    assert submits == []

    def _door(*_a: Any, **_k: Any) -> dict[str, Any]:
        return _abstain("rank_window_unhandled_terms:side")

    monkeypatch.setattr("dms_executor.ask_clarify._submit_validated", _door)
    env = _confirm(path, onto, submit=lambda sql: submits.append(sql))
    assert env["badge"] == "ABSTAIN"
    assert "rank_window_unhandled_terms" in _reason(env)
    assert submits == []


def test_list_vs_aggregate_demotes_inside_the_answer_envelope(
    monkeypatch: pytest.MonkeyPatch,
    lake: tuple[Path, Ontology],
) -> None:
    path, onto = lake
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    monkeypatch.setattr(
        "dms_executor.generative_ask.unhonored_qualifier_reason",
        lambda *_a, **_k: None,
    )

    monkeypatch.setattr("dms_executor.ask_clarify.compile_plan", _compile_scalar)
    env = _confirm(path, onto, submit=lambda _sql: _rows_result())
    assert env["badge"] != "L2_VALIDATED"
    assert env.get("abstained") is True


def test_tier2_uses_validate_then_the_same_guard_door(
    monkeypatch: pytest.MonkeyPatch,
    lake: tuple[Path, Ontology],
) -> None:
    path, _onto = lake
    monkeypatch.setenv("DMS_ASK_CLARIFY", "1")
    validated: list[str] = []
    submitted: list[str] = []
    real_validate = __import__(
        "dms_executor.ask_clarify", fromlist=["validate_compiled_sql"]
    ).validate_compiled_sql
    real_submit = __import__(
        "dms_executor.ask_clarify", fromlist=["_submit_validated"]
    )._submit_validated

    def _validate(sql: str, **kwargs: Any) -> str | None:
        validated.append(sql)
        return real_validate(sql, **kwargs)

    def _submit(sql: str, **kwargs: Any) -> dict[str, Any]:
        submitted.append(sql)
        return real_submit(sql, **kwargs)

    monkeypatch.setattr("dms_executor.ask_clarify.validate_compiled_sql", _validate)
    monkeypatch.setattr("dms_executor.ask_clarify._submit_validated", _submit)
    env = {
        "badge": "L2_VALIDATED",
        "abstained": False,
        "text": "Found 1 row(s).",
        "values": [{"id": "v1", "label": "spend_kg", "value": 30}],
        "sql_used": "SELECT 1",
        "rows": [{"spend_kg": 30}],
        "assumptions": ["main"],
    }
    out = apply_ask_guide(
        env,
        warehouse=path,
        grantable=GRANTS,
        space_id=None,
        session_id="ses_tier",
        submit=lambda _sql: _rows_result(),
        ledger_append=lambda _p: _ledger(),
    )
    assert out["text"] == env["text"]
    assert out["badge"] == "L2_VALIDATED"
    tier2 = out["insights"]["tier2"]
    assert "30" in tier2["text"]
    assert "S1" in tier2["text"]
    assert submitted
    assert any(sql in validated for sql in submitted)

    validated.clear()
    submitted.clear()

    def _deny(sql: str, **_kwargs: Any) -> str:
        validated.append(sql)
        return "ungranted:secret"

    monkeypatch.setattr("dms_executor.ask_clarify.validate_compiled_sql", _deny)
    failed = apply_ask_guide(
        dict(env),
        warehouse=path,
        grantable=GRANTS,
        space_id=None,
        session_id="ses_tier",
        submit=lambda _sql: (_ for _ in ()).throw(AssertionError("tier2 executed")),
        ledger_append=lambda _p: _ledger(),
    )
    assert failed["text"] == env["text"]
    assert failed["badge"] == env["badge"]
    assert "tier2" not in failed.get("insights", {})
    assert submitted == []
    assert validated


def test_scorer_and_deploy_do_not_enable_the_flag() -> None:
    roots = [
        ROOT / "scripts",
        ROOT / "deploy",
        ROOT / ".github",
        ROOT / "apps",
        ROOT / "packages",
    ]
    hits: list[str] = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in {".py", ".yml", ".yaml", ".env", ".sh", ".toml", ""}:
                continue
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if _ON.search(text):
                hits.append(str(path.relative_to(ROOT)))
    assert hits == []


def test_confirm_reading_refuses_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
    lake: tuple[Path, Ontology],
) -> None:
    path, _onto = lake
    monkeypatch.delenv("DMS_ASK_CLARIFY", raising=False)
    with pytest.raises(AskServiceError) as caught:
        confirm_reading(
            option_id_value="opt_x",
            plan={"measure": "spend_kg"},
            warehouse=path,
            grantable=GRANTS,
            space_id=None,
            session_id=None,
            submit=lambda _sql: (_ for _ in ()).throw(AssertionError("submit")),
            ledger_append=lambda _p: None,
        )
    assert caught.value.code == "clarify_disabled"
