"""RANK-WINDOW-02: a token after 'by <measure>' is not a dropped filter.

The shape check used to blank the whole by-phrase through the next
punctuation, and the keyword match locked revenue from the word alone.
'excluding top 3, next 5 skus by revenue at WH-B' then served L2_VALIDATED
with no WHERE. These questions fail on be409e44 and abstain on the fix.

Allow-list, not a denylist. After 'by', only _MEASURE_KEYWORDS and
_MEASURE_PHRASE_FILLERS are accepted. Direction words abstain the same way.
ASC is not implemented. An unseen token (xyzzyplugh) abstains too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cortex_client.qualifiers import rank_window_measure, rank_window_shape_reason
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, rank_window_ask
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.ontology import Ontology

_STEM = "excluding top 3, next 5 skus by revenue"

# Phrases the merge checker served with no WHERE on be409e44.
_CHECKER: tuple[tuple[str, str], ...] = (
    ("excluding top 3, next 5 skus by revenue at WH-B", "wh"),
    ("excluding top 3, next 5 skus by revenue for chemicals", "chemicals"),
    ("ranks 4-8 skus by revenue at garaj", "garaj"),
    ("excluding top 3, next 5 skus by lowest revenue", "lowest"),
    (f"{_STEM} at garaj", "garaj"),
    (f"{_STEM} for RAW", "raw"),
    (f"{_STEM} excluding chemicals", "chemicals"),
    (f"{_STEM} in kuala lumpur", "lumpur"),
    (f"{_STEM} in Q3", "q3"),
    (f"{_STEM} only RAW", "raw"),
    (f"{_STEM} at site B", "site"),
    (f"{_STEM} from SUP-01", "sup"),
    (f"{_STEM} with low stock", "stock"),
    (f"{_STEM} ascending", "ascending"),
)

# Not in the checker list. Includes fillers the question-level list forgives
# ("please", "that", "are") and direction words other than lowest/ascending.
_OWN: tuple[tuple[str, str], ...] = (
    (f"{_STEM} near the port", "port"),
    (f"{_STEM} last month", "month"),
    (f"{_STEM} among imported items", "imported"),
    (f"{_STEM} please", "please"),
    (f"{_STEM} that are perishable", "perishable"),
    ("ranks 4-8 skus by sales above quota", "quota"),
    ("excluding top 3, next 5 skus by turnover before tax", "tax"),
    ("excluding top 3, next 5 skus by kg despite returns", "returns"),
    ("excluding top 3, next 5 skus by quantity sold indoors", "indoors"),
    (f"{_STEM} xyzzyplugh", "xyzzyplugh"),
    ("ranks 6-10 skus by revenue during the monsoon", "monsoon"),
    ("excluding top 3, next 5 skus by asc revenue", "asc"),
    ("excluding top 3, next 5 skus by bottom revenue", "bottom"),
    ("excluding top 3, next 5 skus by least revenue", "least"),
    ("excluding top 3, next 5 skus by smallest revenue", "smallest"),
    ("excluding top 3, next 5 skus by worst revenue", "worst"),
)

_UNFILTERED = re.compile(r"\bwhere\b", re.I)


def _unfiltered(sql: str) -> bool:
    """The be409e44 serve: a ranked window with no WHERE clause."""
    text = " ".join(sql.lower().split())
    return _UNFILTERED.search(text) is None and "order by" in text and "offset" in text


def _blob(env: dict[str, Any]) -> str:
    return " ".join(str(a) for a in env.get("assumptions") or []) + " " + str(env.get("text"))


def _listed(blob: str) -> set[str]:
    part = blob.split("rank_window_unhandled_terms:", 1)[1]
    return set(part.split()[0].rstrip(".,;)").split(","))


def _assert_named_abstain(
    env: dict[str, Any] | None, token: str, submits: list[str]
) -> None:
    assert env is not None
    assert_envelope_valid(env)
    assert env["badge"] != "L2_VALIDATED"
    assert env["badge"] == "ABSTAIN", _blob(env)
    assert env["rows"] == [] and env["values"] == []
    blob = _blob(env)
    assert "rank_window_unhandled_terms:" in blob, blob
    assert token in _listed(blob), blob
    assert [s for s in submits if _unfiltered(s)] == []


@pytest.fixture()
def lake(tmp_path: Path) -> Path:
    path = tmp_path / "rank_window_02.duckdb"
    ensure_demo_warehouse(path)
    onto = load_verified_ontology(path)
    assert onto is not None and onto.verified
    return path


def _specs(onto: Ontology) -> dict[str, str]:
    return {name: m.description or "" for name, m in onto.measures.items()}


def _lane(lake: Path, question: str) -> tuple[dict[str, Any] | None, list[str]]:
    onto = load_verified_ontology(lake)
    assert onto is not None
    submits: list[str] = []

    def submit(sql: str) -> SimpleNamespace:
        submits.append(sql)
        return SimpleNamespace(ok=True, status="ok", run_id="run_rw", output={"rows": []})

    env = rank_window_ask(
        question, onto=onto, allowed={"transactions", "inventory"}, lake=lake,
        space_id=None, session_id=None, submit=submit,
        ledger_append=lambda _p: SimpleNamespace(entry_id="led_rw", hash="hash_rw"),
    )
    got, why, _reading = rank_window_measure(question, _specs(onto))
    assert got is None
    assert why is not None and why.startswith("rank_window_unhandled_terms:")
    shape = rank_window_shape_reason(question)
    assert shape is not None and shape.startswith("rank_window_unhandled_terms:")
    return env, submits


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


@pytest.mark.parametrize(("question", "token"), _CHECKER)
def test_live_ask_abstains_on_checker_trailing_filter(
    lake: Path, minter: ManifestMinter, question: str, token: str
) -> None:
    env, sql, insights = _live(lake, minter, question)
    _assert_named_abstain(env, token, sql)
    assert insights == []


@pytest.mark.parametrize(("question", "token"), _CHECKER)
def test_lane_abstains_on_checker_trailing_filter(
    lake: Path, question: str, token: str
) -> None:
    env, submits = _lane(lake, question)
    _assert_named_abstain(env, token, submits)


@pytest.mark.parametrize(("question", "token"), _OWN)
def test_live_ask_abstains_on_unlisted_by_phrase_token(
    lake: Path, minter: ManifestMinter, question: str, token: str
) -> None:
    env, sql, insights = _live(lake, minter, question)
    _assert_named_abstain(env, token, sql)
    assert insights == []


@pytest.mark.parametrize(("question", "token"), _OWN)
def test_lane_abstains_on_unlisted_by_phrase_token(
    lake: Path, question: str, token: str
) -> None:
    env, submits = _lane(lake, question)
    _assert_named_abstain(env, token, submits)
