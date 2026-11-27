"""ONTO-GAPS-01. Flag default off. Synthetic schema only. No held-out text."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from dms_core.control_plane.onto_data import (
    ontology_data_store,
    reset_ontology_data_store,
)
from dms_executor.demo_warehouse import SERVING_DIALECT
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ground_defs import HEADER
from dms_executor.onto_gap import (
    drain_since,
    gap_log_path,
    note_reconfirm,
    question_hash,
    reset_gap_state,
)
from dms_executor.ontology import Ontology
from dms_executor.reconfirm import apply_reconfirm, peek_pending

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verify"))
sys.path.insert(0, str(ROOT / "scripts" / "onto"))

import onto_steward  # noqa: E402
import propose_gaps  # noqa: E402

_QUESTION = "How many shipments are in each status?"
_CELL = "qxv-staged-gap"
_SQL_GAP = "SELECT status, COUNT(*) AS n FROM shipments GROUP BY status"
_SQL_OK = "SELECT status, COUNT(*) AS n FROM shipments GROUP BY status"
_STEWARD = "162994ce-d61d-4363-8601-3654bece12af"
_REF = "onto-gaps-01-fixture"
_VOLATILE = {"as_of", "audit_id", "ticket_id", "answer_id"}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DMS_ONTO_GAP_LOG", str(tmp_path / "gaps.jsonl"))
    monkeypatch.setenv("DMS_ONTO_GAP_ASYNC", "0")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    for name in (
        "DMS_ONTO_GAP_PROPOSE",
        "DMS_ONTO_PG",
        "DMS_GROUND_DEFS",
        "DMS_SCHEMA_CONTEXT",
        "DMS_CLOOP_B",
        "DMS_AI_FIRST",
        "DMS_DEF_VALIDATE",
        "DMS_AO_COMPILE",
        "DATABASE_URL",
        "OPENVAULT_URL",
        "OPENVAULT_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_ontology_data_store()
    reset_gap_state()
    yield
    reset_gap_state()
    reset_ontology_data_store()


def _arm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_ONTO_GAP_PROPOSE", "1")
    monkeypatch.setenv("DMS_ONTO_PG", "1")
    monkeypatch.setenv("OPENVAULT_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENVAULT_API_KEY", "ov_test_gap_not_a_secret")


def _warehouse(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE shipments (shipment_id INTEGER PRIMARY KEY, status VARCHAR)")
    con.execute("INSERT INTO shipments VALUES (1, ?)", [_CELL])
    con.close()
    return path


def _ontology(*measures: tuple[str, str]) -> Ontology:
    onto = Ontology()
    onto.add_object("shipments", "shipments", ["shipment_id"])
    for name, expression in measures:
        onto.add_measure(name, "shipments", expression, description="shipment rows")
    onto.verified = True
    return onto


def _client(payload: dict[str, Any] | Exception) -> Any:
    from ao_vendor.llm.ov_client import OpenVaultOntologyClient

    def transport(_url: str, _headers: dict[str, str], _body: dict[str, Any]) -> Any:
        if isinstance(payload, Exception):
            raise payload
        return payload

    return OpenVaultOntologyClient(transport=transport)


def _proposal(**over: Any) -> dict[str, Any]:
    body = {
        "name": "shipments_by_status",
        "grain": "shipments",
        "expression": "COUNT(*)",
        "filters": [],
        "time_column": None,
        "unit": "count",
        "description": "shipment rows",
        "assumptions": [],
    }
    body.update(over)
    return body


def _ask(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sql: str,
    *,
    onto: Ontology,
    embedder: Any = None,
    ground: str | None = None,
    db_name: str = "ship.duckdb",
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    if ground is None:
        monkeypatch.delenv("DMS_GROUND_DEFS", raising=False)
    else:
        monkeypatch.setenv("DMS_GROUND_DEFS", ground)
    path = _warehouse(tmp_path, db_name)
    submitted: list[str] = []
    seen: dict[str, Any] = {}

    def submit(statement: str) -> Any:
        submitted.append(statement)
        handle = duckdb.connect(str(path))
        try:
            cur = handle.execute(statement)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            handle.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_gap", output={"rows": rows})

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen["ctx"] = json.dumps(ctx, sort_keys=True, default=str)
        seen["raw"] = ctx
        return {"query_sql": sql}

    env = maybe_generative_ask(
        _QUESTION,
        warehouse=path,
        grantable={"shipments"},
        compute=compute,
        submit=submit,
        ledger_append=lambda _payload: SimpleNamespace(entry_id="led", hash="h"),
        ontology=onto,
        dialect=SERVING_DIALECT,
        embedder=embedder,
        demo_gate=False,
        demo_ontology_allowed=False,
    )
    return env, submitted, seen


def _offer(env: dict[str, Any]) -> dict[str, Any]:
    return apply_reconfirm(
        env,
        question=_QUESTION,
        space_id=None,
        session_id=None,
        tables=["shipments"],
        ask_path=None,
        ontology="",
        schema="",
        complete=lambda _prompt: "",
    )


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip(item) for key, item in value.items() if key not in _VOLATILE}
    if isinstance(value, list):
        return [_strip(item) for item in value]
    return value


def _embed(texts: list[str]) -> list[list[float]]:
    return [
        [1.0, 0.0] if index == 0 or "shipments_by_status" in text else [0.0, 1.0]
        for index, text in enumerate(texts)
    ]


def test_flag_off_does_not_log_or_change_the_envelope() -> None:
    env = {
        "reconfirm": {"reason_code": "unknown_measure:absent"},
        "text": "same",
        "assumptions": ["GEN-01: unknown_measure:absent"],
    }
    note_reconfirm(env, question=_QUESTION, space_id=None, session_id=None)
    assert env["reconfirm"]["reason_code"] == "unknown_measure:absent"
    assert env["text"] == "same"
    assert not gap_log_path().exists()


def test_flag_off_prompt_envelope_and_route_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    onto = _ontology(("row_count", "COUNT(*)"))
    seq = {"n": 0}

    def _run(flag: str | None, pg: str | None) -> tuple[Any, Any, Any]:
        if flag is None:
            monkeypatch.delenv("DMS_ONTO_GAP_PROPOSE", raising=False)
        else:
            monkeypatch.setenv("DMS_ONTO_GAP_PROPOSE", flag)
        if pg is None:
            monkeypatch.delenv("DMS_ONTO_PG", raising=False)
        else:
            monkeypatch.setenv("DMS_ONTO_PG", pg)
        seq["n"] += 1
        env, submitted, seen = _ask(
            monkeypatch,
            tmp_path,
            _SQL_OK,
            onto=onto,
            db_name=f"byte_{seq['n']}.duckdb",
        )
        return _strip(env), seen["ctx"], submitted

    base_env, base_ctx, base_sql = _run(None, None)
    assert base_sql == [_SQL_OK]
    assert _run("0", None) == (base_env, base_ctx, base_sql)
    assert _run("1", None) == (base_env, base_ctx, base_sql)
    assert _run("1", "0") == (base_env, base_ctx, base_sql)
    armed_env, armed_ctx, armed_sql = _run("1", "1")
    assert armed_env == base_env
    assert armed_ctx == base_ctx
    assert armed_sql == base_sql
    assert isinstance(base_env, dict)
    assert base_env.get("abstained") is False


def test_absent_measure_is_proposed_and_not_served(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _arm(monkeypatch)
    client = _client(_proposal())
    env, submitted, _seen = _ask(monkeypatch, tmp_path, _SQL_GAP, onto=_ontology())
    assert env is not None
    assert submitted == []
    assert env["abstained"] is True
    offered = _offer(env)
    assert offered["reconfirm"]["reason_code"].startswith("unknown_measure")
    assert client.bodies == []
    outcomes = drain_since(since="0", client=client)
    assert len(outcomes) == 1
    assert outcomes[0]["status"] == "done"
    assert outcomes[0]["reason_code"] == "measure_proposed:shipments_by_status"
    assert offered["reconfirm"]["reason_code"] == "measure_proposed:shipments_by_status"
    assert offered["reconfirm"]["yes_is_approval"] is False
    assert "awaits steward approval" in offered["text"]
    assert offered["abstained"] is True
    assert offered.get("rows") in ([], None)
    pending = peek_pending(offered["reconfirm"]["token"])
    assert pending is not None
    assert str(pending.get("reason_code") or "").startswith("unknown_measure")
    assert "sql" not in pending
    body = client.bodies[0]
    assert body["role"] == "ontology"
    assert body["schema"] == "MeasureGapProposal"
    assert "model" not in body
    sent = json.dumps(body)
    assert _CELL not in sent
    assert "status" in sent
    stored = ontology_data_store().diff(outcomes[0]["version_id"])
    proposed = stored["proposed_only"] + stored["changed"] + stored["same"]
    measures = [row for row in proposed if row["kind"] == "measure"]
    assert len(measures) == 1
    assert measures[0]["status"] == "proposed"
    assert measures[0]["payload"]["name"] == "shipments_by_status"
    assert measures[0]["payload"]["expression"] == "COUNT(*)"
    provenance = measures[0]["provenance"]
    assert provenance["question_hash"] == question_hash(_QUESTION)
    assert _QUESTION not in json.dumps(provenance)
    assert provenance["model_stamp"] == {"role": "ontology", "schema": "MeasureGapProposal"}
    assert provenance["validation"]["ok"] is True
    assert ontology_data_store().approved_items() == []
    again = drain_since(since="0", client=client)
    assert again == []


def test_ungranted_column_is_dropped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _arm(monkeypatch)
    client = _client(_proposal(name="hidden_total", expression="SUM(hidden_qty)"))
    env, _submitted, _seen = _ask(monkeypatch, tmp_path, _SQL_GAP, onto=_ontology())
    assert env is not None
    offered = _offer(env)
    outcomes = drain_since(since="0", client=client)
    assert outcomes[0]["status"] == "dropped"
    assert outcomes[0]["drop_reason"] == "ungranted_column"
    assert outcomes[0]["version_id"] is None
    assert offered["reconfirm"]["reason_code"].startswith("unknown_measure")
    assert ontology_data_store().diff().get("version_id") is None


def test_duplicate_expression_is_dropped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _arm(monkeypatch)
    client = _client(_proposal(name="shipments_by_status", expression="count(*)"))
    env, _submitted, _seen = _ask(
        monkeypatch,
        tmp_path,
        _SQL_GAP,
        onto=_ontology(("row_count", "COUNT(*)")),
        db_name="dup.duckdb",
    )
    assert env is not None
    # The existing measure matches COUNT(*), so this SQL is not a gap.
    # Log the reconfirm directly: the expression still has to be rejected.
    reset_gap_state()
    from dms_executor.onto_gap import bind_gap_context

    path = tmp_path / "dup.duckdb"
    bind_gap_context(
        question=_QUESTION,
        warehouse=str(path),
        grantable={"shipments"},
        ontology=_ontology(("row_count", "COUNT(*)")),
        space_id=None,
        session_id=None,
    )
    held = {
        "answer_id": "ans_dup",
        "abstained": True,
        "badge": "ABSTAIN",
        "text": "gap",
        "assumptions": ["GEN-01: unknown_measure:absent"],
        "reconfirm": {"reason_code": "unknown_measure:absent", "token": "rcf_dup"},
        "rows": [],
        "values": [],
        "sql_used": None,
    }
    note_reconfirm(held, question=_QUESTION, space_id=None, session_id=None)
    outcomes = drain_since(since="0", client=client)
    assert outcomes[0]["drop_reason"] == "duplicate_measure"
    assert outcomes[0]["version_id"] is None
    assert held["reconfirm"]["reason_code"] == "unknown_measure:absent"


def test_openvault_422_keeps_todays_reconfirm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ao_vendor.llm.ov_client import AoLlmError

    _arm(monkeypatch)
    client = _client(AoLlmError("ao_ov_http_422"))
    env, submitted, _seen = _ask(monkeypatch, tmp_path, _SQL_GAP, onto=_ontology())
    assert env is not None
    assert submitted == []
    offered = _offer(env)
    before = offered["reconfirm"]["reason_code"]
    outcomes = drain_since(since="0", client=client)
    assert outcomes[0]["status"] == "gap_propose_unavailable"
    assert outcomes[0]["drop_reason"] == "gap_propose_unavailable"
    assert offered["reconfirm"]["reason_code"] == before
    assert offered["abstained"] is True
    assert ontology_data_store().approved_items() == []


def test_steward_approval_grounds_and_serves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _arm(monkeypatch)
    client = _client(_proposal())
    env, _submitted, _seen = _ask(monkeypatch, tmp_path, _SQL_GAP, onto=_ontology())
    assert env is not None
    offered = _offer(env)
    outcomes = drain_since(since="0", client=client)
    version_id = outcomes[0]["version_id"]
    approved = onto_steward.approve_version_cmd(
        version_id=version_id,
        actor=_STEWARD,
        approval_ref=_REF,
        store=ontology_data_store(),
    )
    assert approved["n"] >= 1
    assert approved["approval_ref"] == _REF
    onto = Ontology.from_store()
    assert "shipments_by_status" in onto.measures
    assert onto.measures["shipments_by_status"].expression == "COUNT(*)"
    onto.verified = True
    served, ran, seen = _ask(
        monkeypatch,
        tmp_path,
        _SQL_OK,
        onto=onto,
        embedder=_embed,
        ground="1",
        db_name="approved.duckdb",
    )
    assert served is not None
    assert served["abstained"] is False
    assert served["badge"] == "L2_VALIDATED"
    assert ran == [_SQL_OK]
    assert served["rows"]
    assert "1" in served["text"]
    raw = seen["raw"]
    assert HEADER in raw["governed_definitions"]
    assert "shipments_by_status" in raw["governed_definitions"]
    assert "COUNT(*)" in raw["governed_definitions"]
    # The reconfirm Yes was not what approved this version.
    assert offered["reconfirm"]["yes_is_approval"] is False


def test_cli_refuses_when_the_flag_is_off() -> None:
    assert propose_gaps.main(["--since", "0"]) == 2


def test_cli_since_drains(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _arm(monkeypatch)
    seen: dict[str, str] = {}

    def fake(*, since: str, client: Any = None) -> list[dict[str, Any]]:
        seen["since"] = since
        return [
            {
                "gap_id": "abc",
                "status": "done",
                "reason_code": "measure_proposed:shipments_by_status",
                "version_id": "ver",
                "drop_reason": None,
            }
        ]

    monkeypatch.setattr("dms_executor.onto_gap.drain_since", fake)
    assert propose_gaps.main(["--since", "1970-01-01T00:00:00Z"]) == 0
    assert seen["since"] == "1970-01-01T00:00:00Z"
    printed = capsys.readouterr().out
    assert "gap_id=abc" in printed
    assert "measure_proposed:shipments_by_status" in printed
    assert _QUESTION not in printed
    assert _CELL not in printed
