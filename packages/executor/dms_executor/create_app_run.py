"""Wire a Create app to governed SQL and to the Create surface.

``duckdb`` is not called here. Reads go through ``run_readonly``. Grants go
through ``serve_gap``. A doc or slides chain calls ``start_session``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, NoReturn

from dms_core.create_app.chain import plan_handoff, research_turn
from dms_core.create_app.connectors import confirm_action, dry_run_action
from dms_core.create_app.contract import contract_document
from dms_core.create_app.harness import (
    CreateAppProblem,
    load_app,
    preview_html,
    public_view,
    resolve_grants,
    run_app,
    save_app,
    token_matches,
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_conn: Any = None
_secret_get: Any = None
_http_post: Any = None


def bind_writeback(conn: Any) -> None:
    """Test and deploy hook for the action-core connection. Unset writes nothing."""
    global _conn
    _conn = conn


def bind_salesforce(secret_get: Any, http_post: Any = None) -> None:
    """Secrets come from this callable. A deployment binds OpenVault. Env is ignored."""
    global _secret_get, _http_post
    _secret_get = secret_get
    _http_post = http_post


def ceiling() -> set[str]:
    raw = os.environ.get("DMS_CREATE_GRANTS", "").strip()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _warehouse() -> Path:
    raw = os.environ.get("DMS_CREATE_WAREHOUSE", "").strip()
    if not raw:
        raise CreateAppProblem("warehouse_missing")
    return Path(raw)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def governed_query(sql: str, *, grants: set[str], warehouse: Path | None = None) -> dict[str, Any]:
    """One granted read. Anything else returns a code and no rows."""
    from dms_executor.demo_warehouse import SERVING_DIALECT
    from dms_executor.grant_struct import serve_gap
    from dms_executor.sql_loop import run_readonly

    text = (sql or "").strip()
    if not text:
        return {"ok": False, "code": "sql_missing", "rows": []}
    path = warehouse or _warehouse()
    gap = serve_gap(text, grantable=set(grants), dialect=SERVING_DIALECT, warehouse=path)
    if gap:
        return {"ok": False, "code": gap, "rows": []}
    rows, err = run_readonly(text, path)
    if err or rows is None:
        return {"ok": False, "code": err or "sql_failed", "rows": []}
    clean = []
    for row in rows:
        if isinstance(row, dict):
            clean.append({str(key): _jsonable(val) for key, val in row.items()})
    return {"ok": True, "code": "ok", "rows": clean}


def schema_catalog(grants: set[str], warehouse: Path | None = None) -> list[dict[str, Any]]:
    """Column names and types. No cell values."""
    from dms_executor.sql_loop import run_readonly

    path = warehouse or _warehouse()
    tables: list[dict[str, Any]] = []
    for name in sorted(grants):
        if _IDENT.fullmatch(name) is None:
            continue
        sql = (
            "SELECT column_name, data_type FROM information_schema.columns "
            f"WHERE table_schema = 'main' AND table_name = '{name}' ORDER BY column_name"
        )
        rows, err = run_readonly(sql, path)
        columns: list[dict[str, str]] = []
        if not err and rows:
            for row in rows:
                col = row.get("column_name")
                typ = row.get("data_type")
                if isinstance(col, str):
                    columns.append({"name": col, "type": typ if isinstance(typ, str) else ""})
        tables.append({"name": name, "columns": columns})
    return tables


def _links() -> list[dict[str, str]]:
    raw = os.environ.get("DMS_CREATE_ONTOLOGY", "").strip()
    if not raw:
        return []
    import json

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, str]] = []
    for item in parsed:
        if (
            isinstance(item, dict)
            and isinstance(item.get("from"), str)
            and isinstance(item.get("to"), str)
        ):
            out.append({"from": item["from"], "to": item["to"]})
    return out


def _context_problem(exc: BaseException) -> NoReturn:
    from dms_executor.create_surface import CreateProblem

    if isinstance(exc, CreateProblem) and exc.args and isinstance(exc.args[0], str):
        raise CreateAppProblem(exc.args[0]) from None
    raise exc


def pack_create_context(
    *, actor: str, kind: str, grants: set[str], context: dict[str, Any] | None
) -> dict[str, Any]:
    """The #681 pack. Off, or with no context, this is empty. No second packer."""
    from dms_executor.create_context import context_enabled, pack_sources

    if not context_enabled() or not context:
        return {}
    form = context.get("questionnaire")
    try:
        return pack_sources(
            actor=actor,
            kind=kind,
            granted=grants,
            chat_ids=list(context.get("chat_ids") or []),
            project_id=str(context.get("project_id") or ""),
            questionnaire=form if isinstance(form, dict) else None,
        )
    except Exception as exc:  # noqa: BLE001 - CreateProblem becomes a harness code
        _context_problem(exc)


def layout_for(kind: str) -> list[dict[str, Any]]:
    """The #681 layout hook. Empty until DMS-STYLE-V2-01 fills it."""
    from dms_executor.create_context import layout_blocks

    blocks = layout_blocks(kind)
    return [item for item in blocks if isinstance(item, dict)]


def generate_app(
    *,
    space_id: str,
    goal: str,
    actor: str,
    api_key: str,
    grants: list[str] | None = None,
    catalog: list[Any] | None = None,
    transport: Any = None,
    cursor_transport: Any = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bound = resolve_grants(grants, ceiling())
    mapped = catalog if catalog is not None else schema_catalog(bound)
    ov = os.environ.get("OPENVAULT_URL", "").strip() or None
    return run_app(
        goal=goal,
        catalog=mapped,
        grants=bound,
        space_id=space_id,
        actor=actor,
        api_key=api_key,
        links=_links(),
        transport=transport,
        cursor_transport=cursor_transport,
        query_fn=lambda sql, granted: governed_query(sql, grants=granted),
        ov_url=ov,
        sources=pack_create_context(actor=actor, kind="app", grants=bound, context=context),
        layout=layout_for("app"),
        context=context if context else None,
    )


def read_app(*, app_id: str) -> dict[str, Any]:
    return public_view(load_app(app_id))


def app_preview(*, app_id: str) -> str:
    return preview_html(app_id)


def publish_app(*, app_id: str) -> dict[str, Any]:
    record = load_app(app_id)
    if not record.get("ok"):
        raise CreateAppProblem("judge_failed")
    record["published"] = True
    save_app(record)
    return public_view(record)


def query_app(*, app_id: str, sql: str, presented: str) -> dict[str, Any]:
    record = load_app(app_id)
    if not token_matches(presented, str(record.get("token_hash") or "")):
        raise CreateAppProblem("token_rejected")
    grants = set(record.get("grants") or [])
    return governed_query(sql, grants=grants)


def _bundle_actions(record: dict[str, Any]) -> list[dict[str, Any]]:
    bundle = record.get("bundle")
    if not isinstance(bundle, dict) or not isinstance(bundle.get("actions"), list):
        return []
    return [item for item in bundle["actions"] if isinstance(item, dict)]


def dry_run_app(
    *, app_id: str, action_id: str, actor: str, payload: dict[str, Any] | None
) -> dict[str, Any]:
    record = load_app(app_id)
    audit = list(record.get("audit") or [])
    result = dry_run_action(
        actions=_bundle_actions(record),
        action_id=action_id,
        payload=payload,
        grants=set(record.get("grants") or []),
        actor=actor,
        audit=audit,
    )
    record["audit"] = audit
    save_app(record)
    return result


def confirm_app(
    *,
    app_id: str,
    action_id: str,
    actor: str,
    approval_ref: str | None,
    payload: dict[str, Any] | None,
    tenant_id: str,
    space_id: str | None,
) -> dict[str, Any]:
    record = load_app(app_id)
    audit = list(record.get("audit") or [])
    result = confirm_action(
        actions=_bundle_actions(record),
        action_id=action_id,
        payload=payload,
        grants=set(record.get("grants") or []),
        actor=actor,
        approval_ref=approval_ref,
        audit=audit,
        conn=_conn,
        tenant_id=tenant_id,
        space_id=space_id,
        audit_id=app_id,
        secret_get=_secret_get,
        http_post=_http_post,
    )
    record["audit"] = audit
    save_app(record)
    return scrub_result(result)


def scrub_result(result: dict[str, Any]) -> dict[str, Any]:
    from dms_core.create_app.connectors import scrub

    return scrub(result)


def chain_app(
    *,
    app_id: str,
    kind: str,
    goal: str,
    actor: str,
    api_key: str,
    audience: str,
    tenant_id: str,
    requested_grants: list[str] | None,
    transport: Any = None,
) -> dict[str, Any]:
    record = load_app(app_id)
    return _start_chain(
        source="app",
        source_ref=app_id,
        record=record,
        source_grants=set(record.get("grants") or []),
        kind=kind,
        goal=goal,
        actor=actor,
        api_key=api_key,
        audience=audience,
        tenant_id=tenant_id,
        space_id=str(record.get("space_id") or ""),
        requested_grants=requested_grants,
        transport=transport,
    )


def chain_answer(
    *,
    source_ref: str,
    kind: str,
    goal: str,
    actor: str,
    api_key: str,
    audience: str,
    tenant_id: str,
    space_id: str,
    grants: list[str],
    requested_grants: list[str] | None,
    record: dict[str, Any] | None = None,
    transport: Any = None,
) -> dict[str, Any]:
    source_grants = set(grants) & ceiling()
    if not source_grants:
        raise CreateAppProblem("grants_required")
    return _start_chain(
        source="answer",
        source_ref=source_ref,
        record=record,
        source_grants=source_grants,
        kind=kind,
        goal=goal,
        actor=actor,
        api_key=api_key,
        audience=audience,
        tenant_id=tenant_id,
        space_id=space_id,
        requested_grants=requested_grants,
        transport=transport,
    )


def _start_chain(
    *,
    source: str,
    source_ref: str,
    record: dict[str, Any] | None,
    source_grants: set[str],
    kind: str,
    goal: str,
    actor: str,
    api_key: str,
    audience: str,
    tenant_id: str,
    space_id: str,
    requested_grants: list[str] | None,
    transport: Any,
) -> dict[str, Any]:
    handoff = plan_handoff(
        source=source,
        source_ref=source_ref,
        kind=kind,
        goal=goal,
        source_grants=source_grants,
        requested_grants=requested_grants,
        record=record,
    )
    if not handoff.get("ok"):
        raise CreateAppProblem(str(handoff.get("code") or "handoff_incomplete"))
    if kind == "research":
        return research_turn(handoff, transport=transport)
    from dms_executor.create_surface import create_enabled, start_session

    if not create_enabled():
        return {**handoff, "started": False, "code": "create_surface_disabled"}
    stored = record.get("context") if isinstance(record, dict) else None
    try:
        session = start_session(
            space_id=space_id,
            kind=kind,
            goal=handoff["goal"] + "\ncontext_ref=" + handoff["context_ref"],
            audience=audience or "operator",
            api_key=api_key,
            actor=actor,
            tenant_id=tenant_id,
            inputs=[],
            context=stored if isinstance(stored, dict) and stored else None,
        )
    except Exception as exc:  # noqa: BLE001 - CreateProblem becomes a harness code
        _context_problem(exc)
    return {**handoff, "started": True, "session_id": session["session_id"]}


def contract_body() -> dict[str, Any]:
    return contract_document()
