"""UX-GRANT-DATA-01. Flag default off. On, a scope miss is a grant card.

The card has no rows and no figures. A steward allow is the only grant.
That allow re-runs the original question. A filed measure is not an answer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from dms_core.control_plane.connect_grants import (
    audit_rows,
    granted_tables,
    register_source,
)
from dms_core.control_plane.connect_grants import (
    reset as reset_grants,
)
from dms_core.control_plane.onto_data import OntologyDataStore
from dms_executor.abstain import build_abstain
from dms_executor.gen_path_refuse import customer_abstain_text
from dms_executor.grant_askback import (
    apply_action,
    consider,
    enabled,
    reset_pending,
    steward_may_grant,
)
from dms_executor.schema_retry import _catalog_columns
from dms_executor.verified_queries import (
    PROVENANCE_GRANT_PROPOSAL,
    lookup_verified_query,
    retrieve_verified_examples,
)

QUESTION = "What were total sales by month last year?"
REASON = "unknown_measure:revenue_total"
SPACE = "11111111-1111-4111-8111-111111111111"
ACTOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_DIGIT = re.compile(r"\d")
_EMPTY = SimpleNamespace(measures={})


@pytest.fixture(autouse=True)
def _books() -> None:
    reset_grants()
    reset_pending()
    yield
    reset_grants()
    reset_pending()


def _lake(path: Path) -> None:
    import duckdb

    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE sales (amount DOUBLE, sold_on DATE)")
    con.execute("CREATE TABLE orders (id INTEGER)")
    con.close()


def _stack(path: Path) -> None:
    _lake(path)
    register_source(
        source_id="src_sales",
        space_id=SPACE,
        connector_id="erp",
        exposed=["sales"],
        actor=ACTOR,
        token_id=ACTOR,
    )


def _abstain(reason: str = REASON) -> dict[str, Any]:
    return build_abstain(
        reason=reason,
        question=QUESTION,
        stage="generative",
        answer_id="ans_grant_test",
        text=customer_abstain_text(reason),
        rows=[],
        values=[],
        sql_used=None,
        assumptions=[f"GEN-01: {reason}"],
        as_of="2026-11-28T00:00:00Z",
        space_id=SPACE,
        session_id="sess",
        ask_mode="live",
        route="generated",
        abstain_reason=reason.split(":", 1)[0],
    )


def _embed(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for index, text in enumerate(texts):
        if index == 0 or "sales.amount" in text:
            out.append([1.0, 0.0])
        else:
            out.append([0.0, 1.0])
    return out


def _propose(spec: dict[str, Any]) -> dict[str, str]:
    table = spec["table"]
    column = spec["column"]
    return {
        "need": "Sales by month",
        "formula": f"SUM({table}.{column})",
        "sql_preview": f"SELECT SUM({table}.{column}) AS value FROM {table}",
    }


def _no_figures(env: dict[str, Any]) -> None:
    assert env["rows"] == []
    assert env["values"] in ([], None)
    assert not env.get("sql_used")
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert not _DIGIT.search(str(env.get("text") or ""))
    card = env.get("grant_askback")
    if isinstance(card, dict) and isinstance(card.get("text"), str):
        assert not _DIGIT.search(card["text"])


def test_flag_defaults_off_and_only_the_digit_turns_it_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DMS_GRANT_ASKBACK", raising=False)
    assert enabled() is False
    monkeypatch.setenv("DMS_GRANT_ASKBACK", "true")
    assert enabled() is False
    monkeypatch.setenv("DMS_GRANT_ASKBACK", "1")
    assert enabled() is True


def test_flag_off_envelope_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_GRANT_ASKBACK", raising=False)
    _stack(tmp_path / "lake.duckdb")
    env = _abstain()
    snap = json.dumps(env, sort_keys=True)
    out = consider(
        env,
        question=QUESTION,
        space_id=SPACE,
        session_id="sess",
        ontology=_EMPTY,
        warehouse=tmp_path / "lake.duckdb",
        grantable={"orders"},
        embedder=_embed,
        propose=_propose,
    )
    assert out is env
    assert json.dumps(env, sort_keys=True) == snap
    assert "grant_askback" not in env
    assert "grant" not in {row["action"] for row in audit_rows()}


def test_flag_on_fake_stack_shows_grant_card_without_figures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_GRANT_ASKBACK", "1")
    lake = tmp_path / "lake.duckdb"
    _stack(lake)
    columns = _catalog_columns(lake)
    assert ("sales", "amount") == (columns[0][0], columns[0][1]) or any(
        row[0] == "sales" and row[1] == "amount" for row in columns
    )
    env = consider(
        _abstain(),
        question=QUESTION,
        space_id=SPACE,
        session_id="sess",
        ontology=_EMPTY,
        warehouse=lake,
        grantable={"orders"},
        embedder=_embed,
        propose=_propose,
    )
    _no_figures(env)
    card = env["grant_askback"]
    assert card["kind"] == "grant"
    assert card["can_grant"] is True
    assert card["served"] is False
    assert card["text"] == (
        "Sales by month needs sales.amount from erp, which this Space can't see yet. "
        "Ask steward to grant / Define the measure / Pick a different measure"
    )
    assert env["text"] == card["text"]
    assert "amount" in card["formula"]
    assert "sales" in card["sql_preview"].lower()
    assert not _DIGIT.search(card["formula"])
    assert not _DIGIT.search(card["sql_preview"])
    assert granted_tables(SPACE) == ()


def test_bm25_ranks_the_connected_column_without_an_embedder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_GRANT_ASKBACK", "1")
    lake = tmp_path / "lake.duckdb"
    _stack(lake)
    env = consider(
        _abstain(),
        question=QUESTION,
        space_id=SPACE,
        session_id="sess",
        ontology=_EMPTY,
        warehouse=lake,
        grantable={"orders"},
        embedder=None,
        propose=None,
    )
    _no_figures(env)
    card = env["grant_askback"]
    assert card["table"] == "sales"
    assert card["column"] == "amount"
    assert "revenue total" in card["text"]
    assert granted_tables(SPACE) == ()


def test_ask_does_not_grant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_GRANT_ASKBACK", "1")
    lake = tmp_path / "lake.duckdb"
    _stack(lake)
    consider(
        _abstain(),
        question=QUESTION,
        space_id=SPACE,
        session_id="sess",
        ontology=_EMPTY,
        warehouse=lake,
        grantable={"orders"},
        embedder=_embed,
        propose=_propose,
    )
    assert "grant" not in {row["action"] for row in audit_rows()}
    assert granted_tables(SPACE) == ()


def test_approval_re_runs_the_question_and_does_not_auto_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_GRANT_ASKBACK", "1")
    lake = tmp_path / "lake.duckdb"
    _stack(lake)
    env = consider(
        _abstain(),
        question=QUESTION,
        space_id=SPACE,
        session_id="sess",
        ontology=_EMPTY,
        warehouse=lake,
        grantable={"orders"},
        embedder=_embed,
        propose=_propose,
    )
    token = env["grant_askback"]["token"]
    store = OntologyDataStore()
    with pytest.raises(PermissionError):
        apply_action(
            token=token,
            action="grant",
            allow=False,
            role="steward",
            actor=ACTOR,
            token_id=ACTOR,
            store=store,
            rerun=lambda *_a, **_k: _abstain(),
        )
    assert granted_tables(SPACE) == ()
    seen: list[str] = []

    def rerun(question: str, *, space_id: str | None, session_id: str | None) -> dict[str, Any]:
        assert "grant" in {row["action"] for row in audit_rows()}
        assert question == QUESTION
        assert space_id == SPACE
        assert session_id == "sess"
        seen.append(question)
        out = _abstain()
        out["answer_id"] = "ans_regate"
        return out

    result = apply_action(
        token=token,
        action="grant",
        allow=True,
        role="steward",
        actor=ACTOR,
        token_id=ACTOR,
        store=store,
        rerun=rerun,
    )
    assert seen == [QUESTION]
    assert result["answer_id"] == "ans_regate"
    _no_figures(result)
    assert result.get("sql_used") in (None, "")
    assert granted_tables(SPACE) == ("sales",)
    with pytest.raises(KeyError):
        apply_action(
            token=token,
            action="grant",
            allow=True,
            role="steward",
            actor=ACTOR,
            token_id=ACTOR,
            store=store,
            rerun=rerun,
        )
    assert [row["action"] for row in audit_rows()].count("grant") == 1


def test_denied_steward_does_not_grant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_GRANT_ASKBACK", "1")
    lake = tmp_path / "lake.duckdb"
    _stack(lake)
    env = consider(
        _abstain(),
        question=QUESTION,
        space_id=SPACE,
        session_id="sess",
        ontology=_EMPTY,
        warehouse=lake,
        grantable={"orders"},
        embedder=_embed,
        propose=_propose,
    )
    store = OntologyDataStore()
    store.deny_steward(ACTOR)
    assert steward_may_grant(role="steward", actor=ACTOR, allow=True, store=store) is False
    with pytest.raises(PermissionError):
        apply_action(
            token=env["grant_askback"]["token"],
            action="grant",
            allow=True,
            role="steward",
            actor=ACTOR,
            token_id=ACTOR,
            store=store,
            rerun=lambda *_a, **_k: _abstain(),
        )
    assert granted_tables(SPACE) == ()


def test_define_files_a_proposal_and_lookup_does_not_serve_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_GRANT_ASKBACK", "1")
    monkeypatch.delenv("DMS_VQ_RAG", raising=False)
    lake = tmp_path / "lake.duckdb"
    _stack(lake)
    env = consider(
        _abstain(),
        question=QUESTION,
        space_id=SPACE,
        session_id="sess",
        ontology=_EMPTY,
        warehouse=lake,
        grantable={"orders"},
        embedder=_embed,
        propose=_propose,
    )
    store = OntologyDataStore()
    filed = apply_action(
        token=env["grant_askback"]["token"],
        action="define_measure",
        allow=False,
        role="viewer",
        actor=ACTOR,
        token_id=ACTOR,
        store=store,
        warehouse=lake,
        dialect="duckdb",
    )
    _no_figures(filed)
    assert filed["grant_askback"]["served"] is False
    assert filed["text"] == "The measure was filed as a proposal. It is not an answer."
    version_id = filed["grant_askback"]["version_id"]
    stored = store._versions[version_id]
    assert stored.source == "grant-askback"
    assert store._items
    assert all(item.status == "proposed" for item in store._items.values())
    assert lookup_verified_query(QUESTION, space_id=SPACE, warehouse=lake, dialect="duckdb") is None
    examples = retrieve_verified_examples(
        QUESTION, space_id=SPACE, warehouse=lake, dialect="duckdb", force=True
    )
    assert all(row.get("provenance") != PROVENANCE_GRANT_PROPOSAL for row in examples)
    assert granted_tables(SPACE) == ()


def test_nothing_accessible_names_the_gap_and_offers_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_GRANT_ASKBACK", "1")
    env = consider(
        _abstain(),
        question=QUESTION,
        space_id=SPACE,
        session_id="sess",
        ontology=_EMPTY,
        warehouse=None,
        grantable=set(),
        embedder=None,
        propose=None,
        catalog=[],
    )
    _no_figures(env)
    card = env["grant_askback"]
    assert card["kind"] == "missing"
    assert "revenue_total" in card["text"]
    assert card["actions"] == [{"id": "connect_source", "label": "Connect a source"}]
    assert "grant" not in {row["action"] for row in audit_rows()}


def test_other_abstains_stay_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_GRANT_ASKBACK", "1")
    env = _abstain("insights_timeout")
    snap = json.dumps(env, sort_keys=True)
    out = consider(
        env,
        question=QUESTION,
        space_id=SPACE,
        session_id="sess",
        ontology=_EMPTY,
        warehouse=None,
        grantable=set(),
        catalog=[],
    )
    assert out is env
    assert json.dumps(env, sort_keys=True) == snap
