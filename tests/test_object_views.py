"""Object VIEWs over landed bronze -- Palantir-lean, extract-only."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dms_executor.bronze import write_bronze_rows
from dms_executor.object_views import (
    ObjectViewError,
    ObjectViewSpec,
    materialize_silver_from_object,
    publish_object_views,
)


@pytest.fixture()
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "ontology_views.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    return db


def test_publish_object_view_uses_human_names(lake: Path) -> None:
    write_bronze_rows(
        table="tbl_cust_v2",
        columns=["cust_id", "nm"],
        rows=[["C1", "Acme"], ["C2", "Beta"]],
        path=lake,
    )
    con = duckdb.connect(str(lake))
    try:
        published = publish_object_views(
            con,
            [
                ObjectViewSpec(
                    name="customer",
                    relation="bronze.tbl_cust_v2",
                    columns={"customer_id": "cust_id", "customer_name": "nm"},
                )
            ],
        )
        assert published == ["objects.customer"]
        cols = [r[0] for r in con.execute("DESCRIBE SELECT * FROM objects.customer").fetchall()]
        assert "customer_id" in cols
        assert "customer_name" in cols
        assert "nm" not in cols
        rows = con.execute(
            "SELECT customer_name FROM objects.customer ORDER BY customer_id"
        ).fetchall()
        assert [r[0] for r in rows] == ["Acme", "Beta"]
    finally:
        con.close()
    arranged = materialize_silver_from_object("customer", path=lake)
    assert arranged == "silver.customer"
    con = duckdb.connect(str(lake))
    try:
        silver_cols = [
            r[0] for r in con.execute("DESCRIBE SELECT * FROM silver.customer").fetchall()
        ]
        assert silver_cols == ["customer_id", "customer_name"]
        gold_n = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'gold' AND table_name = 'customer'"
        ).fetchone()
        assert gold_n is not None and int(gold_n[0]) == 0
    finally:
        con.close()


def test_object_view_refuses_raw_mysql_relation() -> None:
    con = duckdb.connect(":memory:")
    try:
        with pytest.raises(ObjectViewError, match="bronze|silver|gold"):
            publish_object_views(
                con,
                [
                    ObjectViewSpec(
                        name="customer",
                        relation="mysql.orders",
                        columns={"id": "id"},
                    )
                ],
            )
    finally:
        con.close()


def test_publish_allows_digit_prefixed_bronze_table(lake: Path) -> None:
    write_bronze_rows(
        table="01_clean_sales",
        columns=["sku", "qty"],
        rows=[["A", "1"], ["B", "2"]],
        path=lake,
    )
    con = duckdb.connect(str(lake))
    try:
        published = publish_object_views(
            con,
            [
                ObjectViewSpec(
                    name="clean_sale",
                    relation="bronze.01_clean_sales",
                    columns={"sku": "sku", "qty": "qty"},
                )
            ],
        )
        assert published == ["objects.clean_sale"]
        n = con.execute("SELECT COUNT(*) FROM objects.clean_sale").fetchone()
        assert n is not None and int(n[0]) == 2
    finally:
        con.close()
    assert materialize_silver_from_object("clean_sale", path=lake) == "silver.clean_sale"

