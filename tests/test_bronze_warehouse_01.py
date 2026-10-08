"""BRONZE-WAREHOUSE-01 / dms#303: the executor file is the bronze grant file.

One split layout. The bronze table lives only in the ingest file
(DMS_WAREHOUSE_DB), registered to Finance. CORTEX_WAREHOUSE_DB is a
separate serving file with no copy of that table. The question is asked
from Finance through Executor.live_ask.

| executor warehouse_path | in grantable_tables | on f0e6c61f |
| ingest file             | yes                 | L0 42.5     |
| none (default)          | yes                 | L0 42.5     |
| serving file            | no                  | ABSTAIN     |

The serving-file row answers L0 42.5 on parent 9a7be856 and fails there
on the abstain assert. ingest-file and default-none pass on both commits.
No keys. No live scored round.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.models import AskResponse
from dms_executor import Executor
from dms_executor.bronze import _ensure_registry, bronze_table_for_sheet
from dms_executor.envelope import assert_envelope_valid
from dms_executor.lake_schema import ensure_lake_schemas

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
FILENAME = "granted.xlsx"


class _Stub:
    """Cortex stand-in. The bronze lane returns before ask()."""

    def submit(self, _req: Any) -> Any:
        from cortex_contract.execution import QueryResult

        return QueryResult(ok=True, status="bound", run_id="run-bronze-warehouse")

    def ask(self, _req: Any) -> AskResponse:
        return AskResponse(
            answer="I cannot answer that here.",
            abstained=True,
            badge="abstain",
            rows=[],
            route="abstain",
            audit_id="aud-stub",
        )


def _question(filename: str) -> str:
    return (
        f"In {filename} sheet Sales, what are the top 3 "
        "categories by sales_value_myr?"
    )


def _seed_ingest(path: Path, filename: str, *, space_id: str, amount: float) -> str:
    table = bronze_table_for_sheet(filename, "Sales")
    ident = table.split(".", 1)[-1]
    con = duckdb.connect(str(path))
    try:
        ensure_lake_schemas(con)
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute(
            f'CREATE TABLE bronze."{ident}" (category VARCHAR, sales_value_myr DOUBLE)'
        )
        con.execute(
            f'INSERT INTO bronze."{ident}" VALUES '
            "('Electronics', ?), ('Home', 20.0), ('Sports', 5.0)",
            [amount],
        )
        _ensure_registry(con)
        con.execute(
            """
            INSERT INTO bronze._ingest_registry
              (table_name, filename, sha256, ingest_id, created_at, space_id)
            VALUES (?, ?, 'abc', 'ing-bronze-warehouse', now(), ?)
            """,
            [ident, filename, space_id],
        )
    finally:
        con.close()
    return table


def _split(tmp_path: Path) -> tuple[Path, Path, str]:
    """Ingest holds the Finance bronze table. Serving is a different file."""
    ingest = tmp_path / "ingest.duckdb"
    serving = tmp_path / "serving.duckdb"
    table = _seed_ingest(ingest, FILENAME, space_id=FINANCE, amount=42.5)
    duckdb.connect(str(serving)).close()
    return ingest, serving, table


def _point_split(monkeypatch: pytest.MonkeyPatch, ingest: Path, serving: Path) -> None:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(ingest))
    monkeypatch.setenv("CORTEX_WAREHOUSE_DB", str(serving))
    monkeypatch.delenv("DMS_ORACLE_WAREHOUSE", raising=False)
    monkeypatch.delenv("CORTEX_HOME", raising=False)
    monkeypatch.delenv("DMS_CCA_CASCADE", raising=False)


def _top_value(env: dict[str, Any]) -> Any:
    rows = env.get("rows") or []
    if rows and isinstance(rows[0], dict):
        return rows[0].get("sales_value_myr")
    return None


def _answered_l0(env: dict[str, Any], *, which: str) -> None:
    badge = env.get("badge")
    assert badge == "L0_CERTIFIED" and env.get("abstained") is False, (
        f"{which}: expected L0_CERTIFIED 42.5; got badge={badge!r} "
        f"value={_top_value(env)!r} text={env.get('text')!r}"
    )
    rows = env.get("rows") or []
    assert rows and rows[0].get("category") == "Electronics"
    assert rows[0].get("sales_value_myr") == 42.5
    assert "42.5" in str(env.get("text") or "")
    numbers = [
        float(item["value"])
        for item in (env.get("values") or [])
        if isinstance(item, dict)
        and isinstance(item.get("value"), (int, float))
        and not isinstance(item.get("value"), bool)
    ]
    assert 42.5 in numbers
    assert_envelope_valid(env)


def _abstained_ungranted(env: dict[str, Any], table: str) -> None:
    reason = f"ungranted_table:{table}"
    badge = env.get("badge")
    text = str(env.get("text") or "")
    values = env.get("values")
    rows = env.get("rows")
    ok = (
        badge == "ABSTAIN"
        and reason in text
        and not values
        and not rows
        and env.get("abstained") is True
    )
    assert ok, (
        f"serving file: expected ABSTAIN {reason} and no values; "
        f"got badge={badge!r} value={_top_value(env)!r} "
        f"text={text!r} values={values!r}"
    )
    assert_envelope_valid(env)


@pytest.mark.parametrize(
    ("which", "in_grantable", "kind"),
    [
        ("ingest", True, "answer"),
        ("default", True, "answer"),
        ("serving", False, "abstain"),
    ],
    ids=["ingest-file", "default-none", "serving-file"],
)
def test_finance_bronze_ask_follows_executor_warehouse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    which: str,
    in_grantable: bool,
    kind: str,
) -> None:
    """Three rows, one split. Membership is column 2. Outcome is column 3."""
    monkeypatch.setenv("DMS_LANE_BRONZE_SHEET", "1")
    ingest, serving, table = _split(tmp_path)
    assert table == "bronze.granted_Sales"
    _point_split(monkeypatch, ingest, serving)
    warehouse: Path | None = {"ingest": ingest, "default": None, "serving": serving}[which]
    exe = Executor(cortex=_Stub(), warehouse_path=warehouse)  # type: ignore[arg-type]
    held = exe._warehouse  # noqa: SLF001 - the row is which file the executor holds
    assert held == warehouse
    member = table in exe.grantable_tables(space_id=FINANCE)
    assert member is in_grantable
    env = exe.live_ask(
        _question(FILENAME),
        space_id=FINANCE,
        session_id="ses_bronze_warehouse",
    )
    if kind == "answer":
        _answered_l0(env, which=which)
    else:
        _abstained_ungranted(env, table)
