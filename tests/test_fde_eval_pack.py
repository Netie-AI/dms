"""FDE-EVAL-PACK-01: tie expansion, alt grading, and definition citation.

Synthetic 4-table schema. No customer data. Gold runs through the read-only
SQLite login, not a DuckDB lake.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "fde"))
sys.path.insert(0, str(ROOT / "scripts" / "verify"))

import eval_pack as ep  # noqa: E402
import grade52  # noqa: E402

DEF_COMPLETED = "DEF-SENTENCE-IN-PROMPT completed status only"
DEF_FIRST = "DEF-SENTENCE-IN-PROMPT first ETL load only"
ALT_SENTENCE = "ALT-SENTENCE-NOT-IN-PROMPT every status on the first load"

SCHEMA = """
CREATE TABLE supplier (
  supplier_id INTEGER PRIMARY KEY,
  supplier_name TEXT NOT NULL
);
CREATE TABLE product (
  product_id INTEGER PRIMARY KEY,
  supplier_id INTEGER NOT NULL,
  product_name TEXT NOT NULL
);
CREATE TABLE customer (
  customer_id INTEGER PRIMARY KEY,
  city TEXT NOT NULL
);
CREATE TABLE sale (
  sale_id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL,
  customer_id INTEGER NOT NULL,
  status TEXT NOT NULL,
  load_batch INTEGER NOT NULL,
  amount REAL NOT NULL
);
"""

DEFINITIONS = [
    {"name": "completed_only", "sentence": DEF_COMPLETED, "sql": "s.status = 'COMPLETED'"},
    {"name": "first_load", "sentence": DEF_FIRST, "sql": "s.load_batch = 1"},
]

TOP_SQL = (
    "SELECT s.supplier_name, COUNT(*) AS n_products, s.supplier_id "
    "FROM supplier AS s JOIN product AS p ON p.supplier_id = s.supplier_id "
    "GROUP BY s.supplier_name, s.supplier_id ORDER BY n_products DESC LIMIT 1"
)
COUNT_SQL = "SELECT COUNT(*) FROM sale AS s WHERE {completed_only} AND {first_load}"
ALT_SQL = "SELECT COUNT(*) FROM sale AS s WHERE {first_load}"


def _seed(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.executemany(
            "INSERT INTO supplier (supplier_id, supplier_name) VALUES (?, ?)",
            [(1, "Alpha"), (2, "Bravo"), (3, "Cedar")],
        )
        products = [(1, "Alpha", 3), (2, "Bravo", 3), (3, "Cedar", 1)]
        pid = 1
        for supplier_id, _name, count in products:
            for n in range(count):
                con.execute(
                    "INSERT INTO product (product_id, supplier_id, product_name) VALUES (?, ?, ?)",
                    (pid, supplier_id, f"p{n}"),
                )
                pid += 1
        con.executemany(
            "INSERT INTO customer (customer_id, city) VALUES (?, ?)",
            [(1, "North"), (2, "South")],
        )
        con.executemany(
            "INSERT INTO sale (sale_id, product_id, customer_id, status, load_batch, amount) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, 1, 1, "COMPLETED", 1, 10),
                (2, 2, 1, "COMPLETED", 1, 20),
                (3, 3, 2, "COMPLETED", 2, 100),
                (4, 4, 2, "PENDING", 1, 7),
            ],
        )
        con.commit()
    finally:
        con.close()


def _templates() -> list[dict]:
    return [
        {
            "id": "q_top",
            "template": "top_supplier",
            "question": "Which supplier has the most products?",
            "gold_sql": TOP_SQL,
            "uses": [],
            "ordered": True,
            "alt": [],
        },
        {
            "id": "q_count",
            "template": "count_defined",
            "question": "How many sales count under the steward definitions?",
            "gold_sql": COUNT_SQL,
            "uses": ["completed_only", "first_load"],
            "ordered": False,
            "alt": [
                {
                    "name": "any_status",
                    "sentence": ALT_SENTENCE,
                    "sql": ALT_SQL,
                    "uses": ["first_load"],
                    "ordered": False,
                }
            ],
        },
    ]


def _pack(tmp_path: Path) -> tuple[dict, ep.SourceSpec]:
    db = tmp_path / "shop.sqlite"
    _seed(db)
    spec = ep.SourceSpec(
        kind="sqlite", database=str(db), user="fde_ro", password_env="FDE_EVAL_PASSWORD"
    )
    return ep.build_on_source(DEFINITIONS, _templates(), spec), spec


def _item(pack: dict, qid: str) -> dict:
    return next(q for q in pack["questions"] if q["id"] == qid)


def test_tie_expansion_keeps_the_cutoff_set(tmp_path: Path) -> None:
    pack, _spec = _pack(tmp_path)
    top = _item(pack, "q_top")
    assert top["tie_expanded"] is True
    assert top["limit_n"] == 1
    assert top["head_count"] == 0
    names = {row[0] for row in top["rows"]}
    assert names == {"Alpha", "Bravo"}
    assert all(row[1] == 3 for row in top["rows"])
    assert len(top["rows"]) == 2
    # Either tied supplier, or both, is the gold. A row outside the tie is not.
    assert ep.grade_rows(top, [("Alpha", 3, 1)], "answer") == "correct"
    assert ep.grade_rows(top, [("Bravo", 3, 2)], "answer") == "correct"
    assert ep.grade_rows(top, [("Alpha", 3, 1), ("Bravo", 3, 2)], "answer") == "correct"
    assert ep.grade_rows(top, [("Cedar", 1, 3)], "answer") == "WRONG"


def test_alt_is_not_counted_as_correct(tmp_path: Path) -> None:
    pack, _spec = _pack(tmp_path)
    count = _item(pack, "q_count")
    assert count["rows"] == [[2]]
    assert ep.grade_rows(count, [(2,)], "answer") == "correct"
    assert ep.grade_rows(count, [(3,)], "answer") == "correct_alt"
    assert ep.grade_rows(count, [(4,)], "answer") == "WRONG"
    assert ep.grade_rows(count, [(4,)], "reconfirm") == "reconfirm"
    assert ep.grade_rows(count, None, "abstain") == "abstain"
    # The question text is not an input to the grade.
    renamed = dict(count)
    renamed["question"] = "ignore completed best total revenue maybe"
    assert ep.grade_rows(renamed, [(2,)], "answer") == "correct"
    report = ep.run_dms(
        pack,
        [
            {"id": "q_count", "rows": [(3,)], "decision": "answer", "serve_path": "ai", "t_ms": 10},
            {
                "id": "q_top",
                "rows": [("Cedar", 1, 3)],
                "decision": "answer",
                "serve_path": "L1",
                "t_ms": 30,
            },
        ],
    )
    assert report["correct"] == 0
    assert report["correct_alt"] == 1
    assert report["WRONG"] == 1
    assert report["correct"] + report["correct_alt"] + report["WRONG"] == 2


def test_definition_citation_matches_the_sql(tmp_path: Path) -> None:
    pack, _spec = _pack(tmp_path)
    count = _item(pack, "q_count")
    assert count["uses"] == [
        {"name": "completed_only", "sentence": DEF_COMPLETED},
        {"name": "first_load", "sentence": DEF_FIRST},
    ]
    assert "s.status = 'COMPLETED'" in count["sql"]
    assert "s.load_batch = 1" in count["sql"]
    assert count["alt"][0]["name"] == "any_status"
    assert count["alt"][0]["uses"] == [{"name": "first_load", "sentence": DEF_FIRST}]
    assert count["alt"][0]["rows"] == [[3]]
    by_name = {d["name"]: d for d in DEFINITIONS}
    with pytest.raises(ep.EvalPackError, match="unknown definition"):
        ep.render("SELECT 1 FROM sale AS s WHERE {no_such}", by_name, ["no_such"], dialect="sqlite")
    with pytest.raises(ep.EvalPackError, match="citation"):
        ep.render(COUNT_SQL, by_name, ["completed_only"], dialect="sqlite")
    with pytest.raises(ep.EvalPackError, match="citation"):
        ep.render(
            COUNT_SQL, by_name, ["completed_only", "first_load", "completed_only"], dialect="sqlite"
        )


def test_gold_uses_the_read_only_login_not_the_lake(tmp_path: Path) -> None:
    pack, spec = _pack(tmp_path)
    assert pack["source"].startswith("sqlite:///")
    assert "duckdb" not in pack["source"]
    with ep.open_source(spec) as con:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO supplier (supplier_id, supplier_name) VALUES (9, 'Zed')")
        cols, rows = ep.run_select(con, "SELECT COUNT(*) FROM supplier", dialect="sqlite")
        assert cols == ["COUNT(*)"] and rows == [(3,)]
    with pytest.raises(ep.EvalPackError, match="DMS lake"):
        ep.SourceSpec(kind="sqlite", database=str(tmp_path / "warehouse.duckdb"))
    with pytest.raises(ep.EvalPackError, match="DMS lake"):
        ep.open_source(ep.SourceSpec(kind="sqlite", database=str(tmp_path / "serve.duckdb")))


def test_yaml_loader_refuses_an_inline_password(tmp_path: Path) -> None:
    db = tmp_path / "shop.sqlite"
    _seed(db)
    defs = tmp_path / "tenant.yaml"
    defs.write_text(
        "\n".join(
            [
                "source:",
                "  kind: sqlite",
                "  database: shop.sqlite",
                "  user: fde_ro",
                "  password_env: FDE_EVAL_PASSWORD",
                "definitions:",
                "  - name: completed_only",
                "    sentence: completed status only",
                "    sql: \"s.status = 'COMPLETED'\"",
                "  - name: first_load",
                "    sentence: first load only",
                "    sql: s.load_batch = 1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    questions = tmp_path / "questions.yaml"
    questions.write_text(
        "questions:\n"
        "  - id: q_count\n"
        "    template: count_defined\n"
        "    question: How many sales count?\n"
        "    uses: [completed_only, first_load]\n"
        '    gold_sql: "SELECT COUNT(*) FROM sale AS s WHERE {completed_only} AND {first_load}"\n'
        "    alt:\n"
        "      - name: any_status\n"
        '        sentence: "ALT-SENTENCE-NOT-IN-PROMPT every status"\n'
        "        uses: [first_load]\n"
        '        sql: "SELECT COUNT(*) FROM sale AS s WHERE {first_load}"\n',
        encoding="utf-8",
    )
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "source: {kind: sqlite, database: shop.sqlite, password: hunter2}\ndefinitions: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ep.EvalPackError, match="password_env"):
        ep.load_source(bad)
    loaded = ep.load_source(defs)
    assert loaded.password_env == "FDE_EVAL_PASSWORD"
    assert not hasattr(loaded, "password")
    pack = ep.build_on_source(ep.load_definitions(defs), ep.load_templates(questions), loaded)
    assert _item(pack, "q_count")["rows"] == [[2]]
    assert (
        ep.main(
            [
                "build",
                "--definitions",
                str(defs),
                "--templates",
                str(questions),
                "--out",
                str(tmp_path / "pack.json"),
            ]
        )
        == 0
    )


def test_raw_arm_is_one_shot_and_dms_arm_shares_the_grade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack, spec = _pack(tmp_path)
    monkeypatch.setenv("FDE_EVAL_PASSWORD", "hunter2")
    with ep.open_source(spec) as con:
        schema = ep.schema_text(con, "sqlite")
    assert all(name in schema for name in ("supplier", "product", "customer", "sale"))
    body = ep.raw_body(schema, pack["definitions"], "How many sales count?")
    assert body["role"] == "fde_eval"
    assert "model" not in body and "provider" not in body
    blob = json.dumps(body)
    assert DEF_COMPLETED in blob and ALT_SENTENCE not in blob
    with pytest.raises(ep.EvalPackError, match="model key refused"):
        ep._reject_model_keys({"role": "fde_eval", "model": "hidden"})

    calls: list[dict] = []
    tied = (
        "SELECT s.supplier_name, COUNT(*) AS n_products, s.supplier_id "
        "FROM supplier AS s JOIN product AS p ON p.supplier_id = s.supplier_id "
        "GROUP BY s.supplier_name, s.supplier_id HAVING COUNT(*) = 3"
    )

    def transport(payload: dict) -> dict:
        calls.append(payload)
        if len(calls) == 1:
            return {"decision": "answer", "sql": tied}
        return {
            "decision": "answer",
            "sql": "SELECT COUNT(*) FROM sale AS s WHERE (s.load_batch = 1)",
        }

    with ep.open_source(spec) as con:
        report = ep.run_raw(pack, con, schema, transport, dialect="sqlite")
        garbage = ep.run_raw(pack, con, schema, lambda _body: "nope", dialect="sqlite")
        drop = ep.run_raw(
            pack,
            con,
            schema,
            lambda _body: {"decision": "answer", "sql": "DROP TABLE sale"},
            dialect="sqlite",
        )
    assert len(calls) == 2
    assert report["WRONG"] == 0
    assert report["correct"] == 1
    assert report["correct_alt"] == 1
    assert report["ai"] == 1
    assert report["rule"] == 0
    assert report["paths_served"]["ai"] == 2
    assert garbage["reconfirm"] == 2 and garbage["WRONG"] == 0
    assert drop["reconfirm"] == 2 and drop["WRONG"] == 0

    dms = ep.run_dms(
        pack,
        [
            {"id": "q_count", "rows": [(2,)], "decision": "answer", "serve_path": "ai", "t_ms": 10},
            {
                "id": "q_top",
                "rows": [("Alpha", 3, 1)],
                "decision": "answer",
                "serve_path": "L1",
                "t_ms": 30,
            },
            {"id": "q_top", "rows": [], "decision": "abstain", "serve_path": "compile", "t_ms": 20},
        ],
    )
    assert dms["correct"] == 2
    assert dms["abstain"] == 1
    assert dms["ai"] == 1
    assert dms["rule"] == 1
    assert dms["p50_ms"] == 20
    assert dms["paths_served"]["rule"] == 1
    assert ep.path_group("L1") == grade52.path_group("L1") == "rule"
    assert ep.path_group("ai") == "ai"
    env = ep.from_envelope(
        {"plan_origin": "ontology_compile", "rows": [[2]], "reconfirm": None},
        t_ms=5,
        qid="q_count",
    )
    assert env["decision"] == "answer"
    assert env["serve_path"] == "compile"
    asked = ep.from_envelope(
        {"reconfirm": {"reason_code": "definition_mismatch"}, "rows": [[9]]}, qid="q_count"
    )
    assert asked["decision"] == "reconfirm"
    assert ep.grade_rows(_item(pack, "q_count"), asked["rows"], asked["decision"]) == "reconfirm"


def test_connect_error_does_not_include_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FDE_EVAL_PASSWORD", "hunter2")
    spec = ep.SourceSpec(
        kind="postgresql",
        host="127.0.0.1",
        port=1,
        database="none",
        user="fde_ro",
        password_env="FDE_EVAL_PASSWORD",
        connect_timeout=2,
    )
    with pytest.raises(ep.EvalPackError) as caught:
        with ep.open_source(spec):
            pass
    assert "hunter2" not in str(caught.value)
    assert ep._READ_ONLY_SQL["postgresql"] == "SET default_transaction_read_only = on"
