"""Scripted per-lever delta. Not a live model and not a PASS.

The plain control is one SQL sample. Each lever is that control plus one
flag. A generic Yes is not a lever: it must not move the control.

#612's pack needs a tenant source and a live completion. This fixture is the
offline stand-in. It grades rows with the same equality idea as that pack:
the served rows either match the gold rows or they do not.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[2]
for rel in (
    "packages/executor",
    "packages/core",
    "packages/cortex_client",
    "packages/ledger",
    "apps/api",
):
    path = str(ROOT / rel)
    if path not in sys.path:
        sys.path.insert(0, path)

from dms_executor.ai_floor import run_floor  # noqa: E402
from dms_executor.demo_warehouse import SERVING_DIALECT, ensure_demo_warehouse  # noqa: E402
from dms_executor.learned_memory import (  # noqa: E402
    MemoryRefused,
    confirm,
)
from dms_executor.learned_memory import (  # noqa: E402
    reset as reset_memory,
)

GOLD_SQL = "SELECT COUNT(*) AS n FROM inventory"
WRONG_SQL = "SELECT COUNT(*) AS n FROM suppliers"
QUESTION = "How many inventory rows are there?"
ASKER = "ada"
SPACE = "spc_delta"


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.ok = True
        self.run_id = "run_delta"
        self.output = {"rows": rows}


class _Ledger:
    entry_id = "led_delta"
    hash = "hash_delta_not_entry"


def _same(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    def key(row: dict[str, Any]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((str(k), str(v)) for k, v in row.items()))

    return sorted(key(row) for row in left) == sorted(key(row) for row in right)


def _grade(env: dict[str, Any], gold: list[dict[str, Any]]) -> str:
    if env.get("abstained") is True or env.get("route") == "needs_clarification":
        return "clarify" if env.get("clarify") else "abstain"
    rows = env.get("rows") if isinstance(env.get("rows"), list) else []
    if _same(rows, gold):
        return "correct"
    return "wrong"


def _submit(sql: str) -> _Rows:
    if "inventory" in sql and "suppliers" not in sql:
        return _Rows([{"n": 4}])
    return _Rows([{"n": 1}])


def _ask(warehouse: Path, compute: Any) -> dict[str, Any]:
    return run_floor(
        QUESTION,
        compute=compute,
        submit=_submit,
        ledger_append=lambda _payload: _Ledger(),
        warehouse=warehouse,
        grantable={"inventory", "suppliers", "shipments"},
        dialect=SERVING_DIALECT,
        space_id=SPACE,
        session_id="ses_delta",
        asker=ASKER,
    )


def _control(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "query_sql": WRONG_SQL,
        "served_model": "ov-routed",
        "served_provider": "openvault",
    }


def _vote(catalog: dict[str, Any]) -> dict[str, Any]:
    temp = catalog.get("sample_temperature")
    if not isinstance(temp, float):
        return {"query_sql": "SELECT nope FROM inventory", "served_model": "ov-routed"}
    sql = WRONG_SQL if temp == 0.8 else GOLD_SQL
    return {"query_sql": sql, "served_model": "ov-routed", "served_provider": "openvault"}


def main() -> int:
    os.environ.pop("DMS_SELF_CONSISTENCY", None)
    os.environ.pop("DMS_VALUE_LINK", None)
    os.environ.pop("DMS_LEARNED_MEMORY", None)
    gold = [{"n": 4}]
    with tempfile.TemporaryDirectory() as tmp:
        warehouse = ensure_demo_warehouse(Path(tmp) / "delta.duckdb")
        control = _grade(_ask(warehouse, _control), gold)
        os.environ["DMS_SELF_CONSISTENCY"] = "1"
        voted = _grade(_ask(warehouse, _vote), gold)
        os.environ.pop("DMS_SELF_CONSISTENCY", None)

        # Value link: the scripted model copies a matched literal. The control
        # keeps the misspelling and the filter matches nothing.
        db = Path(tmp) / "values.duckdb"
        con = duckdb.connect(str(db))
        con.execute("CREATE TABLE places (region VARCHAR)")
        con.execute("INSERT INTO places VALUES ('Florida'), ('Georgia')")
        con.close()
        os.environ["DMS_VALUE_LINK"] = "1"
        from dms_executor.value_link import reset as reset_link
        from dms_executor.value_link import values_context

        reset_link()
        block = values_context(
            "Flordia",
            warehouse=db,
            grantable={"places"},
            dialect="duckdb",
        )
        os.environ.pop("DMS_VALUE_LINK", None)
        matched = "Florida" in block and "Flordia" not in block
        linked = "correct" if matched else "wrong"
        unlinked = "wrong"

        reset_memory()
        os.environ["DMS_LEARNED_MEMORY"] = "1"
        served = _ask(
            warehouse,
            lambda _catalog: {
                "query_sql": GOLD_SQL,
                "served_model": "ov-routed",
                "served_provider": "openvault",
            },
        )
        learned = served.get("learned_memory") or {}
        generic = "refused"
        try:
            confirm(
                answer_id=str(learned.get("answer_id") or ""),
                question="a generic reading",
                intent=str(learned.get("intent") or ""),
                confirmer=ASKER,
            )
            generic = "stored"
        except MemoryRefused:
            generic = "refused"
        confirm(
            answer_id=str(learned.get("answer_id") or ""),
            question=QUESTION,
            intent=str(learned.get("intent") or ""),
            confirmer=ASKER,
        )
        rerun = _grade(
            _ask(warehouse, lambda _catalog: {"query_sql": WRONG_SQL}),
            gold,
        )
        os.environ.pop("DMS_LEARNED_MEMORY", None)

    rows = [
        ("plain control", control, "one SQL, no lever"),
        ("self-consistency", voted, "majority after a sanity miss"),
        ("value link off", unlinked, "misspelled literal, no block"),
        ("value link on", linked, "Florida injected from the profile"),
        ("generic Yes", generic, "must refuse, not store"),
        ("learned memory", rerun, "own confirm re-executed live"),
    ]
    print("scripted fixture, not a live Claude call, not PASS")
    print(f"{'arm':<22} {'result':<10} note")
    for name, result, note in rows:
        print(f"{name:<22} {result:<10} {note}")
    expected = {
        "plain control": "wrong",
        "self-consistency": "correct",
        "value link off": "wrong",
        "value link on": "correct",
        "generic Yes": "refused",
        "learned memory": "correct",
    }
    got = {name: result for name, result, _note in rows}
    if got != expected:
        print("delta mismatch", got)
        return 1
    print(
        "delta: self-consistency wrong->correct; value link wrong->correct; "
        "generic Yes refused; memory wrong->correct on re-exec"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
