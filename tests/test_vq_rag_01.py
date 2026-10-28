"""VQ-RAG-01: verified queries are few-shot examples, not a replayed answer.

``DMS_VQ_RAG`` off keeps the exact-match replay. On, lookup does not serve,
save-on-Yes writes one Space row, a revoked row is not retrieved, and a
scored-pack SQL is not stored.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.skills_quarantine import item_content_hash
from dms_executor.sql_loop import AskCallBudget
from dms_executor.verified_queries import (
    list_verified_queries,
    lookup_verified_query,
    maybe_save_checked_sql,
    maybe_verified_ask,
    retrieve_verified_examples,
    revoke_verified_example,
)

SPACE = "space-vq-rag-01"
SQL = "SELECT 1 AS n FROM inventory LIMIT 1"
QUESTION = "how many units are in inventory"


def _grant(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_executor.verified_queries as vq

    real = vq._grantable

    def _wrapped(space_id: str | None, warehouse: Path | None) -> set[str]:
        return set(real(space_id, warehouse)) | {"inventory"}

    monkeypatch.setattr(vq, "_grantable", _wrapped)


def _clear_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DMS_SCORED_PACK_HASHES",
        "DMS_SCORED_ITEM_HASHES",
        "DMS_SCORED_ITEM_HASHES_FILE",
        "DMS_SCORED_RESULT_HASHES",
        "DMS_VQ_RAG",
    ):
        monkeypatch.delenv(name, raising=False)


def _warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "vq.duckdb"
    ensure_demo_warehouse(path)
    return path


class _Submit:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, sql: str) -> Any:
        self.calls.append(sql)

        class _Result:
            ok = True
            output = {"rows": [{"n": 1}]}
            run_id = "run-vq-rag"

        return _Result()


class _Ledger:
    entry_id = "led-vq-rag"
    hash = "hash-vq-rag-not-the-entry"

    def __call__(self, payload: dict[str, Any]) -> _Ledger:
        return self


def _save(
    path: Path,
    *,
    question: str = QUESTION,
    sql: str = SQL,
    provenance: str = "yes:vote",
) -> dict[str, Any] | None:
    return maybe_save_checked_sql(
        question=question,
        sql=sql,
        space_id=SPACE,
        warehouse=path,
        grantable={"inventory"},
        dialect="duckdb",
        provenance=provenance,
        served_route="model",
        abstained=False,
    )


def test_replay_does_not_serve_when_flag_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_hashes(monkeypatch)
    _grant(monkeypatch)
    path = _warehouse(tmp_path)
    from dms_executor.verified_queries import register_verified_query

    register_verified_query(
        space_id=SPACE,
        question=QUESTION,
        sql=SQL,
        synonyms=["stock on hand"],
        path=path,
        dialect="duckdb",
    )
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    submit = _Submit()
    assert lookup_verified_query(QUESTION, space_id=SPACE, warehouse=path, dialect="duckdb") is None
    assert (
        lookup_verified_query("stock on hand", space_id=SPACE, warehouse=path, dialect="duckdb")
        is None
    )
    missed = maybe_verified_ask(
        QUESTION,
        space_id=SPACE,
        warehouse=path,
        submit=submit,
        ledger_append=_Ledger(),
        dialect="duckdb",
    )
    assert missed is None
    assert submit.calls == []
    found = retrieve_verified_examples(
        QUESTION,
        space_id=SPACE,
        warehouse=path,
        grantable={"inventory"},
        dialect="duckdb",
    )
    assert [row["sql"] for row in found] == [SQL]
    monkeypatch.delenv("DMS_VQ_RAG")
    served = maybe_verified_ask(
        QUESTION,
        space_id=SPACE,
        warehouse=path,
        submit=submit,
        ledger_append=_Ledger(),
        dialect="duckdb",
    )
    assert served is not None
    assert served["badge"] == "L0_CERTIFIED"
    assert served["sql_used"] == SQL
    assert submit.calls == [SQL]


def test_save_on_yes_writes_one_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_hashes(monkeypatch)
    _grant(monkeypatch)
    path = _warehouse(tmp_path)
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    saved = _save(path, provenance="yes:reconfirm")
    assert saved is not None
    listed = list_verified_queries(space_id=SPACE, path=path)
    assert [row["question"] for row in listed] == [QUESTION]
    found = retrieve_verified_examples(
        QUESTION,
        space_id=SPACE,
        warehouse=path,
        grantable={"inventory"},
        dialect="duckdb",
    )
    assert len(found) == 1
    assert found[0]["sql"] == SQL
    assert found[0]["provenance"] == "yes:reconfirm"
    assert found[0]["schema_hash"]
    monkeypatch.delenv("DMS_VQ_RAG")
    assert _save(path, provenance="yes:vote") is None
    assert len(list_verified_queries(space_id=SPACE, path=path)) == 1


def test_vote_agreement_writes_one_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_hashes(monkeypatch)
    _grant(monkeypatch)
    path = _warehouse(tmp_path)
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    from dms_executor.generative_ask import _second_reading

    rows = [{"n": 1}]

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        assert ctx.get("sql_vote_kind") == "reading"
        return {"sql": SQL}

    def model_sql(body: dict[str, Any] | None) -> str | None:
        if isinstance(body, dict):
            text = body.get("sql")
            return text if isinstance(text, str) else None
        return None

    published = _second_reading(
        {"abstained": False, "rows": rows, "sql_used": SQL},
        question=QUESTION,
        ctx={},
        compute=compute,
        warehouse=path,
        dialect="duckdb",
        model_sql=model_sql,
        check=lambda _sql: None,
        abstain=lambda *a, **k: {"abstained": True, "rows": []},
        empty_answer=lambda sql, _attempts: {"abstained": True, "rows": [], "sql_used": sql},
        publish=lambda sql, _attempts: {
            "abstained": False,
            "sql_used": sql,
            "rows": rows,
            "served_route": "model",
        },
        attempts=[],
        space_id=SPACE,
        session_id="sess-vq-rag",
        grantable={"inventory"},
        budget=AskCallBudget(),
    )
    assert published.get("abstained") is False
    listed = list_verified_queries(space_id=SPACE, path=path)
    assert len(listed) == 1
    assert listed[0]["question"] == QUESTION
    assert listed[0]["sql"] == SQL


def test_reconfirm_yes_writes_one_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_hashes(monkeypatch)
    _grant(monkeypatch)
    path = _warehouse(tmp_path)
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    from dms_executor import Executor
    from dms_executor.reconfirm import _save_pending

    executor = Executor(warehouse_path=path)

    def _live(question: str, **_kwargs: Any) -> dict[str, Any]:
        return {
            "abstained": False,
            "badge": "L2_VALIDATED",
            "sql_used": SQL,
            "question": question,
            "served_route": "model",
            "rows": [{"n": 1}],
        }

    executor.live_ask = _live  # type: ignore[method-assign]
    executor.offer_reconfirm = lambda env, **_kwargs: env  # type: ignore[method-assign]
    token = _save_pending(
        {
            "suggested_question": QUESTION,
            "space_id": SPACE,
            "session_id": "sess-vq-rag",
            "tables": None,
            "ask_path": None,
        }
    )
    env = executor.confirm_reconfirm(token, True)
    assert env.get("reconfirm_accepted") is True
    listed = list_verified_queries(space_id=SPACE, path=path)
    assert len(listed) == 1
    found = retrieve_verified_examples(
        QUESTION,
        space_id=SPACE,
        warehouse=path,
        grantable={"inventory"},
        dialect="duckdb",
    )
    assert found[0]["provenance"] == "yes:reconfirm"
    assert found[0]["sql"] == SQL


def test_revoked_examples_are_not_retrieved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_hashes(monkeypatch)
    _grant(monkeypatch)
    path = _warehouse(tmp_path)
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    saved = _save(path)
    assert saved is not None
    assert retrieve_verified_examples(
        QUESTION,
        space_id=SPACE,
        warehouse=path,
        grantable={"inventory"},
        dialect="duckdb",
    )
    assert revoke_verified_example(asset_id=saved["asset_id"], space_id=SPACE, path=path)
    assert (
        retrieve_verified_examples(
            QUESTION,
            space_id=SPACE,
            warehouse=path,
            grantable={"inventory"},
            dialect="duckdb",
        )
        == []
    )
    monkeypatch.delenv("DMS_VQ_RAG")
    assert lookup_verified_query(QUESTION, space_id=SPACE, warehouse=path, dialect="duckdb") is None


def test_pack_question_is_not_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_hashes(monkeypatch)
    _grant(monkeypatch)
    path = _warehouse(tmp_path)
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    monkeypatch.setenv("DMS_SCORED_ITEM_HASHES", item_content_hash(SQL))
    assert _save(path, question="pack question that must not be stored") is None
    assert list_verified_queries(space_id=SPACE, path=path) == []
    assert (
        retrieve_verified_examples(
            "pack question",
            space_id=SPACE,
            warehouse=path,
            grantable={"inventory"},
            dialect="duckdb",
        )
        == []
    )


def test_embedding_overrides_lexical_similarity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_hashes(monkeypatch)
    _grant(monkeypatch)
    path = _warehouse(tmp_path)
    monkeypatch.setenv("DMS_VQ_RAG", "1")
    lexical = "total units in inventory"
    distant = "cold room capacity"
    assert _save(path, question=lexical) is not None
    assert _save(path, question=distant) is not None
    asked = "total units in the inventory"
    lexical_hit = retrieve_verified_examples(
        asked,
        space_id=SPACE,
        warehouse=path,
        grantable={"inventory"},
        dialect="duckdb",
    )
    assert lexical_hit[0]["question"] == lexical
    assert retrieve_verified_examples(
        "zzzz qq",
        space_id=SPACE,
        warehouse=path,
        grantable={"inventory"},
        dialect="duckdb",
    ) == []

    def embed(texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for index, text in enumerate(texts):
            if index == 0 or "cold" in text.casefold():
                out.append([1.0, 0.0])
            else:
                out.append([0.0, 1.0])
        return out

    embedded = retrieve_verified_examples(
        asked,
        space_id=SPACE,
        warehouse=path,
        grantable={"inventory"},
        dialect="duckdb",
        embedder=embed,
    )
    assert [row["question"] for row in embedded] == [distant]


def test_examples_stay_off_the_insights_body() -> None:
    from dms_executor import _insights_compute_seam

    seen: dict[str, Any] = {}

    class _Client:
        def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
            seen["question"] = question
            seen["ontology"] = kwargs.get("ontology")
            return {"ok": True}

    _insights_compute_seam(
        _Client(),
        "how many units are in inventory",
        session_id="sess-vq-rag",
        space_id=SPACE,
        ontology={
            "tables": ["inventory"],
            "vq_examples": "Question: cold room capacity\nSQL: SELECT 1 AS n FROM inventory",
        },
    )
    onto = seen["ontology"]
    assert isinstance(onto, dict)
    assert "vq_examples" not in onto
    assert "model" not in onto
    assert "strict" not in onto
    assert seen["question"].endswith(
        "Examples:\nQuestion: cold room capacity\nSQL: SELECT 1 AS n FROM inventory"
    )
