# ruff: noqa: F811 - the imported fixtures are used by name
"""TOTAL-GUARD-01 (dms#531): a stored verified query replays through the same rule.

Found by the adversary round (lens D, doors): ``maybe_verified_ask`` replays a
registered statement as L0 with ``DMS_VQ_RAG`` off (the default) and ran no
check on it. A registered ``SUM`` over a ``ROLLUP`` was served doubled under
the strongest badge. The envelope is asserted from ``POST /v1/chat/ask``.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest
from dms_executor.total_guard import repeated_input_reason
from test_vq02_verified_register import (  # noqa: F401 - fixtures and helpers
    FINANCE,
    _gate_allows,
    _live_client,
    _MarkerCortex,
    minter,
    warehouse,
)

Q = "TOTAL-GUARD steward: total capacity of all warehouses?"
GOOD = "SELECT SUM(capacity_kg) AS total FROM locations"
ROLLUP = (
    "SELECT SUM(t) AS total FROM (SELECT name, SUM(capacity_kg) AS t FROM locations "
    "GROUP BY ROLLUP(name))"
)


def _figures(warehouse: Path) -> tuple[float, float]:
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        good = con.execute(GOOD).fetchone()
        wrong = con.execute(ROLLUP).fetchone()
    finally:
        con.close()
    assert good and wrong
    return float(good[0]), float(wrong[0])


def _register_and_ask(
    sql: str,
    warehouse: Path,
    minter,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
):
    _gate_allows(monkeypatch)
    cortex = _MarkerCortex()
    client = _live_client(warehouse, minter, monkeypatch, cortex)
    reg = client.post(
        "/v1/studio/verified-queries",
        json={"space_id": FINANCE, "question": Q, "sql": sql},
    )
    assert reg.status_code == 200, reg.text
    ask = client.post(
        "/v1/chat/ask",
        json={"question": Q, "space_id": FINANCE, "session_id": "ses_total_guard_vq"},
    )
    assert ask.status_code == 200, ask.text
    return cortex, ask.json()


def test_registered_rollup_total_is_not_replayed(
    warehouse: Path,
    minter,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    good, wrong = _figures(warehouse)
    assert wrong == 2 * good, "the registered statement doubles the total"
    assert (repeated_input_reason(ROLLUP) or "").startswith("repeated_input:grouping_sets")
    cortex, env = _register_and_ask(ROLLUP, warehouse, minter, monkeypatch)
    text = str(env.get("text") or "")
    # Not the stored statement, not its figure, not the verified badge for it.
    assert env.get("sql_used") != ROLLUP
    assert ROLLUP not in str(env)
    for shape in {str(int(wrong)), f"{wrong:g}", f"{wrong:.1f}"}:
        assert not re.search(rf"(?<![\d.]){re.escape(shape)}(?![\d.])", text), (shape, text)
    assert all(row.get("total") != wrong for row in env.get("rows") or [])
    assert cortex.submits == [] or all(
        "ROLLUP" not in str(getattr(r, "body", "")) for r in cortex.submits
    ), "the stored ROLLUP statement reached Cortex submit"
    # The question took the normal ladder instead (no silent drop).
    assert cortex.asks, "the question did not fall through to the ladder"


def test_registered_plain_total_still_replays_as_l0(
    warehouse: Path,
    minter,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    cortex, env = _register_and_ask(GOOD, warehouse, minter, monkeypatch)
    assert env["badge"] == "L0_CERTIFIED", (env["badge"], env.get("text"))
    assert env["abstained"] is False
    assert env["rows"] == cortex.sql_output["rows"]  # type: ignore[index]
    assert env["sql_used"] == GOOD
    assert cortex.asks == [], "a correct stored statement replays without the ladder"
