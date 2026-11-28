"""BIRD-EVIDENCE-01. Flag default off. No word list. No skip, no xfail."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.insights import SPACE_KNOWLEDGE_MARK
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
