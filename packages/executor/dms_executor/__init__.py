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
from dms_core.ask import AskServiceError, GroundingRefused
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
from dms_executor.demo_ask import answer_demo_question, normalize_ask_question
from dms_executor.demo_grants import DemoSessionStore, ingested_bronze_tables
from dms_executor.demo_warehouse import DEMO_TABLES, ensure_demo_warehouse, execute_sql
from dms_executor.envelope import (
    assert_envelope_valid,
    build_answer_envelope,
    normalize_contributing_sources,
)
from dms_executor.library_tree import build_library_tree
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
from dms_executor.reveal import allowlisted_roots, is_filesystem_uri, reveal_path
from dms_executor.triage import classify_bytes, classify_grid
from dms_executor.warehouse_browse import (
    list_warehouse_tables,
    preview_bronze_table,
    preview_warehouse_table,
)

logger = logging.getLogger(__name__)

#: The demo tenant and its single steward. Real multi-tenancy arrives with the
#: Postgres control plane (P-DMS-2); until then every session is this user.
DEMO_TENANT_ID = "tenant_demo"
DEMO_USER_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"




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
        session_store: Any | None = None,
    ) -> None:
        self._cortex = cortex
        self._minter = minter or ManifestMinter(openvault_url=openvault_url)
        self._preferred_openvault_url = openvault_url
        self._warehouse = Path(warehouse_path) if warehouse_path else None
        self._bound_sessions: set[str] = set()
        # The Space boundary reads its facts from here. Defaults to the DR-0002
        # seed so the boundary holds without Postgres; swap for a Postgres-backed
        # store when P-DMS-2 lands, without touching the serving path.
        self._session_store = session_store or DemoSessionStore(
            # Bound to this Executor's warehouse, not the process-wide default,
            # so an Executor pointed at another warehouse grants that one's
            # uploads rather than the demo warehouse's.
            uploads=lambda: ingested_bronze_tables(self._warehouse)
        )
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

    def grantable_tables(self, *, space_id: str | None) -> list[str]:
        """Tables this Space may read at all, before any grounding selection.

        ``None`` means no Space was chosen, which is the personal context: the
        demo set stands. A Space id that exists grants what DR-0002 gave it. A
        Space id that does not exist grants nothing - an unknown Space must not
        widen into full warehouse access, which is what made the label
        decorative.
        """
        if space_id is None:
            # The demo set plus anything uploaded. An upload has no data_sources
            # row, so it is grantable only through the store — without this,
            # grounding on your own file outside a Space had nothing to grant.
            uploaded = {
                g.table_name
                for g in self._session_store.list_user_source_grants(
                    DEMO_TENANT_ID, DEMO_USER_ID
                )
                if g.table_name and g.table_name not in DEMO_TABLES
            }
            return sorted({*DEMO_TABLES, *uploaded})
        ctx = intersect_space_grants(
            self._session_store,
            tenant_id=DEMO_TENANT_ID,
            user_id=DEMO_USER_ID,
            space_id=space_id,
            # Only the grant set is read back here; the real session id is
            # assigned by demo_acl, which knows the grounding scope too.
            session_id="_grantable_probe",
            pool_id="default",
        )
        return sorted(resolve_session_acl(ctx).row_predicates)

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

        A selected table that cannot be granted is **refused, not dropped**.
        Dropping it silently emptied the selection, and an empty selection fell
        back to the full set — so ticking one uploaded file, which is never in
        the demo set, granted the entire demo warehouse while the UI read
        "Grounded in 1 file". Widening silently is the defect; refusing loudly
        is acceptable (R-0005), provided the refusal names what to do next.

        No selection at all still means no narrowing. "Ground in nothing" is not
        the same request as "ground in something I cannot have".

        With a ``space_id`` the grantable set is the Space's grants, not the
        whole warehouse (DR-0002). Without one the caller is outside any Space
        and the demo set stands - that is the personal context, not a boundary
        being skipped.
        """
        grantable = self.grantable_tables(space_id=space_id)
        selection = [t for t in (tables or []) if t]
        ungrantable = [t for t in selection if t not in grantable]
        if ungrantable:
            raise GroundingRefused(ungrantable=ungrantable, grantable=grantable)
        # An upload is grantable on request but is not part of the default
        # readable set: asking with nothing ticked must not quietly widen the
        # manifest to every file anyone has ever uploaded.
        default_readable = [t for t in grantable if t in DEMO_TABLES]
        readable = selection or default_readable
        # A different manifest must be a different bound session — reusing the id
        # would serve the question under whatever manifest happened to be bound
        # first. That guard covered the grounding scope but not the Space, so
        # switching Space inside one chat was served under the Space bound
        # first: the boundary held at mint time and leaked at bind time.
        base = session_id or f"ses_{uuid4().hex[:16]}"
        parts = []
        if space_id:
            parts.append(f"space:{space_id}")
        if selection:
            parts.append(f"scope:{'+'.join(sorted(selection))}")
        sid = f"{base}::{'::'.join(parts)}" if parts else base
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
            # Carry the engine's own message. This used to raise
            # f"status={status!r}" and drop ``result.error`` on the floor, so a
            # submit that failed because it *timed out* arrived indistinguishable
            # from one refused on policy - which is how #43 stayed misdiagnosed:
            # the diagnosis was in the field being discarded.
            reason = getattr(result, "error", None)
            detail = f"status={status!r}"
            if reason:
                detail = f"{detail} error={reason}"
            raise AskServiceError(str(code), detail)
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
        question = normalize_ask_question(question)
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
        return map_ask_response_to_envelope(
            resp,
            space_id=space_id,
            session_id=acl.session_id,
            # The manifest that was actually minted, so the count the viewer
            # reads comes from the grant and not from the request.
            grounded_tables=sorted(acl.row_predicates),
            question=question,
        )

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
    "catalog": "L1_GOVERNED_METRIC",  # META-01 semantic browse — not FreeRoute SQL
    "query_skill": "L2_VALIDATED",
    "session": "L2_VALIDATED",
    "generated": "L2_VALIDATED",
    "abstain": "ABSTAIN",
    "blocked": "ABSTAIN",
}


def _chart_from_cortex_spec(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    """Map Cortex chart_spec (type/x_label/y_label) to DMS SimpleChart shape."""
    if not isinstance(spec, dict):
        return None
    ctype = str(spec.get("type") or "").lower()
    title = str(spec.get("title") or "Result")
    if ctype == "bignum":
        return {
            "kind": "bignum",
            "value": spec.get("value"),
            "label": str(spec.get("label") or ""),
            "title": title,
        }
    x = spec.get("x_label")
    y = spec.get("y_label")
    if ctype in ("bar", "line", "hbar") and x and y:
        kind = "line" if ctype == "line" else ("hbar" if ctype == "hbar" else "bar")
        return {"kind": kind, "x": str(x), "y": str(y), "title": title}
    return None


def _chart_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Fallback when Cortex omits chart_spec: category + measure → hbar."""
    if not rows:
        return None
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
        return {"kind": "hbar", "x": cat_key, "y": num_key, "title": "Result"}
    return None


def map_ask_response_to_envelope(
    resp: AskResponse,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    grounded_tables: list[str] | None = None,
    question: str | None = None,
    competing_scopes: list[str] | None = None,
) -> dict[str, Any]:
    """Map contract Answer-shaped AskResponse into UI envelope."""
    from dms_executor.envelope import (
        _DOC_ROUTE_KINDS,
        assert_envelope_valid,
        build_answer_envelope,
        normalize_badge,
        normalize_contributing_sources,
        unmapped_badge,
    )

    badge_raw = resp.badge
    route_l = (resp.route or "").lower()
    if not badge_raw:
        if resp.abstained or route_l in ("abstain", "blocked", "needs_clarification"):
            badge_raw = "abstain"
        else:
            badge_raw = "l2_validated"
    # Unknown engine badge => ABSTAIN, not L2_VALIDATED. See normalize_badge():
    # defaulting an unrecognised badge to a *confident* one means the failure
    # mode of DMS lagging Cortex is a green badge over an unvalidated answer.
    # Go through envelope.py rather than this module's own _BADGE_MAP copy.
    # Two maps for one vocabulary is a drift waiting to happen, and the drift
    # would be invisible: both sides would still produce a legal badge.
    unknown_badge = unmapped_badge(badge_raw, abstained=bool(resp.abstained))
    badge = normalize_badge(badge_raw, abstained=bool(resp.abstained))
    abstained = bool(resp.abstained) or badge == "ABSTAIN"
    if abstained:
        badge = "ABSTAIN"
    text = resp.answer or ("Abstained." if abstained else "")
    if unknown_badge:
        # Name it. An abstention whose reason is "the engine said something this
        # build does not understand" is actionable; a bare "Abstained." is not,
        # and would read on stage as the product simply failing.
        text = (
            f"I can't vouch for this answer: the engine returned a confidence "
            f"level this build does not recognise ({unknown_badge!r}). "
            f"Rather than show you a number under a badge I cannot stand behind, "
            f"I'm abstaining. This usually means DMS is older than the engine."
        )
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
    rows = list(resp.rows or [])
    chart = None
    if rows and not abstained:
        chart = _chart_from_cortex_spec(resp.chart_spec) or _chart_from_rows(rows)
    assumptions: list[str] = []
    if resp.assumptions:
        if isinstance(resp.assumptions, str):
            assumptions = [resp.assumptions]
        else:
            assumptions = list(resp.assumptions)
    assumptions.append("live Cortex ask")
    sources = normalize_contributing_sources(
        resp.contributing_sources, space_id=space_id
    )
    token = resp.drillthrough_token
    if sources and not token:
        # E7: never claim sources without a drillthrough token.
        sources = []
    route = (resp.route or "").lower()
    sql_out = resp.sql_used
    if not abstained and not (sql_out and str(sql_out).strip()):
        if route in _DOC_ROUTE_KINDS or any(s.get("kind") != "sql" for s in sources):
            sql_out = "-- document retrieval (no SQL)"
        else:
            sql_out = "-- live ask (SQL not returned)"
    env = build_answer_envelope(
        answer_id=resp.receipt_id or f"ans_live_{session_id or 'x'}",
        text=text,
        values=values,
        badge=badge,
        abstained=abstained,
        sql_used=None if abstained else sql_out,
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
        grounded_tables=grounded_tables,
        question=question,
        competing_scopes=competing_scopes,
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
    "assert_envelope_valid",
    "build_answer_envelope",
    "normalize_contributing_sources",
    "build_library_tree",
    "classify_bytes",
    "classify_grid",
    "ingest_batch",
    "ingest_csv_bytes",
    "infer_contract",
    "intersect_space_grants",
    "get_serving_engine",
    "list_bronze_tables",
    "list_warehouse_tables",
    "load_pipeline_by_name",
    "load_pipeline_yaml",
    "map_ask_response_to_envelope",
    "preview_bronze_table",
    "preview_warehouse_table",
    "allowlisted_roots",
    "is_filesystem_uri",
    "reveal_path",
    "resolve_session_acl",
    "run_promote",
    "sign_gold_metric",
    "validate_pipeline_dict",
    "write_bronze_rows",
]
