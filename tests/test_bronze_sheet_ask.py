"""Uniquely scoped xlsx+sheet top-N must hit bronze, not demo transactions."""

from __future__ import annotations

from pathlib import Path

import duckdb
from dms_executor.bronze import bronze_table_for_sheet
from dms_executor.bronze_sheet_ask import maybe_bronze_sheet_ask
from dms_executor.envelope import assert_envelope_valid
from dms_executor.lake_schema import ensure_lake_schemas


def _seed(path: Path) -> str:
    ident = bronze_table_for_sheet("cf98e431_p50_01_sales_messy.xlsx", "Sales").split(
        ".", 1
    )[-1]
    con = duckdb.connect(str(path))
    try:
        ensure_lake_schemas(con)
        con.execute('CREATE SCHEMA IF NOT EXISTS bronze')
        con.execute(
            f'CREATE TABLE bronze."{ident}" (category VARCHAR, sales_value_myr DOUBLE)'
        )
        con.execute(
            f"""
            INSERT INTO bronze."{ident}" VALUES
              ('Electronics', 1545366.40),
              ('Home', 1199018.49),
              ('Sports', 400000.00),
              ('Misc', 100.00)
            """
        )
    finally:
        con.close()
    return ident


def test_scoped_top3_matches_seeded_bronze(tmp_path: Path) -> None:
    db = tmp_path / "wh.duckdb"
    _seed(db)
    env = maybe_bronze_sheet_ask(
        "In cf98e431_p50_01_sales_messy.xlsx sheet Sales, what are the top 3 "
        "categories by sales_value_myr?",
        warehouse=db,
        space_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
    )
    assert env is not None
    assert env["badge"] == "L0_CERTIFIED"
    assert env["abstained"] is False
    cats = [r["category"] for r in env["rows"]]
    assert cats == ["Electronics", "Home", "Sports"]
    assert env["rows"][0]["sales_value_myr"] == 1545366.40
    assert_envelope_valid(env)


def test_no_sql_invite_is_not_intercepted(tmp_path: Path) -> None:
    db = tmp_path / "wh.duckdb"
    _seed(db)
    env = maybe_bronze_sheet_ask(
        "From the documents about aa64458a_p50_03_inventory_messy.xlsx Wide_Fill, "
        "summarize the top 3 category sales totals without running warehouse SQL.",
        warehouse=db,
    )
    assert env is None


def test_unscoped_ask_falls_through(tmp_path: Path) -> None:
    db = tmp_path / "wh.duckdb"
    _seed(db)
    assert maybe_bronze_sheet_ask("What is total stock value by category?", warehouse=db) is None


def _seed_encoding(path: Path) -> str:
    ident = bronze_table_for_sheet("encoding_value_norm.xlsx", "Sales").split(".", 1)[-1]
    con = duckdb.connect(str(path))
    try:
        ensure_lake_schemas(con)
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute(
            f'CREATE TABLE bronze."{ident}" '
            "(sku VARCHAR, city VARCHAR, sales_value_myr DOUBLE)"
        )
        con.execute(
            f"""
            INSERT INTO bronze."{ident}" VALUES
              ('SKU-BETA', 'Kuala Lumpur', 1500.75),
              ('SKU-ALPHA', 'Kuala Lumpur', 200.00),
              ('SKU-GAMMA', 'Johor Bahru', 900.00)
            """
        )
    finally:
        con.close()
    return ident


def test_exact_sku_beta_and_unique_bare_beta_certify(tmp_path: Path) -> None:
    db = tmp_path / "enc.duckdb"
    _seed_encoding(db)
    hit = maybe_bronze_sheet_ask(
        "In encoding_value_norm.xlsx sheet Sales, what is total sales_value_myr "
        "for sku SKU-BETA?",
        warehouse=db,
    )
    assert hit is not None
    assert hit["badge"] == "L0_CERTIFIED"
    assert hit["abstained"] is False
    assert hit["rows"][0]["sku"] == "SKU-BETA"
    assert hit["rows"][0]["sales_value_myr"] == 1500.75
    assert_envelope_valid(hit)

    mapped = maybe_bronze_sheet_ask(
        "In encoding_value_norm.xlsx sheet Sales, what is total sales_value_myr "
        "for sku BETA?",
        warehouse=db,
    )
    assert mapped is not None
    assert mapped["abstained"] is False
    assert mapped["badge"] == "L0_CERTIFIED"
    assert mapped["rows"][0]["sku"] == "SKU-BETA"
    assert mapped["rows"][0]["sales_value_myr"] == 1500.75
    assert any("unique landed encoding" in a for a in mapped["assumptions"])
    assert_envelope_valid(mapped)


def test_exact_kuala_lumpur_and_unique_kl_certify(tmp_path: Path) -> None:
    db = tmp_path / "enc.duckdb"
    _seed_encoding(db)
    hit = maybe_bronze_sheet_ask(
        "In encoding_value_norm.xlsx sheet Sales, total sales_value_myr "
        "for city Kuala Lumpur?",
        warehouse=db,
    )
    assert hit is not None
    assert hit["badge"] == "L0_CERTIFIED"
    assert hit["rows"][0]["city"] == "Kuala Lumpur"
    assert hit["rows"][0]["sales_value_myr"] == 1700.75
    assert_envelope_valid(hit)

    mapped = maybe_bronze_sheet_ask(
        "In encoding_value_norm.xlsx sheet Sales, total sales_value_myr for city KL?",
        warehouse=db,
    )
    assert mapped is not None
    assert mapped["abstained"] is False
    assert mapped["badge"] == "L0_CERTIFIED"
    assert mapped["rows"][0]["city"] == "Kuala Lumpur"
    assert mapped["rows"][0]["sales_value_myr"] == 1700.75
    assert_envelope_valid(mapped)


def test_ambiguous_beta_still_abstains(tmp_path: Path) -> None:
    ident = bronze_table_for_sheet("encoding_value_norm.xlsx", "Sales").split(".", 1)[-1]
    db = tmp_path / "amb.duckdb"
    con = duckdb.connect(str(db))
    try:
        ensure_lake_schemas(con)
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute(
            f'CREATE TABLE bronze."{ident}" '
            "(sku VARCHAR, city VARCHAR, sales_value_myr DOUBLE)"
        )
        con.execute(
            f"""
            INSERT INTO bronze."{ident}" VALUES
              ('SKU-BETA', 'Kuala Lumpur', 1500.75),
              ('BETA-PRO', 'Johor Bahru', 90.00)
            """
        )
    finally:
        con.close()
    miss = maybe_bronze_sheet_ask(
        "In encoding_value_norm.xlsx sheet Sales, what is total sales_value_myr "
        "for sku BETA?",
        warehouse=db,
    )
    assert miss is not None
    assert miss["abstained"] is True
    assert miss["badge"] == "ABSTAIN"
    assert not miss["rows"]
    assert "1500.75" not in (miss["text"] or "")
    assert_envelope_valid(miss)


def test_resolve_unique_encoding_word_and_initials() -> None:
    from dms_executor.bronze_sheet_ask import resolve_unique_encoding

    assert resolve_unique_encoding("BETA", ["SKU-BETA", "SKU-ALPHA"]) == "SKU-BETA"
    assert resolve_unique_encoding("KL", ["Kuala Lumpur", "Johor Bahru"]) == "Kuala Lumpur"
    assert resolve_unique_encoding("BETA", ["SKU-BETA", "BETA-PRO"]) is None
    assert resolve_unique_encoding("crop", ["Crop Insurance Services"]) is None
    assert resolve_unique_encoding("BETA", ["SKU-BETA"]) == "SKU-BETA"


def test_malay_and_synonym_and_sales_only_scope(tmp_path: Path) -> None:
    db = tmp_path / "wh.duckdb"
    _seed(db)
    malay = maybe_bronze_sheet_ask(
        "Dalam fail cf98e431_p50_01_sales_messy.xlsx helaian Sales, apakah 3 "
        "kategori teratas mengikut sales_value_myr?",
        warehouse=db,
    )
    syn = maybe_bronze_sheet_ask(
        "In cf98e431_p50_01_sales_messy.xlsx sheet Sales, top 3 product families "
        "by MYR sales (cat / product line synonym for category)?",
        warehouse=db,
    )
    only = maybe_bronze_sheet_ask(
        "Using cf98e431_p50_01_sales_messy.xlsx, on the Sales sheet only "
        "(ignore Wide_Fill), what are the top 3 categories by sales_value_myr?",
        warehouse=db,
    )
    for env in (malay, syn, only):
        assert env is not None
        assert [r["category"] for r in env["rows"]] == ["Electronics", "Home", "Sports"]


def test_multi_table_first_band_ingest_answers_hostile_sales(tmp_path: Path) -> None:
    """Messy Sales is MULTI_TABLE; first header band must still land in bronze."""
    from dms_executor.batch_ingest import ingest_batch

    docs = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "hostile_score"
    xlsx = docs / "cf98e431_p50_01_sales_messy.xlsx"
    db = tmp_path / "ingest.duckdb"
    receipt = ingest_batch(
        [(xlsx.name, xlsx.read_bytes())],
        path=db,
        space_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
    )
    sales = bronze_table_for_sheet(xlsx.name, "Sales")
    tables = {f.table for f in receipt.files if f.table}
    assert sales in tables
    env = maybe_bronze_sheet_ask(
        "In cf98e431_p50_01_sales_messy.xlsx sheet Sales, what are the top 3 "
        "categories by sales_value_myr?",
        warehouse=db,
    )
    assert env is not None
    assert [r["category"] for r in env["rows"]] == ["Electronics", "Home", "Sports"]
    assert abs(env["rows"][0]["sales_value_myr"] - 1545366.40) < 0.02


def test_blank_hanging_sheet_keeps_rows_after_blank_band(tmp_path: Path) -> None:
    from dms_executor.batch_ingest import ingest_batch

    xlsx = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "hostile_score"
        / "blank_rows_hanging.xlsx"
    )
    db = tmp_path / "hang.duckdb"
    ingest_batch([(xlsx.name, xlsx.read_bytes())], path=db)
    env = maybe_bronze_sheet_ask(
        "In blank_rows_hanging.xlsx sheet Sales, what are the top 3 categories "
        "by sales_value_myr?",
        warehouse=db,
    )
    assert env is not None
    got = [(r["category"], round(r["sales_value_myr"], 2)) for r in env["rows"]]
    assert got == [("Electronics", 1500.75), ("Home", 899.45), ("Sports", 300.00)]


def test_stacked_multi_table_csv_does_not_union_second_region(tmp_path: Path) -> None:
    from dms_executor.batch_ingest import ingest_batch

    csv_path = (
        Path(__file__).resolve().parents[1]
        / "tests"
        / "fixtures"
        / "ingest"
        / "03_two_tables_stacked.csv"
    )
    db = tmp_path / "stack.duckdb"
    receipt = ingest_batch([(csv_path.name, csv_path.read_bytes())], path=db)
    assert receipt.ingested == 1
    table = next(f.table for f in receipt.files if f.table)
    ident = table.split(".", 1)[-1]
    con = duckdb.connect(str(db), read_only=True)
    try:
        cols = [str(r[0]).lower() for r in con.execute(f'DESCRIBE bronze."{ident}"').fetchall()]
        blob = " ".join(
            str(x) for row in con.execute(f'SELECT * FROM bronze."{ident}"').fetchall() for x in row
        )
    finally:
        con.close()
    assert "region" not in cols
    assert "North" not in blob
    assert "South" not in blob


def test_hostile_pack_bronze_ceiling_unique_encoding(tmp_path: Path) -> None:
    """Scoped intercepts stay 0 WRONG. Unique encodings certify. F32/RAG miss."""
    import sys

    from dms_executor.batch_ingest import ingest_batch

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    from score_answers import QUESTION_PACK, judge, resolve_oracle

    docs = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "hostile_score"
    db = tmp_path / "hostile.duckdb"
    seen: set[str] = set()
    files: list[tuple[str, bytes]] = []
    for case in QUESTION_PACK:
        name = str(case["workbook"])
        if name in seen:
            continue
        seen.add(name)
        files.append((name, (docs / name).read_bytes()))
    ingest_batch(files, path=db)

    must_miss = {"rag_sum_invite_no_sql", "f32_ambiguous_categoty_top3"}
    must_correct = {
        "empty_filter_beta_vs_sku_beta",
        "empty_filter_kl_vs_kuala_lumpur",
    }
    wrong: list[str] = []
    answered = 0
    for case in QUESTION_PACK:
        cid = str(case["id"])
        env = maybe_bronze_sheet_ask(str(case["question"]), warehouse=db)
        if cid in must_miss:
            assert env is None, cid
            continue
        assert env is not None, cid
        expected = resolve_oracle(case, docs / str(case["workbook"]))
        outcome, detail = judge(env, expected)
        if outcome == "WRONG":
            wrong.append(f"{cid}: {detail}")
        if outcome == "correct":
            answered += 1
        if cid in must_correct:
            assert outcome == "correct", (cid, detail)
    assert not wrong
    assert answered == len(QUESTION_PACK) - len(must_miss)


def test_named_sheet_survives_tools_only_space(tmp_path: Path) -> None:
    from dms_executor import Executor
    from dms_executor.demo_warehouse import ensure_demo_warehouse
    from dms_executor.metric_tools import register_metric_tool

    db = tmp_path / "tools_enc.duckdb"
    ensure_demo_warehouse(db)
    _seed_encoding(db)
    space = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    register_metric_tool(
        name="order_count",
        sql="SELECT 1 AS row_count",
        description="Row count.",
        space_id=space,
        warehouse=db,
    )

    class _NoCortex:
        def ask(self, req: object) -> None:
            raise AssertionError("named-sheet ask must not reach Cortex")

    exe = Executor(cortex=_NoCortex(), warehouse_path=db)  # type: ignore[arg-type]
    env = exe.live_ask(
        "In encoding_value_norm.xlsx sheet Sales, what is total sales_value_myr "
        "for sku BETA?",
        space_id=space,
        session_id="ses_enc_tools",
    )
    assert_envelope_valid(env)
    assert env["badge"] == "L0_CERTIFIED"
    assert env["abstained"] is False
    assert env["rows"][0]["sku"] == "SKU-BETA"
    assert env["rows"][0]["sales_value_myr"] == 1500.75
