"""RANK-WINDOW-01: a digit outside the ranking span is not a dropped filter.

On 16120ea2 the shape check forgave every ``tok.isdigit()``. Window numbers
are already blanked, so that only dropped an ID or exclusion written outside
the span. Fillers (and, for, from, only) then left nothing, and the lane
submitted unfiltered ``ORDER BY outbound_value_myr DESC LIMIT 5 OFFSET 3``.

These nine phrasings must abstain ``ungrounded_qualifier:<token>`` through
``Executor.live_ask``. They fail on 16120ea2. Years stay on QUAL-GUARD
``time_filter``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl

# Must-fail on 16120ea2. Token is the leftover number, not the window's own.
_MUST_FAIL: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("excluding top 3 and sku 10023, next 5 skus by revenue", ("10023",)),
    ("excluding top 3 and 10023, next 5 skus by revenue", ("10023",)),
    ("excluding top 3 and sku 1001 and sku 1002, next 5 skus by revenue", ("1001", "1002")),
    ("excluding top 3 and sku-03, next 5 skus by revenue", ("03",)),
    ("ranks 4-8 skus by revenue, 3 and 7 only", ("3", "7")),
    ("ranks 4-8 skus by revenue, 2 only", ("2",)),
    ("excluding top 3, next 5 skus from 3 by revenue", ("3",)),
    ("next 5 skus by revenue excluding 3", ("3",)),
    ("top 5 skus by revenue, not 10023", ("10023",)),
)

# Number after the measure, past the comma so it is outside the by-phrase.
# Fillers only besides the digit, so 16120ea2 serves an unfiltered window.
_AFTER_MEASURE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("excluding top 3, next 5 skus by revenue, and 10023", ("10023",)),
    ("what are the ranks 4-8 skus by sales, for 88", ("88",)),
    ("excluding top 3, next 5 skus by revenue, from 42 please", ("42",)),
    ("ranks 6 to 10 skus by turnover, with 7 only", ("7",)),
    ("excluding top 3, next 5 skus by quantity sold, to 15", ("15",)),
    ("after the top 3, the next 5 skus by revenue, only 1001", ("1001",)),
    ("positions 4 to 8 skus by revenue, for 6", ("6",)),
    ("excluding top 3, next 5 skus by kg, and 03", ("03",)),
    ("skus ranked 6 to 10 by sales, and 202", ("202",)),
    ("excluding top 3, show the next 5 skus by revenue, from 4", ("4",)),
)

_UNFILTERED = re.compile(r"\bwhere\b", re.I)
_YEAR_Q = "excluding top 3, next 5 skus by revenue, 2024"


def _unfiltered(sql: str) -> bool:
    text = " ".join(sql.lower().split())
    return _UNFILTERED.search(text) is None and "order by" in text and "offset" in text


def _blob(env: dict[str, Any]) -> str:
    return " ".join(str(a) for a in env.get("assumptions") or []) + " " + str(env.get("text"))


def _listed(blob: str) -> set[str]:
    part = blob.split("ungrounded_qualifier:", 1)[1]
    return set(part.split()[0].rstrip(".,;)").split(","))


@dataclass
class _LakeCortex:
    sql: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        self.insights.append(question)
        return None

    def compute_query(self, question: str, **kwargs: Any) -> dict[str, Any] | None:
        raise AssertionError("ask lane called /dms/query")

    def submit(self, req: Any) -> QueryResult:
        if req.plan.get("kind") == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="run_bind", output={})
        sql = str(req.body.get("sql") or "")
        self.sql.append(sql)
        return QueryResult(ok=True, status="ok", run_id="run_rw", output={"rows": []})

    def ledger_append(self, req: Any) -> Any:
        return SimpleNamespace(entry_id="led_rw", hash="hash_rw")

    def ask(self, req: Any) -> Any:
        raise AssertionError("rank-window ask fell through to Cortex contract ask")


@pytest.fixture()
def lake(tmp_path: Path) -> Path:
    path = tmp_path / "rank_window_03.duckdb"
    ensure_demo_warehouse(path)
    return path


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    m = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-08T00:00:00+00:00",
            expires_at="2026-10-08T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


def _live(
    lake: Path, minter: ManifestMinter, question: str
) -> tuple[dict[str, Any], list[str], list[str]]:
    cortex = _LakeCortex()
    exe = Executor(cortex=cortex, minter=minter, warehouse_path=lake)  # type: ignore[arg-type]
    env = exe.live_ask(question, session_id="ses_fa93d955a1e545eb")
    return env, cortex.sql, cortex.insights


def _assert_named_digit(
    env: dict[str, Any], tokens: tuple[str, ...], submits: list[str]
) -> None:
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN", _blob(env)
    assert env["abstained"] is True
    assert env["rows"] == [] and env["values"] == []
    assert env.get("sql_used") in (None, "")
    blob = _blob(env)
    assert "ungrounded_qualifier:" in blob, blob
    assert _listed(blob) == set(tokens), blob
    assert [s for s in submits if _unfiltered(s)] == []
    assert submits == []


@pytest.mark.parametrize(("question", "tokens"), _MUST_FAIL)
def test_live_ask_abstains_on_stray_digit(
    lake: Path, minter: ManifestMinter, question: str, tokens: tuple[str, ...]
) -> None:
    env, sql, insights = _live(lake, minter, question)
    _assert_named_digit(env, tokens, sql)
    assert insights == []


@pytest.mark.parametrize(("question", "tokens"), _AFTER_MEASURE)
def test_live_ask_abstains_on_digit_after_measure(
    lake: Path, minter: ManifestMinter, question: str, tokens: tuple[str, ...]
) -> None:
    env, sql, insights = _live(lake, minter, question)
    _assert_named_digit(env, tokens, sql)
    assert insights == []


def test_year_outside_the_window_stays_time_filter(
    lake: Path, minter: ManifestMinter
) -> None:
    """A calendar year is QUAL-GUARD time_filter, not ungrounded_qualifier."""
    env, sql, insights = _live(lake, minter, _YEAR_Q)
    assert_envelope_valid(env)
    blob = _blob(env)
    assert env["badge"] == "ABSTAIN", blob
    assert "unhonored_qualifier:time_filter=year=2024" in blob, blob
    assert "ungrounded_qualifier:" not in blob
    assert sql == [] and insights == []
