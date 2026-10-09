"""Schema index and extract SQL share one DuckDB attach.

A read_only connect beside the serving attach raises ConnectionException
on DuckDB 1.5, so the index fails and the loop never runs the model's SQL.
"""

from __future__ import annotations

import threading
from pathlib import Path

from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse
from dms_executor.ontology import _table_columns
from dms_executor.schema_context import build_space_index
from dms_executor.sql_loop import run_readonly

_GRANTS = {
    "inventory",
    "locations",
    "suppliers",
    "transactions",
    "shipments",
    "alerts",
}


def test_index_and_column_read_share_the_serving_file(tmp_path: Path) -> None:
    db = ensure_demo_warehouse(tmp_path / "serving.duckdb")
    stamps: dict[str, str] = {}
    cols: dict[str, dict[str, set[str]]] = {}

    def _build() -> None:
        stamps["v"] = build_space_index(db, "sp-attach", _GRANTS, "duckdb")

    def _columns() -> None:
        cols["v"] = _table_columns(db)

    build = threading.Thread(target=_build)
    read = threading.Thread(target=_columns)
    build.start()
    read.start()
    build.join(15)
    read.join(15)
    assert stamps["v"] == ""
    assert "sku" in cols["v"]["inventory"]


def test_extract_waits_instead_of_a_second_access_mode(tmp_path: Path) -> None:
    db = ensure_demo_warehouse(tmp_path / "extract.duckdb")
    started = threading.Event()
    entered = threading.Event()
    release = threading.Event()
    result: dict[str, object] = {}

    def _hold() -> None:
        con = connect_file(db)
        started.set()
        assert release.wait(5)
        con.close()

    def _run() -> None:
        entered.set()
        rows, err = run_readonly("SELECT COUNT(*) AS n FROM inventory", db)
        result["rows"] = rows
        result["err"] = err

    holder = threading.Thread(target=_hold)
    holder.start()
    assert started.wait(5)
    worker = threading.Thread(target=_run)
    worker.start()
    assert entered.wait(5)
    worker.join(0.4)
    assert worker.is_alive()
    release.set()
    worker.join(10)
    holder.join(5)
    assert result["err"] is None
    rows = result["rows"]
    assert isinstance(rows, list) and rows[0]["n"] == 7
