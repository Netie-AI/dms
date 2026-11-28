"""BIRD-EVIDENCE-01. Flag default off. No word list. No skip, no xfail."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from cortex_client.compute import _insights_body
from cortex_client.insights import SPACE_KNOWLEDGE_MARK
from dms_core.pii import NAME_MASK_KEY
from dms_executor import _insights_compute_seam
from dms_executor.demo_warehouse import SERVING_DIALECT
from dms_executor.generative_ask import (
    currency_mismatch_reason,
    dropped_stated_value_reason,
    maybe_generative_ask,
    unhonored_qualifier_reason,
)
from dms_executor.ontology import Ontology
from dms_executor.reconfirm import question_requests_pii
from dms_executor.space_knowledge import (
    HEADER,
    ROLE,
    KnowledgeDisabled,
    approve_note,
    knowledge_prompt,
    list_notes,
    notes_in_rank_order,
    propose_note,
    rank_request_body,
    retrieve_notes,
    revise_note,
    space_knowledge_enabled,
)
from dms_executor.sql_currency import asked_currencies


def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SPACE_KNOWLEDGE", "1")


def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_SPACE_KNOWLEDGE", raising=False)


def test_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _off(monkeypatch)
    assert space_knowledge_enabled() is False
    monkeypatch.setenv("DMS_SPACE_KNOWLEDGE", "0")
    assert space_knowledge_enabled() is False


def test_flag_off_stores_nothing_and_reads_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _off(monkeypatch)
    path = tmp_path / "off.duckdb"
    duckdb.connect(str(path)).close()

    def _boom(*_a: Any, **_k: Any) -> list[list[float]]:
        raise AssertionError("embedder called with the flag off")

    with pytest.raises(KnowledgeDisabled):
        propose_note(
            space_id="space-k",
            body="high value means amount > 1000",
            path=path,
        )
    assert (
        retrieve_notes("what is the total amount", space_id="space-k", warehouse=path) == []
    )
    assert (
        knowledge_prompt(
            "what is the total amount",
            space_id="space-k",
            warehouse=path,
            embedder=_boom,
            ask_context="Czech koruna refers to Currency = 'CZK'",
        )
        == ""
    )
    monkeypatch.setenv("DMS_SPACE_KNOWLEDGE", "0")
    assert (
        knowledge_prompt(
            "what is the total amount",
            space_id="space-k",
            warehouse=path,
            embedder=_boom,
            ask_context="email the owner",
        )
        == ""
    )


def test_approve_versions_and_retrieval_stays_in_the_space(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _on(monkeypatch)
    path = tmp_path / "notes.duckdb"
    duckdb.connect(str(path)).close()
    proposed = propose_note(
        space_id="space-k",
        body="high value means amount > 1000",
        sql_fragment="amount > 1000",
        actor="steward-a",
        path=path,
    )
    assert proposed["status"] == "proposed"
    assert proposed["version"] == 1
    assert retrieve_notes(
        "what is the total amount", space_id="space-k", warehouse=path
    ) == []
    approved = approve_note(note_id=proposed["note_id"], actor="steward-a", path=path)
    assert approved["status"] == "approved"
    assert approved["approved_by"] == "steward-a"
    other = propose_note(
        space_id="space-other",
        body="other space fact",
        actor="steward-a",
        path=path,
    )
    approve_note(note_id=other["note_id"], actor="steward-a", path=path)
    rows = retrieve_notes(
        "what is the total amount", space_id="space-k", warehouse=path
    )
    assert [row["body"] for row in rows] == ["high value means amount > 1000"]
    assert rows[0]["sql_fragment"] == "amount > 1000"
    listed = list_notes(space_id="space-k", path=path)
    assert {row["note_id"] for row in listed} == {proposed["note_id"]}
    revised = revise_note(
        note_id=proposed["note_id"],
        body="high value means amount > 5000",
        sql_fragment="amount > 5000",
        actor="steward-b",
        path=path,
    )
    assert revised["version"] == 2
    assert revised["status"] == "proposed"
    assert revised["supersedes"] == proposed["note_id"]
    still = retrieve_notes(
        "what is the total amount", space_id="space-k", warehouse=path
    )
    assert [row["body"] for row in still] == ["high value means amount > 1000"]
    approve_note(note_id=revised["note_id"], actor="steward-b", path=path)
    current = retrieve_notes(
        "what is the total amount", space_id="space-k", warehouse=path
    )
    assert [row["body"] for row in current] == ["high value means amount > 5000"]
    assert current[0]["version"] == 2
    visible = list_notes(space_id="space-k", path=path)
    assert [row["note_id"] for row in visible] == [revised["note_id"]]


def test_destructive_fragment_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _on(monkeypatch)
    path = tmp_path / "bad.duckdb"
    duckdb.connect(str(path)).close()
    with pytest.raises(ValueError, match="sql_fragment_rejected"):
        propose_note(
            space_id="space-k",
            body="do not run this",
            sql_fragment="DROP TABLE orders",
            path=path,
        )


def test_embedder_ranks_without_a_shared_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _on(monkeypatch)
    path = tmp_path / "embed.duckdb"
    duckdb.connect(str(path)).close()
    kept = propose_note(
        space_id="space-k",
        body="qxv marker means zeta_mark is set",
        actor="steward-a",
        path=path,
    )
    decoy = propose_note(
        space_id="space-k",
        body="pile of qrv units",
        actor="steward-a",
        path=path,
    )
    approve_note(note_id=kept["note_id"], actor="steward-a", path=path)
    approve_note(note_id=decoy["note_id"], actor="steward-a", path=path)

    def embed(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for index, text in enumerate(texts):
            if index == 0 or "zeta_mark" in text:
                out.append([1.0, 0.0])
            else:
                out.append([0.0, 1.0])
        return out

    question = "which vendors are past the inspection window"
    block = knowledge_prompt(
        question, space_id="space-k", warehouse=path, embedder=embed
    )
    assert block.startswith(SPACE_KNOWLEDGE_MARK)
    assert "qxv marker means zeta_mark is set" in block
    assert "pile of qrv units" not in block
    assert HEADER in block


def test_model_rank_uses_a_role_and_no_model_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _on(monkeypatch)
    path = tmp_path / "rank.duckdb"
    duckdb.connect(str(path)).close()
    chosen = propose_note(
        space_id="space-k",
        body="zeta rule is the inspection window",
        actor="steward-a",
        path=path,
    )
    decoy = propose_note(
        space_id="space-k",
        body="total amount of every order",
        actor="steward-a",
        path=path,
    )
    approve_note(note_id=chosen["note_id"], actor="steward-a", path=path)
    approve_note(note_id=decoy["note_id"], actor="steward-a", path=path)
    seen: dict[str, Any] = {}

    def ranker(question: str, notes: list[dict[str, Any]]) -> list[str]:
        body = rank_request_body(question, notes)
        seen["body"] = body
        return [chosen["note_id"]]

    question = "what is the total amount"
    block = knowledge_prompt(
        question,
        space_id="space-k",
        warehouse=path,
        ranker=ranker,
        ask_context="Czech koruna refers to Currency = 'CZK'",
    )
    body = seen["body"]
    assert body["role"] == ROLE
    assert "model" not in body
    assert "provider" not in body
    assert "strict" not in body
    blob = str(body)
    assert "model_id" not in blob
    assert "zeta rule is the inspection window" in block
    assert "total amount of every order" not in block
    assert "Czech koruna refers to Currency = 'CZK'" in block
    assert notes_in_rank_order("not-json", [{"note_id": "kn_x", "body": "a"}]) is None
    assert notes_in_rank_order(["nope"], [{"note_id": "kn_x", "body": "a"}]) == []


_QUESTION = "what is the total amount"
_NOTE = "high value means amount > 1000"
_CONTEXT = "Czech koruna refers to Currency = 'CZK'. email is not a column"
_SQL = "SELECT amount FROM orders"
_VOLATILE = {
    "answer_id",
    "audit_id",
    "as_of",
    "drillthrough_token",
    "timings",
    "served_attribution",
}


def _ontology() -> Ontology:
    onto = Ontology()
    onto.add_object("orders", "orders", ["tier"])
    onto.add_measure("total_amount", "orders", "SUM(amount)", description="pile")
    onto.verified = True
    return onto


def _embed(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for index, text in enumerate(texts):
        if index == 0 or "1000" in text:
            out.append([1.0, 0.0])
        else:
            out.append([0.0, 1.0])
    return out


def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DMS_GROUND_DEFS",
        "DMS_VQ_RAG",
        "DMS_CLOOP_B",
        "DMS_DEF_VALIDATE",
        "DMS_SCHEMA_CONTEXT",
        "DMS_AI_FIRST",
        "DMS_SCALE_MISS",
        "DMS_ASK_LADDER",
        "DMS_NO_WORD_RULES",
        "DMS_RULES_OFF",
    ):
        monkeypatch.delenv(name, raising=False)


def _lake(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE orders (amount INTEGER, tier VARCHAR)")
    con.execute("INSERT INTO orders VALUES (50, 'high'), (20, 'low')")
    con.close()
    return path


def _stable(env: dict[str, Any] | None) -> str:
    if not isinstance(env, dict):
        return ""
    kept = {key: value for key, value in env.items() if key not in _VOLATILE}
    return json.dumps(kept, sort_keys=True, default=str)


def _ask(
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
    *,
    flag: str | None,
    context: str | None,
    embedder: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any], list[str]]:
    _quiet(monkeypatch)
    if flag is None:
        monkeypatch.delenv("DMS_SPACE_KNOWLEDGE", raising=False)
    else:
        monkeypatch.setenv("DMS_SPACE_KNOWLEDGE", flag)
    seen: dict[str, Any] = {}
    gates: list[str] = []

    def _wrap(fn: Any) -> Any:
        def _inner(question: str, *args: Any, **kwargs: Any) -> Any:
            gates.append(question)
            return fn(question, *args, **kwargs)

        return _inner

    monkeypatch.setattr(
        "dms_executor.generative_ask.unhonored_qualifier_reason",
        _wrap(unhonored_qualifier_reason),
    )
    monkeypatch.setattr(
        "dms_executor.generative_ask.dropped_stated_value_reason",
        _wrap(dropped_stated_value_reason),
    )
    monkeypatch.setattr(
        "dms_executor.generative_ask.currency_mismatch_reason",
        _wrap(currency_mismatch_reason),
    )

    def submit(statement: str) -> Any:
        handle = duckdb.connect(str(path))
        try:
            cur = handle.execute(statement)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            handle.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_k", output={"rows": rows})

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen["raw"] = ctx
        seen["ctx"] = json.dumps(ctx, sort_keys=True, default=str)
        return {"query_sql": _SQL}

    env = maybe_generative_ask(
        _QUESTION,
        space_id="space-k",
        warehouse=path,
        grantable={"orders"},
        compute=compute,
        submit=submit,
        ledger_append=lambda _payload: SimpleNamespace(entry_id="led", hash="h"),
        ontology=_ontology(),
        dialect=SERVING_DIALECT,
        embedder=embedder,
        demo_gate=False,
        demo_ontology_allowed=False,
        ask_context=context,
    )
    seen["gates"] = gates
    return env, seen, gates


def test_flag_off_prompt_envelope_and_route_stay_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _lake(tmp_path, "off-ask.duckdb")
    _on(monkeypatch)
    proposed = propose_note(
        space_id="space-k",
        body=_NOTE,
        sql_fragment="amount > 1000",
        actor="steward-a",
        path=path,
    )
    approve_note(note_id=proposed["note_id"], actor="steward-a", path=path)

    def _boom(_texts: list[str]) -> list[list[float]]:
        raise AssertionError("embedder called with the flag off")

    env, seen, _gates = _ask(
        monkeypatch, path, flag=None, context=_CONTEXT, embedder=_boom
    )
    env0, seen0, _gates0 = _ask(
        monkeypatch, path, flag="0", context=_CONTEXT, embedder=_boom
    )
    bare, seen_bare, _gates_bare = _ask(
        monkeypatch, path, flag=None, context=None, embedder=_boom
    )
    assert seen["ctx"] == seen0["ctx"] == seen_bare["ctx"]
    assert "space_knowledge" not in seen["raw"]
    assert _NOTE not in seen["ctx"]
    assert "CZK" not in seen["ctx"]
    assert _stable(env) == _stable(env0) == _stable(bare)
    assert (env or {}).get("route") == (bare or {}).get("route")
    blob = json.dumps(env, default=str)
    assert "named_filter=high" not in blob


def test_note_reaches_the_model_and_not_the_gates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _lake(tmp_path, "on-ask.duckdb")
    _on(monkeypatch)
    proposed = propose_note(
        space_id="space-k",
        body=_NOTE,
        sql_fragment="amount > 1000",
        actor="steward-a",
        path=path,
    )
    approve_note(note_id=proposed["note_id"], actor="steward-a", path=path)
    env, seen, gates = _ask(
        monkeypatch, path, flag="1", context=_CONTEXT, embedder=_embed
    )
    raw = seen["raw"]
    assert isinstance(raw, dict)
    block = raw["space_knowledge"]
    assert _NOTE in block
    assert "amount > 1000" in block
    assert _CONTEXT in block
    assert gates, "the qualifier gates did not run"
    for question in gates:
        assert _NOTE not in question
        assert "CZK" not in question
        assert "email" not in question
        assert "high" not in question
    leaked = f"{_QUESTION}\n{_NOTE}\n{_CONTEXT}"
    assert "named_filter=high" in str(
        dropped_stated_value_reason(_QUESTION + " " + _NOTE, _SQL, path, grantable={"orders"})
    )
    assert dropped_stated_value_reason(_QUESTION, _SQL, path, grantable={"orders"}) is None
    assert "CZK" in asked_currencies(leaked)
    assert asked_currencies(_QUESTION) == frozenset()
    assert question_requests_pii(leaked) is True
    assert question_requests_pii(_QUESTION) is False
    blob = json.dumps(env, default=str)
    assert "named_filter=high" not in blob
    assert env is not None
    assert env.get("abstained") is not True

    class _Client:
        def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
            seen["asked"] = question
            seen["posted"] = kwargs.get("ontology")
            return {"ok": True}

    _insights_compute_seam(
        _Client(),
        _QUESTION,
        session_id=None,
        space_id="space-k",
        ontology=raw,
    )
    asked = str(seen["asked"])
    assert _NOTE in asked
    assert _CONTEXT in asked
    posted = seen["posted"]
    assert isinstance(posted, dict)
    assert "space_knowledge" not in posted
    assert _NOTE not in json.dumps(posted)

    masked: dict[str, Any] = {}

    def _mask(**kwargs: Any) -> dict[str, str]:
        masked["text"] = kwargs.get("text")
        return {"text": str(kwargs.get("text") or "")}

    monkeypatch.setattr("cortex_client.compute.fail_closed_mask_payload", _mask)
    body = _insights_body(
        asked,
        session_id=None,
        space_id="space-k",
        ontology={NAME_MASK_KEY: {"name_values": ["Pat"]}},
    )
    assert _NOTE not in str(masked.get("text") or "")
    assert "CZK" not in str(masked.get("text") or "")
    assert _NOTE in str(body["question"])
    assert "space_knowledge" not in body.get("ontology", {})


def test_knowledge_routes_stay_unmounted_when_the_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _off(monkeypatch)
    from dms_api.app import create_app

    off_paths = create_app().openapi()["paths"]
    assert not any(path.startswith("/v1/studio/knowledge") for path in off_paths)
    monkeypatch.setenv("DMS_SPACE_KNOWLEDGE", "1")
    on_paths = create_app().openapi()["paths"]
    assert "/v1/studio/knowledge" in on_paths
    assert "/v1/studio/knowledge/{note_id}/approve" in on_paths


def test_listed_gold_sql_never_reaches_the_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A listed statement stored as a note, or sent as context, stays out of the prompt."""
    from dms_executor.skills_quarantine import WRITE_BLOCKED, item_content_hash

    for name in (
        "DMS_SCORED_PACK_HASHES",
        "DMS_SCORED_ITEM_HASHES",
        "DMS_SCORED_ITEM_HASHES_FILE",
        "DMS_SCORED_RESULT_HASHES",
    ):
        monkeypatch.delenv(name, raising=False)
    _on(monkeypatch)
    path = tmp_path / "gold.duckdb"
    duckdb.connect(str(path)).close()
    gold = "SELECT total FROM t_gold"
    kept = propose_note(
        space_id="space-k",
        body="qxv marker means zeta_mark is set",
        actor="steward-a",
        path=path,
    )
    approve_note(note_id=kept["note_id"], actor="steward-a", path=path)
    leaked = propose_note(
        space_id="space-k",
        body="stored fragment label",
        sql_fragment=gold,
        actor="steward-a",
        path=path,
    )
    approve_note(note_id=leaked["note_id"], actor="steward-a", path=path)
    monkeypatch.setenv("DMS_SCORED_ITEM_HASHES", item_content_hash(gold))
    with pytest.raises(ValueError, match=WRITE_BLOCKED):
        propose_note(
            space_id="space-k",
            body="another copy",
            sql_fragment="select   total  from   t_gold",
            actor="steward-a",
            path=path,
        )
    with pytest.raises(ValueError, match=WRITE_BLOCKED):
        revise_note(
            note_id=kept["note_id"],
            body="qxv marker means zeta_mark is set",
            sql_fragment=gold,
            actor="steward-a",
            path=path,
        )
    spelled = "select   total  from   t_gold"
    blocked = knowledge_prompt(
        "what is the total amount",
        space_id="space-k",
        warehouse=path,
        ask_context=spelled,
    )
    assert "t_gold" not in blocked
    assert "stored fragment label" not in blocked
    assert "zeta_mark" in blocked
    shown = knowledge_prompt(
        "what is the total amount",
        space_id="space-k",
        warehouse=path,
        ask_context="high value means amount > 1000",
    )
    assert "t_gold" not in shown
    assert "zeta_mark" in shown
    assert "high value means amount > 1000" in shown
