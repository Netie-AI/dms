"""GROUND-DEFS-01. Flag default off. No word list. No skip, no xfail."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from dms_executor import _insights_compute_seam
from dms_executor.demo_warehouse import SERVING_DIALECT
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ground_defs import HEADER, build_grounding, mismatch_envelope
from dms_executor.ontology import Ontology, demo_ontology
from dms_executor.pipeline_failure import _reason_code, reconfirm_disposition

_QUESTION = "Which vendors are past the inspection window?"
_EXPR = (
    "COUNT(*) FILTER (WHERE CAST(f.zeta_mark AS DATE) "
    "< CURRENT_DATE - INTERVAL 90 DAY)"
)
_DECOY = "SUM(f.qrv_amt)"
_WRONG = (
    "SELECT vendor_id FROM vendor_file "
    "WHERE CAST(zeta_mark AS DATE) < CURRENT_DATE - INTERVAL 1 YEAR"
)
_RIGHT = (
    "SELECT vendor_id FROM vendor_file "
    "WHERE CAST(zeta_mark AS DATE) < CURRENT_DATE - INTERVAL 90 DAY"
)
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.casefold()))


def _ontology() -> Ontology:
    onto = Ontology()
    onto.add_object("vendor_file", "vendor_file", ["vendor_id"])
    onto.add_measure(
        "zeta_rule",
        "vendor_file",
        _EXPR,
        description="qxv filed marker",
    )
    onto.add_measure(
        "qrv_bucket",
        "vendor_file",
        _DECOY,
        description="qxv pile",
    )
    onto.verified = True
    return onto


def _embed(texts: list[str]) -> list[list[float]]:
    """High score only for the governed doc. Ignores shared words."""
    out: list[list[float]] = []
    for index, text in enumerate(texts):
        if index == 0 or "zeta_rule" in text:
            out.append([1.0, 0.0])
        else:
            out.append([0.0, 1.0])
    return out


def _no_shared_tokens() -> None:
    asked = _tokens(_QUESTION)
    for text in ("zeta_rule", "qrv_bucket", "qxv filed marker", "qxv pile", _EXPR, _DECOY):
        assert asked.isdisjoint(_tokens(text)), text


def test_names_share_no_tokens_with_the_question() -> None:
    _no_shared_tokens()


def test_flag_off_returns_nothing_and_does_not_embed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DMS_GROUND_DEFS", raising=False)

    def _boom(_texts: list[str]) -> list[list[float]]:
        raise AssertionError("embedder called with the flag off")

    block, defs = build_grounding(_QUESTION, _ontology(), embedder=_boom)
    assert block == ""
    assert defs == []
    monkeypatch.setenv("DMS_GROUND_DEFS", "0")
    again, again_defs = build_grounding(_QUESTION, _ontology(), embedder=_boom)
    assert again == block
    assert again_defs == defs


def test_embedder_puts_the_definition_in_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_shared_tokens()
    monkeypatch.setenv("DMS_GROUND_DEFS", "1")
    block, defs = build_grounding(
        _QUESTION,
        _ontology(),
        embedder=_embed,
        grantable={"vendor_file"},
        examples=[
            {
                "question": "qxv marker count",
                "sql": "SELECT vendor_id FROM vendor_file",
            }
        ],
    )
    assert [item.name for item in defs] == ["zeta_rule"]
    assert "qrv_bucket" not in block
    assert block.startswith(HEADER + "\n")
    assert "name: zeta_rule" in block
    assert "grain: vendor_file" in block
    assert "expression: " + " ".join(_EXPR.split()) in block
    assert "as_of: CURRENT_DATE" in block
    assert "example_question: qxv marker count" in block
    assert "example_sql: SELECT vendor_id FROM vendor_file" in block


def test_wrong_threshold_reconfirms_and_the_governed_one_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DMS_GROUND_DEFS", "1")
    _block, defs = build_grounding(_QUESTION, _ontology(), embedder=_embed)
    wrong = mismatch_envelope(
        _WRONG, defs, question=_QUESTION, space_id="space-z", session_id=None
    )
    assert wrong is not None
    assert wrong["abstained"] is True
    assert wrong["reconfirm"]["reason_code"] == "definition_mismatch"
    assert wrong["reconfirm"]["code"] == "definition_mismatch"
    assert "zeta_rule" in wrong["reconfirm"]["definition"]
    assert "90" in wrong["reconfirm"]["definition"]
    assert wrong.get("rows") == []
    assert mismatch_envelope(
        _RIGHT, defs, question=_QUESTION, space_id="space-z", session_id=None
    ) is None
    # An extra conjunct on another column does not hide the governed predicate.
    with_status = _RIGHT + " AND status = 'open'"
    assert (
        mismatch_envelope(
            with_status, defs, question=_QUESTION, space_id=None, session_id=None
        )
        is None
    )


def test_audit_overdue_year_threshold_is_a_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The measured miss: 1 YEAR on last_audit_date is not the 90-day rule."""
    monkeypatch.setenv("DMS_GROUND_DEFS", "1")
    onto = demo_ontology(Path("/no/such/dms-lake"))
    governed = onto.measures["audit_overdue"].expression

    def embed(texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0] if index == 0 or "audit_overdue" in text else [0.0, 1.0]
            for index, text in enumerate(texts)
        ]

    _block, defs = build_grounding(
        "Which suppliers have an audit overdue?",
        onto,
        embedder=embed,
        grantable={"suppliers"},
    )
    assert defs[0].name == "audit_overdue"
    assert "90" in defs[0].predicate_sql
    assert defs[0].as_of == "CURRENT_DATE"
    invented = (
        "SELECT supplier_id FROM suppliers WHERE last_audit_date < "
        "(SELECT MAX(last_audit_date) FROM suppliers) - INTERVAL 1 YEAR"
    )
    missed = mismatch_envelope(
        invented, defs, question="Which suppliers have an audit overdue?",
        space_id=None, session_id=None,
    )
    assert missed is not None
    assert missed["reconfirm"]["reason_code"] == "definition_mismatch"
    matched = (
        "SELECT supplier_id FROM suppliers WHERE "
        + governed.split("WHERE", 1)[1].rstrip(")")
    )
    assert mismatch_envelope(
        matched, defs, question="Which suppliers have an audit overdue?",
        space_id=None, session_id=None,
    ) is None


def test_bm25_grounds_the_audit_rule_without_an_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No embedder: the existing BM25 path still ranks the governed rule.

    This is not a phrase table. A question that shares no tokens is covered
    by the embedder test above and does not rank here.
    """
    monkeypatch.setenv("DMS_GROUND_DEFS", "1")
    onto = demo_ontology(Path("/no/such/dms-lake-bm25"))
    block, defs = build_grounding(
        "Which suppliers have an audit overdue?",
        onto,
        embedder=None,
        grantable={"suppliers"},
    )
    assert [item.name for item in defs] == ["audit_overdue"]
    assert "INTERVAL 90 DAY" in block
    assert block.startswith(HEADER + "\n")


def test_definition_mismatch_is_an_offer() -> None:
    assert _reason_code("definition_mismatch") == "definition_mismatch"
    assert reconfirm_disposition("definition_mismatch") == "offer"


def test_ungranted_grain_is_not_grounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_GROUND_DEFS", "1")
    block, defs = build_grounding(
        _QUESTION, _ontology(), embedder=_embed, grantable={"other_table"}
    )
    assert block == ""
    assert defs == []


_ASK_N = 0


def _ask(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sql: str,
    *,
    flag: str | None,
    embedder: Any,
    space_id: str = "space-z",
    vq: str | None = None,
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    global _ASK_N
    _ASK_N += 1
    if flag is None:
        monkeypatch.delenv("DMS_GROUND_DEFS", raising=False)
    else:
        monkeypatch.setenv("DMS_GROUND_DEFS", flag)
    if vq is None:
        monkeypatch.delenv("DMS_VQ_RAG", raising=False)
    else:
        monkeypatch.setenv("DMS_VQ_RAG", vq)
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    monkeypatch.delenv("DMS_DEF_VALIDATE", raising=False)
    path = tmp_path / f"ground_defs_{_ASK_N}.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE vendor_file (vendor_id INTEGER, zeta_mark DATE, status VARCHAR)"
    )
    con.execute("INSERT INTO vendor_file VALUES (1, DATE '2020-01-01', 'open')")
    con.close()
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
        return SimpleNamespace(ok=True, status="ok", run_id="run_g", output={"rows": rows})

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        seen["ctx"] = json.dumps(ctx, sort_keys=True, default=str)
        seen["raw"] = ctx
        return {"query_sql": sql}

    env = maybe_generative_ask(
        _QUESTION,
        space_id=space_id,
        warehouse=path,
        grantable={"vendor_file"},
        compute=compute,
        submit=submit,
        ledger_append=lambda _payload: SimpleNamespace(entry_id="led", hash="h"),
        ontology=_ontology(),
        dialect=SERVING_DIALECT,
        embedder=embedder,
        demo_gate=False,
        demo_ontology_allowed=False,
    )
    return env, submitted, seen


def test_flag_off_prompt_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _boom(_texts: list[str]) -> list[list[float]]:
        raise AssertionError("embedder called with the flag off")

    _env, submitted, seen = _ask(
        monkeypatch, tmp_path, _WRONG, flag=None, embedder=_boom
    )
    off = seen["ctx"]
    assert "governed_definitions" not in seen["raw"]
    assert HEADER not in off
    _env0, submitted0, seen0 = _ask(
        monkeypatch, tmp_path, _WRONG, flag="0", embedder=_boom
    )
    assert seen0["ctx"] == off
    assert submitted == submitted0
    assert submitted == [_WRONG]


def test_ask_reconfirms_a_wrong_threshold_and_serves_the_governed_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _no_shared_tokens()
    env, submitted, seen = _ask(
        monkeypatch, tmp_path, _WRONG, flag="1", embedder=_embed
    )
    assert env is not None
    assert submitted == []
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert env["reconfirm"]["reason_code"] == "definition_mismatch"
    assert env.get("rows") in ([], None) or env.get("rows") == []
    assert not env.get("values")
    raw = seen["raw"]
    assert isinstance(raw, dict)
    assert HEADER in raw["governed_definitions"]
    assert "zeta_rule" in raw["governed_definitions"]
    assert "INTERVAL 90 DAY" in raw["governed_definitions"]

    class _Client:
        def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
            seen["asked"] = question
            seen["posted"] = kwargs.get("ontology")
            return {"ok": True}

    _insights_compute_seam(
        _Client(),
        _QUESTION,
        session_id=None,
        space_id="space-z",
        ontology=raw,
    )
    assert seen["asked"].rstrip().endswith(raw["governed_definitions"].rstrip())
    posted = seen["posted"]
    assert isinstance(posted, dict)
    assert "governed_definitions" not in posted
    assert "zeta_mark" not in json.dumps(posted)

    served, ran, _seen = _ask(
        monkeypatch, tmp_path, _RIGHT, flag="1", embedder=_embed
    )
    assert served is not None
    assert served["abstained"] is False
    assert ran == [_RIGHT]
    assert served["badge"] == "L2_VALIDATED"


def test_verified_examples_share_the_block_and_not_a_second_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen_space: dict[str, Any] = {}

    def fake_retrieve(*_args: Any, **kwargs: Any) -> list[dict[str, str]]:
        seen_space["space_id"] = kwargs.get("space_id")
        return [
            {
                "question": "qxv marker count",
                "sql": "SELECT vendor_id FROM vendor_file",
            }
        ]

    monkeypatch.setattr(
        "dms_executor.generative_ask.retrieve_verified_examples", fake_retrieve
    )
    _env, _submitted, seen = _ask(
        monkeypatch,
        tmp_path,
        _RIGHT,
        flag="1",
        embedder=_embed,
        space_id="space-z",
        vq="1",
    )
    raw = seen["raw"]
    assert seen_space["space_id"] == "space-z"
    assert "vq_examples" not in raw
    assert "example_question: qxv marker count" in raw["governed_definitions"]
    assert "example_sql: SELECT vendor_id FROM vendor_file" in raw["governed_definitions"]
