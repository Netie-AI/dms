"""Query execution — the only DMS package allowed to call duckdb.execute.

Manifest minting + signing + submit() live here. Path enforcement is Cortex's job.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from cortex_client import CortexClient
from cortex_client.models import AskRequest, AskResponse
from cortex_contract.execution import PoolSpec, SubmitRequest
from dms_core.ask import AskServiceError
from dms_core.ports import ServingEnginePort

from dms_executor.acl import (
    SessionContext,
    SourceGrant,
    intersect_space_grants,
    mint_manifest_for_session,
    resolve_session_acl,
)
from dms_executor.batch_ingest import ingest_batch
from dms_executor.bronze import (
    IngestReceipt,
    ingest_csv_bytes,
    list_bronze_tables,
    write_bronze_rows,
)
from dms_executor.contract_infer import infer_contract
from dms_executor.demo_ask import answer_demo_question
from dms_executor.demo_warehouse import DEMO_TABLES, ensure_demo_warehouse, execute_sql
from dms_executor.manifest import (
    ManifestMinter,
    SessionAcl,
    SubmitError,
    classify_submit_error,
    reject_hostile_chat_sql,
    should_rement,
)
from dms_executor.openvault_discovery import local_start_command, probe_openvault
from dms_executor.pipeline_loader import (
    load_pipeline_by_name,
    load_pipeline_yaml,
    validate_pipeline_dict,
)
from dms_executor.promote import run_promote, sign_gold_metric
from dms_executor.triage import classify_bytes, classify_grid

logger = logging.getLogger(__name__)


class Executor:
    """Serving engine + manifest-aware submit path."""

    def __init__(
        self,
        *,
        cortex: CortexClient | None = None,
        minter: ManifestMinter | None = None,
        warehouse_path: Path | str | None = None,
        openvault_url: str | None = None,
        fetch_key_on_start: bool = False,
    ) -> None:
        self._cortex = cortex
        self._minter = minter or ManifestMinter(openvault_url=openvault_url)
        self._preferred_openvault_url = openvault_url
        self._warehouse = Path(warehouse_path) if warehouse_path else None
        self._bound_sessions: set[str] = set()
        if fetch_key_on_start:
            self.startup()

    def startup(self) -> None:
        """Fetch OpenVault intermediate signing key into memory only."""
        ensure_demo_warehouse(self._warehouse)
        resolved, start_cmd = probe_openvault(preferred_url=self._preferred_openvault_url)
        if resolved:
            self._minter.openvault_url = resolved
            try:
                self._minter.fetch_intermediate()
                logger.info(
                    "executor startup: OpenVault %s — intermediate signing key loaded",
                    resolved,
                )
                return
            except Exception as exc:  # noqa: BLE001 — demo can run without OV
                logger.warning(
                    "executor startup: OpenVault reachable at %s but key fetch failed: %s",
                    resolved,
                    exc,
                )
                return
        logger.warning(
            "OpenVault offline — demo continues without signed manifests; start via: %s",
            start_cmd or local_start_command(),
        )

    def close(self) -> None:
        self._minter.close()
        self._bound_sessions.clear()

    def execute(self, sql: str) -> list[dict[str, Any]]:
        reject_hostile_chat_sql(sql)
        ensure_demo_warehouse(self._warehouse)
        return execute_sql(sql, path=self._warehouse)

    def demo_ask(self, question: str, *, space_id: str | None = None) -> dict[str, Any]:
        ensure_demo_warehouse(self._warehouse)
        return answer_demo_question(question, space_id=space_id)

    def mint_manifest(self, session: SessionContext | SessionAcl) -> Any:
        if isinstance(session, SessionAcl):
            return self._minter.mint_manifest(session)
        return mint_manifest_for_session(self._minter, session)

    def demo_acl(
        self,
        *,
        session_id: str | None = None,
        space_id: str | None = None,
        tables: list[str] | None = None,
    ) -> SessionAcl:
        """Session ACL for this turn, optionally narrowed to chosen tables.

        ``row_predicates`` keys *are* the readable set — the manifest is what
        Cortex enforces — so grounding a question in specific files is not a
        hint passed alongside the question, it is a smaller manifest. A question
        grounded in ``transactions`` cannot read ``suppliers`` because the
        engine will refuse the SQL, not because the prompt asked it nicely.

        Unknown names are dropped rather than trusted: the caller sends whatever
        the user ticked, and a table that is not in the demo set has no
        predicate to grant. An empty or fully-unknown selection falls back to
        the full set, so "ground in nothing" means "no narrowing" rather than
        an ACL that can read nothing at all (R-0005).
        """
        scoped = [t for t in (tables or []) if t in DEMO_TABLES]
        readable = scoped or list(DEMO_TABLES)
        # A different scope is a different manifest, so it must be a different
        # bound session — reusing the id would serve the question under whatever
        # manifest happened to be bound first.
        base = session_id or f"ses_{uuid4().hex[:16]}"
        sid = f"{base}::scope:{'+'.join(sorted(scoped))}" if scoped else base
        return SessionAcl(
            session_id=sid,
            org_id="tenant_demo",
            space_id=space_id,
            row_predicates={t: "TRUE" for t in readable},
            allowed_paths=[],
            pool_id="default",
            ttl_seconds=900,
        )

    def bind_session(self, session: SessionContext | SessionAcl) -> Any:
        """Mint + submit plan.kind=session_bind (C4-min)."""
        if self._cortex is None:
            raise RuntimeError("CortexClient required for bind_session")
        acl = session if isinstance(session, SessionAcl) else resolve_session_acl(session)
        manifest = self._minter.mint_manifest(acl)
        req = SubmitRequest(
            pool=PoolSpec(id=acl.pool_id),
            plan={"kind": "session_bind"},
            body={},
            manifest=manifest,
        )
        try:
            result = self._cortex.submit(req)
        except Exception as exc:  # noqa: BLE001
            err = classify_submit_error(exc)
            if should_rement(err.code):
                self._minter.invalidate(acl.session_id)
                manifest = self._minter.mint_manifest(acl)
                req = SubmitRequest(
                    pool=PoolSpec(id=acl.pool_id),
                    plan={"kind": "session_bind"},
                    body={},
                    manifest=manifest,
                )
                try:
                    result = self._cortex.submit(req)
                except Exception as exc2:  # noqa: BLE001
                    err2 = classify_submit_error(exc2)
                    raise AskServiceError(err2.code, err2.detail) from exc2
            else:
                raise AskServiceError(err.code, err.detail) from exc
        ok = getattr(result, "ok", None)
        status = getattr(result, "status", None)
        if ok is False or (status is not None and status not in ("bound", "ok")):
            code = status or "session_bind_failed"
            raise AskServiceError(str(code), f"status={status!r}")
        self._bound_sessions.add(acl.session_id)
        return result

    def live_ask(
        self,
        question: str,
        *,
        space_id: str | None = None,
        session_id: str | None = None,
        tables: list[str] | None = None,
    ) -> dict[str, Any]:
        """Mint → session_bind (once per session) → contract ask.

        ``tables`` narrows the manifest to the files the user grounded the
        question in, so the scope is enforced by the engine rather than
        suggested to the model.
        """
        if self._cortex is None:
            raise RuntimeError("CortexClient required for live_ask")
        acl = self.demo_acl(session_id=session_id, space_id=space_id, tables=tables)
        if acl.session_id not in self._bound_sessions:
            self.bind_session(acl)
        try:
            resp = self._cortex.ask(
                AskRequest(
                    question=question,
                    session_id=acl.session_id,
                    space_id=space_id,
                )
            )
        except Exception as exc:  # noqa: BLE001
            err = classify_submit_error(exc)
            if err.code in {"session_unbound", "session_expired"}:
                self._bound_sessions.discard(acl.session_id)
                self.bind_session(acl)
                resp = self._cortex.ask(
                    AskRequest(
                        question=question,
                        session_id=acl.session_id,
                        space_id=space_id,
                    )
                )
            else:
                raise AskServiceError(err.code, err.detail) from exc
        return map_ask_response_to_envelope(resp, space_id=space_id, session_id=acl.session_id)

    def submit_sql(
        self,
        sql: str,
        session: SessionContext | SessionAcl,
        *,
        reminted: bool = False,
    ) -> Any:
        reject_hostile_chat_sql(sql)
        if self._cortex is None:
            raise RuntimeError("CortexClient required for submit")
        acl = session if isinstance(session, SessionAcl) else resolve_session_acl(session)
        manifest = self._minter.mint_manifest(acl)
        req = SubmitRequest(
            pool=PoolSpec(id=acl.pool_id),
            plan={"kind": "sql"},
            body={"sql": sql},
            manifest=manifest,
        )
        try:
            return self._cortex.submit(req)
        except Exception as exc:  # noqa: BLE001 — classify then re-raise
            err = classify_submit_error(exc)
            if should_rement(err.code) and not reminted:
                self._minter.invalidate(acl.session_id)
                return self.submit_sql(sql, acl, reminted=True)
            if err.code in {
                "manifest_signature_invalid",
                "path_not_allowed",
                "statement_not_allowed",
                "manifest_rejected",
            }:
                logger.error("security_event submit code=%s", err.code)
            raise SubmitError(err.code, err.detail) from exc


_BADGE_MAP = {
    "certified": "L0_CERTIFIED",
    "certified_metric": "L0_CERTIFIED",
    "governed_metric": "L1_GOVERNED_METRIC",
    "query_skill": "L2_VALIDATED",
    "session": "L2_VALIDATED",
    "generated": "L2_VALIDATED",
    "abstain": "ABSTAIN",
    "blocked": "ABSTAIN",
}


def map_ask_response_to_envelope(
    resp: AskResponse,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Map contract Answer-shaped AskResponse into UI envelope."""
    from dms_executor.envelope import assert_envelope_valid, build_answer_envelope

    badge_raw = (resp.badge or "abstain").lower()
    badge = _BADGE_MAP.get(badge_raw, "L2_VALIDATED")
    abstained = bool(resp.abstained) or badge == "ABSTAIN"
    if abstained:
        badge = "ABSTAIN"
    text = resp.answer or ("Abstained." if abstained else "")
    values = list(resp.values or [])
    # Promote ALL numeric cells from rows (E4 — every decimal in prose must be
    # present in values[]; a single first-cell v0 is not enough for listings).
    if resp.rows:
        seen = {
            float(v["value"])
            for v in values
            if isinstance(v, dict) and isinstance(v.get("value"), (int, float))
        }
        idx = len(values)
        for row in list(resp.rows)[:50]:
            for key, val in row.items():
                if not isinstance(val, (int, float)) or isinstance(val, bool):
                    continue
                fv = float(val)
                if any(abs(fv - s) < 1e-9 for s in seen):
                    continue
                values.append({"id": f"v{idx}", "value": fv, "label": key})
                seen.add(fv)
                idx += 1
                if idx >= 80:
                    break
            if idx >= 80:
                break
    if not values and resp.rows:
        values = [{"id": "v_count", "value": float(len(resp.rows)), "label": "row_count"}]
    chart = None
    rows = list(resp.rows or [])
    if rows and not abstained:
        keys = list(rows[0].keys())
        num_key = next(
            (
                k
                for k in keys
                if isinstance(rows[0].get(k), (int, float)) and not isinstance(rows[0].get(k), bool)
            ),
            None,
        )
        cat_key = next((k for k in keys if k != num_key and isinstance(rows[0].get(k), str)), None)
        if num_key and cat_key:
            chart = {
                "kind": "hbar",
                "x": cat_key,
                "y": num_key,
                "title": "Result",
            }
    assumptions: list[str] = []
    if resp.assumptions:
        if isinstance(resp.assumptions, str):
            assumptions = [resp.assumptions]
        else:
            assumptions = list(resp.assumptions)
    assumptions.append("live Cortex ask")
    sources = list(resp.contributing_sources or [])
    token = resp.drillthrough_token
    if sources and not token:
        # E7: never claim sources without a drillthrough token.
        sources = []
    env = build_answer_envelope(
        answer_id=resp.receipt_id or f"ans_live_{session_id or 'x'}",
        text=text,
        values=values,
        badge=badge,
        abstained=abstained,
        sql_used=None if abstained else resp.sql_used,
        assumptions=assumptions,
        as_of=datetime_now(),
        space_id=space_id,
        ask_mode="live",
        session_id=session_id,
        contributing_sources=sources,
        rows=[] if abstained else rows,
        chart=None if abstained else chart,
        suggestions=list(resp.suggestions or []),
        audit_id=resp.receipt_id or resp.audit_id,
        route=resp.route,
        drillthrough_token=None if abstained else token,
    )
    assert_envelope_valid(env)
    return env


def datetime_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_serving_engine() -> ServingEnginePort:
    return Executor()


__all__ = [
    "Executor",
    "IngestReceipt",
    "ManifestMinter",
    "SessionAcl",
    "SessionContext",
    "SourceGrant",
    "answer_demo_question",
    "classify_bytes",
    "classify_grid",
    "ingest_batch",
    "ingest_csv_bytes",
    "infer_contract",
    "intersect_space_grants",
    "get_serving_engine",
    "list_bronze_tables",
    "load_pipeline_by_name",
    "load_pipeline_yaml",
    "map_ask_response_to_envelope",
    "resolve_session_acl",
    "run_promote",
    "sign_gold_metric",
    "validate_pipeline_dict",
    "write_bronze_rows",
]
