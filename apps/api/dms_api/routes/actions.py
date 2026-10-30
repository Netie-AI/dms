"""Governed actions HTTP (ACT-02), mounted under ``/v1/amend/actions``.

A steward proposes one typed change to one object, cites the governed answer
that prompted it, and applies it with a named approval reference. Applying
writes exactly one cell of the DMS-owned ``dms.action_writeback`` table, re-reads
it, and appends a Cortex ledger receipt inside the same transaction. No customer
source system and no Excel file is ever written (DR-0005, hard rule 5). There is
no auto-apply: every apply is a steward request, and a revert is a new proposal
that still needs its own apply.

**Off by default.** ``DMS_ACTIONS`` must be the digit 1. While it is not, every
route here fails to *match* (``_FlaggedRoute.matches``), so any method, path or
body under ``/v1/amend/actions`` gets the same 404 the router gives an unknown
path, byte for byte, before any body is read. No route is in the OpenAPI schema
either way. The router dependency repeats the check for the case where the flag
flips between match and dispatch.

Order inside every route: ``compliance_gate`` (call-through to Cortex F5, task ids
``action.*``; values never go into the template) -> ``enforce`` -> for mutations,
the steward grant and the configured identity (DR-0004 Option A: one configured
identity proposes and applies, so the receipt says separation of duties is not
proven) -> the work. Apply also checks the Cortex ledger is reachable and its
chain verifies *before* any write.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from cortex_client import compliance_gate
from cortex_client.models import LedgerAppendRequest
from dms_core.control_plane.action_queries import (
    EvidenceRefused,
    cell_state,
    governed_evidence,
    list_action_proposals,
    list_cells,
    list_decisions,
    load_action_version,
    space_in_tenant,
)
from dms_core.control_plane.action_queries import (
    decision_json as _decision_json,
)
from dms_core.control_plane.action_types import (
    ACTION_TYPES,
    ActionSpec,
    ActionSpecError,
    ActionType,
    actions_enabled,
    spec_sha,
    state_hash,
    validate_spec,
)
from dms_core.control_plane.actions import (
    APPROVAL_REF_MIN,
    ActionsDisabled,
    ApprovalRefRequired,
    PreconditionFailed,
    TokenDiffMismatch,
    VerifyFailed,
    apply_action_version,
    decline_action_version,
    get_decision,
    propose_action,
    record_decision,
)
from dms_core.control_plane.advisory_lock import AdvisoryLockTimeout
from dms_core.control_plane.onto_data import ontology_data_store
from dms_core.control_plane.proposals import (
    ActionRequiresApply,
    ConflictError,
    StaleTokenError,
)
from dms_core.control_plane.session import AppRole, set_tenant_context
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict
from starlette.routing import Match
from starlette.types import Scope

from dms_api.deps import CortexDep, SettingsDep
from dms_api.gatekeeping import enforce

#: The shipped default actor. Applying as it would sign the ledger as nobody in
#: particular (the scripts/verify/onto_steward.py precedent), so it is refused.
DEFAULT_ACTOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
IDENTITY = "config_actor (DR-0004 Option A)"
SEPARATION_OF_DUTIES = {
    "proven": False,
    "reason": "one configured identity proposes and applies",
}
PROPOSED_ONLY = {
    "executed": False,
    "detail": "Proposed only; nothing changes until a steward applies.",
}
APPLIED = {
    "executed": True,
    "target": "dms.action_writeback",
    "source_system_changed": False,
    "detail": (
        "One row in a DMS-owned writeback table changed and was re-read. "
        "No customer source system or Excel file was written."
    ),
}
DECLINED = {
    "executed": False,
    "detail": "Declined. Nothing was written to dms.action_writeback.",
}


class _FlaggedRoute(APIRoute):
    """A route that does not exist while ``DMS_ACTIONS`` is off.

    Not matching (rather than answering 404 from the handler) is what makes the
    flag-off surface identical to main for every method and every body: the
    router falls through to its own not-found, and nothing here reads the body.
    """

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if not actions_enabled():
            return Match.NONE, {}
        return super().matches(scope)


def _require_actions_flag() -> None:
    if not actions_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(
    prefix="/actions",
    route_class=_FlaggedRoute,
    dependencies=[Depends(_require_actions_flag)],
)


# --- bodies ---------------------------------------------------------------------


class ProposeActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    space_id: str
    action_type: str
    object_key: dict[str, Any]
    value: Any = None
    reason: str = ""
    evidence_audit_id: str | None = None


class ApplyActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_token: str
    approval_ref: str


class DeclineActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_token: str
    approval_ref: str
    reason: str = ""


class RevertDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = ""


# --- helpers --------------------------------------------------------------------


def _refuse(status: int, code: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, **extra})


def _action_error(exc: Exception) -> HTTPException:
    """One map from core refusals to HTTP. Subclasses before their bases."""
    if isinstance(exc, ActionSpecError):
        return _refuse(400, exc.code)
    if isinstance(exc, ApprovalRefRequired):
        return _refuse(400, "approval_ref_required")
    if isinstance(exc, PreconditionFailed):
        return _refuse(
            409,
            exc.code,
            current_value=exc.current_value,
            current_row_version=exc.current_row_version,
        )
    if isinstance(exc, TokenDiffMismatch):
        return _refuse(409, "token_diff_mismatch")
    if isinstance(exc, AdvisoryLockTimeout):
        return _refuse(409, "lock_timeout")
    if isinstance(exc, VerifyFailed):
        return _refuse(500, "verify_failed")
    if isinstance(exc, StaleTokenError):
        return _refuse(409, "stale_token")
    if isinstance(exc, ActionRequiresApply):
        return _refuse(409, "action_requires_apply")
    if isinstance(exc, ConflictError):
        return _refuse(409, "conflict")
    if isinstance(exc, ActionsDisabled):
        return HTTPException(status_code=404, detail="Not Found")
    raise exc


_CORE_REFUSALS = (
    ActionSpecError,
    ApprovalRefRequired,
    PreconditionFailed,
    TokenDiffMismatch,
    AdvisoryLockTimeout,
    VerifyFailed,
    StaleTokenError,
    ConflictError,
    ActionsDisabled,
)


def _commit(conn: psycopg.Connection) -> None:
    """The one commit of a governed write. A seam so a test can make it fail."""
    conn.commit()


@contextmanager
def _tenant_conn(settings: Any, *, role: AppRole) -> Iterator[tuple[psycopg.Connection, uuid.UUID]]:
    if not settings.database_url:
        raise _refuse(503, "database_unavailable")
    tenant = uuid.UUID(str(settings.dms_tenant_id))
    with psycopg.connect(settings.database_url) as conn:
        set_tenant_context(conn, tenant, role=role)
        yield conn, tenant


def _uuid_or_none(raw: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


def _space(raw: str) -> uuid.UUID:
    space = _uuid_or_none(raw)
    if space is None:
        raise _refuse(422, "space_id_invalid")
    return space


def _require_space(conn: psycopg.Connection, space: uuid.UUID) -> None:
    if not space_in_tenant(conn, space_id=space):
        raise _refuse(404, "space_not_found")


def _require_steward_identity(settings: Any) -> None:
    """DR-0004 Option A: the configured actor must hold the steward grant and be set."""
    store = ontology_data_store()
    if not store.holds_steward_grant(
        role=settings.dms_actor_role, actor_id=settings.dms_actor_user_id
    ):
        raise _refuse(403, "steward_grant_required")
    if str(settings.dms_actor_user_id).strip().lower() == DEFAULT_ACTOR:
        raise _refuse(403, "steward_identity_unset")


def _require_approval_ref(raw: str) -> str:
    ref = (raw or "").strip()
    if len(ref) < APPROVAL_REF_MIN:
        raise _refuse(400, "approval_ref_required")
    return ref


def _resource(at: ActionType, key: dict[str, Any]) -> str:
    return f"{at.object_type}:" + "@".join(str(key.get(k, "")) for k in at.key_fields)


def _template(at: ActionType | None, sha: str | None) -> dict[str, Any]:
    """The gate's template: identifiers and the spec digest, never a value."""
    if at is None:
        return {}
    return {
        "action_type": at.name,
        "object_type": at.object_type,
        "field": at.field,
        "spec_sha": sha or "",
    }


def _source_value(spec: ActionSpec) -> str | None:
    """Rule 12: the key must match one source row exactly. Returns its value."""
    from dms_api.wiring import resolve_action_key

    found = resolve_action_key(spec.action_type, dict(spec.object_key))
    status = found.get("status")
    if status == "found":
        return found.get("source_value")
    if status == "not_found":
        raise _refuse(
            422,
            "object_key_not_found",
            object_key=dict(spec.object_key),
            candidates=list(found.get("candidates") or []),
        )
    if status == "ambiguous":
        raise _refuse(422, "object_key_ambiguous", matches=found.get("matches"))
    raise _refuse(422, "unsupported_source", source_kind=found.get("source_kind"))


def _peek_version(settings: Any, proposal_id: str, token: str) -> dict[str, Any] | None:
    """Which object an apply or decline names, so the gate sees its resource.

    A read under the tenant context; nothing is written and nothing is returned
    to the caller before the gate has decided.
    """
    pid = _uuid_or_none(proposal_id)
    if pid is None or not settings.database_url:
        return None
    with _tenant_conn(settings, role="viewer") as (conn, tenant):
        return load_action_version(conn, tenant_id=tenant, proposal_id=pid, idempotency_token=token)


def _peek_decision(settings: Any, decision_id: str) -> dict[str, Any] | None:
    did = _uuid_or_none(decision_id)
    if did is None or not settings.database_url:
        return None
    with _tenant_conn(settings, role="viewer") as (conn, tenant):
        return get_decision(conn, tenant_id=tenant, decision_id=did)


def _target_of(row: dict[str, Any] | None) -> tuple[ActionType | None, str | None, str | None]:
    """(action type, resource, spec_sha) of a stored diff or decision, if known."""
    if row is None:
        return None, None, None
    src = row.get("diff", row)
    at = ACTION_TYPES.get(str(src.get("action_type")))
    if at is None:
        return None, None, None
    return at, _resource(at, dict(src.get("object_key") or {})), src.get("spec_sha")


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _chain_readback(cortex: Any) -> dict[str, Any]:
    """Reported, not a gate: the entry already exists once this runs."""
    try:
        verified = cortex.verify_ledger()
    except Exception:  # noqa: BLE001 - a readback that cannot run says so
        return {"ok": None, "reason": "cortex_unavailable"}
    return {"ok": bool(verified.ok), "first_break": verified.first_break}


def _void_append(cortex: Any, actor: str, event_type: str, entry_id: str, version_id: Any) -> None:
    """Best effort: tell the ledger an appended entry's transaction did not commit."""
    try:
        cortex.ledger_append(
            LedgerAppendRequest(
                event_type=event_type,
                payload={
                    "entry_id": entry_id,
                    "proposal_version_id": str(version_id),
                    "reason": "commit_failed",
                },
                actor=actor,
            )
        )
    except Exception:  # noqa: BLE001 - the 503 below names the entry either way
        pass


def _propose_response(out: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return _jsonable(
        {
            "proposal_id": out["proposal_id"],
            "version_num": out["version_num"],
            "idempotency_token": out["idempotency_token"],
            "status": out["status"],
            "diff": out["diff"],
            **extra,
            "mutation": PROPOSED_ONLY,
        }
    )


# --- routes ---------------------------------------------------------------------


@router.get("/types", include_in_schema=False)
def list_action_types(settings: SettingsDep, cortex: CortexDep) -> list[dict[str, Any]]:
    """The closed registry. Identifiers only; it changes by PR, not by request."""
    decision = compliance_gate(
        action="action.list",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "action.list", "filled_template": {"list": "types"}},
        client=cortex,
    )
    enforce(decision, mutation=False)
    return [
        {
            "name": at.name,
            "object_type": at.object_type,
            "source_kind": at.source_kind,
            "key_fields": list(at.key_fields),
            "field": at.field,
            "value_min": str(at.value_min),
            "value_max": str(at.value_max),
            "description": at.description,
        }
        for at in ACTION_TYPES.values()
    ]


@router.get("/proposals", include_in_schema=False)
def list_action_proposals_route(
    settings: SettingsDep,
    cortex: CortexDep,
    space_id: str = Query(..., description="the Space whose action proposals to list"),
) -> list[dict[str, Any]]:
    decision = compliance_gate(
        action="action.list",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "action.list", "filled_template": {"list": "proposals"}},
        client=cortex,
    )
    enforce(decision, mutation=False)
    space = _space(space_id)
    with _tenant_conn(settings, role="viewer") as (conn, tenant):
        _require_space(conn, space)
        return list_action_proposals(conn, tenant_id=tenant, space_id=space)


@router.post("/proposals", include_in_schema=False)
def propose_action_route(
    body: ProposeActionBody, settings: SettingsDep, cortex: CortexDep
) -> dict[str, Any]:
    try:
        spec = validate_spec(
            {
                "action_type": body.action_type,
                "object_key": body.object_key,
                "value": body.value,
                "op": "set",
                "reason": body.reason,
            }
        )
    except ActionSpecError as exc:
        raise _refuse(400, exc.code) from exc
    at = ACTION_TYPES[spec.action_type]
    decision = compliance_gate(
        action="action.propose",
        actor=settings.dms_actor_user_id,
        resource=_resource(at, spec.object_key),
        metadata={"task_id": "action.propose", "filled_template": _template(at, spec_sha(spec))},
        client=cortex,
    )
    enforce(decision)
    _require_steward_identity(settings)
    space = _space(body.space_id)

    with _tenant_conn(settings, role="steward") as (conn, tenant):
        _require_space(conn, space)
        evidence = None
        if body.evidence_audit_id is not None:
            try:
                evidence = governed_evidence(
                    conn, audit_id=body.evidence_audit_id, source_table=at.source_table
                )
            except EvidenceRefused as exc:
                raise _refuse(422, exc.code) from exc
        source_value = _source_value(spec)
        try:
            out = propose_action(
                conn,
                tenant_id=tenant,
                space_id=space,
                actor_id=uuid.UUID(str(settings.dms_actor_user_id)),
                spec=spec,
                source_value=source_value,
                evidence=evidence,
            )
        except _CORE_REFUSALS as exc:
            raise _action_error(exc) from exc
        _commit(conn)
    return _propose_response(out)


@router.post("/proposals/{proposal_id}/apply", include_in_schema=False)
def apply_action_route(
    proposal_id: str, body: ApplyActionBody, settings: SettingsDep, cortex: CortexDep
) -> dict[str, Any]:
    target = _peek_version(settings, proposal_id, body.idempotency_token)
    at, resource, sha = _target_of(target)
    decision = compliance_gate(
        action="action.apply",
        actor=settings.dms_actor_user_id,
        resource=resource,
        metadata={"task_id": "action.apply", "filled_template": _template(at, sha)},
        client=cortex,
    )
    enforce(decision)
    _require_steward_identity(settings)
    approval_ref = _require_approval_ref(body.approval_ref)
    if not settings.database_url:
        raise _refuse(503, "database_unavailable")
    if target is None or at is None:
        raise _refuse(409, "stale_token")
    if cortex is None:
        raise _refuse(503, "ledger_unavailable")
    try:
        chain_before = cortex.verify_ledger()
    except Exception as exc:  # noqa: BLE001 - no verified chain, no write
        raise _refuse(503, "ledger_unavailable") from exc
    if not chain_before.ok:
        raise _refuse(503, "ledger_chain_broken", first_break=chain_before.first_break)

    actor = settings.dms_actor_user_id
    with _tenant_conn(settings, role="steward") as (conn, tenant):
        current = load_action_version(
            conn,
            tenant_id=tenant,
            proposal_id=target["proposal_id"],
            idempotency_token=body.idempotency_token,
        )
        if current is None:
            raise _refuse(409, "stale_token")
        cited = (current["diff"].get("evidence") or {}).get("audit_id")
        if cited is not None:
            try:
                governed_evidence(conn, audit_id=cited, source_table=at.source_table)
            except EvidenceRefused as exc:
                raise _refuse(422, "evidence_not_validated", reason=exc.code) from exc
        try:
            result = apply_action_version(
                conn,
                tenant_id=tenant,
                idempotency_token=body.idempotency_token,
                actor_id=actor,
                approval_ref=approval_ref,
            )
        except _CORE_REFUSALS as exc:
            raise _action_error(exc) from exc

        after = result["after"] or {}
        payload = _jsonable(
            {
                "schema": "dms.action.apply/1",
                "proposal_id": result["proposal_id"],
                "version_id": result["version_id"],
                "version_num": result["version_num"],
                "action_type": result["action_type"],
                "object_type": result["object_type"],
                "object_key": result["object_key"],
                "field": result["field"],
                "op": result["op"],
                "before_hash": result["before_hash"],
                "after_hash": result["after_hash"],
                "value": after.get("value"),
                "approval_ref": result["approval_ref"],
                "evidence_audit_id": (result["evidence"] or {}).get("audit_id"),
                "verified": True,
            }
        )
        try:
            appended = cortex.ledger_append(
                LedgerAppendRequest(event_type="action.apply", payload=payload, actor=actor)
            )
        except Exception as exc:  # noqa: BLE001 - no receipt, no apply
            raise _refuse(503, "ledger_append_failed") from exc
        recorded = record_decision(
            conn,
            result,
            decision="applied",
            cortex_entry_id=appended.entry_id,
            space_id=target["space_id"],
        )
        try:
            _commit(conn)
        except Exception as exc:  # noqa: BLE001 - the entry exists; the write does not
            _void_append(
                cortex, actor, "action.apply.void", appended.entry_id, result["version_id"]
            )
            raise _refuse(
                503, "commit_failed_after_append", cortex_entry_id=appended.entry_id
            ) from exc

    return _jsonable(
        {
            "decision_id": recorded["decision_id"],
            "proposal_id": result["proposal_id"],
            "version_num": result["version_num"],
            "status": "applied",
            "action_type": result["action_type"],
            "object": {"object_type": result["object_type"], "object_key": result["object_key"]},
            "field": result["field"],
            "op": result["op"],
            "before": result["before"],
            "after": result["after"],
            "before_hash": result["before_hash"],
            "after_hash": result["after_hash"],
            "verified": result["verified"],
            "rows_changed": result["rows_changed"],
            "approval_ref": result["approval_ref"],
            "evidence": result["evidence"],
            "identity": IDENTITY,
            "separation_of_duties": SEPARATION_OF_DUTIES,
            "ledger_entry_id": appended.entry_id,
            "ledger_seq": recorded["ledger_seq"],
            "chain": _chain_readback(cortex),
            "source_kind": at.source_kind,
            "summary": current["diff"].get("summary"),
            "mutation": APPLIED,
        }
    )


@router.post("/proposals/{proposal_id}/decline", include_in_schema=False)
def decline_action_route(
    proposal_id: str, body: DeclineActionBody, settings: SettingsDep, cortex: CortexDep
) -> dict[str, Any]:
    target = _peek_version(settings, proposal_id, body.idempotency_token)
    at, resource, sha = _target_of(target)
    decision = compliance_gate(
        action="action.decline",
        actor=settings.dms_actor_user_id,
        resource=resource,
        metadata={"task_id": "action.decline", "filled_template": _template(at, sha)},
        client=cortex,
    )
    enforce(decision)
    _require_steward_identity(settings)
    approval_ref = _require_approval_ref(body.approval_ref)
    if not settings.database_url:
        raise _refuse(503, "database_unavailable")
    if target is None:
        raise _refuse(409, "stale_token")
    if cortex is None:
        raise _refuse(503, "ledger_unavailable")

    actor = settings.dms_actor_user_id
    reason = (body.reason or "").strip()[:500]
    with _tenant_conn(settings, role="steward") as (conn, tenant):
        try:
            result: dict[str, Any] = dict(
                decline_action_version(
                    conn,
                    tenant_id=tenant,
                    idempotency_token=body.idempotency_token,
                    approval_ref=approval_ref,
                    actor_id=actor,
                )
            )
        except _CORE_REFUSALS as exc:
            raise _action_error(exc) from exc
        result["evidence"] = {**(result.get("evidence") or {}), "decline_reason": reason}
        payload = _jsonable(
            {
                "schema": "dms.action.decline/1",
                "proposal_id": result["proposal_id"],
                "version_id": result["version_id"],
                "version_num": result["version_num"],
                "action_type": result["action_type"],
                "object_type": result["object_type"],
                "object_key": result["object_key"],
                "field": result["field"],
                "op": result["op"],
                "before_hash": result["before_hash"],
                "approval_ref": approval_ref,
                "reason": reason,
                "verified": False,
            }
        )
        try:
            appended = cortex.ledger_append(
                LedgerAppendRequest(event_type="action.decline", payload=payload, actor=actor)
            )
        except Exception as exc:  # noqa: BLE001 - no receipt, no decline
            raise _refuse(503, "ledger_append_failed") from exc
        recorded = record_decision(
            conn,
            result,
            decision="declined",
            cortex_entry_id=appended.entry_id,
            space_id=target["space_id"],
        )
        try:
            _commit(conn)
        except Exception as exc:  # noqa: BLE001
            _void_append(
                cortex, actor, "action.decline.void", appended.entry_id, result["version_id"]
            )
            raise _refuse(
                503, "commit_failed_after_append", cortex_entry_id=appended.entry_id
            ) from exc
    return _jsonable(
        {
            "decision_id": recorded["decision_id"],
            "proposal_id": result["proposal_id"],
            "version_num": result["version_num"],
            "status": "rejected",
            "ledger_entry_id": appended.entry_id,
            "ledger_seq": recorded["ledger_seq"],
            "identity": IDENTITY,
            "mutation": DECLINED,
        }
    )


@router.get("/objects", include_in_schema=False)
def list_action_objects(
    settings: SettingsDep,
    cortex: CortexDep,
    object_type: str = Query(...),
    space_id: str = Query(..., description="the Space the caller is working in"),
) -> list[dict[str, Any]]:
    """Writeback cells of one object type (cleared cells show value null).

    Cells are tenant-scoped, like the source rows they override. The Space must
    exist in the tenant; it does not narrow which cells are shown.
    """
    decision = compliance_gate(
        action="action.list",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "action.list", "filled_template": {"list": "objects"}},
        client=cortex,
    )
    enforce(decision, mutation=False)
    space = _space(space_id)
    known = {at.object_type for at in ACTION_TYPES.values()}
    if object_type not in known:
        raise _refuse(422, "object_type_unknown")
    with _tenant_conn(settings, role="viewer") as (conn, tenant):
        _require_space(conn, space)
        return _jsonable(list_cells(conn, tenant_id=tenant, object_type=object_type))


@router.get("/decisions", include_in_schema=False)
def list_action_decisions(
    settings: SettingsDep,
    cortex: CortexDep,
    space_id: str = Query(..., description="the Space whose decisions to list"),
) -> list[dict[str, Any]]:
    decision = compliance_gate(
        action="action.list",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "action.list", "filled_template": {"list": "decisions"}},
        client=cortex,
    )
    enforce(decision, mutation=False)
    space = _space(space_id)
    with _tenant_conn(settings, role="viewer") as (conn, tenant):
        _require_space(conn, space)
        return _jsonable(list_decisions(conn, tenant_id=tenant, space_id=space))


@router.get("/decisions/{decision_id}", include_in_schema=False)
def get_action_decision(
    decision_id: str,
    settings: SettingsDep,
    cortex: CortexDep,
    space_id: str = Query(..., description="the Space the decision belongs to"),
) -> dict[str, Any]:
    decision = compliance_gate(
        action="action.list",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "action.list", "filled_template": {"list": "decision"}},
        client=cortex,
    )
    enforce(decision, mutation=False)
    space = _space(space_id)
    did = _uuid_or_none(decision_id)
    with _tenant_conn(settings, role="viewer") as (conn, tenant):
        _require_space(conn, space)
        row = None if did is None else get_decision(conn, tenant_id=tenant, decision_id=did)
    if row is None or row.get("space_id") != space:
        raise _refuse(404, "decision_not_found")
    return _jsonable(_decision_json(row))


@router.post("/decisions/{decision_id}/revert", include_in_schema=False)
def revert_action_decision(
    decision_id: str, body: RevertDecisionBody, settings: SettingsDep, cortex: CortexDep
) -> dict[str, Any]:
    """Propose the inverse of an applied decision. It still needs its own apply."""
    prior = _peek_decision(settings, decision_id)
    at, resource, _sha = _target_of(prior)
    decision = compliance_gate(
        action="action.revert",
        actor=settings.dms_actor_user_id,
        resource=resource,
        metadata={"task_id": "action.revert", "filled_template": _template(at, None)},
        client=cortex,
    )
    enforce(decision)
    _require_steward_identity(settings)
    if not settings.database_url:
        raise _refuse(503, "database_unavailable")
    if prior is None or at is None:
        raise _refuse(404, "decision_not_found")
    if prior.get("decision") != "applied":
        raise _refuse(409, "decision_not_applied")

    before = prior.get("before") or {}
    restore = before.get("value")
    raw = {
        "action_type": at.name,
        "object_key": dict(prior.get("object_key") or {}),
        "op": "clear" if restore is None else "set",
        "value": restore,
        "reason": (body.reason or "").strip() or f"revert decision {prior['id']}",
    }
    try:
        spec = validate_spec(raw)
    except ActionSpecError as exc:
        raise _refuse(400, exc.code) from exc

    with _tenant_conn(settings, role="steward") as (conn, tenant):
        # A revert undoes one decision, so it is made against that decision's
        # after-state. If the cell moved on since, reverting would also undo the
        # later change: refuse before proposing anything.
        now = cell_state(
            conn,
            tenant_id=tenant,
            object_type=at.object_type,
            object_key=spec.object_key,
            field=at.field,
        )
        if state_hash(now) != prior.get("after_hash"):
            raise _refuse(
                409,
                "object_changed",
                current_value=now["value"],
                current_row_version=now["row_version"],
            )
        source_value = _source_value(spec)
        try:
            out = propose_action(
                conn,
                tenant_id=tenant,
                space_id=prior.get("space_id"),
                actor_id=uuid.UUID(str(settings.dms_actor_user_id)),
                spec=spec,
                source_value=source_value,
                evidence={"reverts_decision_id": str(prior["id"])},
            )
        except _CORE_REFUSALS as exc:
            raise _action_error(exc) from exc
        if out["diff"]["expected_before_hash"] != prior.get("after_hash"):
            raise _refuse(409, "object_changed")  # pragma: no cover - same txn as the read
        _commit(conn)
    return _propose_response(out, reverts_decision_id=str(prior["id"]))


__all__ = ["router"]
