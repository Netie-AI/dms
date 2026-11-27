"""R1-ARBITER-01. DMS_AI_ARBITER defaults off.

Off, the generative envelope object and its served_route stay as the caller
left them. On, a wrong-grain compile does not beat model SQL the shape
checker accepts. A missing checker reconfirms and serves no rows.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from dms_executor.arbiter import (
    ResultSet,
    arbiter_enabled,
    compile_context,
    gate_generative,
    serve,
)
from dms_executor.route_stamp import (
    ROUTE_AI,
    ROUTE_AI_EXAMPLE,
    ROUTE_CERTIFIED,
    ROUTE_COMPILE,
    ROUTE_L1,
    ROUTE_RULE,
    route_of,
)

ROOT = Path(__file__).resolve().parents[1]
ARBITER = ROOT / "packages" / "executor" / "dms_executor" / "arbiter.py"
FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
SKU_Q = "How many SKUs do we have in inventory?"
COMPILE_SQL = "SELECT SUM(qty) AS total_qty FROM stock"
MODEL_SQL = "SELECT location, qty FROM stock"
GRAIN = {"group_by": ["location"]}
_VOLATILE = frozenset({"as_of", "ticket_id", "engine_as_of", "engine_as_of_after"})


def _rows_digest(rows: object) -> str:
    payload = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _ai_env(sql: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "abstained": False,
        "sql_used": sql,
        "rows": rows,
        "values": [{"id": "v0", "value": 1.0, "label": "n"}],
        "served_route": "model",
        "ladder_rung": "generate_sql",
        "plan_origin": "generate_sql",
        "served_model": "answer",
        "badge": "L2_VALIDATED",
        "route": "generated",
        "text": "Checked answer.",
    }


def _set(sql: str, rows: list[dict[str, Any]], *, envelope: dict[str, Any] | None) -> ResultSet:
    from dms_executor.arbiter import table_of

    columns, data = table_of(rows)
    return ResultSet(
        sql=sql,
        columns=tuple(columns),
        rows=tuple(tuple(row) for row in data),
        envelope=envelope,
        executed=True,
    )


def _install(fn: Any) -> None:
    mod = types.ModuleType("dms_executor.shape_check")
    mod.check_answer_shape = fn  # type: ignore[attr-defined]
    sys.modules["dms_executor.shape_check"] = mod


def _drop_shape() -> None:
    sys.modules.pop("dms_executor.shape_check", None)


def _grain_check(
    question: str,
    sql: str,
    columns: list[str],
    sample_rows: list[list[Any]],
    row_count: int,
    ontology_slice: dict[str, Any],
) -> SimpleNamespace:
    del question, sql, sample_rows
    grain = [str(name) for name in ontology_slice.get("group_by") or []]
    present = [name for name in grain if name in columns]
    if grain and not present:
        return SimpleNamespace(verdict="misfit", reasons=["missing_grain_column"], fix_hint=None)
    if grain and present and row_count >= 1:
        return SimpleNamespace(verdict="fits", reasons=["grain_column_present"], fix_hint=None)
    return SimpleNamespace(verdict="unknown", reasons=["no_grain"], fix_hint=None)


def _fixed(verdict: str):
    def check_answer_shape(*_a: Any, **_k: Any) -> SimpleNamespace:
        return SimpleNamespace(verdict=verdict, reasons=[verdict], fix_hint=None)

    return check_answer_shape


def _serve_fn() -> ast.FunctionDef:
    tree = ast.parse(ARBITER.read_text(encoding="utf-8"))
    return next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "serve"
    )


def _serve_returns(fn: ast.FunctionDef) -> list[ast.Return]:
    found: list[ast.Return] = []

    def _walk(stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, ast.Return):
                found.append(stmt)
            for child in _bodies(stmt):
                _walk(child)

    _walk(fn.body)
    return found


def _bodies(stmt: ast.stmt) -> list[list[ast.stmt]]:
    out: list[list[ast.stmt]] = []
    for name in ("body", "orelse", "finalbody"):
        block = getattr(stmt, name, None)
        if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
            out.append(block)
    if isinstance(stmt, ast.Try):
        for handler in stmt.handlers:
            out.append(handler.body)
    return out


def _assigns_served_route(stmt: ast.stmt) -> bool:
    targets: list[ast.expr] = []
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
    elif isinstance(stmt, ast.AnnAssign) and stmt.target is not None:
        targets = [stmt.target]
    for target in targets:
        if (
            isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == "served_route"
        ):
            return True
    return False


def _return_is_stamped(fn_body: list[ast.stmt], ret: ast.Return) -> bool:
    def _seek(stmts: list[ast.stmt], seen: bool) -> bool | None:
        flagged = seen
        for stmt in stmts:
            if stmt is ret:
                return flagged
            if _assigns_served_route(stmt):
                flagged = True
            for child in _bodies(stmt):
                got = _seek(child, flagged)
                if got is not None:
                    return got
        return None

    return _seek(fn_body, False) is True


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_AI_ARBITER", raising=False)
    assert arbiter_enabled() is False
    monkeypatch.setenv("DMS_AI_ARBITER", "0")
    assert arbiter_enabled() is False


def test_compile_context_off_does_not_set_ai_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_AI_ARBITER", raising=False)
    monkeypatch.delenv("DMS_AI_FIRST", raising=False)
    from dms_executor.ai_first import ai_first_enabled

    with compile_context():
        assert ai_first_enabled() is False
    assert "DMS_AI_FIRST" not in __import__("os").environ


def test_compile_context_on_restores_ai_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_AI_ARBITER", "1")
    monkeypatch.delenv("DMS_AI_FIRST", raising=False)
    from dms_executor.ai_first import ai_first_enabled

    with compile_context():
        assert ai_first_enabled() is True
    assert ai_first_enabled() is False


def test_flag_off_gate_is_the_same_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_AI_ARBITER", raising=False)
    env = {"served_route": ROUTE_L1, "ladder_rung": "l1", "rows": [{"n": 1}]}
    got = gate_generative(env, question="q", submit=lambda _sql: None)
    assert got is env
    assert got["served_route"] == ROUTE_L1


def test_every_serve_return_sets_served_route() -> None:
    fn = _serve_fn()
    returns = _serve_returns(fn)
    assert len(returns) >= 6
    for ret in returns:
        assert _return_is_stamped(fn.body, ret), ast.dump(ret)


def test_runtime_paths_set_a_closed_route() -> None:
    model_rows = [{"location": "A", "qty": 10}, {"location": "B", "qty": 5}]
    same = [{"location": "A", "qty": 10}]
    ai_same = _ai_env(MODEL_SQL, same)
    cases = {
        "agree": serve(
            question="stock",
            ai=_set(MODEL_SQL, same, envelope=ai_same),
            compile=_set(COMPILE_SQL, same, envelope=None),
        ),
        "certified": serve(
            question="stock",
            certified=_set(
                "SELECT 1 AS n",
                [{"n": 1}],
                envelope={"abstained": False, "sql_used": "SELECT 1 AS n", "rows": [{"n": 1}]},
            ),
        ),
        "ai_only": serve(
            question="stock",
            ai=_set(MODEL_SQL, model_rows, envelope=_ai_env(MODEL_SQL, model_rows)),
        ),
        "example": serve(
            question="stock",
            ai=_set(MODEL_SQL, model_rows, envelope=_ai_env(MODEL_SQL, model_rows)),
            examples=[{"sql": MODEL_SQL, "source": "example"}],
        ),
        "compile_only": serve(
            question="stock",
            compile=ResultSet(sql=COMPILE_SQL, columns=(), rows=(), envelope=None, executed=False),
        ),
        "empty": serve(question="stock"),
    }
    for name, env in cases.items():
        assert env["served_route"] in {
            ROUTE_AI,
            ROUTE_AI_EXAMPLE,
            ROUTE_CERTIFIED,
            ROUTE_RULE,
        }, name
        assert str(env.get("route_reason") or "").strip(), name
        assert route_of(env) != "unattributed", name
    assert cases["agree"]["served_route"] == ROUTE_AI
    assert cases["agree"]["agreed_with"] == "compile"
    assert cases["certified"]["served_route"] == ROUTE_CERTIFIED
    assert cases["example"]["served_route"] == ROUTE_AI_EXAMPLE
    assert cases["compile_only"]["rows"] == []
    assert cases["empty"]["rows"] == []


def test_wrong_grain_model_wins_and_question_text_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(_grain_check)
    monkeypatch.delenv("DMS_AI_ARBITER", raising=False)
    try:
        compile_rows = [{"total_qty": 15}]
        model_rows = [{"location": "A", "qty": 10}, {"location": "B", "qty": 5}]
        ai = _set(MODEL_SQL, model_rows, envelope=_ai_env(MODEL_SQL, model_rows))
        compiled = _set(COMPILE_SQL, compile_rows, envelope=None)
        first = serve(
            question="stock value by location",
            ai=ai,
            compile=compiled,
            ontology_slice=GRAIN,
        )
        second = serve(
            question="unrelated wording",
            ai=ai,
            compile=compiled,
            ontology_slice=GRAIN,
        )
    finally:
        _drop_shape()
    assert first["served_route"] == ROUTE_AI
    assert first["route_reason"] == "shape_fits"
    assert first["sql_used"] == MODEL_SQL
    assert len(first["rows"]) == 2
    assert "total_qty" not in first["rows"][0]
    assert second["served_route"] == first["served_route"]
    assert second["sql_used"] == first["sql_used"]


def test_both_misfit_reconfirms_with_no_rows() -> None:
    _install(_fixed("misfit"))
    try:
        one = [{"location": "A", "qty": 1}]
        env = serve(
            question="stock",
            ai=_set(MODEL_SQL, one, envelope=_ai_env(MODEL_SQL, one)),
            compile=_set(COMPILE_SQL, [{"total_qty": 9}], envelope=None),
            ontology_slice=GRAIN,
        )
    finally:
        _drop_shape()
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    assert env["sql_used"] is None
    assert env["served_route"] == ROUTE_RULE
    assert env["reconfirm"]["reason_code"] == "arbiter_disagree"
    roles = {item["role"] for item in env["reconfirm"]["previews"]}
    assert roles == {"compile", "ai"}
    for item in env["reconfirm"]["previews"]:
        assert set(item) == {"role", "sql", "columns", "row_count"}


def test_both_fit_reconfirms() -> None:
    _install(_fixed("fits"))
    try:
        env = serve(
            question="stock",
            ai=_set(
                MODEL_SQL,
                [{"location": "A", "qty": 1}],
                envelope=_ai_env(MODEL_SQL, [{"location": "A", "qty": 1}]),
            ),
            compile=_set(COMPILE_SQL, [{"total_qty": 9}], envelope=None),
            ontology_slice=GRAIN,
        )
    finally:
        _drop_shape()
    assert env["rows"] == []
    assert env["reconfirm"]["reason_code"] == "arbiter_disagree"


def test_shape_check_missing_reconfirms_and_does_not_crash() -> None:
    _drop_shape()
    env = serve(
        question="stock",
        ai=_set(
            MODEL_SQL,
            [{"location": "A", "qty": 1}],
            envelope=_ai_env(MODEL_SQL, [{"location": "A", "qty": 1}]),
        ),
        compile=_set(COMPILE_SQL, [{"total_qty": 9}], envelope=None),
        ontology_slice=GRAIN,
    )
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["values"] == []
    assert env["route_reason"] == "shape_check_missing"
    assert env["reconfirm"]["reason_code"] == "arbiter_disagree"


def test_agree_does_not_need_the_checker() -> None:
    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("checker must not run")

    _install(_boom)
    try:
        rows = [{"location": "A", "qty": 10}]
        env = serve(
            question="stock",
            ai=_set(MODEL_SQL, rows, envelope=_ai_env(MODEL_SQL, rows)),
            compile=_set(COMPILE_SQL, rows, envelope=None),
        )
    finally:
        _drop_shape()
    assert env["served_route"] == ROUTE_AI
    assert env["agreed_with"] == "compile"
    assert env["rows"] == rows


def test_apply_generative_serves_model_sql_not_compile() -> None:
    from dms_executor.arbiter import apply_generative

    _install(_grain_check)
    try:
        env = apply_generative(
            _ai_env(MODEL_SQL, [{"location": "A", "qty": 10}, {"location": "B", "qty": 5}]),
            question="stock value by location",
            submit=lambda sql: SimpleNamespace(ok=True, output={"rows": [{"total_qty": 15}]}),
            ontology_slice=GRAIN,
            examples=[{"source": "compile", "sql": COMPILE_SQL}],
        )
    finally:
        _drop_shape()
    assert env is not None
    assert env["served_route"] == ROUTE_AI
    assert env["sql_used"] == MODEL_SQL
    assert len(env["rows"]) == 2


def test_compile_envelope_is_not_served() -> None:
    from dms_executor.arbiter import apply_generative

    env = apply_generative(
        {
            "abstained": False,
            "sql_used": COMPILE_SQL,
            "rows": [{"total_qty": 15}],
            "served_route": "ontology_compile",
            "ladder_rung": "compile",
            "plan_origin": "ontology_compile",
            "badge": "L2_VALIDATED",
        },
        question="stock",
        submit=lambda _sql: (_ for _ in ()).throw(AssertionError("submit")),
    )
    assert env is not None
    assert env["abstained"] is True
    assert env["rows"] == []
    assert env["served_route"] == ROUTE_RULE


def test_flag_on_compile_route_does_not_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_AI_ARBITER", "1")
    monkeypatch.delenv("DMS_AI_FIRST", raising=False)
    from dms_executor.ai_first import begin_example_turn, end_example_turn
    from dms_executor.generative_ask import _submit_validated
    from dms_executor.ontology import NO_SILENT_PAD, Coverage

    token = begin_example_turn()

    def _submit(sql: str) -> None:
        raise AssertionError(sql)

    try:
        with compile_context():
            env = _submit_validated(
                "SELECT 1 AS n",
                question="how many",
                space_id=FINANCE,
                session_id="ses_arbiter",
                submit=_submit,
                ledger_append=lambda _payload: None,
                notes=(),
                plan_source="ontology_plan",
                coverage=Coverage(
                    include=("n",),
                    exclude=(NO_SILENT_PAD,),
                    unsure=("none",),
                ),
                served_route=ROUTE_COMPILE,
            )
    finally:
        end_example_turn(token)
    assert env["abstained"] is True
    assert env["rows"] == []


def test_no_question_phrase_rules_in_arbiter() -> None:
    text = ARBITER.read_text(encoding="utf-8")
    assert " in question" not in text
    assert "re.compile" not in text
    assert "re.search" not in text
    lowered = text.lower()
    for name in ("openai", "anthropic", "gpt-", "claude"):
        assert name not in lowered


def _stable(env: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in env.items() if key not in _VOLATILE}


@pytest.fixture
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DMS_AI_FIRST", raising=False)
    from dms_executor.demo_warehouse import ensure_demo_warehouse

    db = tmp_path / "arbiter.duckdb"
    ensure_demo_warehouse(db)
    return db


def _pack_ask(db: Path, question: str = SKU_Q) -> tuple[dict[str, Any], list[str]]:
    from cortex_client.models import (
        AskRequest,
        AskResponse,
        LedgerAppendRequest,
        LedgerAppendResponse,
    )
    from cortex_contract.execution import Manifest, QueryResult
    from dms_executor import Executor
    from dms_executor.manifest import ManifestMinter, SessionAcl

    seen: list[str] = []

    class _Stub:
        def compute_insights(self, question: str, **_k: Any) -> dict[str, Any]:
            seen.append(question)
            return {"ontology": {"metrics": []}}

        def submit(self, req: Any) -> QueryResult:
            return QueryResult(ok=True, status="ok", run_id="run_sql", output={"rows": [{"n": 1}]})

        def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
            _ = req
            return LedgerAppendResponse(entry_id="led_ok", hash="hash_ok_not_entry")

        def ask(self, req: AskRequest) -> AskResponse:
            _ = req
            return AskResponse(
                answer="Abstained.",
                badge="abstain",
                sql_used=None,
                rows=[],
                assumptions="fixture",
                audit_id="aud_copy",
                route="abstain",
                abstained=True,
            )

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
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdA",
        )

    minter.mint_manifest = _mint  # type: ignore[method-assign]
    minter.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    minter.close = lambda: None  # type: ignore[method-assign]
    minter.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    exe = Executor(cortex=_Stub(), minter=minter, warehouse_path=db)  # type: ignore[arg-type]
    try:
        return exe.live_ask(question, space_id=FINANCE, session_id="ses_arbiter"), seen
    finally:
        exe.close()


def test_flag_off_pack_envelope_is_byte_identical(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_AI_ARBITER", raising=False)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("arbiter serve")

    monkeypatch.setattr("dms_executor.arbiter.serve", _boom)
    monkeypatch.setattr("dms_executor.arbiter.serve_certified", _boom)
    monkeypatch.setattr("dms_executor.arbiter.apply_generative", _boom)
    unset, prompts_unset = _pack_ask(warehouse)
    monkeypatch.setenv("DMS_AI_ARBITER", "0")
    zero, prompts_zero = _pack_ask(warehouse)
    assert unset["served_route"] == zero["served_route"] == ROUTE_L1
    assert _stable(unset) == _stable(zero)
    assert prompts_unset == prompts_zero == []


def test_flag_on_certified_keeps_rows_and_stamps(
    warehouse: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_AI_ARBITER", raising=False)
    off, _ = _pack_ask(warehouse)
    monkeypatch.setenv("DMS_AI_ARBITER", "1")
    on, prompts = _pack_ask(warehouse)
    assert on["served_route"] == ROUTE_CERTIFIED
    assert on["route_reason"] == "certified_no_ai_sql"
    assert on["badge"] == off["badge"] == "L1_GOVERNED_METRIC"
    assert on["sql_used"] == off["sql_used"]
    assert _rows_digest(on["rows"]) == _rows_digest(off["rows"])
    assert prompts == []
