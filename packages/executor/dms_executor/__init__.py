"""Query execution — the only DMS package allowed to call duckdb.execute.

Manifest minting + signing + submit() live here. Path enforcement is Cortex's job.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from cortex_client import CortexClient
from cortex_client.compute import insights_budget_stop
from cortex_client.models import AskRequest, AskResponse, LedgerAppendRequest
from cortex_contract.execution import PoolSpec, SubmitRequest
from dms_core.ask import AskServiceError, ExecutedTrace, GroundingRefused
from dms_core.ports import ServingEnginePort

from dms_executor import action_keys as action_keys
from dms_executor import executed_trace
from dms_executor.abstain import (
    backstop_missing_ticket,
    build_abstain,
    refuse_ungranted_answer,
)
from dms_executor.acl import (
    SessionContext,
    SourceGrant,
    intersect_space_grants,
    mint_manifest_for_session,
    resolve_session_acl,
)
from dms_executor.ao_compile import compile_source
from dms_executor.batch_ingest import ingest_batch
from dms_executor.bronze import (
    IngestReceipt,
    ingest_csv_bytes,
    list_bronze_tables,
    list_source_pulls,
    write_bronze_rows,
)
from dms_executor.bronze_sheet_ask import (
    bronze_grant_abstain,
    bronze_lane_table,
    bronze_sheet_lane_enabled,
    maybe_bronze_sheet_ask,
)
from dms_executor.contract_infer import infer_contract
from dms_executor.db_connector import (
    DEFAULT_MAX_ROWS,
    SourceConfig,
    SourceConnectionError,
    UnknownSourceTable,
    ingest_source_database,
    list_source_keys,
)
from dms_executor.demo_ask import (
    answer_demo_question,
    normalize_ask_question,
    with_grounded_scope,
)
from dms_executor.demo_grants import (
    DemoSessionStore,
    canonical_space_id,
    ingested_bronze_tables,
    is_demo_space,
)
from dms_executor.demo_pack import (
    curated_pack_metric_miss,
    curated_pack_present,  # noqa: F401 - re-exported; files-on-disk check
    curated_pack_status,  # noqa: F401 - re-exported; wiring health reads it
    is_curated_l0_without_pack_metric,
    is_uncertified_paraphrase,
    maybe_pack_ask,
    maybe_uncertified_refuse_ask,
)
from dms_executor.demo_warehouse import (
    DEMO_TABLES,
    SERVING_DIALECT,
    ReservedParamError,
    connect_serving,
    ensure_demo_warehouse,
    execute_sql,
    read_health_engine_clock,
    sql_has_reserved_as_of,
    stamp_engine_clock,
    warehouse_path,
)
from dms_executor.envelope import (
    RESERVED_PARAM_AS_OF,
    assert_envelope_valid,
    build_answer_envelope,
    chart_from_rows,
    normalize_contributing_sources,
    reserved_as_of_abstain,
)
from dms_executor.generative_ask import (
    maybe_generative_ask,
    path_miss_envelope,
    untyped_numeric_reason,
    with_served_attribution,
)
from dms_executor.grant_struct import (
    customer_grant_reason,
    relation_gap,
    serve_gap,
    sql_refusal_envelope,
)
from dms_executor.lake_registry import (
    SERVING_DEADLINE_EXCEEDED_REASON,
    SERVING_DEADLINE_RESERVE_REASON,
    SERVING_ERRORS,
    AskControl,
    ServingDeadlineExceeded,
    ServingDeadlineReserve,
    ServingLeaseCap,
    ServingLeaseQueueFull,
    ServingWaitCancelled,
    ask_disconnected,
    ask_took_serving_lease,
    bind_ask_control,
    bind_ask_deadline,
    block_serving_lease,
    current_ask_control,
    raise_if_ask_stopped,
    reset_ask_control,
    reset_ask_deadline,
    reset_serving_lease_block,
    serving_deadline_missed,
    serving_deadline_s,
)
from dms_executor.library_tree import build_library_tree
from dms_executor.manifest import (
    ManifestMinter,
    OpenVaultTokenError,
    SecurityEvent,
    SessionAcl,
    SubmitError,
    classify_submit_error,
    should_rement,
)
from dms_executor.name_echo import (
    begin_grant_turn,
    end_grant_turn,
    hide_echo,
    note_turn_grants,
    turn_grants,
)
from dms_executor.openvault_discovery import local_start_command, probe_openvault
from dms_executor.pipeline_loader import (
    load_pipeline_by_name,
    load_pipeline_yaml,
    validate_pipeline_dict,
)
from dms_executor.promote import (
    LakeBusy,
    latest_promote_receipt,
    run_promote,
    sign_gold_metric,
)
from dms_executor.reveal import (
    allowlisted_roots,
    is_filesystem_uri,
    resolve_allowlisted_file,
    reveal_path,
)
from dms_executor.session_followup import maybe_followup, snapshot_turn, turn_key
from dms_executor.source_links import verify_source_links
from dms_executor.space_ontology import (
    REASON_STORE_UNAVAILABLE,
    bronze_catalog,
    check_sql_against_space,
    derive_and_store,
    load_space_ontology,
    ontology_store,
    set_ontology_store,
    source_identity,
    space_ontology_views,
    stored_catalogs,
    stored_catalogs_by_source,
)
from dms_executor.sql_currency import tables_read as tables_read_by_sql
from dms_executor.sql_currency import tables_read_checked as tables_read_checked_by_sql
from dms_executor.sql_loop import apply_sql_credit, extract_dialect
from dms_executor.triage import classify_bytes, classify_grid
from dms_executor.verified_queries import (
    list_verified_queries,
    maybe_save_checked_sql,
    maybe_verified_ask,
    openvault_embedder,
    register_verified_query,
)
from dms_executor.warehouse_browse import (
    list_promote_targets,
    list_warehouse_tables,
    preview_bronze_table,
    preview_warehouse_table,
)
from dms_executor.warehouse_identity import (
    bronze_missing_from_serving,
    identity_check,
    ingest_warehouse_path,
    serving_sync_state,
    serving_warehouse_path,
    sync_bronze_to_serving,
)
from dms_executor.xlsx_orch import run_crosscheck, run_extract, run_golden

logger = logging.getLogger(__name__)


def _grant_name_ok(name: str) -> bool:
    """One or two identifier parts. A digit-first part is not granted.

    ``bronze.2024_sales`` made Cortex refuse the whole Space manifest. Unicode
    letters stay: DuckDB keeps "Ä" and "ä" as two tables, and a grant check
    that drops them cannot serve a granted Unicode table.
    """
    parts = str(name).split(".")
    if not 1 <= len(parts) <= 2:
        return False
    for part in parts:
        if not part or not (part[0].isalpha() or part[0] == "_"):
            return False
        if any(not (c.isalnum() or c == "_") for c in part):
            return False
    return True


#: The demo tenant and its single steward. Real multi-tenancy arrives with the
#: Postgres control plane (P-DMS-2); until then every session is this user.
DEMO_TENANT_ID = "tenant_demo"
DEMO_USER_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def default_readable_tables(granted: list[str], *, space_id: str | None) -> list[str]:
    """What a turn reads when nothing was ticked.

    A seeded demo Space (and no Space) reads the demo spine only. A Space
    created for its own data reads the tables it was granted: its bronze,
    not the demo spine. Never wider than ``granted``.
    """
    if is_demo_space(space_id):
        return [t for t in granted if t in DEMO_TABLES]
    return list(granted)


def _insights_compute_seam(
    cortex: Any,
    question: str,
    *,
    session_id: str | None,
    space_id: str | None,
    ontology: dict[str, Any],
) -> dict[str, Any] | None:
    """Ask-path planner: Cortex Insights generate + ranking. Never /dms/query.

    Calls ``compute_insights`` only. Fakes without that method miss (no
    ``compute_query``, so no leftover /dms/query). Timeout is the Insights
    bound inside the client, not the 120s contract timeout. Pre-gates still
    run because this stays a callable — ``maybe_generative_ask`` returns
    early on ``compute is None`` before paraphrase and _UNSURE_ASK.
    """
    fn = getattr(cortex, "compute_insights", None)
    if not callable(fn):
        return None
    onto: dict[str, Any] | None = ontology
    feedback = None
    asked = question
    examples = ""
    ai_rows: list[dict[str, Any]] = []
    # vq_examples is a writer prompt, not an Insights field. Pin 279cbd85
    # stays quiet because the key is removed before the POST.
    # verified_examples is the same kind of lift: question text, not a body field.
    from cortex_client.insights import VERIFIED_EXAMPLES_FIELD

    _hidden = {"sql_loop_feedback", "sql_vote_prompt", "sql_vote_kind", "vq_examples"}
    if isinstance(ontology, dict) and (
        _hidden.intersection(ontology) or VERIFIED_EXAMPLES_FIELD in ontology
    ):
        from cortex_client.compute import lift_verified_examples

        lifted, ai_rows = lift_verified_examples(ontology)
        if isinstance(lifted, dict) and _hidden.intersection(lifted):
            raw = lifted.get("sql_loop_feedback")
            feedback = raw if isinstance(raw, dict) else None
            vote_prompt = lifted.get("sql_vote_prompt")
            if isinstance(vote_prompt, str) and vote_prompt.strip():
                asked = vote_prompt
            raw_examples = lifted.get("vq_examples")
            if isinstance(raw_examples, str) and raw_examples.strip():
                examples = raw_examples.strip()
            onto = {k: v for k, v in lifted.items() if k not in _hidden}
        else:
            onto = lifted
    if examples:
        asked = f"{asked.rstrip()}\nExamples:\n{examples}"
    if ai_rows:
        from dms_executor.ai_first import understand_question

        asked = str(understand_question(asked, examples=ai_rows)["question"])
    try:
        kwargs: dict[str, Any] = {
            "session_id": session_id,
            "space_id": space_id,
            "ontology": onto,
        }
        if feedback is not None:
            kwargs["sql_feedback"] = feedback
        try:
            return fn(asked, **kwargs)
        except TypeError:
            return fn(
                asked,
                session_id=session_id,
                space_id=space_id,
                ontology=onto,
            )
    except (
        ServingLeaseCap,
        ServingLeaseQueueFull,
        ServingWaitCancelled,
        ServingDeadlineExceeded,
        ServingDeadlineReserve,
    ):
        raise
    except Exception as exc:  # noqa: BLE001 — miss into contract ask, do not 503
        if ask_disconnected():
            raise ServingWaitCancelled() from exc
        interrupted = type(exc).__name__ == "InterruptException"
        if interrupted or serving_deadline_missed():
            raise ServingDeadlineExceeded() from exc
        if ask_took_serving_lease() and _is_timeout_exc(exc):
            raise ServingDeadlineExceeded() from exc
        return None


def _is_timeout_exc(exc: BaseException) -> bool:
    name = type(exc).__name__
    return name in {
        "TimeoutError",
        "TimeoutException",
        "ReadTimeout",
        "ConnectTimeout",
        "WriteTimeout",
        "PoolTimeout",
    }


def _seen(
    seen: list[dict[str, Any] | None], payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Record the Insights payload for SERVED-ATTR-01, then pass it on."""
    seen.append(payload)
    return payload


class Executor:
    """Serving engine + manifest-aware submit path."""

    def __init__(
        self,
        *,
        cortex: CortexClient | None = None,
        minter: ManifestMinter | None = None,
        warehouse_path: Path | str | None = None,
        dialect: str | None = SERVING_DIALECT,
        openvault_url: str | None = None,
        fetch_key_on_start: bool = False,
        session_store: Any | None = None,
    ) -> None:
        self._cortex = cortex
        self._minter = minter or ManifestMinter(openvault_url=openvault_url)
        self._preferred_openvault_url = openvault_url
        self._warehouse = Path(warehouse_path) if warehouse_path else None
        self.dialect = dialect
        self._bound_sessions: set[str] = set()
        self._turns: dict[tuple[str, str], dict[str, Any]] = {}
        # The Space boundary reads its facts from here. Defaults to the DR-0002
        # seed so the boundary holds without Postgres; swap for a Postgres-backed
        # store when P-DMS-2 lands, without touching the serving path.
        self._session_store = session_store or DemoSessionStore(
            # Bound to this Executor's warehouse, not the process-wide default,
            # so an Executor pointed at another warehouse grants that one's
            # uploads rather than the demo warehouse's.
            uploads=lambda: ingested_bronze_tables(self._warehouse),
            warehouse=self._warehouse,
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
        from dms_executor.schema_context import stop_index_builds

        stop_index_builds()
        self._minter.close()
        self._bound_sessions.clear()
        self._turns.clear()
        from dms_executor.lake_registry import close_lakes

        close_lakes()

    def begin_client_watch(self) -> tuple[Any, AskControl]:
        """Bind the ask-thread cancel flag. The route sets ``disconnected``."""
        ctrl = AskControl()
        return bind_ask_control(ctrl), ctrl

    def end_client_watch(self, token: Any) -> None:
        reset_ask_control(token)

    def _store_turn(
        self, session_id: str | None, space_id: str | None, env: dict[str, Any]
    ) -> None:
        key = turn_key(session_id, space_id)
        if key is None:
            return
        snap = snapshot_turn(env)
        if snap is None:
            self._turns.pop(key, None)
            return
        self._turns[key] = snap

    def execute(
        self, sql: str, *, grantable: set[str] | None = None
    ) -> list[dict[str, Any]]:
        if sql_has_reserved_as_of(sql):
            raise ReservedParamError(RESERVED_PARAM_AS_OF)
        gap = serve_gap(
            sql,
            grantable=set(grantable or ()),
            dialect=self.dialect,
        )
        if gap:
            raise SecurityEvent(gap, gap)
        ensure_demo_warehouse(self._warehouse)
        return execute_sql(sql, path=self._warehouse, product=True)

    def answer_user_sql(
        self,
        sql: str,
        *,
        space_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """User SQL. A real $as_of placeholder is ABSTAIN and is not executed."""
        if sql_has_reserved_as_of(sql):
            return reserved_as_of_abstain(
                space_id=space_id,
                session_id=session_id,
                route="user_sql",
                question=sql,
            )
        try:
            grants = set(self.grantable_tables(space_id=space_id))
        except Exception:  # noqa: BLE001 -- refuse closed, do not run the SQL
            grants = set()
        gap = serve_gap(sql, grantable=grants, dialect=self.dialect)
        if gap:
            return sql_refusal_envelope(
                reason=gap,
                shown=customer_grant_reason(gap),
                space_id=space_id,
                session_id=session_id,
                route="user_sql",
                question=None,
            )
        rows = self.execute(sql, grantable=grants)
        env = build_answer_envelope(
            answer_id="ans_user_sql",
            text="Query result.",
            badge="L2_VALIDATED",
            rows=rows,
            sql_used=sql,
            space_id=space_id,
            session_id=session_id,
            ask_mode="live",
            route="user_sql",
            question=sql,
        )
        stamp_engine_clock(env)
        assert_envelope_valid(env)
        return env

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
            granted = sorted({*DEMO_TABLES, *uploaded})
            # NAME-ECHO-01: the scrub reads this set; it never derives its own.
            note_turn_grants(self, space_id, granted)
            return granted
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
        granted = sorted(resolve_session_acl(ctx).row_predicates)
        # F-c: one granted name that breaks the SHARED NAMING RULE (a legacy
        # ``bronze.2024_sales``) made ``cortex_row_predicates`` refuse the whole
        # Space's manifest, so every ask abstained ``submit_failed``. Such a table
        # is not grantable (new ingests never create one); it is named, not hidden.
        bad = [t for t in granted if not _grant_name_ok(t)]
        if bad:
            logger.warning(
                "space %s: not granting %s (name breaks the naming rule; re-ingest to rename)",
                space_id,
                ", ".join(bad),
            )
        granted = [t for t in granted if t not in bad]
        # NAME-ECHO-01: the scrub reads this set; it never derives its own.
        note_turn_grants(self, space_id, granted)
        return granted

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
        default_readable = default_readable_tables(grantable, space_id=space_id)
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

    def _submit_verified_sql(
        self,
        sql: str,
        *,
        space_id: str | None,
        session_id: str | None,
        tables: list[str] | None,
    ) -> Any:
        """Bind once, then Cortex submit of steward SQL. No local DuckDB execute."""
        acl = self.demo_acl(session_id=session_id, space_id=space_id, tables=tables)
        if acl.session_id not in self._bound_sessions:
            self.bind_session(acl)
        return self.submit_sql(sql, acl)

    def _ledger_verified_query(
        self,
        *,
        asset_sql: str,
        run_id: str,
        space_id: str | None,
        session_id: str | None,
        event_type: str = "ask.verified_query",
    ) -> Any:
        """Append a verified-ask receipt to the Cortex ledger. No local chain."""
        if self._cortex is None:
            raise RuntimeError("CortexClient required for verified ledger")
        appended = self._cortex.ledger_append(
            LedgerAppendRequest(
                event_type=event_type,
                payload={
                    "sql": asset_sql,
                    "run_id": run_id,
                    "space_id": space_id,
                    "session_id": session_id,
                },
                actor=DEMO_USER_ID,
            )
        )
        # BANK-02: where in the chain this ask's entry sits, for the audit export.
        executed_trace.record_ledger(
            getattr(appended, "entry_id", None), getattr(appended, "seq", None)
        )
        return appended

    def live_ask(
        self,
        question: str,
        *,
        space_id: str | None = None,
        session_id: str | None = None,
        tables: list[str] | None = None,
        ask_path: str | None = None,
    ) -> dict[str, Any]:
        """``_live_ask``, then SERVED-ATTR-01 (dms#305) on whatever it returned.

        Every ask envelope carries ``served_attribution``. When the Insights
        generate seam ran, its setup fields reach the envelope on every path,
        including the contract-ask fallback after a generative miss.
        """
        import httpx
        from cortex_client.compute import (
            begin_answer_model_calls,
            recorded_model_calls,
        )

        begin_answer_model_calls()
        # BANK-02: what actually runs for this ask is recorded as it runs, apart
        # from the envelope (see executed_trace). The caller reads it with
        # take_executed() right after this returns or raises.
        executed_trace.begin()
        seen: list[dict[str, Any] | None] = []
        # Whole-ask clock. Each Insights leg keeps its own 8s HTTP timeout.
        ask_budget_s = serving_deadline_s()
        deadline = time.monotonic() + ask_budget_s
        deadline_tokens = bind_ask_deadline(deadline)
        own_control = current_ask_control() is None
        control_token: Any = None
        ctrl = current_ask_control()
        if ctrl is None:
            ctrl = AskControl()
            control_token = bind_ask_control(ctrl)
        ctrl.deadline = deadline
        ctrl.start()
        example_token = None
        # NAME-ECHO-01: the grant set grantable_tables resolves in this turn is
        # recorded here, so the scrub and the served-SQL check read it rather
        # than opening a connection of their own.
        grant_turn = begin_grant_turn(self, space_id)
        from dms_executor.route_stamp import freeze_route

        try:
            from dms_executor.ai_first import ai_first_enabled, begin_example_turn

            if ai_first_enabled():
                example_token = begin_example_turn()
            try:
                try:
                    env = self._live_ask(
                        question,
                        space_id=space_id,
                        session_id=session_id,
                        tables=tables,
                        ask_path=ask_path,
                        seen=seen,
                    )
                    if serving_deadline_missed():
                        raise ServingDeadlineExceeded()
                except (TimeoutError, httpx.TimeoutException) as exc:
                    if ask_disconnected():
                        raise ServingWaitCancelled() from exc
                    if ask_took_serving_lease():
                        raise ServingDeadlineExceeded() from exc
                    raise
            except OpenVaultTokenError as exc:
                # Every lane needs the signing key; no lane may relabel its absence.
                env = build_abstain(
                    reason="openvault_mint",
                    question=question,
                    stage="openvault",
                    answer_id="ans_ov_mint",
                    text=(
                        "I can't answer this: DMS could not get its OpenVault "
                        f"signing key ({exc.code}). No fallback answer was used."
                    ),
                    values=[],
                    rows=[],
                    sql_used=None,
                    assumptions=[exc.code, "no generative fallback", "no demo fallback"],
                    space_id=space_id,
                    session_id=session_id,
                    ask_mode="live",
                    route="abstain",
                )
                assert_envelope_valid(env)
            except (
                ServingLeaseCap,
                ServingLeaseQueueFull,
                ServingDeadlineExceeded,
                ServingDeadlineReserve,
            ) as exc:
                # build_abstain writes the ticket when DMS_CLOOP_B is on.
                # This exit does not log. While the envelope is built, another
                # serving open must not wait again.
                token = block_serving_lease(exc.code)
                try:
                    if exc.code == ServingLeaseQueueFull.code:
                        text = (
                            "I can't answer this right now: the serving wait "
                            f"queue is full ({exc.code}). No figure was returned."
                        )
                        answer_id = "ans_serving_lease_queue_full"
                    elif exc.code == SERVING_DEADLINE_EXCEEDED_REASON:
                        text = (
                            "I can't answer this right now: the ask ran out of "
                            f"time after a serving connection was granted ({exc.code}). "
                            "No figure was returned."
                        )
                        answer_id = "ans_serving_deadline_exceeded"
                    elif exc.code == SERVING_DEADLINE_RESERVE_REASON:
                        text = (
                            "I can't answer this right now: there is not enough "
                            f"time left to start ({exc.code}). No figure was returned."
                        )
                        answer_id = "ans_serving_deadline_reserve"
                    else:
                        text = (
                            "I can't answer this right now: serving connections are "
                            f"at the cap ({exc.code}). No figure was returned."
                        )
                        answer_id = "ans_serving_lease_cap"
                    deadline_hit = exc.code in {
                        SERVING_DEADLINE_EXCEEDED_REASON,
                        SERVING_DEADLINE_RESERVE_REASON,
                    }
                    env = build_abstain(
                        reason=exc.code,
                        question=question,
                        stage="serving_lease",
                        answer_id=answer_id,
                        text=text,
                        values=[],
                        rows=[],
                        sql_used=None,
                        assumptions=[exc.code],
                        space_id=space_id,
                        session_id=session_id,
                        ask_mode="live",
                        route="abstain",
                        abstain_reason=exc.code if deadline_hit else None,
                    )
                    if deadline_hit:
                        env["serving_deadline_s"] = ask_budget_s
                    assert_envelope_valid(env)
                finally:
                    reset_serving_lease_block(token)
            # An answer whose served SQL reads outside the turn's grant is a
            # breach or a misread grant. It abstains; the relation is not hidden.
            refused = refuse_ungranted_answer(env, question=question, grants=turn_grants())
            if refused is not env:
                env = refused
                self._store_turn(session_id, space_id, env)
            stamp_engine_clock(env)
            payload = next((p for p in reversed(seen) if isinstance(p, dict)), None)
            stamped = with_served_attribution(env, payload)
            out = stamped if stamped is not None else env
            if isinstance(out, dict):
                raw_loop = out.get("loop")
                apply_sql_credit(
                    out,
                    payload if isinstance(payload, dict) else None,
                    raw_loop if isinstance(raw_loop, list) else None,
                    dialect=extract_dialect(self.dialect),
                )
            from dms_core.ask import lane_for_route
            from dms_core.pii import mask_unknown_keys

            out["model_calls"] = recorded_model_calls()
            # Lane is the route this path produced. A payload lane is not kept.
            mapped = lane_for_route(out.get("route"))
            if mapped is not None:
                out["lane"] = mapped
            else:
                out.pop("lane", None)
            freeze_route(out)
            masked = mask_unknown_keys(out)
            backstop_missing_ticket(masked, question=question)
            # Last step of the turn, after every loop re-attach. The grant set is
            # the one this turn resolved; none resolved grants nothing.
            if isinstance(masked, dict):
                hide_echo(masked, "", grants=turn_grants())
            return masked
        finally:
            if example_token is not None:
                from dms_executor.ai_first import end_example_turn

                end_example_turn(example_token)
            end_grant_turn(grant_turn)
            if own_control and control_token is not None:
                reset_ask_control(control_token)
            reset_ask_deadline(deadline_tokens)

    def _live_ask(
        self,
        question: str,
        *,
        space_id: str | None = None,
        session_id: str | None = None,
        tables: list[str] | None = None,
        ask_path: str | None = None,
        seen: list[dict[str, Any] | None],
    ) -> dict[str, Any]:
        """Mint → session_bind (once per session) → contract ask.

        ``tables`` narrows the manifest to the files the user grounded the
        question in, so the scope is enforced by the engine rather than
        suggested to the model.

        ``ask_path``: product (default) = certified-first (VQ, pack, planted
        refuse), then the generative pre-gates, then Insights generate+ranking
        (never /dms/query), then the Cortex contract ask on a miss. exact =
        VQ/pack/refuse only. generative = skip VQ/pack; past the planted refuse
        and the pre-gates it uses Insights only and fail-closes named. Isolated
        lanes miss as ABSTAIN (no Cortex mix); over HTTP they run only where the
        server sets DMS_HARNESS_ASK_PATHS.

        GEN-RESTORE-01: no lane POSTs Cortex ``/dms/query`` and no lane binds a
        keyword plan (``bind_on_miss=False`` everywhere).
        """
        if self._cortex is None:
            raise RuntimeError("CortexClient required for live_ask")
        raise_if_ask_stopped()
        question = normalize_ask_question(question)
        ladder = (ask_path or "product").strip().lower()
        if ladder not in {"product", "exact", "generative"}:
            ladder = "product"
        certified_first = ladder != "generative"
        allow_gen = ladder != "exact"
        allow_cortex = ladder == "product"
        # A real Cortex client is the armed product path. Demo-pack abstains
        # must not gate it. Test fakes are not a CortexClient, so the planted
        # refuses stay in front of those asks.
        armed_product = isinstance(self._cortex, CortexClient)
        allow_follow = ladder == "product"
        allow_bronze = ladder == "product"

        # CCA-05 - settle the ambiguous filters before anything executes.
        #
        # "Rental across SEA, commercial only" against a warehouse with no
        # asset-class column runs a real query over real rows and returns a real
        # total that is not the one asked for. No check further down can tell,
        # because nothing about the number looks wrong. So the constraints are
        # bound to landed values first, and an ask whose binding cannot be
        # certified never reaches L0 at all.
        #
        # An ask that constrains none of the cascade stages does not engage it:
        # ``engaged`` is False, no trace is attached, and the question takes the
        # path it always took.
        #
        # The verified-query hook runs FIRST and is not gated. A steward
        # registered that exact question against that exact SQL, which is the
        # human certification the cascade is a substitute for on the inferred
        # path. Gating it would let a term the cascade cannot bind override a
        # person who already decided. F83: a hit still goes through Cortex
        # submit + ledger inside this method — it does not mint L0 locally.
        from dms_executor.cca.cascade import (
            CascadeOutcome,
            attach_cascade,
            cascade_abstain_envelope,
            cascade_enabled,
            run_cascade,
        )

        # One Space-grant lookup per ask. ``grantable_tables`` reads the
        # store and the lake (a serving lease), so a second lookup is a second
        # wait under lease contention. Every lane below reads this one result;
        # a failure is kept and raised again to the next caller, not retried.
        grants_once: list[list[str] | Exception] = []

        def space_grants() -> list[str]:
            if not grants_once:
                try:
                    got = self.grantable_tables(space_id=space_id)
                except Exception as exc:  # noqa: BLE001 -- kept for every caller
                    grants_once.append(exc)
                else:
                    # The list the caller received, including a name added
                    # after the method body noted its own copy.
                    note_turn_grants(self, space_id, got)
                    grants_once.append(got)
            found = grants_once[0]
            if isinstance(found, Exception):
                raise found
            return list(found)

        key = turn_key(session_id, space_id)
        if allow_follow:
            try:
                follow_grants = set(space_grants())
            except SERVING_ERRORS:
                raise
            except Exception:  # noqa: BLE001 -- refuse closed, do not run the SQL
                follow_grants = set()
            follow = maybe_followup(
                question,
                prior=self._turns.get(key) if key else None,
                space_id=space_id,
                session_id=session_id,
                warehouse=self._warehouse,
                tables=tables,
                grantable=follow_grants,
                dialect=self.dialect,
            )
            if follow is not None:
                self._store_turn(session_id, space_id, follow)
                return follow

        if certified_first:
            raise_if_ask_stopped()
            verified_env = maybe_verified_ask(
                question,
                space_id=space_id,
                session_id=session_id,
                warehouse=self._warehouse,
                grantable=set(space_grants()),
                tables=tables,
                submit=lambda sql: self._submit_verified_sql(
                    sql, space_id=space_id, session_id=session_id, tables=tables
                ),
                ledger_append=lambda payload: self._ledger_verified_query(
                    asset_sql=str(payload.get("sql") or ""),
                    run_id=str(payload.get("run_id") or ""),
                    space_id=space_id,
                    session_id=session_id,
                ),
                dialect=self.dialect,
            )
            if verified_env is not None:
                self._store_turn(session_id, space_id, verified_env)
                return verified_env

            # Phrase match + a failed later step is a named refusal (not None).
            # None means the phrase missed, so generative / contract ask may run.
            # DMS_AI_FIRST: a granted phrase is an example, not a submit.
            # A grant miss or a hostile phrase still goes through maybe_pack_ask.
            raise_if_ask_stopped()
            from dms_executor.ai_first import (
                ai_first_enabled,
                note_example,
                pack_example,
            )
            from dms_executor.demo_pack import lookup_pack_metric, match_pack_phrase
            from dms_executor.manifest import SecurityEvent, reject_hostile_chat_sql

            held_pack = False
            if ai_first_enabled():
                phrase = match_pack_phrase(question, tables=tables)
                if phrase is not None:
                    hit = lookup_pack_metric(
                        question,
                        grantable=set(space_grants()),
                        tables=tables,
                        dialect=self.dialect,
                    )
                    hostile = False
                    if hit is not None:
                        try:
                            reject_hostile_chat_sql(hit.sql)
                        except SecurityEvent:
                            hostile = True
                    if hit is not None and not hostile:
                        note_example(pack_example(hit, space_id))
                        held_pack = True
            if not held_pack:
                pack_env = maybe_pack_ask(
                    question,
                    space_id=space_id,
                    session_id=session_id,
                    grantable=set(space_grants()),
                    tables=tables,
                    submit=lambda sql: self._submit_verified_sql(
                        sql, space_id=space_id, session_id=session_id, tables=tables
                    ),
                    ledger_append=lambda payload: self._ledger_verified_query(
                        asset_sql=str(payload.get("sql") or ""),
                        run_id=str(payload.get("run_id") or ""),
                        space_id=space_id,
                        session_id=session_id,
                        event_type="ask.governed_metric",
                    ),
                    dialect=self.dialect,
                )
                if pack_env is not None and not (
                    armed_product and pack_env.get("abstained") is True
                ):
                    self._store_turn(session_id, space_id, pack_env)
                    return pack_env

        if not armed_product:
            refuse_env = maybe_uncertified_refuse_ask(
                question, space_id=space_id, session_id=session_id
            )
            if refuse_env is not None:
                self._store_turn(session_id, space_id, refuse_env)
                return refuse_env

        if ladder == "exact":
            # Curated l0 phrase missing from PACK_METRICS is step 1, not a
            # later-lane abstain. Other exact misses stay a plain path miss.
            if is_curated_l0_without_pack_metric(question):
                env = curated_pack_metric_miss(
                    question, space_id=space_id, session_id=session_id
                )
            else:
                env = path_miss_envelope(
                    question,
                    "exact-match miss: not a certified VQ/pack hit",
                    space_id=space_id,
                    session_id=session_id,
                )
            self._store_turn(session_id, space_id, env)
            return env

        # Narrow-only. demo_acl's default readable set is grantable intersect
        # DEMO_TABLES; an upload is readable only when explicitly selected.
        # This path used ``requested or grantable_tables(...)``, so with
        # nothing ticked the cascade and retrieve opened every bronze upload
        # tagged to the Space and sent DISTINCT samples in the Insights body.
        # Intersection with granted still narrows a selection; an unread grant
        # never falls back to the whole space.
        try:
            granted = space_grants()
        except (
            ServingLeaseCap,
            ServingLeaseQueueFull,
            ServingWaitCancelled,
            ServingDeadlineExceeded,
            ServingDeadlineReserve,
        ):
            raise
        except Exception:  # noqa: BLE001 -- empty context, never the whole space
            granted = []
        selection = [t for t in (tables or []) if t]
        requested = [t for t in selection if t in set(granted)]
        default_readable = default_readable_tables(granted, space_id=space_id)
        readable = requested or default_readable
        raise_if_ask_stopped()
        cascade = (
            run_cascade(
                question,
                warehouse=ensure_demo_warehouse(self._warehouse),
                tables=readable,
            )
            if cascade_enabled()
            else CascadeOutcome(engaged=False)
        )
        if cascade.blocked_at is not None:
            return cascade_abstain_envelope(
                cascade,
                question=question,
                space_id=space_id,
                session_id=session_id,
                # What was actually read, not what was asked for. Echoing the
                # request's own list put an ungranted table name on the envelope
                # as though the answer had been grounded on it.
                grounded_tables=requested,
            )

        asked = question
        question = with_grounded_scope(question, tables)
        # dms#284: an ungranted sheet refuses before any model call. The
        # phrase lane only names the sheet server's table. The grant is
        # relation_gap, the same keys as serve_gap. No probe SQL is built.
        # A later lane must not answer that question from another table.
        if allow_bronze:
            target = bronze_lane_table(question)
            if target is None:
                bronze_env = None
            elif not space_id:
                bronze_env = bronze_grant_abstain(
                    question,
                    reason="no_space",
                    space_id=space_id,
                    session_id=session_id,
                )
            else:
                active = set(
                    ingested_bronze_tables(self._warehouse, space_id=space_id)
                )
                bronze_readable = set(active).intersection(granted)
                sheet_gap = relation_gap(
                    target,
                    grantable=bronze_readable,
                    dialect=self.dialect,
                )
                if sheet_gap == "sql_dialect_unknown":
                    bronze_env = sql_refusal_envelope(
                        reason=sheet_gap,
                        space_id=space_id,
                        session_id=session_id,
                        route="abstain",
                        question=question,
                    )
                elif sheet_gap is None:
                    bronze_env = (
                        maybe_bronze_sheet_ask(
                            question,
                            space_id=space_id,
                            session_id=session_id,
                            warehouse=self._warehouse,
                        )
                        if bronze_sheet_lane_enabled()
                        else None
                    )
                else:
                    bronze_env = bronze_grant_abstain(
                        question,
                        reason=f"ungranted_table:{target}",
                        space_id=space_id,
                        session_id=session_id,
                    )
            if bronze_env is not None:
                env = attach_cascade(bronze_env, cascade)
                self._store_turn(session_id, space_id, env)
                return env
        from dms_executor.ontology import Ontology, onto_from_data_enabled

        data_onto = Ontology.from_store() if onto_from_data_enabled() else None
        space_onto = None
        if data_onto is None and allow_gen and not is_demo_space(space_id):
            # ONTO-DERIVE-01: the Space's own stored ontology, never the demo
            # one and never another Space's. A store that cannot be read is a
            # named ABSTAIN: answering without the join rules it holds would
            # be a silent downgrade.
            try:
                space_onto = load_space_ontology(space_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ontology store unreadable for %s: %s", space_id, exc)
                env = path_miss_envelope(
                    question,
                    f"{REASON_STORE_UNAVAILABLE}: the Space ontology store could not "
                    "be read, so no join can be checked",
                    space_id=space_id,
                    session_id=session_id,
                )
                self._store_turn(session_id, space_id, env)
                return env
        if allow_gen:
            # Insights generate + ranking. Never POST /dms/query. Nothing binds
            # on a miss (bind_on_miss=False). Pre-gates stay before this call.
            raise_if_ask_stopped()
            gen_env = maybe_generative_ask(
                question,
                space_id=space_id,
                session_id=session_id,
                warehouse=self._warehouse,
                grantable=set(readable),
                tables=tables,
                compute=lambda catalog: _seen(
                    seen,
                    _insights_compute_seam(
                        self._cortex,
                        question,
                        session_id=session_id,
                        space_id=space_id,
                        ontology=catalog,
                    ),
                ),
                submit=lambda sql: self._submit_verified_sql(
                    sql, space_id=space_id, session_id=session_id, tables=tables
                ),
                ledger_append=lambda payload: self._ledger_verified_query(
                    asset_sql=str(payload.get("sql") or ""),
                    run_id=str(payload.get("run_id") or ""),
                    space_id=space_id,
                    session_id=session_id,
                    event_type="ask.generated_ontology",
                ),
                bind_on_miss=False,
                ontology=data_onto if data_onto is not None else space_onto,
                demo_ontology_allowed=data_onto is None and space_onto is None,
                # Extract and EXPLAIN run on the DuckDB serving copy
                # (connect_serving), not on the source connector in self.dialect.
                dialect=SERVING_DIALECT,
                embedder=openvault_embedder(self._cortex),
                demo_gate=not armed_product,
            )
            if gen_env is not None:
                # cq_sku_count is not in PACK_METRICS. A generic GEN-01 abstain
                # hides that exact-match / pack-metric miss. A confident
                # generative answer is left as-is. None still reaches Cortex ask.
                # A budget stop keeps its named reason (insights_timeout:<leg>).
                if (
                    gen_env.get("abstained")
                    and not insights_budget_stop(gen_env)
                    and is_curated_l0_without_pack_metric(asked)
                ):
                    kept_loop = gen_env.get("loop")
                    gen_env = curated_pack_metric_miss(
                        asked, space_id=space_id, session_id=session_id
                    )
                    if kept_loop:
                        gen_env["loop"] = kept_loop
                env = attach_cascade(gen_env, cascade)
                self._store_turn(session_id, space_id, env)
                return env
        if not allow_cortex:
            env = path_miss_envelope(
                question,
                # Say what happened. "did not certify" implied a plan was tried;
                # since GEN-03 this lane has no plan source to try (R-0011).
                "generative miss: no plan source on this lane (Insights "
                "returned no SQL and no ranking; keyword bind is off)",
                space_id=space_id,
                session_id=session_id,
            )
            self._store_turn(session_id, space_id, env)
            return env
        acl = self.demo_acl(session_id=session_id, space_id=space_id, tables=tables)
        if acl.session_id not in self._bound_sessions:
            self.bind_session(acl)
        try:
            raise_if_ask_stopped()
            resp = self._cortex.ask(
                AskRequest(
                    question=question,
                    session_id=acl.session_id,
                    space_id=space_id,
                )
            )
        except (
            ServingLeaseCap,
            ServingLeaseQueueFull,
            ServingWaitCancelled,
            ServingDeadlineExceeded,
            ServingDeadlineReserve,
        ):
            raise
        except Exception as exc:  # noqa: BLE001
            err = classify_submit_error(exc)
            if err.code in {"session_unbound", "session_expired"}:
                self._bound_sessions.discard(acl.session_id)
                self.bind_session(acl)
                raise_if_ask_stopped()
                resp = self._cortex.ask(
                    AskRequest(
                        question=question,
                        session_id=acl.session_id,
                        space_id=space_id,
                    )
                )
            else:
                raise AskServiceError(err.code, err.detail) from exc
        raw_sql = getattr(resp, "sql_used", None)
        sql_used = str(raw_sql or "")
        if sql_used.strip() and not getattr(resp, "abstained", False):
            gap = serve_gap(
                sql_used,
                grantable=set(readable),
                dialect=self.dialect,
                warehouse=self._warehouse,
            )
            # A relation miss abstains here, before the envelope mapper can
            # demote it into a different sentence. The ticket keeps the name.
            # A granted lake alias or a bare grant falls through and stands.
            # A file or a script uses the structural code.
            from dms_executor.name_echo import (
                served_relation_miss,
                served_relations_covered,
            )

            if (
                gap
                and served_relation_miss(
                    gap, sql_used, set(readable), dialect=self.dialect
                )
                and served_relations_covered(sql_used, set(readable))
            ):
                pass
            elif gap and served_relation_miss(
                gap, sql_used, set(readable), dialect=self.dialect
            ):
                refused = refuse_ungranted_answer(
                    {
                        "abstained": False,
                        "sql_used": sql_used,
                        "answer_id": "ans_served_ungranted",
                        "text": "",
                        "rows": [],
                        "values": [],
                    },
                    question=question,
                    grants=set(readable),
                )
                refused["space_id"] = space_id
                refused["session_id"] = session_id
                route = getattr(resp, "route", None)
                if isinstance(route, str) and route.strip():
                    refused["route"] = route
                env = attach_cascade(refused, cascade)
                self._store_turn(session_id, space_id, env)
                return env
            elif gap:
                # One code. The relation name is not part of it.
                refused = sql_refusal_envelope(
                    reason=gap,
                    shown=customer_grant_reason(gap),
                    space_id=space_id,
                    session_id=session_id,
                    route="abstain",
                    question=question,
                )
                env = attach_cascade(refused, cascade)
                self._store_turn(session_id, space_id, env)
                return env
        if sql_used and not is_demo_space(space_id) and not getattr(resp, "abstained", False):
            # dms#277 F-e: the contract ask's SQL obeys the same text-numeric rule.
            num_why = untyped_numeric_reason(sql_used, self._warehouse or warehouse_path())
            if num_why:
                env = path_miss_envelope(
                    question, num_why, space_id=space_id, session_id=session_id
                )
                self._store_turn(session_id, space_id, env)
                return env
        if space_onto is not None and sql_used and not getattr(resp, "abstained", False):
            # ONTO-DERIVE-01: the contract ask never saw this Space's join rules.
            # Its SQL answers only if it passes the same rule generation does.
            join_why = check_sql_against_space(
                sql_used, space_onto, self._warehouse or warehouse_path(), readable
            )
            if join_why:
                env = path_miss_envelope(
                    question, join_why, space_id=space_id, session_id=session_id
                )
                self._store_turn(session_id, space_id, env)
                return env
        # DMS_AI_FIRST still ran the contract grant check above. An ungranted,
        # hostile, numeric, or unverified-join SQL stayed a direct abstain.
        # A SQL that would have been served is not the answer.
        from dms_executor.ai_first import ai_first_enabled, no_model_envelope

        if ai_first_enabled() and not getattr(resp, "abstained", False):
            env = attach_cascade(
                no_model_envelope(
                    question, space_id=space_id, session_id=session_id
                ),
                cascade,
            )
            self._store_turn(session_id, space_id, env)
            return env
        env = attach_cascade(
            map_ask_response_to_envelope(
                resp,
                space_id=space_id,
                session_id=acl.session_id,
                # The manifest that was actually minted, so the count the viewer
                # reads comes from the grant and not from the request.
                grounded_tables=sorted(acl.row_predicates),
                question=question,
            ),
            cascade,
        )
        self._store_turn(session_id, space_id, env)
        return env

    def offer_reconfirm(
        self,
        env: dict[str, Any],
        *,
        question: str,
        space_id: str | None = None,
        session_id: str | None = None,
        tables: list[str] | None = None,
        ask_path: str | None = None,
        cortex: Any = None,
    ) -> dict[str, Any]:
        """Attach a reconfirm offer when this envelope is not a checked answer."""
        from dms_executor.reconfirm import (
            apply_reconfirm,
            complete_with_model,
            granted_schema_names,
            ontology_text,
            schema_text,
        )

        return apply_reconfirm(
            env,
            question=question,
            space_id=space_id,
            session_id=session_id,
            tables=tables,
            ask_path=ask_path,
            ontology=ontology_text(space_id),
            schema=schema_text(None, granted_schema_names(space_id, tables)),
            complete=lambda prompt: complete_with_model(cortex, prompt),
        )

    def reconfirm_ask_path(self, token: str) -> str | None:
        from dms_executor.reconfirm import peek_pending

        pending = peek_pending(token)
        if pending is None:
            return None
        raw = pending.get("ask_path")
        return str(raw) if isinstance(raw, str) else None

    def reconfirm_pending(self, token: str) -> dict[str, Any] | None:
        from dms_executor.reconfirm import peek_pending

        return peek_pending(token)

    def confirm_reconfirm(self, token: str, yes: bool) -> dict[str, Any]:
        """Yes re-runs the suggested question on the normal ladder. No logs a ticket.

        With ``DMS_RECONFIRM_RUN_HELD`` on, a pending entry that holds validated
        SQL runs that SQL after a fresh grant check and read-only check.
        """
        from dms_executor.reconfirm import (
            decline_envelope,
            held_ready,
            reconfirm_run_held_enabled,
            take_pending,
        )

        pending = take_pending(token)
        if pending is None:
            raise KeyError(token)
        if not yes:
            return decline_envelope(pending)
        if reconfirm_run_held_enabled() and held_ready(pending):
            return self._run_held_sql(pending)
        suggested = str(pending.get("suggested_question") or "").strip()
        if not suggested:
            raise ValueError("empty suggestion")
        env = self.live_ask(
            suggested,
            space_id=pending.get("space_id"),
            session_id=pending.get("session_id"),
            tables=pending.get("tables"),
            ask_path=pending.get("ask_path"),
        )
        offered = self.offer_reconfirm(
            env,
            question=suggested,
            space_id=pending.get("space_id"),
            session_id=pending.get("session_id"),
            tables=pending.get("tables"),
            ask_path=pending.get("ask_path"),
            cortex=self._cortex,
        )
        if not isinstance(offered.get("reconfirm"), dict):
            offered = dict(offered)
            offered["reconfirm_accepted"] = True
            raw_space = pending.get("space_id")
            route = offered.get("served_route")
            maybe_save_checked_sql(
                question=suggested,
                sql=str(offered.get("sql_used") or ""),
                space_id=raw_space if isinstance(raw_space, str) else None,
                warehouse=self._warehouse,
                grantable=None,
                dialect=self.dialect,
                provenance="yes:reconfirm",
                served_route=route.strip() if isinstance(route, str) else "",
                abstained=offered.get("abstained") is True
                or str(offered.get("badge") or "") == "ABSTAIN",
            )
        return offered

    def _run_held_sql(self, pending: dict[str, Any]) -> dict[str, Any]:
        """Execute SQL the user confirmed. Grant and read-only checks run again."""
        from datetime import UTC, datetime

        from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
        from dms_executor.generative_ask import validate_compiled_sql
        from dms_executor.grant_struct import serve_gap
        from dms_executor.sql_loop import run_readonly

        sql = str(pending.get("sql") or "").strip()
        raw_grants = pending.get("grantable")
        grantable = (
            {str(name) for name in raw_grants if str(name).strip()}
            if isinstance(raw_grants, list)
            else set()
        )
        code = str(pending.get("reason_code") or "").strip()
        question = str(
            pending.get("suggested_question") or pending.get("question") or ""
        ).strip()
        space_id = pending.get("space_id") if isinstance(pending.get("space_id"), str) else None
        session_id = (
            pending.get("session_id") if isinstance(pending.get("session_id"), str) else None
        )
        provenance = f"yes:definition:{code}"

        def abstain(reason: str) -> dict[str, Any]:
            env = build_abstain(
                reason=reason,
                question=question,
                stage="reconfirm_yes",
                answer_id="ans_reconfirm_yes",
                text=(
                    "I cannot run that query against the granted tables, "
                    "so I am not answering it."
                ),
                abstain_reason=reason,
                values=[],
                rows=[],
                sql_used=None,
                assumptions=[reason],
                space_id=space_id,
                session_id=session_id,
                ask_mode="live",
                route="abstain",
            )
            assert_envelope_valid(env)
            return env

        gap = serve_gap(sql, grantable=grantable, dialect=self.dialect)
        why = validate_compiled_sql(
            sql,
            grantable=grantable,
            warehouse=self._warehouse,
            dialect=self.dialect,
        )
        if gap or why:
            return abstain(gap or why or "ungranted")
        warehouse = self._warehouse
        if warehouse is None or not Path(warehouse).is_file():
            return abstain("warehouse_missing")
        rows, err = run_readonly(sql, Path(warehouse))
        if err or rows is None:
            return abstain(err or "execute_failed")
        env = build_answer_envelope(
            answer_id="ans_reconfirm_yes",
            text="The confirmed query ran.",
            badge="L2_VALIDATED",
            abstained=False,
            rows=rows,
            sql_used=sql,
            assumptions=[provenance],
            as_of=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            space_id=space_id,
            session_id=session_id,
            ask_mode="live",
            route="generated",
            question=question,
        )
        assert_envelope_valid(env)
        abstained = env.get("abstained") is True or str(env.get("badge") or "") == "ABSTAIN"
        if not abstained:
            env["reconfirm_accepted"] = True
            env["provenance"] = provenance
        route = env.get("served_route")
        maybe_save_checked_sql(
            question=question,
            sql=sql,
            space_id=space_id,
            warehouse=warehouse,
            grantable=grantable,
            dialect=self.dialect,
            provenance=provenance,
            served_route=route.strip() if isinstance(route, str) else "",
            abstained=abstained,
        )
        return env

    def take_executed(self) -> ExecutedTrace:
        """What the last ``live_ask`` on this thread ran: statements with row counts,
        and the ledger entries DMS appended with their seq.

        Read once, immediately after ``live_ask``; it clears. Empty when the ask
        ran no statement through Cortex (a local bronze or demo path).
        """
        return executed_trace.take()

    def submit_sql(
        self,
        sql: str,
        session: SessionContext | SessionAcl,
        *,
        reminted: bool = False,
    ) -> Any:
        if sql_has_reserved_as_of(sql):
            raise ReservedParamError(RESERVED_PARAM_AS_OF)
        if self._cortex is None:
            raise RuntimeError("CortexClient required for submit")
        acl = session if isinstance(session, SessionAcl) else resolve_session_acl(session)
        gap = serve_gap(
            sql,
            grantable=set(acl.row_predicates),
            dialect=self.dialect,
        )
        if gap:
            raise SecurityEvent(gap, gap)
        manifest = self._minter.mint_manifest(acl)
        req = SubmitRequest(
            pool=PoolSpec(id=acl.pool_id),
            plan={"kind": "sql"},
            body={"sql": sql},
            manifest=manifest,
        )
        try:
            result = self._cortex.submit(req)
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
        else:
            from dms_executor.verified_queries import rows_from_submit_result

            executed_trace.record(sql, len(rows_from_submit_result(result)))
            return result


#: Engine routes that mean "no answer was produced". Authoritative over any
#: badge the engine sends alongside them - see map_ask_response_to_envelope.
_REFUSAL_ROUTES = frozenset({"abstain", "blocked", "needs_clarification", "refused"})

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


_chart_from_rows = chart_from_rows


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
    # BANK-02: the engine's own SQL and rows, taken before anything below drops
    # them for an abstain or swaps in a placeholder. This is what ran.
    executed_trace.record(resp.sql_used, len(resp.rows or []))
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

    # A refusal is a refusal whatever badge rides along with it.
    #
    # This check used to sit inside `if not badge_raw`, so it was reachable only
    # when the engine sent no badge at all. An engine that refused AND sent a
    # confident badge went straight through: a manifest refusal arrives as
    # route/layer "refused" carrying badge "session", "session" maps to
    # L2_VALIDATED, and the customer read "generated - check sources" over a
    # question the engine had declined to answer, with abstained=False.
    #
    # Every E1-E9 invariant passed while that happened, and that is the point:
    # a refusal dressed as an answer is internally consistent, so no amount of
    # checking the envelope against itself can catch it. The signal lives in the
    # route, and the route has to outrank the badge.
    #
    # Reproduced end to end by scripts/repro_refused_badge.py (F40 / Cortex#11).
    # The engine-side fix - teaching _is_abstain_signal about "refused" - is
    # still needed and is not what this is. This is the half DMS owns: whatever
    # the engine sends, DMS does not put a confident badge on a refusal.
    refused = route_l in _REFUSAL_ROUTES
    engine_unsure = resp.unsure is True
    if refused or engine_unsure:
        badge_raw = "abstain"
    elif not badge_raw:
        badge_raw = "abstain" if resp.abstained else "l2_validated"
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
    # VQ-04: Cortex L1 on an uncertified planted paraphrase (vocabulary
    # "how full" / delayed+per-warehouse regex) must not keep a green badge.
    # E9: do not render the engine figures under ABSTAIN.
    if is_uncertified_paraphrase(question) and not abstained:
        refused_env = maybe_uncertified_refuse_ask(
            question or "", space_id=space_id, session_id=session_id
        )
        if refused_env is not None:
            return refused_env
    fields: dict[str, Any] = {
        "answer_id": resp.receipt_id or f"ans_live_{session_id or 'x'}",
        "text": text,
        "values": values,
        "sql_used": None if abstained else sql_out,
        "assumptions": assumptions,
        "as_of": datetime_now(),
        "space_id": space_id,
        "ask_mode": "live",
        "session_id": session_id,
        "contributing_sources": sources,
        "rows": [] if abstained else rows,
        "chart": None if abstained else chart,
        "suggestions": list(resp.suggestions or []),
        "audit_id": resp.receipt_id or resp.audit_id,
        "route": resp.route,
        "drillthrough_token": None if abstained else token,
        "grounded_tables": grounded_tables,
        "competing_scopes": competing_scopes,
        "exclude_reasons": list(resp.exclude_reasons) if resp.exclude_reasons else None,
    }
    if abstained:
        env = build_abstain(
            reason="abstain",
            question=question or "",
            stage="cortex_ask",
            **fields,
        )
    else:
        env = build_answer_envelope(badge=badge, abstained=False, question=question, **fields)
    assert_envelope_valid(env)
    from dms_executor.route_stamp import ROUTE_L1, ROUTE_VERIFIED, stamp_route

    # Cortex built this answer. Its own L0/L1 badge names the rung (grade52
    # reads the same badge). Any other badge, L2 included, does not say which
    # route served it, so nothing is stamped and it stays unattributed. An
    # abstain keeps the abstain:<stage> that build_abstain wrote.
    served = {"L0_CERTIFIED": ROUTE_VERIFIED, "L1_GOVERNED_METRIC": ROUTE_L1}.get(
        str(env.get("badge") or "")
    )
    if served and env.get("abstained") is not True:
        stamp_route(env, served)
    return env


def datetime_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_serving_engine() -> ServingEnginePort:
    return Executor()


__all__ = [
    "run_crosscheck",
    "run_extract",
    "run_golden",
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
    "DEFAULT_MAX_ROWS",
    "SourceConfig",
    "SourceConnectionError",
    "UnknownSourceTable",
    "ingest_csv_bytes",
    "ingest_source_database",
    "verify_source_links",
    "bronze_catalog",
    "derive_and_store",
    "load_space_ontology",
    "ontology_store",
    "set_ontology_store",
    "source_identity",
    "space_ontology_views",
    "stored_catalogs",
    "stored_catalogs_by_source",
    "list_source_keys",
    "list_source_pulls",
    "canonical_space_id",
    "is_demo_space",
    "default_readable_tables",
    "compile_source",
    "connect_serving",
    "warehouse_path",
    "serving_sync_state",
    "infer_contract",
    "intersect_space_grants",
    "get_serving_engine",
    "hide_echo",
    "read_health_engine_clock",
    "list_bronze_tables",
    "list_promote_targets",
    "list_warehouse_tables",
    "list_verified_queries",
    "load_pipeline_by_name",
    "load_pipeline_yaml",
    "map_ask_response_to_envelope",
    "preview_bronze_table",
    "preview_warehouse_table",
    "register_verified_query",
    "allowlisted_roots",
    "is_filesystem_uri",
    "resolve_allowlisted_file",
    "reveal_path",
    "resolve_session_acl",
    "LakeBusy",
    "latest_promote_receipt",
    "run_promote",
    "sign_gold_metric",
    "validate_pipeline_dict",
    "write_bronze_rows",
    "bronze_missing_from_serving",
    "identity_check",
    "ingest_warehouse_path",
    "serving_warehouse_path",
    "sync_bronze_to_serving",
    "tables_read_by_sql",
    "tables_read_checked_by_sql",
]
