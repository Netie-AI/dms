"""Case schema, loader and extension-SQL guard. A malformed case or an out-of-bounds
extension must fail at load/build time, loudly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from rt_cases import CaseError, ext_sql_path_for, load_cases, validate_case
from rt_data import (
    ExtensionSqlError,
    build_family_db,
    check_extension_sql,
    split_sql_statements,
    table_counts,
)

SAMPLE = Path(__file__).with_name("sample_cases.yaml")


def _case(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "A-001",
        "family": "a",
        "question": "How many SKUs?",
        "model_sql": "SELECT COUNT(DISTINCT sku) AS n FROM inventory",
        "expect": "answer",
        "gold_sql": "SELECT COUNT(DISTINCT sku) AS n FROM inventory",
    }
    base.update(over)
    return base


def test_sample_file_loads_three_cases() -> None:
    cases = load_cases(SAMPLE)
    assert [c.id for c in cases] == ["B-001", "B-002", "D-001"]
    assert cases[0].control and not cases[1].control
    assert (
        cases[2].lane == "sheet" and cases[2].ask_path == "product" and cases[2].model_sql is None
    )
    assert [c.id for c in load_cases(SAMPLE, family="b")] == ["B-001", "B-002"]


def test_valid_case_gets_defaults() -> None:
    c = validate_case(_case())
    assert (c.lane, c.space, c.ask_path, c.control) == ("gen", "finance", "generative", False)
    assert c.space_id == "cccccccc-cccc-cccc-cccc-cccccccccccc"
    assert validate_case(_case(space="company")).space_id is None


@pytest.mark.parametrize(
    "over,needle",
    [
        ({"surprise": 1}, "unknown key"),
        ({"id": "Z-001"}, "id must look like"),
        ({"id": "A-1"}, "id must look like"),
        ({"id": "B-001"}, "does not match family"),
        ({"family": "z", "id": "A-001"}, "family must be one of"),
        ({"question": "  "}, "question must be"),
        ({"lane": "wat"}, "lane must be"),
        ({"space": "hr"}, "space must be"),
        ({"expect": "maybe"}, "expect must be"),
        ({"ask_path": "exact"}, "ask_path must be"),
        ({"model_sql": None}, "requires a non-empty model_sql"),
        ({"model_sql": "DROP TABLE inventory"}, "must start with SELECT or WITH"),
        ({"gold_sql": None}, "requires gold_sql"),
        ({"gold_sql": "DELETE FROM inventory"}, "gold_sql must be a SELECT"),
        ({"grounded_tables": ["bronze.x"]}, "lane sheet only"),
        ({"control": "yes"}, "control must be true or false"),
        ({"control": True, "expect": "abstain", "gold_sql": None}, "control"),
        ({"trap": 5}, "trap must be a string"),
    ],
)
def test_bad_cases_are_rejected(over: dict[str, Any], needle: str) -> None:
    with pytest.raises(CaseError, match=needle):
        validate_case(_case(**over))


def test_sheet_lane_rules() -> None:
    sheet = _case(id="D-001", family="d", lane="sheet", model_sql=None, ask_path=None)
    sheet.pop("ask_path")
    assert validate_case(sheet).ask_path == "product"
    with pytest.raises(CaseError, match="model_sql must be absent"):
        validate_case(sheet | {"model_sql": "SELECT 1"})
    with pytest.raises(CaseError, match="product ladder only"):
        validate_case(sheet | {"ask_path": "generative"})


def test_expect_abstain_needs_no_gold() -> None:
    c = validate_case(_case(expect="abstain", gold_sql=None))
    assert c.gold_sql is None


def test_duplicate_ids_and_non_list_are_rejected(tmp_path: Path) -> None:
    f = tmp_path / "a.yaml"
    f.write_text(
        "- id: A-001\n  family: a\n  question: q\n  expect: abstain\n  model_sql: SELECT 1\n"
        "- id: A-001\n  family: a\n  question: q\n  expect: abstain\n  model_sql: SELECT 1\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseError, match="duplicate case id"):
        load_cases(f)
    g = tmp_path / "b.yaml"
    g.write_text("id: A-001\n", encoding="utf-8")
    with pytest.raises(CaseError, match="top level must be a YAML list"):
        load_cases(g)
    with pytest.raises(CaseError, match="does not exist"):
        load_cases(tmp_path / "nope")


def test_ext_sql_path_is_found_next_to_the_family_file(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text("[]", encoding="utf-8")
    assert ext_sql_path_for(tmp_path, "a") is None
    (tmp_path / "a.ext.sql").write_text("-- none", encoding="utf-8")
    assert ext_sql_path_for(tmp_path, "a") == tmp_path / "a.ext.sql"
    assert ext_sql_path_for(tmp_path / "a.yaml", "a") == tmp_path / "a.ext.sql"


# ------------------------------------------------------------------ extension SQL guard
def test_split_statements_respects_quotes_and_comments() -> None:
    sql = (
        "-- c\nINSERT INTO alerts VALUES ('A;1','x');  -- tail\nUPDATE alerts SET resolved = TRUE;"
    )
    assert split_sql_statements(sql) == [
        "INSERT INTO alerts VALUES ('A;1','x')",
        "UPDATE alerts SET resolved = TRUE",
    ]


GOOD = [
    "INSERT INTO inventory VALUES ('SKU-Z','WH-A',1,1,1,'SUP-01','RAW',NULL)",
    "UPDATE transactions SET unit_cost_myr = 5.0 WHERE txn_id = 'T001'",
    "DELETE FROM alerts WHERE alert_id = 'AL-1'",
    "ALTER TABLE shipments ADD COLUMN carrier VARCHAR",
    "INSERT INTO shipments SELECT 'SH-9', sku, 'WH-A', 1, 'x', 1 FROM inventory LIMIT 1",
]


@pytest.mark.parametrize("stmt", GOOD)
def test_extension_allows_dml_on_the_six_tables(stmt: str) -> None:
    check_extension_sql([stmt])


@pytest.mark.parametrize(
    "stmt",
    [
        "CREATE TABLE extra (a INT)",
        "DROP TABLE inventory",
        "INSERT INTO meta VALUES ('k','v')",
        "INSERT INTO bronze.sales VALUES (1)",
        "INSERT INTO main.inventory VALUES (1)",
        "INSERT INTO sales VALUES (1)",
        "UPDATE inventory SET quantity_kg = 1 FROM customers",
        "INSERT INTO inventory SELECT * FROM read_csv('C:/x.csv')",
        "COPY inventory FROM 'x.csv'",
        "ATTACH 'x.duckdb' AS x",
        "SELECT 1",
        "PRAGMA database_list",
        "INSERT INTO inventory VALUES (1) ; DROP TABLE alerts",
        "INSERT INTO shipments SELECT 1 FROM glob('*')",
    ],
)
def test_extension_rejects_everything_else(stmt: str) -> None:
    with pytest.raises(ExtensionSqlError):
        check_extension_sql(split_sql_statements(stmt))


def test_bad_extension_fails_the_build_before_anything_is_applied(tmp_path: Path) -> None:
    ext = tmp_path / "x.ext.sql"
    ext.write_text(
        "INSERT INTO inventory VALUES ('SKU-Z','WH-A',1,1,1,'SUP-01','RAW',NULL);\n"
        "CREATE TABLE sneaky (a INT);\n",
        encoding="utf-8",
    )
    with pytest.raises(ExtensionSqlError, match="CREATE|blocked"):
        build_family_db("a", ext, tmp_path / "db")


def test_missing_extension_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(ExtensionSqlError, match="not found"):
        build_family_db("a", tmp_path / "nope.sql", tmp_path / "db")


def test_runtime_failure_in_an_extension_names_the_statement(tmp_path: Path) -> None:
    ext = tmp_path / "x.ext.sql"
    ext.write_text("INSERT INTO inventory VALUES ('only','two');\n", encoding="utf-8")
    with pytest.raises(ExtensionSqlError, match="statement 1 failed"):
        build_family_db("a", ext, tmp_path / "db")


def test_three_copies_are_equal_and_gold_is_hashed(tmp_path: Path) -> None:
    ext = tmp_path / "a.ext.sql"
    ext.write_text(
        "INSERT INTO inventory VALUES ('SKU-ZETA','WH-A',10,5,1.5,'SUP-01','RAW',NULL);\n"
        "INSERT INTO suppliers VALUES ('SUP-05','Zeta Co','MY',3,0.1,'2026-01-01');\n",
        encoding="utf-8",
    )
    db = build_family_db("a", ext, tmp_path / "db")
    assert db.ext_statements == 2
    assert (
        table_counts(db.dms) == table_counts(db.cortex) == table_counts(db.gold) == db.table_counts
    )
    assert db.table_counts["inventory"] == 8 and db.table_counts["suppliers"] == 5
    assert len(db.gold_sha256) == 64
    assert len({db.dms, db.cortex, db.gold}) == 3
