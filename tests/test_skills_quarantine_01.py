"""SKILLS-QUARANTINE-01: scored rows never leave the verified-question store.

Readers (each has its own test, and the N-row test hits all three):
- list_verified_queries
- lookup_verified_query
- maybe_verified_ask

Exclusion keys are pack hash, item_content_hash(sql), and item_result_hash(rows).
Question text is not a key.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import duckdb
import pytest
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import build_answer_envelope
from dms_executor.manifest import SecurityEvent
from dms_executor.skills_quarantine import (
    CONFIG_STAMP,
    HASH_FAILED,
    RESULT_UNCOMPUTABLE,
    WRITE_BLOCKED,
    _readonly_rows,
    filter_retrieved_rows,
    item_content_hash,
    item_result_hash,
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
SAME_WORDING = "shared wording synthetic"
SQL = "SELECT 1"
BASE_SQL = "SELECT a AS b FROM t WHERE a > 1 ORDER BY b LIMIT 2"
LISTED = "ab" * 32
OTHER = "cd" * 32


def _insert(
    path: Path,
    *,
    asset_id: str,
    question: str,
    pack_hash: str | None,
    sql: str = SQL,
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
                sql,
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
    register_verified_query(space_id=SPACE, question=KEEPER, sql=SQL, path=path, dialect="duckdb")
    _insert(path, asset_id="vq_scored", question=SCORED_Q, pack_hash=LISTED)
    return path


def _clear_hash_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DMS_SCORED_PACK_HASHES",
        "DMS_SCORED_ITEM_HASHES",
        "DMS_SCORED_ITEM_HASHES_FILE",
        "DMS_SCORED_RESULT_HASHES",
    ):
        monkeypatch.delenv(name, raising=False)


def _grant_names(monkeypatch: pytest.MonkeyPatch, *tables: str) -> None:
    """The allow-list only serves a table the Space grant names."""
    import dms_executor.verified_queries as vq

    extra = set(tables)
    real = vq._grantable

    def _wrapped(space_id: str | None, warehouse: Path | None) -> set[str]:
        return set(real(space_id, warehouse)) | extra

    monkeypatch.setattr(vq, "_grantable", _wrapped)


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


def test_item_content_hash_same_sql_different_spelling() -> None:
    digest = item_content_hash(BASE_SQL)
    variants = [
        'select  A   as  "b"  from  T  where  A>1  order  by  b  limit  2',
        "SELECT a AS `b` FROM t WHERE a > 1 ORDER BY b LIMIT 2",
        "SELECT a AS b FROM t WHERE a > 1 ORDER BY b LIMIT 2 -- note",
        "SELECT a AS b FROM t WHERE a > 1 /* note */ ORDER BY b LIMIT 2",
    ]
    assert [item_content_hash(sql) for sql in variants] == [digest, digest, digest, digest]


def test_item_content_hash_different_sql() -> None:
    digest = item_content_hash(BASE_SQL)
    changed = [
        BASE_SQL.replace("> 1", "> 2"),
        BASE_SQL.replace("LIMIT 2", "LIMIT 3"),
        BASE_SQL.replace("ORDER BY b", "ORDER BY a"),
        BASE_SQL.replace("FROM t", "FROM u"),
    ]
    assert all(item_content_hash(sql) != digest for sql in changed)
    assert item_content_hash("SELECT a FROM t WHERE a = 'X'") != item_content_hash(
        "SELECT a FROM t WHERE a = 'x'"
    )


def test_item_content_hash_unparsed_sql_still_hashes() -> None:
    assert item_content_hash("not sql !!!") == item_content_hash("NOT   SQL !!!")
    assert len(item_content_hash("not sql !!!")) == 64


def test_item_result_hash_canonical_rows() -> None:
    digest = item_result_hash([{"n": 1}, {"n": None}])
    assert digest == item_result_hash([{"n": None}, {"n": 1.0}])
    assert digest != item_result_hash([{"n": 2}])
    assert item_result_hash([{"a": 1, "b": 2}]) != item_result_hash([{"b": 2, "a": 1}])
    assert item_result_hash([{"n": True}]) != item_result_hash([{"n": 1}])
    assert item_result_hash([{"n": True}]) != item_result_hash([{"n": "true"}])
    assert item_result_hash([{"n": None}]) != item_result_hash([{"n": ""}])
    assert item_result_hash([{"n": False}]) != item_result_hash([{"n": ""}])
    assert len(item_result_hash([])) == 64


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
    assert lookup_verified_query(SCORED_Q, space_id=SPACE, warehouse=path, dialect="duckdb") is None
    hit = lookup_verified_query(KEEPER, space_id=SPACE, warehouse=path, dialect="duckdb")
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
        dialect="duckdb",
    )
    assert missed is None
    assert submit.calls == []
    env = maybe_verified_ask(
        KEEPER,
        space_id=SPACE,
        warehouse=path,
        submit=submit,
        ledger_append=_Ledger(),
        dialect="duckdb",
    )
    assert env is not None
    assert env["abstained"] is False
    assert env["badge"] == "L0_CERTIFIED"
    assert "Found 1 row(s)." in env["text"]
    assert env["rows"] == [{"n": 1}]
    assert submit.calls == [SQL]


def test_reworded_question_same_sql_is_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Must-pass: different question text, same SQL, is excluded."""
    _clear_hash_env(monkeypatch)
    _grant_names(monkeypatch, "t_kept")
    scored_sql = "SELECT a FROM t_scored"
    monkeypatch.setenv("DMS_SCORED_ITEM_HASHES", item_content_hash(scored_sql))
    path = tmp_path / "reword.duckdb"
    register_verified_query(space_id=SPACE, question=KEEPER, sql="SELECT a FROM t_kept", path=path,
        dialect="duckdb")
    _insert(
        path,
        asset_id="vq_reword",
        question="reworded copy of a scored item",
        pack_hash=None,
        sql="select  A  from  t_scored",
    )
    questions = {row["question"] for row in list_verified_queries(space_id=SPACE, path=path)}
    assert questions == {KEEPER}
    assert lookup_verified_query(
        "reworded copy of a scored item", space_id=SPACE, warehouse=path,
        dialect="duckdb",
    ) is None


def test_same_question_different_sql_is_not_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Must-fail: same question wording, different SQL, is not excluded."""
    _clear_hash_env(monkeypatch)
    monkeypatch.setenv(
        "DMS_SCORED_ITEM_HASHES", item_content_hash("SELECT a FROM t_scored")
    )
    rows = [
        {"question": SAME_WORDING, "sql": "SELECT a FROM t_scored"},
        {"question": SAME_WORDING, "sql": "SELECT a FROM t_kept"},
    ]
    kept = filter_retrieved_rows(rows)
    assert [row["sql"] for row in kept] == ["SELECT a FROM t_kept"]


def test_equivalent_sql_same_rows_excluded_by_result_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Must-pass: different SQL, same result rows, excluded via the result hash."""
    _clear_hash_env(monkeypatch)
    listed = item_result_hash([{"n": 1}])
    monkeypatch.setenv("DMS_SCORED_RESULT_HASHES", listed)
    in_memory = filter_retrieved_rows(
        [
            {
                "question": "equivalent sql",
                "sql": "SELECT 2 - 1 AS n",
                "result_rows": [{"n": 1}],
            },
            {
                "question": KEEPER,
                "sql": "SELECT 0 AS n",
                "result_rows": [{"n": 0}],
            },
        ]
    )
    assert [row["question"] for row in in_memory] == [KEEPER]

    path = tmp_path / "result.duckdb"
    register_verified_query(space_id=SPACE, question=KEEPER, sql="SELECT 0 AS n", path=path,
        dialect="duckdb")
    _insert(
        path,
        asset_id="vq_equiv",
        question="stored equivalent sql",
        pack_hash=None,
        sql="SELECT 2 - 1 AS n",
    )
    questions = {row["question"] for row in list_verified_queries(space_id=SPACE, path=path)}
    assert questions == {KEEPER}
    assert lookup_verified_query("stored equivalent sql", space_id=SPACE, warehouse=path,
        dialect="duckdb") is None


def test_different_result_is_not_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Must-fail: a normal row with a different result is not excluded."""
    _clear_hash_env(monkeypatch)
    monkeypatch.setenv("DMS_SCORED_RESULT_HASHES", item_result_hash([{"n": 1}]))
    kept = filter_retrieved_rows(
        [{"question": KEEPER, "sql": "SELECT 2 AS n", "result_rows": [{"n": 2}]}]
    )
    assert [row["question"] for row in kept] == [KEEPER]

    path = tmp_path / "other-result.duckdb"
    register_verified_query(space_id=SPACE, question=KEEPER, sql="SELECT 2 AS n", path=path,
        dialect="duckdb")
    questions = {row["question"] for row in list_verified_queries(space_id=SPACE, path=path)}
    assert questions == {KEEPER}


def test_every_reader_returns_no_scored_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_hash_env(monkeypatch)
    _grant_names(monkeypatch, "t_normal")
    scored = [(f"scored question {i}", f"SELECT {i} AS n FROM t_scored") for i in range(3)]
    normal = [(f"normal question {i}", f"SELECT {i} AS n FROM t_normal") for i in range(2)]
    monkeypatch.setenv(
        "DMS_SCORED_ITEM_HASHES",
        ",".join(item_content_hash(sql) for _, sql in scored),
    )
    path = tmp_path / "many.duckdb"
    for question, sql in normal:
        register_verified_query(space_id=SPACE, question=question, sql=sql, path=path,
            dialect="duckdb")
    for index, (question, sql) in enumerate(scored):
        _insert(path, asset_id=f"vq_scored_{index}", question=question, pack_hash=None, sql=sql)

    listed = list_verified_queries(space_id=SPACE, path=path)
    assert {row["question"] for row in listed} == {question for question, _ in normal}
    submit = _Submit()
    for question, _sql in scored:
        assert lookup_verified_query(question, space_id=SPACE, warehouse=path,
            dialect="duckdb") is None
        assert (
            maybe_verified_ask(
                question,
                space_id=SPACE,
                warehouse=path,
                submit=submit,
                ledger_append=_Ledger(),
                dialect="duckdb",
            )
            is None
        )
    assert submit.calls == []
    for question, sql in normal:
        hit = lookup_verified_query(question, space_id=SPACE, warehouse=path, dialect="duckdb")
        assert hit is not None
        assert hit["sql"] == sql
        env = maybe_verified_ask(
            question,
            space_id=SPACE,
            warehouse=path,
            submit=submit,
            ledger_append=_Ledger(),
            dialect="duckdb",
        )
        assert env is not None
        assert env["abstained"] is False
    assert submit.calls == [sql for _, sql in normal]


def test_scored_pack_write_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "write.duckdb"
    _clear_hash_env(monkeypatch)
    monkeypatch.setenv("DMS_SCORED_PACK_HASHES", LISTED)
    register_verified_query(space_id=SPACE, question=KEEPER, sql=SQL, path=path, dialect="duckdb")
    _insert(path, asset_id="vq_old", question=SCORED_Q, pack_hash=LISTED)
    with caplog.at_level("WARNING"):
        with pytest.raises(ValueError, match=WRITE_BLOCKED):
            register_verified_query(
                space_id=SPACE,
                question=SCORED_Q,
                sql=SQL,
                pack_hash=LISTED,
                path=path,
                dialect="duckdb",
            )
    assert WRITE_BLOCKED in caplog.text
    assert SCORED_Q not in caplog.text
    con = duckdb.connect(str(path))
    try:
        still = con.execute(
            "SELECT asset_id FROM main._verified_queries WHERE asset_id = 'vq_old'"
        ).fetchall()
    finally:
        con.close()
    assert still == [("vq_old",)]

    monkeypatch.delenv("DMS_SCORED_PACK_HASHES", raising=False)
    monkeypatch.setenv("DMS_SCORED_ITEM_HASHES", item_content_hash(SQL))
    with pytest.raises(ValueError, match=WRITE_BLOCKED):
        register_verified_query(space_id=SPACE, question=SCORED_Q, sql=SQL, path=path,
            dialect="duckdb")
    kept = register_verified_query(
        space_id=SPACE, question="second steward item", sql="SELECT 3 AS n", path=path,
        dialect="duckdb",
    )
    assert kept["question"] == "second steward item"


def test_config_hash_list_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_hash_env(monkeypatch)
    monkeypatch.setenv("DMS_SCORED_PACK_HASHES", f" {LISTED.upper()}, {OTHER} ")
    rows = [
        {"question": KEEPER, "pack_hash": LISTED},
        {"question": "second steward item", "pack_hash": OTHER},
        {"question": "third steward item"},
    ]
    assert [row["question"] for row in filter_retrieved_rows(rows)] == ["third steward item"]
    monkeypatch.delenv("DMS_SCORED_PACK_HASHES", raising=False)
    assert [row["question"] for row in filter_retrieved_rows(rows)] == [
        KEEPER,
        "second steward item",
        "third steward item",
    ]


def _abstain_envelope() -> dict[str, Any]:
    return build_answer_envelope(
        answer_id="ans_quarantine_cfg",
        text="No stored skill was used.",
        badge="ABSTAIN",
        abstained=True,
        rows=[],
        values=[],
    )


def test_unset_config_keeps_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No quarantine configured: retrieval and the envelope stay as they are."""
    _clear_hash_env(monkeypatch)
    rows = [{"question": KEEPER, "sql": "SELECT 4 AS n"}]
    assert [row["question"] for row in filter_retrieved_rows(rows)] == [KEEPER]
    env = _abstain_envelope()
    assert "skills_quarantine" not in env
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True


def test_bad_config_returns_no_rows_and_stamps_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Set but empty, malformed, or unreadable: no retrieved rows, ask still envelopes."""
    _clear_hash_env(monkeypatch)
    rows = [
        {"question": KEEPER, "sql": "SELECT 9 AS n FROM t_plain"},
        {"question": SCORED_Q, "sql": "SELECT 8 AS n FROM t_plain"},
    ]
    cases = ["", "not-a-hash", "   ,  "]
    for raw in cases:
        monkeypatch.setenv("DMS_SCORED_ITEM_HASHES", raw)
        monkeypatch.delenv("DMS_SCORED_RESULT_HASHES", raising=False)
        with caplog.at_level("WARNING"):
            assert filter_retrieved_rows(rows) == []
        assert CONFIG_STAMP in caplog.text
        caplog.clear()
        env = _abstain_envelope()
        assert env["skills_quarantine"] == CONFIG_STAMP
        assert env["abstained"] is True
        assert env["badge"] == "ABSTAIN"

    monkeypatch.delenv("DMS_SCORED_ITEM_HASHES", raising=False)
    monkeypatch.setenv("DMS_SCORED_ITEM_HASHES_FILE", str(tmp_path / "absent.txt"))
    with caplog.at_level("WARNING"):
        assert filter_retrieved_rows(rows) == []
    assert CONFIG_STAMP in caplog.text
    assert "absent.txt" not in caplog.text


def test_operator_hash_file_lists_only_its_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _clear_hash_env(monkeypatch)
    sql = "SELECT a FROM t_file"
    path = tmp_path / "hashes.txt"
    path.write_text(item_content_hash(sql) + "\n", encoding="utf-8")
    monkeypatch.setenv("DMS_SCORED_ITEM_HASHES_FILE", str(path))
    kept = filter_retrieved_rows(
        [
            {"question": SCORED_Q, "sql": sql},
            {"question": KEEPER, "sql": "SELECT a FROM t_kept"},
        ]
    )
    assert [row["question"] for row in kept] == [KEEPER]

    empty = tmp_path / "empty.txt"
    empty.write_text("\n", encoding="utf-8")
    monkeypatch.setenv("DMS_SCORED_ITEM_HASHES_FILE", str(empty))
    with caplog.at_level("WARNING"):
        assert filter_retrieved_rows([{"question": KEEPER, "sql": sql}]) == []
    assert CONFIG_STAMP in caplog.text


def test_hash_exception_excludes_only_that_row(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _clear_hash_env(monkeypatch)
    real = item_content_hash

    def boom(sql: str) -> str:
        if "BOOM" in sql:
            raise RuntimeError("boom")
        return real(sql)

    monkeypatch.setattr("dms_executor.skills_quarantine.item_content_hash", boom)
    rows = [
        {"question": SCORED_Q, "sql": "SELECT BOOM"},
        {"question": KEEPER, "sql": "SELECT a FROM t_kept"},
    ]
    with caplog.at_level("WARNING"):
        kept = filter_retrieved_rows(rows)
    assert [row["question"] for row in kept] == [KEEPER]
    assert HASH_FAILED in caplog.text
    assert SCORED_Q not in caplog.text


def test_uncomputable_result_hash_excludes_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _clear_hash_env(monkeypatch)
    monkeypatch.setenv("DMS_SCORED_RESULT_HASHES", item_result_hash([{"n": 1}]))
    path = tmp_path / "bad.duckdb"
    register_verified_query(space_id=SPACE, question=KEEPER, sql="SELECT 0 AS n", path=path,
        dialect="duckdb")
    bad_sql = "SELECT * FROM t_missing_quarantine"
    _insert(path, asset_id="vq_bad", question="bad sql", pack_hash=None, sql=bad_sql)
    with caplog.at_level("WARNING"):
        questions = {row["question"] for row in list_verified_queries(space_id=SPACE, path=path)}
    assert questions == {KEEPER}
    assert RESULT_UNCOMPUTABLE in caplog.text
    assert "t_missing_quarantine" not in caplog.text
    assert "bad sql" not in caplog.text


def test_result_hash_timeout_excludes_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _clear_hash_env(monkeypatch)
    monkeypatch.setenv("DMS_SCORED_RESULT_HASHES", item_result_hash([{"n": 1}]))
    path = tmp_path / "slow.duckdb"
    register_verified_query(space_id=SPACE, question=KEEPER, sql="SELECT 0 AS n", path=path,
        dialect="duckdb")
    _insert(
        path,
        asset_id="vq_slow",
        question="slow sql",
        pack_hash=None,
        sql="SELECT SUM(range) FROM range(100000000000)",
    )
    started = time.monotonic()
    with caplog.at_level("WARNING"):
        questions = {row["question"] for row in list_verified_queries(space_id=SPACE, path=path)}
    elapsed = time.monotonic() - started
    assert questions == {KEEPER}
    assert RESULT_UNCOMPUTABLE in caplog.text
    assert elapsed < 8


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
    text = (ROOT / "packages/executor/dms_executor/verified_queries.py").read_text(encoding="utf-8")
    assert text.count("SELECT asset_id") == 1


def test_malformed_result_rows_excluded(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _clear_hash_env(monkeypatch)
    monkeypatch.setenv("DMS_SCORED_RESULT_HASHES", item_result_hash([{"n": 1}]))
    with caplog.at_level("WARNING"):
        kept = filter_retrieved_rows(
            [{"question": SCORED_Q, "sql": "SELECT 1 AS n", "result_rows": "nope"}]
        )
    assert kept == []
    assert RESULT_UNCOMPUTABLE in caplog.text


_HOSTILE = [
    ("copy_to", "COPY t_probe TO '{dest}'"),
    ("read_csv_auto", "SELECT * FROM read_csv_auto('/etc/passwd')"),
    ("attach", "ATTACH '{dest}' AS other_db"),
    ("install", "INSTALL httpfs"),
    ("load", "LOAD httpfs"),
    ("pragma", "PRAGMA version"),
    ("insert", "INSERT INTO t_probe VALUES (1)"),
    ("update", "UPDATE t_probe SET i = 2"),
]


@pytest.mark.parametrize(("label", "template"), _HOSTILE, ids=[item[0] for item in _HOSTILE])
def test_result_probe_refuses_hostile_sql(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    template: str,
) -> None:
    """Must-fail: hostile probe SQL is refused, writes nothing, returns no rows."""
    del label
    _clear_hash_env(monkeypatch)
    monkeypatch.setenv("DMS_SCORED_RESULT_HASHES", item_result_hash([{"n": 1}]))
    path = tmp_path / "probe.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE t_probe(i INTEGER)")
        con.execute("INSERT INTO t_probe VALUES (1)")
    finally:
        con.close()
    dest = tmp_path / "leak.csv"
    sql = template.format(dest=dest)
    with pytest.raises(SecurityEvent) as caught:
        _readonly_rows(path, sql)
    assert caught.value.code in {"statement_not_allowed", "path_not_allowed"}
    assert not dest.exists()
    assert list(tmp_path.glob("*.csv")) == []
    kept = filter_retrieved_rows(
        [{"question": "hostile", "sql": sql, "asset_id": "vq_hostile"}],
        warehouse=path,
    )
    assert kept == []
    blob = repr(kept)
    assert "root:" not in blob
    assert "/etc/passwd" not in blob


def test_result_probe_disables_external_access(tmp_path: Path) -> None:
    path = tmp_path / "ext.duckdb"
    duckdb.connect(str(path)).close()
    rows = _readonly_rows(path, "SELECT current_setting('enable_external_access') AS flag")
    assert rows == [{"flag": False}]


def _stored_count(path: Path) -> int:
    if not path.exists():
        return 0
    con = duckdb.connect(str(path))
    try:
        names = {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}
        if "_verified_queries" not in names:
            return 0
        row = con.execute("SELECT count(*) FROM main._verified_queries").fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0


# Shapes the read path already treats as unusable. Each one must refuse a write.
_BAD_CONFIGS = (
    ("pack_empty", "DMS_SCORED_PACK_HASHES", ""),
    ("pack_malformed", "DMS_SCORED_PACK_HASHES", "not-a-hash"),
    ("pack_separators", "DMS_SCORED_PACK_HASHES", " , "),
    ("item_empty", "DMS_SCORED_ITEM_HASHES", ""),
    ("item_malformed", "DMS_SCORED_ITEM_HASHES", "not-a-hash"),
    ("item_separators", "DMS_SCORED_ITEM_HASHES", "   ,  "),
    ("result_empty", "DMS_SCORED_RESULT_HASHES", ""),
    ("result_malformed", "DMS_SCORED_RESULT_HASHES", "not-a-hash"),
    ("result_short", "DMS_SCORED_RESULT_HASHES", "abcd"),
    ("file_missing", "DMS_SCORED_ITEM_HASHES_FILE", "__missing__"),
    ("file_empty", "DMS_SCORED_ITEM_HASHES_FILE", "__empty__"),
)


@pytest.mark.parametrize(
    ("label", "env_name", "raw"),
    _BAD_CONFIGS,
    ids=[c[0] for c in _BAD_CONFIGS],
)
def test_invalid_config_write_stores_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    label: str,
    env_name: str,
    raw: str,
) -> None:
    """A write under an unusable hash source stores nothing. Fails on 9475d79."""
    del label
    _clear_hash_env(monkeypatch)
    path = tmp_path / "write.duckdb"
    if raw == "__missing__":
        monkeypatch.setenv(env_name, str(tmp_path / "missing-hash-file"))
    elif raw == "__empty__":
        empty = tmp_path / "empty-hash-file.txt"
        empty.write_text("", encoding="utf-8")
        monkeypatch.setenv(env_name, str(empty))
    else:
        monkeypatch.setenv(env_name, raw)
    with caplog.at_level("WARNING"):
        with pytest.raises(ValueError, match=CONFIG_STAMP):
            register_verified_query(
                space_id=SPACE,
                question=KEEPER,
                sql="SELECT 4 AS n",
                path=path,
                dialect="duckdb",
            )
    assert CONFIG_STAMP in caplog.text
    assert KEEPER not in caplog.text
    assert _stored_count(path) == 0
    monkeypatch.delenv(env_name, raising=False)
    assert list_verified_queries(space_id=SPACE, path=path) == []


def test_valid_config_still_stores_unlisted_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_hash_env(monkeypatch)
    listed = "SELECT 9 AS n"
    monkeypatch.setenv("DMS_SCORED_ITEM_HASHES", item_content_hash(listed))
    path = tmp_path / "valid.duckdb"
    kept = register_verified_query(
        space_id=SPACE, question=KEEPER, sql="SELECT 8 AS n", path=path,
        dialect="duckdb",
    )
    assert kept["sql"] == "SELECT 8 AS n"
    with pytest.raises(ValueError, match=WRITE_BLOCKED):
        register_verified_query(
            space_id=SPACE, question=SCORED_Q, sql=listed, path=path,
            dialect="duckdb",
        )
    stored = list_verified_queries(space_id=SPACE, path=path)
    assert [row["question"] for row in stored] == [KEEPER]


def test_studio_post_refuses_invalid_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Studio POST under not-a-hash is a named 4xx and stores nothing. Fails on 9475d79."""
    from cortex_client.gate import ComplianceDecision
    from dms_api.app import create_app
    from fastapi.testclient import TestClient

    _clear_hash_env(monkeypatch)
    monkeypatch.setenv("DMS_SCORED_ITEM_HASHES", "not-a-hash")
    warehouse = tmp_path / "studio.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(warehouse))

    def allow(*, action: str, actor: str | None = None, **_: Any) -> ComplianceDecision:
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    monkeypatch.setattr("dms_api.routes.studio.compliance_gate", allow)
    client = TestClient(create_app())
    response = client.post(
        "/v1/studio/verified-queries",
        json={"space_id": SPACE, "question": KEEPER, "sql": "SELECT 4 AS n"},
    )
    assert response.status_code == 400
    assert response.status_code not in {200, 500, 503}
    assert response.json()["detail"] == CONFIG_STAMP
    assert _stored_count(warehouse) == 0
    monkeypatch.delenv("DMS_SCORED_ITEM_HASHES", raising=False)
    listed = client.get(f"/v1/studio/verified-queries?space_id={SPACE}")
    assert listed.status_code == 200
    assert listed.json() == []
