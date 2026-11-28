"""Flag-off hooks return without reading a store or calling OpenVault.

On, they enforce the manager assignment and the upload policy. A finding
records the type and a hash, never the secret or the personal value.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from dms_api.settings import Settings

_TEXT_SUFFIXES = (
    ".csv",
    ".txt",
    ".md",
    ".json",
    ".sql",
    ".tsv",
    ".log",
    ".xml",
    ".yaml",
    ".yml",
    ".env",
)


def _enabled() -> bool:
    from dms_core.governance import enabled

    return enabled()


def bind_store(settings: Settings) -> None:
    if not _enabled():
        return
    from dms_core.governance.store import get_store

    get_store().bind(settings.database_url, settings.dms_tenant_id)


def current_subject(settings: Settings) -> Any:
    from dms_api.auth_sso import current_principal
    from dms_api.auth_sso import enabled as sso_on

    if sso_on():
        principal = current_principal()
        if principal is not None and principal.kind == "user":
            return _subject(principal.user_id, principal.email, principal.role, "user")
    role = (settings.dms_actor_role or "steward").strip().lower()
    return _subject(settings.dms_actor_user_id, "", role, "deployment")


def _subject(user_id: str, email: str, role: str, kind: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(user_id=user_id, email=email, sso_role=role, kind=kind)


def _raise(decision: Any) -> None:
    if decision.allowed:
        return
    raise HTTPException(
        status_code=decision.status,
        detail={"code": decision.code, "message": decision.message},
    )


def guard_feature(settings: Settings, feature: str) -> None:
    """No-op unless the flag is exactly ``1``."""
    if not _enabled():
        return
    from dms_core.governance.enforce import feature_decision
    from dms_core.governance.store import get_store

    subject = current_subject(settings)
    row = get_store().assignment(settings.dms_tenant_id, subject.user_id)
    _raise(feature_decision(row, feature))
    _raise(
        _budget(settings, subject.user_id, row),
    )


def _budget(settings: Settings, user_id: str, row: Any) -> Any:
    from dms_core.governance.enforce import budget_decision
    from dms_core.governance.store import get_store

    from dms_api.gov_ov import get_reader

    return budget_decision(
        get_store(),
        tenant_id=settings.dms_tenant_id,
        user_id=user_id,
        assignment=row,
        reader=get_reader(settings.openvault_url),
    )


def guard_text(settings: Settings, text: str, *, source: str) -> str:
    """Return the text to keep. Flag off returns ``text`` unchanged."""
    if not _enabled():
        return text
    from dms_core.governance.scan import scan_text

    findings, redacted = scan_text(text or "")
    if not findings:
        return text
    return _apply_upload_rule(
        settings,
        findings,
        redacted,
        original=text,
        source=source,
        filename="",
    )


def guard_bytes(settings: Settings, *, filename: str, data: bytes) -> bytes:
    """Return the bytes to ingest. Flag off returns ``data`` unchanged."""
    if not _enabled():
        return data
    from dms_core.governance.scan import scan_text

    try:
        text = data.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        binary = True
    if not filename.lower().endswith(_TEXT_SUFFIXES) and not binary:
        binary = True
    findings, redacted = scan_text(text)
    if not findings:
        return data
    kept = _apply_upload_rule(
        settings,
        findings,
        redacted,
        original=text,
        source="upload",
        filename=filename,
        file_sha=hashlib.sha256(data).hexdigest(),
        binary=binary,
    )
    if kept == text:
        return data
    return kept.encode("utf-8")


def _upload_rule(settings: Settings) -> Any:
    from dms_core.governance.store import Policy, get_store

    saved = get_store().upload_rule(settings.dms_tenant_id)
    if saved is not None:
        return saved
    raw = os.environ.get("DMS_GOV_UPLOAD_POLICY", "block")
    upload = raw if raw in {"block", "redact", "warn"} else "block"
    redact = os.environ.get("DMS_GOV_REDACT", "0") == "1"
    return Policy(upload, redact)


def _apply_upload_rule(
    settings: Settings,
    findings: tuple[str, ...],
    redacted: str,
    *,
    original: str,
    source: str,
    filename: str,
    file_sha: str = "",
    binary: bool = False,
) -> str:
    from dms_core.governance.store import Alert, Finding, get_store

    policy = _upload_rule(settings)
    action = policy.upload
    if binary and action == "redact":
        action = "block"
    subject = current_subject(settings)
    digest = file_sha or hashlib.sha256(original.encode("utf-8", errors="replace")).hexdigest()
    store = get_store()
    now = datetime.now(UTC)
    for finding in findings:
        store.add_finding(
            Finding(
                finding_id=str(uuid4()),
                tenant_id=settings.dms_tenant_id,
                user_id=subject.user_id,
                file_sha256=digest,
                filename=(filename or source)[:200],
                finding_type=finding,
                action=action,
                at=now,
            )
        )
    shown = ", ".join(findings)
    store.add_alert(
        Alert(
            alert_id=str(uuid4()),
            tenant_id=settings.dms_tenant_id,
            user_id=subject.user_id,
            kind="upload" if action == "block" else "upload_notice",
            summary=(f"{action}: {shown}. sha256 {digest[:16]}. The value was not stored."),
            at=now,
        )
    )
    if action == "block":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "gov_upload_blocked",
                "message": (
                    f"This was blocked because the scan found {shown}. "
                    "The secret or personal value was not stored. A manager has been alerted."
                ),
            },
        )
    if action == "redact":
        return redacted
    return original


def note_recorded_ask(settings: Settings, rec: Any, envelope: dict[str, Any] | None) -> None:
    """Copy a cost from the envelope when the flag is on. No second question store."""
    if not _enabled():
        return
    from dms_core.governance.store import AskCost, get_store

    usage = envelope.get("usage") if isinstance(envelope, dict) else None
    if not isinstance(usage, dict):
        return
    cost = usage.get("est_cost_usd", usage.get("cost_usd"))
    if isinstance(cost, bool) or not isinstance(cost, int | float):
        cost_usd = None
    else:
        cost_usd = float(cost)
    model = usage.get("model") or usage.get("model_served") or ""
    tokens = usage.get("total_tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int | float):
        tokens = 0
    get_store().remember_cost(
        AskCost(
            ask_id=str(rec.ask_id),
            cost_usd=cost_usd,
            model=str(model).replace("\n", " ")[:120],
            tokens=max(0, int(tokens)),
        )
    )


def refresh_usage(settings: Settings, user_ids: list[str]) -> dict[str, bool]:
    """Fold OpenVault events into the spend log. One call per user, capped at 50."""
    from dms_core.governance.store import Spend, get_store

    from dms_api.gov_ov import get_reader

    store = get_store()
    reader = get_reader(settings.openvault_url)
    month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    priced = True
    ok = True
    seen: list[str] = []
    for user_id in user_ids:
        if user_id in seen:
            continue
        seen.append(user_id)
        if len(seen) > 50:
            break
        read = reader(user_id, month.timestamp())
        if not read.ok:
            ok = False
            continue
        if read.truncated:
            ok = False
        for event in read.events:
            if event.cost_usd is None:
                priced = False
            store.add_spend(
                Spend(
                    event_id=event.event_id,
                    tenant_id=settings.dms_tenant_id,
                    user_id=user_id,
                    at=event.at,
                    tokens=event.tokens,
                    cost_usd=event.cost_usd,
                    model=event.model,
                    source="ov",
                )
            )
        store.mark_readback(settings.dms_tenant_id, user_id, True)
    if not seen:
        priced = False
    return {"ok": ok, "priced": priced}


def is_manager(settings: Settings) -> bool:
    from dms_core.governance.store import get_store

    subject = current_subject(settings)
    row = get_store().assignment(settings.dms_tenant_id, subject.user_id)
    if row is not None:
        return row.role == "manager"
    return subject.sso_role in {"admin", "steward", "manager"}
