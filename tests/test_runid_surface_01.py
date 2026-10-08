"""RUNID-SURFACE-01: SQL submit run_id on pack, verified, and generative envelopes.

The ledger payload already stores the Cortex SQL submit run_id. The ask
envelope must carry that same id as a top-level key. A session_bind run_id
from the same turn must not win. No run_id from Cortex means the key is
absent and the envelope bytes match an untagged envelope. The PII masker
must not rewrite the key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from cortex_client.models import LedgerAppendRequest, LedgerAppendResponse
from cortex_contract.execution import Manifest, QueryResult
from dms_core.pii import mask_envelope, mask_unknown_keys
from dms_executor import Executor
from dms_executor.demo_pack import (
    STOCK_BY_CATEGORY_Q,
    PackMetric,
    envelope_from_pack_submit,
)
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask
from dms_executor.manifest import ManifestMinter, SessionAcl
from dms_executor.verified_queries import (
    envelope_from_verified_submit,
    register_verified_query,
)

from tests.test_gen01_generative_ask import _ontology, _seed

FINANCE = "cccccccc-cccc-cccc-cccc-cccccccccccc"
BIND_RUN = "run-bind-earlier"
# NRIC shape. mask_unknown_keys rewrites this on any key that is not kept.
SQL_RUN = "900101-14-1234"
FIXED_AS_OF = "2026-10-08T00:00:00Z"
VQ_QUESTION = "RUNID-SURFACE steward: capacity of warehouse A?"
VQ_SQL = "SELECT name, capacity_kg FROM locations WHERE location_id = 'WH-A'"
PACK_ROWS = [{"category": "ALPHA", "stock_value_myr": 100.0}]
VQ_ROWS = [{"name": "Warehouse A", "capacity_kg": 100000.0}]
GEN_ROWS = [{"product_category": "ALPHA", "revenue": 100.0}]


def _wire(env: dict[str, Any]) -> str:
    return json.dumps(env)


def _without_run_id(env: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in env.items() if k != "run_id"}


@dataclass
class _Cortex:
    """Bind returns BIND_RUN. SQL submit returns sql_run_id, or no run_id."""

    sql_run_id: str | None
    rows: list[dict[str, Any]]
    submits: list[Any] = field(default_factory=list)
    appends: list[LedgerAppendRequest] = field(default_factory=list)

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        if kind == "sql":
            if self.sql_run_id is None:
                return QueryResult(ok=True, status="ok", output={"rows": self.rows})
            return QueryResult(
                ok=True,
                status="ok",
                run_id=self.sql_run_id,
                output={"rows": self.rows},
            )
        return QueryResult(ok=True, status="bound", run_id=BIND_RUN)

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        self.appends.append(req)
        return LedgerAppendResponse(entry_id="led_runid", hash="hash_runid_not_entry")

    def ask(self, req: Any) -> Any:
        raise AssertionError(f"contract ask must not run, got {req!r}")


def _kinds(cortex: _Cortex) -> list[str]:
    kinds: list[str] = []
    for req in cortex.submits:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        kinds.append(str(kind))
    return kinds


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
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(m, "mint_manifest", _mint)
    monkeypatch.setattr(m, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(m, "close", lambda: None)
    monkeypatch.setattr(m, "invalidate", lambda *_a, **_k: None)
    return m


@pytest.fixture()
def frozen_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    for target in (
        "dms_executor.demo_pack._as_of",
        "dms_executor.verified_queries._as_of",
        "dms_executor.generative_ask._as_of",
        "dms_executor.envelope._now",
    ):
        monkeypatch.setattr(target, lambda: FIXED_AS_OF)


def _live(
    minter: ManifestMinter,
    warehouse: Path,
    *,
    sql_run_id: str | None,
    rows: list[dict[str, Any]],
    question: str,
    session_id: str,
    space_id: str | None = None,
) -> tuple[dict[str, Any], str, _Cortex]:
    cortex = _Cortex(sql_run_id=sql_run_id, rows=rows)
    exe = Executor(cortex=cortex, minter=minter, warehouse_path=warehouse)  # type: ignore[arg-type]
    env = exe.live_ask(
        question,
        space_id=space_id,
        session_id=session_id,
        ask_path="exact",
    )
    assert_envelope_valid(env)
    assert cortex.appends, env.get("text")
    ledger_run = str(cortex.appends[-1].payload.get("run_id") or "")
    return env, ledger_run, cortex


def _assert_submit_wins(env: dict[str, Any], ledger_run: str, cortex: _Cortex) -> None:
    kinds = _kinds(cortex)
    assert "session_bind" in kinds
    assert "sql" in kinds
    assert ledger_run == SQL_RUN
    assert ledger_run != BIND_RUN
    assert env["run_id"] == ledger_run
    assert env["audit_id"] == "led_runid"
    assert env["audit_id"] != env["run_id"]


def _assert_absent_matches_untagged(present: dict[str, Any], absent: dict[str, Any]) -> None:
    assert "run_id" not in absent
    assert '"run_id"' not in _wire(absent)
    assert _wire(_without_run_id(present)) == _wire(absent)


def test_pack_run_id_matches_ledger_and_submit_wins_over_bind(
    minter: ManifestMinter, tmp_path: Path, frozen_as_of: None
) -> None:
    warehouse = tmp_path / "pack.duckdb"
    present, led_present, cortex = _live(
        minter,
        warehouse,
        sql_run_id=SQL_RUN,
        rows=PACK_ROWS,
        question=STOCK_BY_CATEGORY_Q,
        session_id="ses_runid_pack",
    )
    assert present["route"] == "governed_metric"
    assert present["badge"] == "L1_GOVERNED_METRIC"
    _assert_submit_wins(present, led_present, cortex)

    absent, led_absent, _absent_cortex = _live(
        minter,
        warehouse,
        sql_run_id=None,
        rows=PACK_ROWS,
        question=STOCK_BY_CATEGORY_Q,
        session_id="ses_runid_pack",
    )
    assert absent["route"] == "governed_metric"
    assert led_absent == ""
    assert absent["audit_id"] == "led_runid"
    _assert_absent_matches_untagged(present, absent)


def test_verified_run_id_matches_ledger_and_submit_wins_over_bind(
    minter: ManifestMinter, tmp_path: Path, frozen_as_of: None
) -> None:
    warehouse = tmp_path / "vq.duckdb"
    register_verified_query(
        space_id=FINANCE, question=VQ_QUESTION, sql=VQ_SQL, path=warehouse
    )
    present, led_present, cortex = _live(
        minter,
        warehouse,
        sql_run_id=SQL_RUN,
        rows=VQ_ROWS,
        question=VQ_QUESTION,
        session_id="ses_runid_vq",
        space_id=FINANCE,
    )
    assert present["route"] == "verified_query"
    assert present["badge"] == "L0_CERTIFIED"
    _assert_submit_wins(present, led_present, cortex)

    absent, led_absent, _absent_cortex = _live(
        minter,
        warehouse,
        sql_run_id=None,
        rows=VQ_ROWS,
        question=VQ_QUESTION,
        session_id="ses_runid_vq",
        space_id=FINANCE,
    )
    assert absent["route"] == "verified_query"
    assert led_absent == ""
    assert absent["audit_id"] == "led_runid"
    _assert_absent_matches_untagged(present, absent)


def test_generative_run_id_matches_ledger_and_submit_wins_over_bind(
    minter: ManifestMinter, tmp_path: Path, frozen_as_of: None
) -> None:
    warehouse = tmp_path / "gen.duckdb"
    _seed(warehouse)
    onto = load_verified_ontology(warehouse, _ontology())
    assert onto is not None
    cortex = _Cortex(sql_run_id=SQL_RUN, rows=GEN_ROWS)
    exe = Executor(cortex=cortex, minter=minter, warehouse_path=warehouse)  # type: ignore[arg-type]

    def _ask() -> dict[str, Any]:
        env = maybe_generative_ask(
            "What is revenue by product category?",
            space_id=FINANCE,
            session_id="ses_runid_gen",
            warehouse=warehouse,
            grantable={"sales", "lots", "regions"},
            compute=lambda _catalog: {
                "query_plan": {
                    "measure": "revenue",
                    "group_by": [["product", "category"]],
                }
            },
            submit=lambda sql: exe._submit_verified_sql(
                sql,
                space_id=FINANCE,
                session_id="ses_runid_gen",
                tables=None,
            ),
            ledger_append=lambda payload: exe._ledger_verified_query(
                asset_sql=str(payload.get("sql") or ""),
                run_id=str(payload.get("run_id") or ""),
                space_id=FINANCE,
                session_id="ses_runid_gen",
                event_type="ask.generated_ontology",
            ),
            ontology=onto,
        )
        assert env is not None
        assert_envelope_valid(env)
        return env

    present = _ask()
    assert present["route"] == "generated"
    assert present["badge"] == "L2_VALIDATED"
    led_present = str(cortex.appends[-1].payload.get("run_id") or "")
    _assert_submit_wins(present, led_present, cortex)

    cortex.sql_run_id = None
    absent = _ask()
    assert absent["route"] == "generated"
    led_absent = str(cortex.appends[-1].payload.get("run_id") or "")
    assert led_absent == ""
    assert absent["audit_id"] == "led_runid"
    _assert_absent_matches_untagged(present, absent)


def test_audit_id_fallback_unchanged_when_run_id_missing_or_present(
    frozen_as_of: None,
) -> None:
    """Receipt fallback stays the pre-change string. run_id is a second field."""
    metric = PackMetric(
        metric_id="spend_by_country",
        question="q",
        sql="SELECT s.country FROM suppliers s",
        tables=("suppliers",),
    )
    rows = [{"country": "MY"}]
    present = envelope_from_pack_submit(
        metric=metric,
        result=QueryResult(ok=True, status="ok", run_id=SQL_RUN, output={"rows": rows}),
        question="q",
        audit_id=None,
    )
    absent = envelope_from_pack_submit(
        metric=metric,
        result=QueryResult(ok=True, status="ok", output={"rows": rows}),
        question="q",
        audit_id=None,
    )
    assert present["audit_id"] == SQL_RUN
    assert present["run_id"] == SQL_RUN
    assert absent["audit_id"] == "cortex_submit_spend_by_country"
    assert "run_id" not in absent
    assert '"run_id"' not in _wire(absent)

    both = envelope_from_pack_submit(
        metric=metric,
        result=QueryResult(ok=True, status="ok", run_id=SQL_RUN, output={"rows": rows}),
        question="q",
        audit_id="led_explicit",
    )
    assert both["audit_id"] == "led_explicit"
    assert both["run_id"] == SQL_RUN

    verified_present = envelope_from_verified_submit(
        asset_id="vq_runid",
        sql_text="SELECT name FROM locations",
        result=QueryResult(ok=True, status="ok", run_id=SQL_RUN, output={"rows": rows}),
        question="q",
        audit_id=None,
    )
    verified_absent = envelope_from_verified_submit(
        asset_id="vq_runid",
        sql_text="SELECT name FROM locations",
        result=QueryResult(ok=True, status="ok", output={"rows": rows}),
        question="q",
        audit_id=None,
    )
    assert verified_present["audit_id"] == SQL_RUN
    assert verified_present["run_id"] == SQL_RUN
    assert verified_absent["audit_id"] == "cortex_submit_vq_runid"
    assert "run_id" not in verified_absent
    assert _wire(_without_run_id(verified_present)) != _wire(verified_absent)


def test_empty_run_id_omits_key_and_matches_builder_without_it() -> None:
    kwargs: dict[str, Any] = {
        "answer_id": "ans_runid_byte",
        "text": "Found 1 row(s).\n  - category=ALPHA, stock_value_myr=100.0",
        "badge": "L1_GOVERNED_METRIC",
        "rows": PACK_ROWS,
        "sql_used": "SELECT category, stock_value_myr FROM inventory",
        "assumptions": ["executed via Cortex submit"],
        "as_of": FIXED_AS_OF,
        "ask_mode": "live",
        "route": "governed_metric",
        "question": STOCK_BY_CATEGORY_Q,
        "audit_id": "led_runid",
    }
    base = build_answer_envelope(**kwargs)
    blank = build_answer_envelope(**kwargs, run_id="")
    none = build_answer_envelope(**kwargs, run_id=None)
    spaces = build_answer_envelope(**kwargs, run_id="   ")
    assert "run_id" not in base
    assert "run_id" not in blank
    assert "run_id" not in none
    assert "run_id" not in spaces
    assert _wire(base) == _wire(blank) == _wire(none) == _wire(spaces)
    carried = build_answer_envelope(**kwargs, run_id=SQL_RUN)
    assert carried["run_id"] == SQL_RUN
    assert carried["audit_id"] == base["audit_id"] == "led_runid"
    assert _wire(_without_run_id(carried)) == _wire(base)


def test_run_id_survives_pii_mask() -> None:
    masked = mask_unknown_keys(
        {"run_id": SQL_RUN, "note": SQL_RUN, "audit_id": "led_runid"}
    )
    assert masked["note"] != SQL_RUN
    assert masked["note"].startswith("DMSMASK_")
    assert masked["run_id"] == SQL_RUN
    assert masked["audit_id"] == "led_runid"
    env = mask_envelope(
        {
            "answer_id": "ans_runid",
            "text": "ok",
            "rows": [],
            "values": [],
            "audit_id": "led_runid",
            "run_id": SQL_RUN,
            "sql_used": "SELECT 1",
        }
    )
    assert env["run_id"] == SQL_RUN
