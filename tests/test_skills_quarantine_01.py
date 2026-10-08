"""SKILLS-QUARANTINE-01: scored-pack rows never leave the verified-question store.

Readers (each has its own test):
- list_verified_queries
- lookup_verified_query
- maybe_verified_ask
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.skills_quarantine import (
    WRITE_BLOCKED,
    curated_questions_path,
    filter_retrieved_rows,
    questions_file_hash,
)
from dms_executor.verified_queries import (
    list_verified_queries,
    lookup_verified_query,
    maybe_verified_ask,
    normalize_verified_question,
    register_verified_query,
)

ROOT = Path(__file__).resolve().parents[1]
SPACE = "space-quarantine-01"
KEEPER = "ordinary steward item"
SCORED_Q = "synthetic scored item"
SQL = "SELECT 1"
LISTED = "ab" * 32
OTHER = "cd" * 32


def _insert(
    path: Path,
    *,
    asset_id: str,
    question: str,
    pack_hash: str | None,
    synonyms: list[str] | None = None,
) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            INSERT INTO main._verified_queries
              (asset_id, space_id, question, question_norm, sql_text,
               synonyms_json, created_at, pack_hash)
            VALUES (?, ?, ?, ?, ?, ?, now(), ?)
            """,
            [
                asset_id,
                SPACE,
                question,
                normalize_verified_question(question),
                SQL,
                json.dumps(synonyms or []),
                pack_hash,
            ],
        )
    finally:
        con.close()


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keeper row plus a row whose pack hash is listed in the env."""
    monkeypatch.setenv("DMS_SCORED_PACK_HASHES", LISTED)
    path = tmp_path / "store.duckdb"
    register_verified_query(
        space_id=SPACE, question=KEEPER, sql=SQL, path=path
    )
    _insert(path, asset_id="vq_scored", question=SCORED_Q, pack_hash=LISTED)
    return path


class _Submit:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, sql: str) -> Any:
        self.calls.append(sql)

        class _Result:
            ok = True
            output = {"rows": [{"n": 1}]}
            run_id = "run-quarantine"

        return _Result()


class _Ledger:
    entry_id = "led-quarantine"
    hash = "hash-quarantine-not-the-entry"

    def __call__(self, payload: dict[str, Any]) -> _Ledger:
        return self


def test_list_verified_queries_excludes_listed_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _store(tmp_path, monkeypatch)
    questions = {row["question"] for row in list_verified_queries(space_id=SPACE, path=path)}
    assert questions == {KEEPER}
    assert SCORED_Q not in questions


def test_lookup_verified_query_excludes_listed_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _store(tmp_path, monkeypatch)
    assert lookup_verified_query(SCORED_Q, space_id=SPACE, warehouse=path) is None
    hit = lookup_verified_query(KEEPER, space_id=SPACE, warehouse=path)
    assert hit is not None
    assert hit["sql"] == SQL


def test_maybe_verified_ask_excludes_listed_pack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _store(tmp_path, monkeypatch)
    submit = _Submit()
    missed = maybe_verified_ask(
        SCORED_Q,
        space_id=SPACE,
        warehouse=path,
        submit=submit,
        ledger_append=_Ledger(),
    )
    assert missed is None
    assert submit.calls == []
    env = maybe_verified_ask(
        KEEPER,
        space_id=SPACE,
        warehouse=path,
        submit=submit,
        ledger_append=_Ledger(),
    )
    assert env is not None
    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert "Found 1 row(s)." in env["text"]
    assert env["rows"] == [{"n": 1}]
    assert submit.calls == [SQL]


def test_unprovenanced_fingerprint_matches_listed_pack_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = tmp_path / "questions.yaml"
    pack.write_text(
        yaml.safe_dump({"questions": [{"id": "fake", "question": SCORED_Q}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "dms_executor.skills_quarantine.curated_questions_path", lambda: pack
    )
    monkeypatch.delenv("DMS_SCORED_PACK_HASHES", raising=False)
    path = tmp_path / "fp.duckdb"
    register_verified_query(space_id=SPACE, question=KEEPER, sql=SQL, path=path)
    _insert(path, asset_id="vq_fp", question=SCORED_Q, pack_hash=None)
    _insert(
        path,
        asset_id="vq_syn",
        question="synonym carrier",
        pack_hash=None,
        synonyms=[SCORED_Q],
    )
    # Provenance that is not the listed file hash wins over the fingerprint.
    _insert(path, asset_id="vq_other", question=SCORED_Q, pack_hash=OTHER)

    rows = list_verified_queries(space_id=SPACE, path=path)
    ids = {row["asset_id"] for row in rows}
    assert "vq_fp" not in ids
    assert "vq_syn" not in ids
    assert "vq_other" in ids
    assert any(row["question"] == KEEPER for row in rows)
    hit = lookup_verified_query(SCORED_Q, space_id=SPACE, warehouse=path)
    assert hit is not None
    assert hit["asset_id"] == "vq_other"
    assert lookup_verified_query("synonym carrier", space_id=SPACE, warehouse=path) is None


def test_scored_pack_write_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "write.duckdb"
    monkeypatch.setenv("DMS_SCORED_PACK_HASHES", LISTED)
    _insert_ready = tmp_path / "questions.yaml"
    _insert_ready.write_text(
        yaml.safe_dump({"questions": [{"question": SCORED_Q}]}),
        encoding="utf-8",
    )
    register_verified_query(space_id=SPACE, question=KEEPER, sql=SQL, path=path)
    _insert(path, asset_id="vq_old", question=SCORED_Q, pack_hash=LISTED)
    with caplog.at_level("WARNING"):
        with pytest.raises(ValueError, match=WRITE_BLOCKED):
            register_verified_query(
                space_id=SPACE,
                question=SCORED_Q,
                sql=SQL,
                pack_hash=LISTED,
                path=path,
            )
    assert WRITE_BLOCKED in caplog.text
    con = duckdb.connect(str(path))
    try:
        still = con.execute(
            "SELECT asset_id FROM main._verified_queries WHERE asset_id = 'vq_old'"
        ).fetchall()
    finally:
        con.close()
    assert still == [("vq_old",)]

    monkeypatch.setattr(
        "dms_executor.skills_quarantine.curated_questions_path", lambda: _insert_ready
    )
    monkeypatch.delenv("DMS_SCORED_PACK_HASHES", raising=False)
    with pytest.raises(ValueError, match=WRITE_BLOCKED):
        register_verified_query(
            space_id=SPACE, question=SCORED_Q, sql=SQL, path=path
        )
    kept = register_verified_query(
        space_id=SPACE, question="second steward item", sql=SQL, path=path
    )
    assert kept["question"] == "second steward item"


def test_config_hash_list_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SCORED_PACK_HASHES", f" {LISTED.upper()}, {OTHER} ")
    rows = [
        {"question": KEEPER, "pack_hash": LISTED},
        {"question": "second steward item", "pack_hash": OTHER},
        {"question": "third steward item"},
    ]
    assert [row["question"] for row in filter_retrieved_rows(rows)] == [
        "third steward item"
    ]
    monkeypatch.setenv("DMS_SCORED_PACK_HASHES", "")
    assert [row["question"] for row in filter_retrieved_rows(rows)] == [
        KEEPER,
        "second steward item",
        "third steward item",
    ]


def test_in_repo_questions_file_hash_is_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DMS_SCORED_PACK_HASHES", raising=False)
    digest = questions_file_hash(curated_questions_path())
    path = tmp_path / "repo.duckdb"
    register_verified_query(space_id=SPACE, question=KEEPER, sql=SQL, path=path)
    _insert(path, asset_id="vq_repo", question="file hash carrier", pack_hash=digest)
    questions = {row["question"] for row in list_verified_queries(space_id=SPACE, path=path)}
    assert questions == {KEEPER}


def test_pack_name_is_not_an_exclusion_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SCORED_PACK_HASHES", "curated_ceo")
    rows = [{"question": KEEPER, "source": "curated_ceo", "pack_hash": "curated_ceo"}]
    assert [row["question"] for row in filter_retrieved_rows(rows)] == [KEEPER]


def test_old_table_without_provenance_column(tmp_path: Path) -> None:
    path = tmp_path / "old.duckdb"
    ensure_demo_warehouse(path)
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE main._verified_queries (
              asset_id VARCHAR PRIMARY KEY,
              space_id VARCHAR NOT NULL,
              question VARCHAR NOT NULL,
              question_norm VARCHAR NOT NULL,
              sql_text VARCHAR NOT NULL,
              synonyms_json VARCHAR NOT NULL,
              created_at TIMESTAMPTZ
            )
            """
        )
        con.execute(
            """
            INSERT INTO main._verified_queries
              (asset_id, space_id, question, question_norm, sql_text, synonyms_json, created_at)
            VALUES ('vq_old', ?, ?, ?, ?, '[]', now())
            """,
            [SPACE, KEEPER, normalize_verified_question(KEEPER), SQL],
        )
    finally:
        con.close()
    questions = {row["question"] for row in list_verified_queries(space_id=SPACE, path=path)}
    assert questions == {KEEPER}


def test_store_has_one_retrieval_select() -> None:
    text = (ROOT / "packages/executor/dms_executor/verified_queries.py").read_text(
        encoding="utf-8"
    )
    assert text.count("SELECT asset_id") == 1
