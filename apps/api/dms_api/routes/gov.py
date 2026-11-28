"""Manager governance console. Mounted only when ``DMS_GOV_CONSOLE`` is ``1``.

Question text is returned to managers. Members get a refusal, not a blanked
copy of the question. Sensitive values are redacted on the way out when the
manager turns that on. The stored ask row is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cortex_client import compliance_gate
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from dms_api.deps import AskAuditDep, CortexDep, SettingsDep
from dms_api.gatekeeping import enforce
from dms_api.gov_guard import current_subject, is_manager, refresh_usage
from dms_api.settings import Settings

router = APIRouter(prefix="/v1/gov", tags=["governance"])


class AssignmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1, max_length=200)
    email: str = Field(default="", max_length=320)
    assignment: str
    daily_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    monthly_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    daily_tokens: int | None = Field(default=None, ge=0, le=100_000_000_000)
    monthly_tokens: int | None = Field(default=None, ge=0, le=100_000_000_000)
    ask: bool = True
    create: bool = True
    connectors: bool = True


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload: str
    redact_sensitive: bool = False


class PasteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=500_000)


def _manager(settings: Settings) -> Any:
    subject = current_subject(settings)
    if not is_manager(settings):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "gov_manager_required",
                "message": "Question text, budgets, and the governance console are for managers.",
            },
        )
    return subject


def _clean_id(value: str) -> str:
    text = value.strip()
    if not text or any(ch in text for ch in "\r\n\x00"):
        raise HTTPException(
            status_code=422,
            detail={"code": "gov_user", "message": "User id is empty."},
        )
    return text


def _assignment_out(row: Any) -> dict[str, Any]:
    return {
        "user_id": row.user_id,
        "email": row.email,
        "role": row.role,
        "daily_usd": row.daily_usd,
        "monthly_usd": row.monthly_usd,
        "daily_tokens": row.daily_tokens,
        "monthly_tokens": row.monthly_tokens,
        "ask": row.allow_ask,
        "create": row.allow_create,
        "connectors": row.allow_connectors,
    }


def _upload_rule(settings: Settings) -> Any:
    from dms_api.gov_guard import _upload_rule as saved

    return saved(settings)


@router.get("/session")
def gov_session(settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="gov.session",
        metadata={"task_id": "gov.session"},
        client=cortex,
    )
    enforce(decision, mutation=False)
    subject = current_subject(settings)
    policy = _upload_rule(settings)
    return {
        "enabled": True,
        "role": "manager" if is_manager(settings) else "member",
        "user_id": subject.user_id,
        "actor_kind": subject.kind,
        "upload": policy.upload,
        "redact_sensitive": policy.redact_sensitive,
    }


@router.get("/assignments")
def gov_assignments(settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="gov.assignments.read",
        metadata={"task_id": "gov.assignments.read"},
        client=cortex,
    )
    enforce(decision, mutation=False)
    subject = _manager(settings)
    from dms_core.governance.store import get_store

    rows = get_store().assignments(settings.dms_tenant_id)
    return {"actor": subject.user_id, "assignments": [_assignment_out(row) for row in rows]}


@router.put("/assignments")
def gov_assign(body: AssignmentIn, settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="gov.assignment.write",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "gov.assignment.write"},
        client=cortex,
    )
    enforce(decision)
    subject = _manager(settings)
    if body.assignment not in {"manager", "member"}:
        raise HTTPException(
            status_code=422,
            detail={"code": "gov_assignment", "message": "Assignment is manager or member."},
        )
    from dms_core.governance.store import Assignment, get_store

    row = Assignment(
        tenant_id=settings.dms_tenant_id,
        user_id=_clean_id(body.subject_id),
        email=body.email.strip(),
        role=body.assignment,
        daily_usd=body.daily_usd,
        monthly_usd=body.monthly_usd,
        daily_tokens=body.daily_tokens,
        monthly_tokens=body.monthly_tokens,
        allow_ask=body.ask,
        allow_create=body.create,
        allow_connectors=body.connectors,
        updated_by=subject.user_id,
    )
    store = get_store()
    store.upsert_assignment(row)
    return {"assignment": _assignment_out(row), "persisted": store.persist_error == ""}


@router.put("/policy")
def gov_upload_rule(body: PolicyIn, settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="gov.policy.write",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "gov.policy.write"},
        client=cortex,
    )
    enforce(decision)
    _manager(settings)
    if body.upload not in {"block", "redact", "warn"}:
        raise HTTPException(
            status_code=422,
            detail={"code": "gov_policy", "message": "Upload policy is block, redact, or warn."},
        )
    from dms_core.governance.store import Policy, get_store

    policy = Policy(body.upload, body.redact_sensitive)
    get_store().set_upload_rule(settings.dms_tenant_id, policy)
    return {"upload": policy.upload, "redact_sensitive": policy.redact_sensitive}


@router.post("/scan")
def gov_scan(body: PasteIn, settings: SettingsDep, cortex: CortexDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="gov.scan",
        actor=settings.dms_actor_user_id,
        metadata={"task_id": "gov.scan"},
        client=cortex,
    )
    enforce(decision)
    from dms_core.governance.scan import scan_text

    from dms_api.gov_guard import guard_text

    findings, _redacted = scan_text(body.text)
    if not findings:
        return {"action": "allow", "findings": [], "redacted": False, "text": ""}
    kept = guard_text(settings, body.text, source="paste")
    policy = _upload_rule(settings)
    changed = kept != body.text
    return {
        "action": policy.upload,
        "findings": list(findings),
        "redacted": changed,
        "text": kept if changed else "",
    }


@router.get("/audit")
def gov_audit(settings: SettingsDep, cortex: CortexDep, audit: AskAuditDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="gov.audit.read",
        metadata={"task_id": "gov.audit.read"},
        client=cortex,
    )
    enforce(decision, mutation=False)
    _manager(settings)
    from dms_core.governance.scan import pii_kinds, scan_text
    from dms_core.governance.store import get_store

    policy = _upload_rule(settings)
    store = get_store()
    rows = audit.list_between(since=None, until=None, limit=100)
    out: list[dict[str, Any]] = []
    for rec in reversed(rows):
        question = rec.question
        sql = rec.executed_sql
        if policy.redact_sensitive:
            _types, question = scan_text(question)
            _sql_types, sql = scan_text(sql)
        cost = store.cost_for(rec.ask_id)
        out.append(
            {
                "ask_id": rec.ask_id,
                "asked_at": rec.asked_at.isoformat(),
                "actor": rec.actor,
                "actor_kind": rec.actor_kind,
                "question": question,
                "sql": sql,
                "row_count": rec.row_count,
                "cost_usd": None if cost is None else cost.cost_usd,
                "model": "" if cost is None else cost.model,
                "pii": list(pii_kinds(rec.question)),
            }
        )
    return {"rows": out, "redact_sensitive": policy.redact_sensitive}


@router.get("/dashboard")
def gov_dashboard(settings: SettingsDep, cortex: CortexDep, audit: AskAuditDep) -> dict[str, Any]:
    decision = compliance_gate(
        action="gov.dashboard.read",
        metadata={"task_id": "gov.dashboard.read"},
        client=cortex,
    )
    enforce(decision, mutation=False)
    subject = _manager(settings)
    from dms_core.governance.scan import pii_kinds, scan_text
    from dms_core.governance.store import get_store

    store = get_store()
    tenant = settings.dms_tenant_id
    ids = [row.user_id for row in store.assignments(tenant)]
    ids.append(subject.user_id)
    ov = refresh_usage(settings, ids)
    policy = _upload_rule(settings)
    spend = store.spend_for(tenant)
    by_day: list[dict[str, Any]] = []
    for row in spend:
        by_day.append(
            {
                "user_id": row.user_id,
                "day": row.at.astimezone(UTC).date().isoformat(),
                "tokens": row.tokens,
                "cost_usd": row.cost_usd,
                "model": row.model,
            }
        )
    questions: list[dict[str, Any]] = []
    for rec in reversed(audit.list_between(since=None, until=None, limit=8)):
        question = rec.question
        if policy.redact_sensitive:
            _types, question = scan_text(question)
        questions.append(
            {
                "asked_at": rec.asked_at.isoformat(),
                "actor": rec.actor,
                "actor_kind": rec.actor_kind,
                "question": question[:180],
                "row_count": rec.row_count,
                "pii": list(pii_kinds(rec.question)),
            }
        )
    burn: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for assigned in store.assignments(tenant):
        todays = [item for item in spend if item.user_id == assigned.user_id and item.at >= day]
        tokens = sum(item.tokens for item in todays)
        costs = [item.cost_usd for item in todays]
        priced = all(cost is not None for cost in costs) if costs else True
        usd = None if not priced else sum(cost or 0 for cost in costs)
        burn.append(
            {
                "user_id": assigned.user_id,
                "role": assigned.role,
                "daily_usd": assigned.daily_usd,
                "spent_usd": usd,
                "daily_tokens": assigned.daily_tokens,
                "spent_tokens": tokens,
                "usd_priced": priced and assigned.daily_usd is not None,
            }
        )
    findings = store.findings(tenant)
    return {
        "enabled": True,
        "upload": policy.upload,
        "redact_sensitive": policy.redact_sensitive,
        "ov": ov,
        "spend_by_user_day": by_day[-200:],
        "top_questions": questions,
        "blocked": [
            {
                "at": alert.at.isoformat(),
                "kind": alert.kind,
                "summary": alert.summary,
                "user_id": alert.user_id,
            }
            for alert in store.alerts(tenant)[:40]
        ],
        "pii_hits": [
            {
                "at": row.at.isoformat(),
                "finding_type": row.finding_type,
                "user_id": row.user_id,
                "file_sha256": row.file_sha256,
            }
            for row in findings
            if row.finding_type.startswith("pii:")
        ][:40],
        "budget_burn": burn,
        "assignments": [_assignment_out(row) for row in store.assignments(tenant)],
    }


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    if text[:1] in "=+-@":
        text = "'" + text
    if any(ch in text for ch in ',"\r\n'):
        text = '"' + text.replace('"', '""') + '"'
    return text


@router.get("/spend.csv")
def gov_spend_csv(settings: SettingsDep, cortex: CortexDep) -> Response:
    decision = compliance_gate(
        action="gov.spend.export",
        metadata={"task_id": "gov.spend.export"},
        client=cortex,
    )
    enforce(decision, mutation=False)
    subject = _manager(settings)
    from dms_core.governance.store import get_store

    store = get_store()
    ids = [row.user_id for row in store.assignments(settings.dms_tenant_id)]
    ids.append(subject.user_id)
    refresh_usage(settings, ids)
    lines = ["user_id,day,tokens,cost_usd,model,source"]
    for row in store.spend_for(settings.dms_tenant_id):
        lines.append(
            ",".join(
                _csv_cell(part)
                for part in (
                    row.user_id,
                    row.at.astimezone(UTC).date().isoformat(),
                    row.tokens,
                    "" if row.cost_usd is None else f"{row.cost_usd:.6f}",
                    row.model,
                    row.source,
                )
            )
        )
    body = "\n".join(lines) + "\n"
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=gov-spend.csv"},
    )
