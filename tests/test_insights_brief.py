"""The insight miner must report only what conserves, and the deck must carry only what was mined.

Two ways this layer could lie, and each gets a test that proves the lie is caught:

  * an insight whose grouped total does not equal the raw total is the fan-out
    this whole stack exists to make unreachable. mine() must refuse to report
    it and must fail the run, not drop it quietly;
  * a slide that carries a figure the report does not is a retyped number, the
    one artifact a buyer actually holds. The deck is read back and every number
    on it must be present in the insights JSON it was generated from.

Fixtures are built here (R-0002). The lake is not needed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import insights as ins  # noqa: E402
from brief import build_html, build_pptx  # noqa: E402


@pytest.fixture()
def lake(tmp_path: Path):  # noqa: ANN201
    """Orders -> territory, with one territory carrying most of the revenue."""
    import duckdb

    c = duckdb.connect(":memory:")
    (tmp_path / "db").mkdir()
    c.execute(
        "COPY (SELECT * FROM (VALUES "
        "(1, 1, 700.0), (2, 1, 200.0), (3, 2, 50.0), (4, 3, 30.0), (5, 4, 20.0), "
        "(6, NULL, 100.0)"
        ") AS t(order_id, territory_id, amount)) TO '"
        + (tmp_path / "db" / "Sales.Orders.parquet").as_posix() + "' (FORMAT PARQUET)"
    )
    c.execute(
        "COPY (SELECT * FROM (VALUES (1, 'North', 'x'), (2, 'South', 'y'), "
        "(3, 'East', NULL), (4, 'West', NULL)"
        ") AS t(territory_id, name, note)) TO '"
        + (tmp_path / "db" / "Sales.Territory.parquet").as_posix() + "' (FORMAT PARQUET)"
    )
    manifest = {
        "database": "Fixture",
        "tables": [
            {"schema": "Sales", "table": "Orders", "declared_rows": 6, "extracted_rows": 6,
             "columns": 3, "path": "db/Sales.Orders.parquet"},
            {"schema": "Sales", "table": "Territory", "declared_rows": 4, "extracted_rows": 4,
             "columns": 3, "path": "db/Sales.Territory.parquet"},
        ],
        "skipped": [],
        "primary_keys": {"Sales.Orders": ["order_id"], "Sales.Territory": ["territory_id"]},
        "foreign_keys": [
            {"name": "FK_T", "from_table": "Sales.Orders", "from_column": "territory_id",
             "to_table": "Sales.Territory", "to_column": "territory_id"},
        ],
    }
    try:
        yield c, manifest, tmp_path
    finally:
        c.close()


def _with_measure(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(ins, "ROOT", root)
    monkeypatch.setitem(
        ins.MEASURES, "Fixture",
        [{"name": "revenue", "grain": "Sales.Orders", "expr": 'ROUND(SUM(f."amount"), 2)',
          "unit": "USD", "is": "order amount", "is_not": "not net of anything"}],
    )


def test_concentration_is_reported_with_a_conserving_total(lake, monkeypatch) -> None:  # noqa: ANN001
    c, manifest, root = lake
    _with_measure(monkeypatch, root)
    report = ins.mine(c, manifest, top=5)
    assert "error" not in report
    kinds = {i["kind"] for i in report["insights"]}
    assert "dominance" in kinds or "concentration" in kinds
    for i in report["insights"]:
        assert i["conserves"]
        assert abs(sum(v for _, v in i["rows"]) - i["total"]) <= 0.02 * len(i["rows"])
        assert i["sql"].strip().upper().startswith("SELECT"), "every insight carries its SQL"


def test_an_unknown_bucket_means_a_used_dimension_missing_on_the_measure(
    lake, monkeypatch,  # noqa: ANN001
) -> None:
    """100 of 1100 lands on an order with no territory: 9.1 pct, reported."""
    c, manifest, root = lake
    _with_measure(monkeypatch, root)
    report = ins.mine(c, manifest, top=10)
    unknown = [i for i in report["insights"] if i["kind"] == "unknown_bucket"]
    assert unknown, "the NULL-territory share must be surfaced as a data gap"
    assert "9.1 pct" in unknown[0]["headline"]


def test_a_mostly_empty_column_is_not_a_dimension(lake, monkeypatch) -> None:  # noqa: ANN001
    """Territory.note is NULL on half the rows: an annotation, not a segmentation.

    The first run of the miner reported that 95 pct of sales landed on rows a
    mostly-NULL Suffix column could not label. That is not an insight.
    """
    c, manifest, root = lake
    _with_measure(monkeypatch, root)
    rel = "read_parquet('" + (root / "db" / "Sales.Territory.parquet").as_posix() + "')"
    assert ins._label_attrs(c, rel) == ["name"]


def test_a_long_text_column_is_not_a_dimension(tmp_path: Path) -> None:
    import duckdb

    c = duckdb.connect(":memory:")
    try:
        c.execute("CREATE TABLE t AS SELECT i AS id, repeat('x', 200) || i::VARCHAR AS blob, "
                  "'A' AS tag FROM range(1, 10) r(i)")
        assert ins._label_attrs(c, "t") == []  # tag has 1 distinct; blob is too long
        c.execute("UPDATE t SET tag = 'B' WHERE id > 5")
        assert ins._label_attrs(c, "t") == ["tag"]
    finally:
        c.close()


def test_a_non_conserving_grouping_fails_the_run_rather_than_being_dropped(
    lake, monkeypatch,  # noqa: ANN001
) -> None:
    """Force a fan-out past the compiler and prove mine() refuses to report it.

    The compiler itself cannot produce this, so the test swaps in a compile that
    returns an inflating join. If mine() ever reports such a grouping, or hides
    it, the control is decoration.
    """
    c, manifest, root = lake
    _with_measure(monkeypatch, root)
    from ontology import CompiledQuery, Ontology

    orders = (root / "db" / "Sales.Orders.parquet").as_posix()
    real_compile = Ontology.compile

    def inflating(self, measure, **kw):  # noqa: ANN001, ANN202
        got = real_compile(self, measure, **kw)
        if isinstance(got, CompiledQuery) and kw.get("group_by"):
            # a self-join that doubles every row
            sql = (f'SELECT d0."name" AS label, ROUND(SUM(f."amount"), 2) AS revenue '
                   f"FROM read_parquet('{orders}') f "
                   f"LEFT JOIN (SELECT t.territory_id, t.name FROM read_parquet('"
                   + (root / "db" / "Sales.Territory.parquet").as_posix()
                   + "') t UNION ALL SELECT t.territory_id, t.name FROM read_parquet('"
                   + (root / "db" / "Sales.Territory.parquet").as_posix()
                   + "') t) d0 ON f.territory_id = d0.territory_id "
                   'GROUP BY d0."name"')
            return CompiledQuery(sql=sql, measure=got.measure, grain=got.grain,
                                 group_by=got.group_by, notes=got.notes)
        return got

    monkeypatch.setattr(Ontology, "compile", inflating)
    report = ins.mine(c, manifest, top=5)
    assert report["broken"], "the inflated grouping must be named as broken"
    assert not [i for i in report["insights"] if i["dimension"].endswith("name")], (
        "an inflated grouping was reported as an insight"
    )


# --------------------------------------------------------------------------
# the deck carries the report's figures and nothing else
# --------------------------------------------------------------------------


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in re.findall(r"\d[\d,]*\.\d{2}", text)}


def test_every_figure_on_the_deck_is_in_the_report(lake, monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    from pptx import Presentation

    c, manifest, root = lake
    _with_measure(monkeypatch, root)
    report = ins.mine(c, manifest, top=5)
    out = tmp_path / "brief.pptx"
    n = build_pptx(report, out)
    assert n == len(report["insights"])

    allowed: set[str] = set()
    for i in report["insights"]:
        allowed |= {f"{v:.2f}" for _, v in i["rows"]}
        allowed |= {f"{i['total']:.2f}", f"{i['top_share'] * 100:.1f}"}
        allowed |= _numbers(json.dumps(i["rows"]))
    prs = Presentation(str(out))
    seen: set[str] = set()
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                seen |= _numbers(shape.text_frame.text)
            if shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        seen |= _numbers(cell.text)
    stray = seen - allowed
    assert not stray, f"figures on the deck that are not in the report: {sorted(stray)}"
    assert seen, "the deck carried no figures at all"


def test_the_html_brief_carries_the_sql_behind_each_figure(lake, monkeypatch) -> None:  # noqa: ANN001
    c, manifest, root = lake
    _with_measure(monkeypatch, root)
    report = ins.mine(c, manifest, top=5)
    page = build_html(report)
    for i in report["insights"]:
        assert i["headline"] in page or i["headline"].replace("'", "&#x27;") in page
        assert "<pre>" in page and "SELECT" in page
    assert "what the data says, and how we know" in page
