"""Ask a provider for a ``dms.app/1`` bundle, judge it, and repair.

There is no per-run dollar stop. Live cost is the sum of provider
``est_cost_usd`` values. OpenVault 402 stops the run as ``budget_exceeded``.
The OpenVault role on this path is ``create`` unless
``DMS_CREATE_APP_OV_ROLE`` sets another role string. DMS does not name a model.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

from dms_core.create_app.contract import (
    CONTRACT,
    compact_map,
    materialize,
    validate_bundle,
)
from dms_core.create_app.flags import enabled
from dms_core.create_app.sandbox import judge_backend

MAX_REPAIRS = 2
# Same directory as Create sessions. App bundles live in ``apps/`` under it.
# Swap: the object-store port, when a publish target is not local disk.
STORE_ENV = "DMS_CREATE_STORE"

_SYSTEM = (
    "Return one JSON object and nothing else. "
    "The object is a runnable app bundle with contract dms.app/1. "
    "Include frontend files and backend files. "
    "The backend imports dms_data and calls dms_data.query and dms_data.dry_run. "
    "server.py prints DMS_APP_READY after those calls. "
    "SQL belongs in queries and aggregates. "
    "Do not include a rows field, a credential, or a connection string. "
    "Connector ontology is a slot only."
)


class CreateAppDisabled(RuntimeError):
    """``DMS_CREATE_APP`` is not the digit 1."""


class CreateAppProblem(ValueError):
    """A harness refusal. ``code`` is safe to show."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def store_dir() -> Path:
    raw = os.environ.get(STORE_ENV, "").strip()
    if not raw:
        raise CreateAppProblem("store_missing")
    path = Path(raw) / "apps"
    path.mkdir(parents=True, exist_ok=True)
    return path


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_matches(presented: str, stored_hash: str) -> bool:
    digest = token_hash(presented)
    if len(digest) != len(stored_hash):
        return False
    return hmac.compare_digest(digest, stored_hash)


def resolve_grants(requested: list[str] | None, ceiling: set[str]) -> set[str]:
    """Grants in force are the ceiling, narrowed by a request. Never widened."""
    if not ceiling:
        raise CreateAppProblem("grants_required")
    if not requested:
        return set(ceiling)
    bound = {name for name in requested if name in ceiling}
    if not bound:
        raise CreateAppProblem("grants_required")
    return bound


def _role() -> str:
    raw = os.environ.get("DMS_CREATE_APP_OV_ROLE", "create").strip()
    return raw or "create"


def _prompt(
    goal: str,
    schema_map: dict[str, Any],
    repair: dict[str, Any] | None,
    sources: dict[str, Any] | None = None,
    layout: list[dict[str, Any]] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "contract": CONTRACT,
        "goal": goal,
        "schema_map": schema_map,
    }
    if sources:
        payload["sources"] = sources
    if layout:
        payload["layout_blocks"] = layout
    if repair:
        payload["repair"] = repair
    return json.dumps(payload)


def _provider_call(
    *,
    goal: str,
    schema_map: dict[str, Any],
    api_key: str,
    repair: dict[str, Any] | None,
    agent_id: str | None,
    transport: Any,
    cursor_transport: Any,
    sources: dict[str, Any] | None = None,
    layout: list[dict[str, Any]] | None = None,
) -> Any:
    from dms_core import create_generator

    prompt = _SYSTEM + "\n" + _prompt(goal, schema_map, repair, sources, layout)
    if api_key and api_key in prompt:
        raise CreateAppProblem("key_in_prompt")
    cursor_key = os.environ.get("CURSOR_API_KEY", "")
    if cursor_key and cursor_key in prompt:
        raise CreateAppProblem("key_in_prompt")
    provider = create_generator.selected_provider()
    if provider == "cursor_api":
        body = {"prompt": {"text": prompt}, "autoCreatePR": False}
        if agent_id:
            body = {"prompt": {"text": prompt}, "agent_id": agent_id}
        else:
            repo = os.environ.get("DMS_CREATE_CURSOR_REPO", "").strip()
            ref = os.environ.get("DMS_CREATE_CURSOR_REF", "main").strip() or "main"
            if repo:
                body["repos"] = [{"url": repo, "startingRef": ref}]
        if cursor_transport is None:
            if "repos" not in body and not agent_id:
                raise CreateAppProblem("cursor_repo_missing")
            return create_generator.default_cursor(body, create_generator._cursor_key())
        return cursor_transport(body, "stub")
    body = {
        "role": _role(),
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _prompt(goal, schema_map, repair, sources, layout)},
        ],
    }
    send = transport or create_generator.default_transport
    return send(body, api_key)


def _artifact(raw: Any) -> dict[str, Any] | None:
    from dms_core.create_generator import _parse

    artifact, _calls, _usage = _parse(raw)
    if isinstance(artifact, dict) and artifact.get("contract") == CONTRACT:
        return artifact
    if isinstance(raw, dict):
        content = raw.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                content = None
        if isinstance(content, dict) and content.get("contract") == CONTRACT:
            return content
    return artifact if isinstance(artifact, dict) else None


def _usage(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict) and isinstance(raw.get("usage"), dict):
        return raw["usage"]
    return {}


def _add_spend(spend: dict[str, Any], raw: Any) -> None:
    from dms_core.create_generator import _cost, _int

    cost = _cost(raw, _usage(raw))
    tokens = _int(_usage(raw).get("total_tokens"))
    if isinstance(cost, float):
        spend["usd"] = round(float(spend["usd"]) + cost, 6)
    if isinstance(tokens, int):
        spend["tokens"] = int(spend["tokens"]) + tokens
    spend["calls"] = int(spend["calls"]) + 1


def _agent_id(raw: Any) -> str | None:
    from dms_core.create_generator import _cursor_target

    target = _cursor_target(raw)
    if not isinstance(target, dict):
        return None
    ident = target.get("id")
    return ident if isinstance(ident, str) and ident else None


def _actions(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    raw = bundle.get("actions")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _queries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    raw = bundle.get("queries")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def run_app(
    *,
    goal: str,
    catalog: list[Any],
    grants: set[str],
    space_id: str,
    actor: str,
    api_key: str,
    links: list[Any] | None = None,
    transport: Any = None,
    cursor_transport: Any = None,
    query_fn: Any = None,
    ov_url: str | None = None,
    sources: dict[str, Any] | None = None,
    layout: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One create. The provider writes the bundle. DMS judges and may repair."""
    if not enabled():
        raise CreateAppDisabled(os.environ.get("DMS_CREATE_APP", ""))
    if not goal.strip():
        raise CreateAppProblem("goal_required")
    schema_map = compact_map(catalog, grants, links)
    spend: dict[str, Any] = {"usd": 0.0, "tokens": 0, "calls": 0}
    repair: dict[str, Any] | None = None
    bundle: dict[str, Any] | None = None
    judge: dict[str, Any] = {"ok": False, "errors": ["not_judged"]}
    agent_id: str | None = None
    app_id = secrets.token_hex(16)
    root = store_dir() / app_id
    src = root / "src"
    audit: list[dict[str, Any]] = []
    token = secrets.token_urlsafe(32)
    last_code = "provider_failed"

    def ask() -> Any:
        from dms_core.create_generator import GeneratorError

        try:
            return _provider_call(
                goal=goal,
                schema_map=schema_map,
                api_key=api_key,
                repair=repair,
                agent_id=agent_id,
                transport=transport,
                cursor_transport=cursor_transport,
                sources=sources,
                layout=layout,
            )
        except GeneratorError as exc:
            raise CreateAppProblem(exc.code) from None

    try:
        for _attempt in range(MAX_REPAIRS + 1):
            try:
                raw = ask()
            except CreateAppProblem as exc:
                last_code = exc.code
                break
            _add_spend(spend, raw)
            agent_id = _agent_id(raw) or agent_id
            artifact = _artifact(raw)
            errors = validate_bundle(artifact)
            if errors or not isinstance(artifact, dict):
                repair = {"contract": errors or ["bundle_not_object"]}
                bundle = None
                last_code = (errors or ["bundle_not_object"])[0]
                continue
            bundle = artifact
            src.mkdir(parents=True, exist_ok=True)
            materialize(bundle, src)

            def _query(sql: str, bundle_grants: set[str] = grants) -> dict[str, Any]:
                if query_fn is None:
                    return {"ok": False, "code": "query_unbound", "rows": []}
                return query_fn(sql, bundle_grants)

            judge = judge_backend(
                src,
                token=token,
                grants=grants,
                actions=_actions(bundle),
                queries=_queries(bundle),
                query_fn=_query,
                audit=audit,
                ov_url=ov_url,
            )
            if judge["ok"]:
                last_code = "ok"
                break
            repair = {"judge": judge.get("errors") or ["judge_failed"]}
            last_code = str((judge.get("errors") or ["judge_failed"])[0])
    except CreateAppProblem as exc:
        last_code = exc.code

    record: dict[str, Any] = {
        "app_id": app_id,
        "space_id": space_id,
        "actor": actor,
        "goal": goal,
        "grants": sorted(grants),
        "context_ref": f"app:{app_id}",
        "context": context or {},
        "bundle": bundle,
        "schema_map": schema_map,
        "judge": judge,
        "spend": spend,
        "published": False,
        "token_hash": token_hash(token),
        "audit": audit,
        "ok": bool(bundle) and bool(judge.get("ok")),
        "code": "ok" if bundle and judge.get("ok") else last_code,
    }
    _persist(root, record)
    view = public_view(record)
    if record["ok"]:
        view["app_token"] = token
    return view


def _persist(root: Path, record: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.json").write_text(json.dumps(record), encoding="utf-8")


def load_app(app_id: str) -> dict[str, Any]:
    path = store_dir() / app_id / "app.json"
    if not path.is_file():
        raise CreateAppProblem("app_missing")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CreateAppProblem("app_missing") from exc
    if not isinstance(loaded, dict):
        raise CreateAppProblem("app_missing")
    return loaded


def save_app(record: dict[str, Any]) -> None:
    app_id = record.get("app_id")
    if not isinstance(app_id, str) or not app_id:
        raise CreateAppProblem("app_missing")
    _persist(store_dir() / app_id, record)


def public_view(record: dict[str, Any]) -> dict[str, Any]:
    bundle = record.get("bundle") if isinstance(record.get("bundle"), dict) else None
    return {
        "ok": bool(record.get("ok")),
        "code": record.get("code"),
        "app_id": record.get("app_id"),
        "space_id": record.get("space_id"),
        "context_ref": record.get("context_ref"),
        "name": bundle.get("name") if bundle else None,
        "grants": record.get("grants") or [],
        "judge": record.get("judge") or {},
        "spend": record.get("spend") or {"usd": 0.0, "tokens": 0, "calls": 0},
        "published": bool(record.get("published")),
        "audit": record.get("audit") or [],
        "actions": _actions(bundle) if bundle else [],
        "schema_map": record.get("schema_map") or {},
    }


def preview_html(app_id: str) -> str:
    path = store_dir() / app_id / "src" / "index.html"
    if not path.is_file():
        raise CreateAppProblem("preview_missing")
    return path.read_text(encoding="utf-8")
