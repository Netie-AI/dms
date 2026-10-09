"""CURATED-NO-SILENT-FALLBACK-01: name the failed curated step.

Pack match + a later failure must not fall through to a generic generative
abstain. cq_sku_count is on the score-pack allowlist, so that exact phrase
executes when grants, SQL, and the ledger succeed. A curated l0 phrase still
absent from the exact pack is an exact-match / pack-metric miss, not a
GEN-01 sentence. Offline score_curated reads Executor.grantable_tables,
not DEMO_SPACE_GRANTS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import Executor
from dms_executor.demo_pack import SPEND_BY_COUNTRY_Q, maybe_pack_ask
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter, SessionAcl

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
SKU_Q = "How many SKUs do we have in inventory?"
SKU_SYNONYM_Q = "How many SKUs in inventory?"
_GENERIC = "ontology-grounded"


@dataclass
class _Spoof:
    """Generative unsure, or a certified number if the pack step falls through."""

    insights: list[str] = field(default_factory=list)
    asks: list[Any] = field(default_factory=list)
    submits: list[Any] = field(default_factory=list)
    appends: list[Any] = field(default_factory=list)
    sql_ok: bool = True
    ledger_raises: bool = False

    def compute_insights(self, question: str, **_kwargs: Any) -> dict[str, Any]:
        self.insights.append(question)
        return {"unsure": True}

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else getattr(plan, "kind", None)
        if kind == "sql" and not self.sql_ok:
            return QueryResult(ok=False, status="rejected", run_id="run_sql_fail")
        if kind == "sql":
            return QueryResult(
                ok=True,
                status="ok",
                run_id="run_sql",
                output={"rows": [{"country": "MY", "total_spend_myr": 1.0}]},
            )
        return QueryResult(ok=True, status="bound", run_id="run_bind")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        self.appends.append(req)
        if self.ledger_raises:
            raise RuntimeError("ledger down")
        return LedgerAppendResponse(entry_id="led_ok", hash="hash_ok_not_entry")

    def ask(self, req: AskRequest) -> AskResponse:
        self.asks.append(req)
        return AskResponse(
            answer="Spoof total 999999.",
            badge="certified",
            sql_used="SELECT 999999 AS spoof",
            rows=[{"spoof": 999999}],
            audit_id="aud_spoof",
            route="certified_metric",
        )


def _minter() -> ManifestMinter:
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
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdA",
        )

    m.mint_manifest = _mint  # type: ignore[method-assign]
    m.fetch_intermediate = lambda: None  # type: ignore[method-assign]
    m.close = lambda: None  # type: ignore[method-assign]
    m.invalidate = lambda *_a, **_k: None  # type: ignore[method-assign]
    return m


def _ask(tmp_path: Path, question: str, space: str, cortex: _Spoof) -> dict[str, Any]:
    db = tmp_path / "curated.duckdb"
    ensure_demo_warehouse(db)
    exe = Executor(cortex=cortex, minter=_minter(), warehouse_path=db)  # type: ignore[arg-type]
    try:
        return exe.live_ask(question, space_id=space, session_id="ses_fix2")
    finally:
        exe.close()


def test_sku_count_exact_match_does_not_fall_through(tmp_path: Path) -> None:
    """#355 registered this phrase. A working later step answers. No generative."""
    cortex = _Spoof()
    env = _ask(tmp_path, SKU_Q, FINANCE, cortex)
    assert_envelope_valid(env)
    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["abstained"] is False
    assert any("cq_sku_count" in str(a) for a in (env.get("assumptions") or []))
    assert _GENERIC not in env["text"]
    assert "999999" not in env["text"]
    assert "exact-match miss" not in env["text"]
    assert cortex.insights == []
    assert cortex.asks == []


def test_unregistered_l0_names_exact_match_miss_not_generic_abstain(tmp_path: Path) -> None:
    cortex = _Spoof()
    env = _ask(tmp_path, SKU_SYNONYM_Q, FINANCE, cortex)
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert env["rows"] == []
    text = env["text"]
    assert "exact-match miss" in text
    assert "pack-metric miss" in text
    assert _GENERIC not in text
    assert "999999" not in text
    assert "exact match ok" not in text


def test_ops_spend_names_grants_fail(tmp_path: Path) -> None:
    cortex = _Spoof()
    env = _ask(tmp_path, SPEND_BY_COUNTRY_Q, OPS, cortex)
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    text = env["text"]
    assert "exact match ok" in text
    assert "grants fail" in text
    assert "pack-metric miss" not in text
    assert _GENERIC not in text
    assert cortex.insights == []
    assert cortex.asks == []
    assert "999999" not in text


def test_sql_fail_names_cortex_step(tmp_path: Path) -> None:
    cortex = _Spoof(sql_ok=False)
    env = _ask(tmp_path, SPEND_BY_COUNTRY_Q, FINANCE, cortex)
    assert_envelope_valid(env)
    assert "exact match ok" in env["text"]
    assert "Cortex SQL fail" in env["text"]
    assert "grants fail" not in env["text"]
    assert cortex.insights == []
    assert cortex.asks == []


def test_ledger_fail_names_ledger_step(tmp_path: Path) -> None:
    cortex = _Spoof(ledger_raises=True)
    env = _ask(tmp_path, SPEND_BY_COUNTRY_Q, FINANCE, cortex)
    assert_envelope_valid(env)
    assert "exact match ok" in env["text"]
    assert "ledger fail" in env["text"]
    assert "Cortex SQL fail" not in env["text"]
    assert cortex.insights == []
    assert cortex.asks == []


def test_unrelated_question_keeps_generic_generative_abstain(tmp_path: Path) -> None:
    cortex = _Spoof()
    env = _ask(tmp_path, "How many florbs did wibble sell last week?", FINANCE, cortex)
    assert_envelope_valid(env)
    assert env["badge"] == "ABSTAIN"
    assert "pack-metric miss" not in env["text"]
    assert cortex.insights


def test_maybe_pack_ask_ledger_bad_hash_names_ledger() -> None:
    def _submit(_sql: str) -> Any:
        return type("R", (), {"ok": True, "output": {"rows": []}, "run_id": "r"})()

    def _ledger(_payload: dict[str, Any]) -> Any:
        return type("L", (), {"entry_id": "same", "hash": "same"})()

    env = maybe_pack_ask(
        SPEND_BY_COUNTRY_Q,
        space_id=FINANCE,
        grantable={"inventory", "suppliers"},
        submit=_submit,
        ledger_append=_ledger,
        dialect="duckdb",
    )
    assert env is not None
    assert "ledger fail" in env["text"]


def test_score_curated_uses_serve_grants_and_names_pack_miss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from dms_executor import Executor
    from score_curated import run_ab_curated

    seen: list[str | None] = []

    def _empty(self: Executor, *, space_id: str | None) -> list[str]:
        seen.append(space_id)
        return []

    monkeypatch.setattr(Executor, "grantable_tables", _empty)
    pack = tmp_path / "pack.yaml"
    pack.write_text(
        "spaces:\n"
        "  finance: cccccccc-cccc-cccc-cccc-cccccccccccc\n"
        "questions:\n"
        "  - id: cq_sku_count\n"
        "    space: finance\n"
        "    expect: l0\n"
        "    question: How many SKUs do we have in inventory?\n"
        "  - id: cq_sku_count_syn_short\n"
        "    space: finance\n"
        "    expect: l0\n"
        "    question: How many SKUs in inventory?\n"
        "  - id: cq_spend_by_country\n"
        "    space: finance\n"
        "    expect: l0\n"
        "    question: What is our total spend by supplier country?\n",
        encoding="utf-8",
    )
    report = run_ab_curated(pack)
    by_id = {row["id"]: row for row in report["cases"]}
    assert seen, "score_curated did not call Executor.grantable_tables"
    sku = by_id["cq_sku_count"]["exact_text"]
    synonym = by_id["cq_sku_count_syn_short"]["exact_text"]
    spend = by_id["cq_spend_by_country"]["exact_text"]
    assert "exact match ok" in sku
    assert "grants fail" in sku
    assert "pack-metric miss" not in sku
    assert "exact-match miss" in synonym
    assert "pack-metric miss" in synonym
    assert "grants fail" in spend
    assert "999999" not in sku
    assert "COMPLETE" not in str(report.get("claim"))
