"""Governed action core (ACT-01): propose, apply, verify, decline, record.

An action is a versioned proposal (``dms.proposals`` + ``dms.proposal_versions``)
marked in ``dms.action_proposals``. Applying it writes one cell of the DMS-owned
``dms.action_writeback`` table, never a customer source system and never an
Excel file. A human steward confirms every apply with an ``approval_ref``;
there is no auto-apply.

``apply_action_version`` runs inside a savepoint and never commits. Any refusal
rolls the savepoint back, so the version stays ``pending_confirm``. It does not
write the decision: only the caller (ACT-02) holds the Cortex ledger entry, and
``record_decision`` stores that pointer (hard rule 3: one ledger, pointers only).

psycopg only. No cortex_client, duckdb or sqlglot here (layers).
``DMS_ACTIONS`` defaults off and every write path here refuses while it is off.
Nothing on main calls this module.
"""

from __future__ import annotations

import json
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from typing import Any, TypedDict, TypeVar
from uuid import UUID

import psycopg

from dms_core.control_plane.action_types import (
    ACTION_TYPES,
    ALLOWED_KEYS,
    ActionSpec,
    ActionSpecError,
    ActionType,
    actions_enabled,
    canonical_decimal,
    diff_sha,
    mask_json,
    mask_text,
    object_key_hash,
    spec_sha,
    state_hash,
    validate_spec,
)
from dms_core.control_plane.proposals import (
    StaleTokenError,
    _token_for,
    confirm_proposal_version,
    create_proposal_version,
    decline_proposal_version,
)
from dms_core.control_plane.seed import USER_ID as SEED_USER_ID

_T = TypeVar("_T")
_SAVEPOINT = "dms_action_core"
APPROVAL_REF_MIN = 3
#: Actor values that name nobody: the shipped default user and stringified blanks.
_NO_ACTOR = frozenset({"", "none", "null", str(SEED_USER_ID)})


class ActionsDisabled(Exception):
    """``DMS_ACTIONS`` is not 1."""

    status_code = 403
    code = "actions_disabled"


class ApprovalRefRequired(ValueError):
    """A steward apply needs an approval reference of at least 3 characters."""

    status_code = 422
    code = "approval_ref_required"


class TokenDiffMismatch(Exception):
    """The stored diff no longer matches its token or its pinned spec digest."""

    status_code = 409
    code = "token_diff_mismatch"


class PreconditionFailed(Exception):
    """The writeback cell is not in the state the proposal was made against."""

    status_code = 412

    def __init__(self, code: str, current_value: str | None, current_row_version: int) -> None:
        super().__init__(
            f"{code}: current value {current_value!r}, row_version {current_row_version}"
        )
        self.code = code
        self.current_value = current_value
        self.current_row_version = current_row_version


class VerifyFailed(Exception):
    """The re-read after the write does not show the value that was written."""

    status_code = 500
    code = "verify_failed"


class ActorRequired(ValueError):
    """An apply or decline names no actor, or only the shipped default one."""

    status_code = 403
    code = "actor_required"


class ReceiptMismatch(Exception):
    """A decision record that the database does not back.

    ``record_decision`` rebuilds the receipt from the stored version, its pins
    and the writeback row, and refuses a caller record that differs in any
    field. ``field`` names the first one that differs.
    """

    status_code = 409
    code = "receipt_mismatch"

    def __init__(self, field: str, message: str = "") -> None:
        super().__init__(f"receipt_mismatch: {field}" + (f" ({message})" if message else ""))
        self.field = field


class LedgerEntryReused(Exception):
    """The Cortex entry id already backs another pointer in this tenant."""

    status_code = 409
    code = "ledger_entry_reused"


class ApplyResult(TypedDict):
    proposal_id: UUID
    version_id: UUID
    version_num: int
    action_type: str
    object_type: str
    object_key: dict[str, str]
    field: str
    op: str
    before: dict[str, Any]
    after: dict[str, Any] | None
    before_hash: str
    after_hash: str | None
    verified: bool
    rows_changed: int
    approval_ref: str
    evidence: dict[str, Any]
    actor_id: str


def _require_enabled() -> None:
    if not actions_enabled():
        raise ActionsDisabled("DMS_ACTIONS is off")


def _visible(text: str) -> int:
    """Characters a reader can see: not whitespace, control or format (zero-width)."""
    return sum(1 for ch in text if not ch.isspace() and unicodedata.category(ch)[0] not in "CZ")


def _require_ref(approval_ref: str | None) -> str:
    """The approval reference as stored: stripped, secrets scrubbed, PII masked."""
    ref = approval_ref.strip() if isinstance(approval_ref, str) else ""
    if _visible(ref) < APPROVAL_REF_MIN:
        raise ApprovalRefRequired(
            f"approval_ref must have at least {APPROVAL_REF_MIN} visible characters"
        )
    return mask_text(ref)


def _require_actor(actor_id: Any) -> str:
    """DR-0004 Option A: the one configured steward identity, never blank or the default."""
    actor = "" if actor_id is None else str(actor_id).strip()
    if actor.lower() in _NO_ACTOR or _visible(actor) == 0:
        raise ActorRequired("an apply or decline needs the configured steward identity")
    return actor


def _in_savepoint(conn: psycopg.Connection, fn: Callable[[], _T]) -> _T:
    """Run ``fn`` so that a refusal leaves the caller's transaction untouched."""
    conn.execute(f"SAVEPOINT {_SAVEPOINT}")
    try:
        out = fn()
    except BaseException:
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}")
            conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")
        except psycopg.Error:
            pass
        raise
    conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")
    return out


def _json_safe(value: Any) -> Any:
    """Evidence as JSON that round-trips through JSONB unchanged (floats as text)."""
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        return repr(value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _jsonb_or_null(value: Any) -> str | None:
    return None if value is None else json.dumps(_json_safe(value))


def _state(at: ActionType, key: Mapping[str, str], value: str | None, rv: int) -> dict[str, Any]:
    return {
        "object_type": at.object_type,
        "object_key": dict(key),
        "field": at.field,
        "value": value,
        "row_version": rv,
    }


def _read_cell(
    conn: psycopg.Connection,
    tenant_id: UUID,
    at: ActionType,
    key: Mapping[str, str],
    *,
    for_update: bool = False,
) -> tuple[UUID, str | None, int] | None:
    lock = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        f"""
        SELECT id, value, row_version
          FROM dms.action_writeback
         WHERE tenant_id = %s
           AND object_type = %s
           AND object_key_hash = %s
           AND field = %s{lock}
        """,
        (tenant_id, at.object_type, object_key_hash(key), at.field),
    ).fetchone()
    if row is None:
        return None
    return row[0], row[1], int(row[2])


def _summary(at: ActionType, spec: ActionSpec, current: str | None, source: str | None) -> str:
    key_text = " @ ".join(spec.object_key[k] for k in at.key_fields)
    source_text = f"{source if source is not None else 'none'} (source)"
    before_text = f"{current} (override)" if current is not None else source_text
    if spec.op == "clear":
        verb, after_text = "Clear", source_text
    else:
        verb, after_text = "Set", str(spec.value)
    return f"{verb} {at.field} for {at.object_type} {key_text}: {before_text} -> {after_text}"


def propose_action(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    space_id: UUID | None,
    actor_id: UUID | str | None,
    spec: ActionSpec | Mapping[str, Any],
    source_value: Any,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a pending action version. Does not commit."""
    _require_enabled()
    if not isinstance(spec, ActionSpec):
        spec = validate_spec(spec)
    at = ACTION_TYPES[spec.action_type]
    source = None if source_value is None else canonical_decimal(source_value)
    # Evidence is copied into an append-only receipt: no secret, no PII (0005 precedent).
    ev = mask_json(_json_safe(dict(evidence or {})))

    cell = _read_cell(conn, tenant_id, at, spec.object_key)
    current, row_version = (None, 0) if cell is None else (cell[1], cell[2])
    effective = current if current is not None else source
    if spec.op == "clear" and current is None:
        raise ActionSpecError("value_unchanged", "no override to clear")
    if spec.op == "set" and spec.value == effective:
        raise ActionSpecError("value_unchanged", f"value is already {effective}")

    sha = spec_sha(spec)
    diff: dict[str, Any] = {
        "kind": "action",
        **spec.as_dict(),
        "object_type": at.object_type,
        "field": at.field,
        "before": {"value": current, "row_version": row_version, "source_value": source},
        "expected_before_hash": state_hash(_state(at, spec.object_key, current, row_version)),
        "expected_after_hash": state_hash(_state(at, spec.object_key, spec.value, row_version + 1)),
        "spec_sha": sha,
        "source_kind": at.source_kind,
        "evidence": ev,
        "summary": _summary(at, spec, current, source),
    }

    def _write() -> dict[str, Any]:
        proposal_id = uuid.uuid4()
        conn.execute(
            """
            INSERT INTO dms.proposals (id, tenant_id, space_id, created_by)
            VALUES (%s, %s, %s, %s)
            """,
            (proposal_id, tenant_id, space_id, actor_id),
        )
        audit_id = ev.get("audit_id")
        # The version first: create_proposal_version refuses a proposal that is
        # already an action, so no second version can re-point this one's diff.
        ver = create_proposal_version(conn, proposal_id=proposal_id, tenant_id=tenant_id, diff=diff)
        conn.execute(
            """
            INSERT INTO dms.action_proposals (
              proposal_id, tenant_id, action_type, source_kind, spec_sha, evidence_audit_id,
              version_id, diff_sha
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                proposal_id,
                tenant_id,
                at.name,
                at.source_kind,
                sha,
                None if audit_id is None else str(audit_id),
                ver["id"],
                diff_sha(diff),
            ),
        )
        return {**ver, "version_id": ver["id"], "diff": diff}

    return _in_savepoint(conn, _write)


class _Stored(TypedDict):
    """One action version as stored, with the pins its proposal row holds."""

    version_id: UUID
    proposal_id: UUID
    version_num: int
    status: str
    token: str
    diff: dict[str, Any]
    space_id: UUID | None
    spec_sha: str
    action_type: str
    evidence_audit_id: str | None
    pinned_version_id: UUID
    diff_sha: str
    tenant_id: UUID


def _load_action_version(
    conn: psycopg.Connection, version_id: UUID, tenant_id: UUID | None = None
) -> _Stored:
    row = conn.execute(
        """
        SELECT v.id, v.proposal_id, v.version_num, v.status, v.idempotency_token, v.diff,
               p.space_id, a.spec_sha, a.action_type, a.evidence_audit_id, a.version_id,
               a.diff_sha, v.tenant_id
          FROM dms.proposal_versions v
          JOIN dms.proposals p ON p.id = v.proposal_id
          JOIN dms.action_proposals a ON a.proposal_id = v.proposal_id
         WHERE v.id = %s
        """,
        (version_id,),
    ).fetchone()
    if row is None or (tenant_id is not None and row[12] != tenant_id):
        raise StaleTokenError("not an action version")
    if not isinstance(row[5], dict):
        raise TokenDiffMismatch("stored diff is not an object")
    return _Stored(
        version_id=row[0],
        proposal_id=row[1],
        version_num=int(row[2]),
        status=str(row[3]),
        token=str(row[4]),
        diff=row[5],
        space_id=row[6],
        spec_sha=str(row[7]),
        action_type=str(row[8]),
        evidence_audit_id=None if row[9] is None else str(row[9]),
        pinned_version_id=row[10],
        diff_sha=str(row[11]),
        tenant_id=row[12],
    )


def _checked_spec(stored: _Stored, *, token: str) -> tuple[ActionSpec, ActionType]:
    """Refuse a stored diff that is not, byte for byte, the one proposed.

    The token and the whole-diff digest pin every field (expected hashes,
    ``before``, evidence, summary), and the version id pins which version may
    be applied at all. The derived fields are also recomputed from the spec, so
    a diff that disagrees with itself is refused even if it were re-tokened.
    """
    diff = stored["diff"]
    if _token_for(stored["proposal_id"], stored["version_num"], dict(diff)) != token:
        raise TokenDiffMismatch("diff does not match its idempotency token")
    if stored["version_id"] != stored["pinned_version_id"]:
        raise TokenDiffMismatch("an action proposal has one version; this is not it")
    if diff_sha(diff) != stored["diff_sha"]:
        raise TokenDiffMismatch("diff does not match the digest pinned at propose")
    try:
        spec = validate_spec({k: diff.get(k) for k in ALLOWED_KEYS})
    except ActionSpecError as exc:
        raise TokenDiffMismatch(f"stored spec no longer validates ({exc.code})") from exc
    pinned_sha = stored["spec_sha"]
    if spec_sha(spec) != pinned_sha or diff.get("spec_sha") != pinned_sha:
        raise TokenDiffMismatch("spec digest does not match the proposal")
    at = ACTION_TYPES[spec.action_type]
    if (
        spec.action_type != stored["action_type"]
        or diff.get("object_type") != at.object_type
        or diff.get("field") != at.field
        or diff.get("source_kind") != at.source_kind
    ):
        raise TokenDiffMismatch("diff names a different action type")
    before = diff.get("before")
    if not isinstance(before, dict) or set(before) != {"value", "row_version", "source_value"}:
        raise TokenDiffMismatch("diff has no before state")
    rv = before["row_version"]
    if not isinstance(rv, int) or isinstance(rv, bool) or rv < 0:
        raise TokenDiffMismatch("diff before row_version is not a count")
    if diff.get("expected_before_hash") != state_hash(
        _state(at, spec.object_key, before["value"], rv)
    ) or diff.get("expected_after_hash") != state_hash(
        _state(at, spec.object_key, spec.value, rv + 1)
    ):
        raise TokenDiffMismatch("expected state digests do not follow from the spec")
    if diff.get("summary") != _summary(at, spec, before["value"], before["source_value"]):
        raise TokenDiffMismatch("summary does not describe the spec")
    evidence = diff.get("evidence")
    cited = evidence.get("audit_id") if isinstance(evidence, dict) else None
    if (None if cited is None else str(cited)) != stored["evidence_audit_id"]:
        raise TokenDiffMismatch("evidence does not cite the answer pinned at propose")
    return spec, at


def apply_action_version(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    idempotency_token: str,
    actor_id: str,
    approval_ref: str,
    lock_timeout_seconds: float = 2.0,
) -> ApplyResult:
    """Confirm, write, re-read and verify one action version. Never commits."""
    _require_enabled()
    ref = _require_ref(approval_ref)
    actor = _require_actor(actor_id)

    def _apply() -> ApplyResult:
        confirmed = confirm_proposal_version(
            conn,
            tenant_id=tenant_id,
            idempotency_token=idempotency_token,
            lock_timeout_seconds=lock_timeout_seconds,
            allow_action=True,
        )
        version_id = confirmed["id"]
        proposal_id = confirmed["proposal_id"]
        version_num = int(confirmed["version_num"])
        stored = _load_action_version(conn, version_id, tenant_id)
        diff = stored["diff"]
        spec, at = _checked_spec(stored, token=idempotency_token)

        cell = _read_cell(conn, tenant_id, at, spec.object_key, for_update=True)
        current, row_version = (None, 0) if cell is None else (cell[1], cell[2])
        before_hash = state_hash(_state(at, spec.object_key, current, row_version))
        if before_hash != diff.get("expected_before_hash"):
            raise PreconditionFailed("object_changed", current, row_version)

        if cell is None:
            written = conn.execute(
                """
                INSERT INTO dms.action_writeback (
                  id, tenant_id, object_type, object_key, object_key_hash, field,
                  value, row_version, last_version_id
                ) VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, 1, %s)
                ON CONFLICT (tenant_id, object_type, object_key_hash, field) DO NOTHING
                RETURNING id
                """,
                (
                    uuid.uuid4(),
                    tenant_id,
                    at.object_type,
                    json.dumps(spec.object_key),
                    object_key_hash(spec.object_key),
                    at.field,
                    spec.value,
                    version_id,
                ),
            ).fetchone()
        else:
            written = conn.execute(
                """
                UPDATE dms.action_writeback
                   SET value = %s,
                       row_version = row_version + 1,
                       updated_at = now(),
                       last_version_id = %s
                 WHERE id = %s
                   AND row_version = %s
                RETURNING id
                """,
                (spec.value, version_id, cell[0], row_version),
            ).fetchone()
        if written is None:
            now = _read_cell(conn, tenant_id, at, spec.object_key)
            raise PreconditionFailed(
                "object_changed", None if now is None else now[1], 0 if now is None else now[2]
            )

        reread = conn.execute(
            """
            SELECT object_type, object_key, field, value, row_version
              FROM dms.action_writeback
             WHERE id = %s
            """,
            (written[0],),
        ).fetchone()
        if reread is None:
            raise VerifyFailed("written row is not readable")
        after_hash = state_hash(
            {
                "object_type": reread[0],
                "object_key": reread[1],
                "field": reread[2],
                "value": reread[3],
                "row_version": reread[4],
            }
        )
        if reread[3] != spec.value or after_hash != diff.get("expected_after_hash"):
            raise VerifyFailed(
                f"re-read shows {reread[3]!r} at row_version {reread[4]}, wrote {spec.value!r}"
            )

        before = dict(diff.get("before") or {})
        source = before.get("source_value")
        return ApplyResult(
            proposal_id=proposal_id,
            version_id=version_id,
            version_num=version_num,
            action_type=at.name,
            object_type=at.object_type,
            object_key=dict(spec.object_key),
            field=at.field,
            op=spec.op,
            before={"value": current, "row_version": row_version, "source_value": source},
            after={"value": reread[3], "row_version": int(reread[4]), "source_value": source},
            before_hash=before_hash,
            after_hash=after_hash,
            verified=True,
            rows_changed=1,
            approval_ref=ref,
            evidence=dict(diff.get("evidence") or {}),
            actor_id=actor,
        )

    return _in_savepoint(conn, _apply)


def decline_action_version(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    idempotency_token: str,
    approval_ref: str = "",
    actor_id: str = "",
    lock_timeout_seconds: float = 2.0,
) -> ApplyResult:
    """Reject a pending action version. Writes nothing to the writeback table.

    The returned record has ``rows_changed`` 0 and ``verified`` False, ready for
    ``record_decision(..., decision='declined')``. Never commits.
    """
    _require_enabled()
    # Both checks come before the status flips, as in apply: a refused decline
    # leaves the version pending, never rejected with no decision behind it.
    ref = _require_ref(approval_ref)
    actor = _require_actor(actor_id)

    def _decline() -> ApplyResult:
        row = conn.execute(
            """
            SELECT v.id
              FROM dms.proposal_versions v
              JOIN dms.action_proposals a ON a.proposal_id = v.proposal_id
             WHERE v.idempotency_token = %s
               AND v.tenant_id = %s
            """,
            (idempotency_token, tenant_id),
        ).fetchone()
        if row is None:
            raise StaleTokenError("not an action version")
        _checked_spec(_load_action_version(conn, row[0], tenant_id), token=idempotency_token)
        out = decline_proposal_version(
            conn,
            tenant_id=tenant_id,
            idempotency_token=idempotency_token,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        diff = _load_action_version(conn, out["id"], tenant_id)["diff"]
        return ApplyResult(
            proposal_id=out["proposal_id"],
            version_id=out["id"],
            version_num=int(out["version_num"]),
            action_type=str(diff.get("action_type")),
            object_type=str(diff.get("object_type")),
            object_key=dict(diff.get("object_key") or {}),
            field=str(diff.get("field")),
            op=str(diff.get("op")),
            before=dict(diff.get("before") or {}),
            after=None,
            before_hash=str(diff.get("expected_before_hash")),
            after_hash=None,
            verified=False,
            rows_changed=0,
            approval_ref=ref,
            evidence=dict(diff.get("evidence") or {}),
            actor_id=actor,
        )

    return _in_savepoint(conn, _decline)


_UNSET: Any = object()

#: Receipt fields the caller's record must match exactly, after the rebuild.
_RECEIPT_FIELDS = (
    "proposal_id",
    "version_num",
    "action_type",
    "object_type",
    "object_key",
    "field",
    "op",
    "before",
    "after",
    "before_hash",
    "after_hash",
    "verified",
    "rows_changed",
)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, UUID) or isinstance(b, UUID):
        return str(a) == str(b)
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    return bool(a == b)


def _rebuild_receipt(
    conn: psycopg.Connection, result: Mapping[str, Any], decision: str
) -> tuple[_Stored, dict[str, Any]]:
    """The receipt as the database backs it, or ReceiptMismatch.

    The version must be in the status the decision claims (``applied`` or
    ``rejected``) and its diff must pass every pin. An applied receipt is the
    writeback row this version last wrote, re-read and re-hashed here.
    """
    try:
        version_id = UUID(str(result.get("version_id")))
    except ValueError as exc:
        raise ReceiptMismatch("version_id", "not a version id") from exc
    stored = _load_action_version(conn, version_id)
    spec, at = _checked_spec(stored, token=stored["token"])
    diff = stored["diff"]
    want = "applied" if decision == "applied" else "rejected"
    if stored["status"] != want:
        raise ReceiptMismatch("decision", f"version is {stored['status']}, not {want}")
    before = dict(diff["before"])
    receipt: dict[str, Any] = {
        "proposal_id": stored["proposal_id"],
        "version_num": stored["version_num"],
        "action_type": at.name,
        "object_type": at.object_type,
        "object_key": dict(spec.object_key),
        "field": at.field,
        "op": spec.op,
        "before": before,
        "before_hash": diff["expected_before_hash"],
        "after": None,
        "after_hash": None,
        "verified": False,
        "rows_changed": 0,
    }
    row = conn.execute(
        """
        SELECT object_type, object_key, field, value, row_version
          FROM dms.action_writeback
         WHERE last_version_id = %s
        """,
        (version_id,),
    ).fetchone()
    if decision == "applied":
        if row is None:
            raise ReceiptMismatch("after", "no writeback row was last written by this version")
        after_hash = state_hash(
            {
                "object_type": row[0],
                "object_key": row[1],
                "field": row[2],
                "value": row[3],
                "row_version": row[4],
            }
        )
        if after_hash != diff["expected_after_hash"] or row[3] != spec.value:
            raise ReceiptMismatch("after_hash", "the writeback row is not what was applied")
        receipt.update(
            after={
                "value": row[3],
                "row_version": int(row[4]),
                "source_value": before.get("source_value"),
            },
            after_hash=after_hash,
            verified=True,
            rows_changed=1,
        )
    elif row is not None:
        raise ReceiptMismatch("rows_changed", "a declined version wrote the writeback table")
    for name in _RECEIPT_FIELDS:
        if not _same(receipt[name], result.get(name)):
            raise ReceiptMismatch(name, "the record differs from what the database holds")
    return stored, receipt


def record_decision(
    conn: psycopg.Connection,
    result: Mapping[str, Any],
    *,
    decision: str,
    cortex_entry_id: str,
    space_id: UUID | None = _UNSET,
) -> dict[str, Any]:
    """Store the decision and its Cortex ledger pointer. Never commits.

    The caller appends to Cortex first and passes the entry id; this inserts the
    ``dms.ledger_ref`` pointer and the append-only decision row in the same
    transaction as the apply, so a failed append rolls all of it back.

    The record is not trusted: every receipt field is rebuilt from the stored
    version and the writeback row, and a caller record that differs is refused
    (ReceiptMismatch). The Space is the proposal's own; ``space_id``, when
    given, must equal it. Evidence may add keys (a decline reason) but not
    change the ones pinned at propose. An entry id backs one pointer only.
    """
    _require_enabled()
    if decision not in ("applied", "declined"):
        raise ValueError(f"unknown decision {decision!r}")
    if decision == "applied" and not (result.get("verified") and result.get("rows_changed") == 1):
        raise ValueError("an applied decision must have verified one changed row")
    ref = _require_ref(result.get("approval_ref"))
    actor = _require_actor(result.get("actor_id"))
    entry = (cortex_entry_id or "").strip()
    if not entry:
        raise ValueError("cortex_entry_id is required")

    def _record() -> dict[str, Any]:
        stored, receipt = _rebuild_receipt(conn, result, decision)
        if space_id is not _UNSET and not _same(space_id, stored["space_id"]):
            raise ReceiptMismatch("space_id", "the proposal belongs to another Space")
        pinned = stored["diff"].get("evidence") or {}
        evidence = mask_json(_json_safe(dict(result.get("evidence") or {})))
        for key, value in pinned.items():
            if evidence.get(key) != value:
                raise ReceiptMismatch("evidence", f"evidence {key!r} differs from the proposal")
        tenant_id = stored["tenant_id"]
        seq_row = conn.execute(
            """
            INSERT INTO dms.ledger_ref (tenant_id, cortex_entry_id)
            VALUES (%s, %s)
            ON CONFLICT (tenant_id, cortex_entry_id) DO NOTHING
            RETURNING seq
            """,
            (tenant_id, entry),
        ).fetchone()
        if seq_row is None:
            raise LedgerEntryReused(f"cortex entry {entry!r} already backs a pointer")
        decision_id = uuid.uuid4()
        conn.execute(
            """
            INSERT INTO dms.action_decisions (
              id, tenant_id, space_id, proposal_id, proposal_version_id, decision,
              action_type, object_type, object_key, field, op, before, after,
              before_hash, after_hash, rows_changed, verified, approval_ref, actor_id,
              evidence, cortex_entry_id, ledger_seq
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s::jsonb,
              %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s
            )
            """,
            (
                decision_id,
                tenant_id,
                stored["space_id"],
                receipt["proposal_id"],
                stored["version_id"],
                decision,
                receipt["action_type"],
                receipt["object_type"],
                json.dumps(receipt["object_key"]),
                receipt["field"],
                receipt["op"],
                _jsonb_or_null(receipt["before"]),
                _jsonb_or_null(receipt["after"]),
                receipt["before_hash"],
                receipt["after_hash"],
                receipt["rows_changed"],
                receipt["verified"],
                ref,
                actor,
                json.dumps(evidence),
                entry,
                int(seq_row[0]),
            ),
        )
        return {"decision_id": decision_id, "ledger_seq": int(seq_row[0])}

    return _in_savepoint(conn, _record)


def is_action_version(conn: psycopg.Connection, *, tenant_id: UUID, idempotency_token: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM dms.proposal_versions v
          JOIN dms.action_proposals a ON a.proposal_id = v.proposal_id
         WHERE v.idempotency_token = %s
           AND v.tenant_id = %s
        """,
        (idempotency_token, tenant_id),
    ).fetchone()
    return row is not None


def list_overrides(
    conn: psycopg.Connection, *, tenant_id: UUID, object_type: str
) -> list[dict[str, Any]]:
    """Writeback cells that currently override the source (value is not NULL)."""
    rows = conn.execute(
        """
        SELECT object_key, field, value, row_version, updated_at, last_version_id
          FROM dms.action_writeback
         WHERE tenant_id = %s
           AND object_type = %s
           AND value IS NOT NULL
         ORDER BY object_key_hash, field
        """,
        (tenant_id, object_type),
    ).fetchall()
    return [
        {
            "object_type": object_type,
            "object_key": r[0],
            "field": r[1],
            "value": r[2],
            "row_version": int(r[3]),
            "updated_at": r[4],
            "last_version_id": r[5],
        }
        for r in rows
    ]


_DECISION_COLUMNS = (
    "id",
    "tenant_id",
    "space_id",
    "proposal_id",
    "proposal_version_id",
    "decision",
    "action_type",
    "object_type",
    "object_key",
    "field",
    "op",
    "before",
    "after",
    "before_hash",
    "after_hash",
    "rows_changed",
    "verified",
    "approval_ref",
    "actor_id",
    "evidence",
    "cortex_entry_id",
    "ledger_seq",
    "decided_at",
)


def get_decision(
    conn: psycopg.Connection, *, tenant_id: UUID, decision_id: UUID
) -> dict[str, Any] | None:
    row = conn.execute(
        f"""
        SELECT {", ".join(_DECISION_COLUMNS)}
          FROM dms.action_decisions
         WHERE id = %s
           AND tenant_id = %s
        """,
        (decision_id, tenant_id),
    ).fetchone()
    if row is None:
        return None
    return dict(zip(_DECISION_COLUMNS, row, strict=True))
