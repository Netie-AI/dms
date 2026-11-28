"""FDE-BUDGET-01: OpenVault cost and quota preflight.

Read-only. The kit sends GET and reads the route policy file. It does not
call the ship budget write, and it does not edit route policy.

``DMS_FDE_KIT`` must be the digit ``1``. Any other value, including unset,
``true``, ``01``, and `` 1 ``, exits 2 with ``fde_kit_disabled`` and writes
nothing.

Report JSON is ``netie.fde.report/1`` (field kit plan section 4.3). When
``scripts/fde/report.py`` grows a ``render_markdown`` of that same shape, a
one-line swap can share it. Until then the helper below is the contract.

Nothing here defaults to PASS. A route that is absent is SKIPPED. A role
with no hop is a critical FAIL and the evidence names the role. A key used
by another identity in the last hour is a high FAIL. Reports carry masked
ids only. Model names are dropped at the policy parse and never written.

Exit codes: 0 required checks PASS, 1 FAIL or a required check SKIPPED,
2 config or the flag, 3 BLOCKED.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
_CORE = ROOT / "packages" / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

SCHEMA = "netie.fde.report/1"
KIT = "budget"
STAGE = "K7"
FLAG = "DMS_FDE_KIT"
HOUR_S = 3600
USAGE_LIMIT = 1000
DEFAULT_ROLES = ("sql", "ontology")
REQUIRED = ("K7.00", "K7.01", "K7.02", "K7.03")
CHECK_STATUSES = ("PASS", "FAIL", "BLOCKED", "SKIPPED", "MANUAL")
RUN_STATUSES = ("PASS", "FAIL", "BLOCKED", "SKIPPED")
SEVERITIES = ("critical", "high", "medium", "low", "info")
_ROLE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,32}\Z")
_SECRET_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "authorization",
        "bearer",
    }
)
PATH_ROUTE_POLICY = "/api/freeroute/route-policy"
PATH_USAGE = "/api/usage"
PATH_QUOTA = "/api/keys/quota"
PATH_SHIP_BUDGET = "/api/ship/budget"
PATH_SEAT = "/api/system/tenant/policy"
ALLOWED = (
    PATH_ROUTE_POLICY,
    PATH_USAGE,
    PATH_QUOTA,
    PATH_SHIP_BUDGET,
    PATH_SEAT,
)
SOURCE_POLICY = "OpenMW/openmw/openvault/vault/route_policy.py"
SOURCE_USAGE = "OpenMW/openmw/openvault/vault/usage_store.py"
SOURCE_QUOTA = "OpenMW/openmw/openvault/routers/key_quota.py"
SOURCE_SEAT = "OpenMW/openmw/openvault/routers/seat_plane.py"
SOURCE_KIT = "scripts/fde/kits/budget.py"

Transport = Callable[[str, Mapping[str, str]], tuple[int, Any]]
Clock = Callable[[], float]


class RefusedPath(RuntimeError):
    """A path outside the GET allowlist. The request is not sent."""


def kit_enabled(environ: Mapping[str, str]) -> bool:
    """True only when the flag is the digit 1. Spaces and other words are off."""
    return environ.get(FLAG) == "1"


def mask_id(value: str) -> str:
    """Public form of a key id. Short ids are stars. The secret is never an id."""
    text = value.strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def rollup(checks: list[dict[str, Any]]) -> str:
    """Critical or high FAIL wins, then BLOCKED, then any other FAIL.

    An empty list is SKIPPED. A required check that did not run keeps the
    run SKIPPED. Optional skips do not.
    """
    if not checks:
        return "SKIPPED"
    if any(c.get("status") == "FAIL" and c.get("severity") in ("critical", "high") for c in checks):
        return "FAIL"
    if any(c.get("status") == "BLOCKED" for c in checks):
        return "BLOCKED"
    if any(c.get("status") == "FAIL" for c in checks):
        return "FAIL"
    required = [c for c in checks if c.get("id") in REQUIRED]
    if any(c.get("status") == "SKIPPED" for c in required):
        return "SKIPPED"
    if required and all(c.get("status") == "PASS" for c in required):
        return "PASS"
    return "SKIPPED"


def render_markdown(report: Mapping[str, Any]) -> str:
    """Human report. The only input is the JSON object."""
    checks = [c for c in report.get("checks") or [] if isinstance(c, dict)]
    failed = [c for c in checks if c.get("status") == "FAIL"]
    rest = [c for c in checks if c.get("status") != "FAIL"]
    lines = [
        f"# {report.get('kit')} {report.get('stage')}",
        "",
        f"Engagement: {_cell(report.get('engagement'))}",
        f"Status: {_cell(report.get('status'))}",
        f"DMS: {_cell(report.get('dms_sha'))}",
        "",
        "| id | severity | status | title |",
        "| --- | --- | --- | --- |",
    ]
    for row in failed + rest:
        lines.append(
            f"| {_cell(row.get('id'))} | {_cell(row.get('severity'))} | "
            f"{_cell(row.get('status'))} | {_cell(row.get('title'))} |"
        )
    if failed:
        lines.extend(["", "## Failed", ""])
        for row in failed:
            lines.append(f"### {_cell(row.get('id'))} {_cell(row.get('title'))}")
            lines.append("")
            lines.append(_cell(row.get("fix"), limit=500))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _cell(value: object, limit: int = 120) -> str:
    text = str(value if value is not None else "").replace("\n", " ").replace("|", "/")
    return text[:limit]


def _scrub(text: str) -> str:
    try:
        from dms_core.control_plane.ask_audit_scrub import scrub
    except Exception:
        return text
    cleaned = scrub(text)
    return cleaned if isinstance(cleaned, str) else text


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def dms_sha() -> str:
    try:
        raw = subprocess.check_output(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    text = raw.strip()
    if len(text) == 8 and all(c in "0123456789abcdef" for c in text):
        return text
    return "unknown"


def _check(
    check_id: str,
    title: str,
    severity: str,
    status: str,
    evidence: dict[str, Any],
    fix: str,
    source: str,
) -> dict[str, Any]:
    if status not in CHECK_STATUSES:
        status = "SKIPPED"
    if severity not in SEVERITIES:
        severity = "info"
    return {
        "id": check_id,
        "title": title,
        "severity": severity,
        "status": status,
        "evidence": evidence,
        "fix": fix if status in ("FAIL", "BLOCKED", "MANUAL") else "",
        "source": source,
    }


def _document(
    *,
    engagement: str,
    tenant: str,
    started: float,
    finished: float,
    checks: list[dict[str, Any]],
    metrics: dict[str, Any],
    artifacts: list[str],
    cost: dict[str, Any],
) -> dict[str, Any]:
    status = rollup(checks)
    return {
        "schema": SCHEMA,
        "kit": KIT,
        "stage": STAGE,
        "engagement": engagement,
        "tenant_id": tenant,
        "dms_sha": dms_sha(),
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "flags": {FLAG: "1"},
        "status": status,
        "checks": checks,
        "metrics": metrics,
        "artifacts": artifacts,
        "cost": cost,
    }


def _secret_key(name: object) -> bool:
    return str(name).strip().lower() in _SECRET_KEYS


def _walk_secret(value: object) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if _secret_key(key) and item not in (None, "", [], {}):
                return "password_in_engagement"
            found = _walk_secret(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _walk_secret(item)
            if found:
                return found
    return ""


def load_engagement(directory: Path) -> tuple[dict[str, Any], str]:
    """The engagement file, or a config code. A password value refuses the run."""
    path = directory / "engagement.yaml"
    if not path.is_file():
        return {}, "engagement_yaml_missing"
    try:
        import yaml
    except ImportError:
        return {}, "pyyaml_missing"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}, "engagement_yaml_shape"
    if not isinstance(data, dict):
        return {}, "engagement_yaml_shape"
    secret = _walk_secret(data)
    if secret:
        return {}, secret
    return data, ""


def _section(data: Mapping[str, Any], name: str) -> dict[str, Any]:
    raw = data.get(name)
    return dict(raw) if isinstance(raw, dict) else {}


def _name(data: Mapping[str, Any], directory: Path) -> str:
    for key in ("engagement", "tenant"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return directory.name


def _tenant(data: Mapping[str, Any], name: str) -> str:
    value = data.get("tenant_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return name


def required_roles(data: Mapping[str, Any]) -> list[str]:
    """Roles the sheet names. Absent means sql and ontology. Create roles append."""
    budget = _section(data, "budget")
    if "budget_roles" in data:
        explicit = data.get("budget_roles")
    elif "roles" in budget:
        explicit = budget.get("roles")
    else:
        explicit = None
    create = data.get("create_roles") if "create_roles" in data else budget.get("create_roles")
    chosen: list[object] = []
    if explicit is None:
        chosen.extend(DEFAULT_ROLES)
    elif isinstance(explicit, list):
        chosen.extend(explicit)
    if isinstance(create, list):
        chosen.extend(create)
    out: list[str] = []
    seen: set[str] = set()
    for item in chosen:
        token = str(item).strip()
        if token and token not in seen and _ROLE.fullmatch(token):
            seen.add(token)
            out.append(token)
    return out


def _id_field(data: Mapping[str, Any], *keys: str) -> str:
    budget = _section(data, "budget")
    section = _section(data, "openvault")
    for key in keys:
        for source in (section, budget, data):
            value = source.get(key)
            if isinstance(value, str) and value.strip() and " " not in value.strip():
                return value.strip()
    return ""


def _questions_per_day(data: Mapping[str, Any]) -> tuple[int | None, str]:
    budget = _section(data, "budget")
    if "questions_per_day" in data:
        raw = data.get("questions_per_day")
        present = True
    elif "questions_per_day" in budget:
        raw = budget.get("questions_per_day")
        present = True
    else:
        raw = None
        present = False
    if not present:
        return None, "unset"
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None, "invalid"
    return raw, "ok"


def _policy_path(data: Mapping[str, Any], environ: Mapping[str, str]) -> Path | None:
    section = _section(data, "openvault")
    raw = section.get("policy_file") or environ.get("OPENVAULT_ROUTE_POLICY") or ""
    text = str(raw).strip()
    if not text:
        return None
    return Path(text)


def _base_url(data: Mapping[str, Any], environ: Mapping[str, str]) -> str:
    section = _section(data, "openvault")
    env_name = section.get("url_env") or "OPENVAULT_URL"
    if not isinstance(env_name, str) or not env_name.isidentifier():
        env_name = "OPENVAULT_URL"
    raw = str(environ.get(env_name) or "").strip().rstrip("/")
    return raw or "http://127.0.0.1:5000"


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def policy_view(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Hop counts and USD caps. Provider and model strings are not kept."""
    roles: dict[str, int] = {}
    incoming = raw.get("roles")
    if isinstance(incoming, dict):
        for name, hops in incoming.items():
            token = str(name).strip()
            if not _ROLE.fullmatch(token):
                continue
            if isinstance(hops, list):
                roles[token] = len(hops)
            elif isinstance(hops, dict):
                count = hops.get("hops")
                if isinstance(count, bool):
                    roles[token] = 0
                elif isinstance(count, int):
                    roles[token] = count
                elif isinstance(count, list):
                    roles[token] = len(count)
                else:
                    roles[token] = 0
            else:
                roles[token] = 0
    caps = raw.get("caps") if isinstance(raw.get("caps"), dict) else {}
    return {
        "roles": roles,
        "key_caps": _cap_map(caps.get("keys") if isinstance(caps, dict) else None),
        "caller_caps": _cap_map(caps.get("callers") if isinstance(caps, dict) else None),
    }


def _cap_map(raw: object) -> dict[str, dict[str, float | None]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, float | None]] = {}
    for ident, entry in raw.items():
        if not isinstance(ident, str) or not isinstance(entry, dict):
            continue
        out[ident] = {
            "daily_usd": _number(entry.get("daily_usd")),
            "monthly_usd": _number(entry.get("monthly_usd")),
        }
    return out


def _cap_present(row: Mapping[str, Any] | None) -> bool:
    if not row:
        return False
    return row.get("daily_usd") is not None or row.get("monthly_usd") is not None


def _read_policy_file(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "route_policy_unreadable"
    if not isinstance(raw, dict):
        return None, "route_policy_unreadable"
    return policy_view(raw), ""


def live_transport(base: str) -> Transport:
    """GET the allowlist. Any other path raises before a socket opens."""
    import httpx

    root = base.rstrip("/")

    def send(path: str, params: Mapping[str, str]) -> tuple[int, Any]:
        if path not in ALLOWED:
            raise RefusedPath(path)
        try:
            response = httpx.get(root + path, params=dict(params) or None, timeout=5.0)
        except Exception as exc:
            return 0, {"error": type(exc).__name__}
        try:
            body = response.json()
        except Exception:
            body = None
        return int(response.status_code), body

    return send


def _normalize(status: int, body: Any) -> tuple[int, dict[str, Any]]:
    if status == 200 and isinstance(body, dict):
        return 200, body
    if status == 0:
        err = body.get("error") if isinstance(body, dict) else ""
        if not isinstance(err, str) or not err.isidentifier():
            err = "unreachable"
        return 0, {"error": err}
    return status, {}


def _ours(name: str, tenant: str, key_id: str, caller_id: str) -> set[str]:
    return {item for item in (name, tenant, key_id, caller_id) if item}


def _foreign(value: object, ours: set[str]) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or text in ours:
        return ""
    return text


def _show_identity(value: str) -> str:
    if len(value) > 16:
        return mask_id(value)
    return value


class _Fetch:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self.calls: list[tuple[str, str]] = []

    def get(self, path: str, params: Mapping[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        self.calls.append(("GET", path))
        status, body = self.transport_result(path, params or {})
        return _normalize(status, body)

    def transport_result(self, path: str, params: Mapping[str, str]) -> tuple[int, Any]:
        return self._transport(path, params)


def run(ctx: Ctx) -> dict[str, Any]:
    """One preflight. Flag off returns SKIPPED and does not call OpenVault."""
    started = ctx.clock()
    name = _name(ctx.engagement, ctx.engagement_dir)
    tenant = _tenant(ctx.engagement, name)
    if not kit_enabled(ctx.environ):
        report = _document(
            engagement=name,
            tenant=tenant,
            started=started,
            finished=ctx.clock(),
            checks=[],
            metrics={},
            artifacts=[],
            cost={"ov_calls": 0, "usd": 0.0, "source": "OV /api/usage"},
        )
        report["flags"] = {FLAG: ctx.environ.get(FLAG, "")}
        report["status"] = "SKIPPED"
        return report
    try:
        return _evaluate(ctx, started, name, tenant)
    except Exception as exc:
        failed = _check(
            "K7.00",
            "OpenVault preflight",
            "critical",
            "FAIL",
            {"exception": type(exc).__name__},
            "The preflight stopped. The exception class is the evidence. "
            "This kit does not change OpenVault.",
            SOURCE_KIT,
        )
        return _document(
            engagement=name,
            tenant=tenant,
            started=started,
            finished=ctx.clock(),
            checks=[failed],
            metrics={},
            artifacts=[],
            cost={"ov_calls": 0, "usd": 0.0, "source": "OV /api/usage"},
        )


def _evaluate(ctx: Ctx, started: float, name: str, tenant: str) -> dict[str, Any]:
    now = started
    fetch = _Fetch(ctx.transport)
    policy_status, policy_body = fetch.get(PATH_ROUTE_POLICY)
    usage_status, usage_body = fetch.get(
        PATH_USAGE, {"since": str(now - HOUR_S), "limit": str(USAGE_LIMIT)}
    )
    quota_status, quota_body = fetch.get(PATH_QUOTA)
    ship_status, ship_body = fetch.get(PATH_SHIP_BUDGET)
    seat_status, seat_body = fetch.get(PATH_SEAT)

    view, policy_reason = _policy_from(policy_status, policy_body, ctx)
    answers = (policy_status, usage_status, quota_status, ship_status, seat_status)
    checks = [
        _reachable(answers),
        _roles(ctx.engagement, view, policy_reason),
        _cap(ctx, view, policy_reason, quota_status, quota_body),
        _shared(ctx, name, tenant, usage_status, usage_body, now),
        _projection(ctx, view),
        _seat(seat_status, seat_body),
    ]
    metrics: dict[str, Any] = {
        "http": {
            "route_policy": policy_status,
            "usage": usage_status,
            "quota": quota_status,
            "ship_budget": ship_status,
            "seat": seat_status,
        },
        "get_count": len(fetch.calls),
    }
    if view is not None:
        metrics["role_hops"] = dict(view["roles"])
    key_id = _id_field(ctx.engagement, "key_id")
    if key_id:
        metrics["masked_key"] = mask_id(key_id)
    ship_cap = _ship_cap(ship_status, ship_body)
    if ship_cap is not None:
        metrics["ship_monthly_cap_usd"] = ship_cap
    usd, usd_state, events = _usage_usd(usage_status, usage_body, key_id, now)
    metrics["usage_usd"] = usd_state
    metrics["usage_events"] = events
    cost = {"ov_calls": len(fetch.calls), "usd": usd, "source": "OV /api/usage"}
    return _document(
        engagement=name,
        tenant=tenant,
        started=started,
        finished=ctx.clock(),
        checks=checks,
        metrics=metrics,
        artifacts=[],
        cost=cost,
    )


def _policy_from(
    status: int, body: Mapping[str, Any], ctx: Ctx
) -> tuple[dict[str, Any] | None, str]:
    if status == 200 and isinstance(body.get("roles"), dict):
        return policy_view(body), ""
    path = _policy_path(ctx.engagement, ctx.environ)
    if path is None:
        if status == 0:
            return None, "openvault_unreachable"
        return None, "route_policy_unread"
    return _read_policy_file(path)


def _reachable(answers: tuple[int, ...]) -> dict[str, Any]:
    if any(code != 0 for code in answers):
        return _check(
            "K7.00",
            "OpenVault answers read-only GETs",
            "critical",
            "PASS",
            {"answered": True},
            "",
            SOURCE_KIT,
        )
    return _check(
        "K7.00",
        "OpenVault answers read-only GETs",
        "critical",
        "FAIL",
        {"answered": False},
        "Start OpenVault so this host can open it, then rerun. "
        "The kit does not change the route policy or the ship budget.",
        SOURCE_KIT,
    )


def _roles(
    data: Mapping[str, Any], view: dict[str, Any] | None, reason: str
) -> dict[str, Any]:
    title = "Route roles resolve"
    wanted = required_roles(data)
    if not wanted:
        return _check(
            "K7.01",
            title,
            "critical",
            "SKIPPED",
            {"reason": "no_roles_configured"},
            "",
            SOURCE_POLICY,
        )
    if view is None:
        return _check(
            "K7.01",
            title,
            "critical",
            "SKIPPED",
            {"reason": reason or "route_policy_unread", "roles": wanted},
            "",
            SOURCE_POLICY,
        )
    hops: dict[str, int] = view["roles"]
    missing = [role for role in wanted if hops.get(role, 0) <= 0]
    if missing:
        named = ", ".join(missing)
        return _check(
            "K7.01",
            title,
            "critical",
            "FAIL",
            {"missing": missing, "hops": {role: hops.get(role, 0) for role in wanted}},
            f"The route policy has no hop for {named}. Add the hop in OpenVault. "
            "This kit does not edit the policy file.",
            SOURCE_POLICY,
        )
    return _check(
        "K7.01",
        title,
        "critical",
        "PASS",
        {"hops": {role: hops.get(role, 0) for role in wanted}},
        "",
        SOURCE_POLICY,
    )


def _cap(
    ctx: Ctx,
    view: dict[str, Any] | None,
    reason: str,
    quota_status: int,
    quota_body: Mapping[str, Any],
) -> dict[str, Any]:
    title = "Tenant key has a USD cap"
    key_id = _id_field(ctx.engagement, "key_id")
    caller_id = _id_field(ctx.engagement, "caller_id")
    evidence: dict[str, Any] = {}
    if key_id:
        evidence["masked_key"] = mask_id(key_id)
    if caller_id:
        evidence["masked_caller"] = mask_id(caller_id)
    quota = _quota_row(quota_status, quota_body, key_id)
    if quota:
        evidence.update(quota)
    if not key_id and not caller_id:
        return _check(
            "K7.02",
            title,
            "critical",
            "FAIL",
            {"reason": "no_key_id"},
            "Set openvault.key_id to the vault key id. Do not put the key secret in the file.",
            SOURCE_POLICY,
        )
    if view is None:
        evidence["reason"] = reason or "cap_unread"
        return _check("K7.02", title, "critical", "SKIPPED", evidence, "", SOURCE_POLICY)
    key_row = view["key_caps"].get(key_id) if key_id else None
    caller_row = view["caller_caps"].get(caller_id) if caller_id else None
    if _cap_present(key_row):
        evidence["scope"] = "key"
        evidence["daily_usd"] = key_row["daily_usd"] if key_row else None
        evidence["monthly_usd"] = key_row["monthly_usd"] if key_row else None
        return _check("K7.02", title, "critical", "PASS", evidence, "", SOURCE_POLICY)
    if _cap_present(caller_row):
        evidence["scope"] = "caller"
        evidence["daily_usd"] = caller_row["daily_usd"] if caller_row else None
        evidence["monthly_usd"] = caller_row["monthly_usd"] if caller_row else None
        return _check("K7.02", title, "critical", "PASS", evidence, "", SOURCE_POLICY)
    evidence["reason"] = "no_usd_cap"
    return _check(
        "K7.02",
        title,
        "critical",
        "FAIL",
        evidence,
        "Set a daily or monthly USD cap for this key, or for its caller, in the route policy. "
        "A missing cap is not an unlimited pool. This kit does not write the ship budget.",
        SOURCE_QUOTA,
    )


def _quota_row(status: int, body: Mapping[str, Any], key_id: str) -> dict[str, Any]:
    if not key_id or status != 200:
        return {}
    rows = body.get("keys")
    if not isinstance(rows, list):
        return {}
    masked = mask_id(key_id)
    matched = [row for row in rows if isinstance(row, dict) and row.get("masked_id") == masked]
    if len(matched) != 1:
        return {"quota": "unread" if not matched else "mask_ambiguous"}
    row = matched[0]
    out: dict[str, Any] = {"quota": "read"}
    limit = row.get("daily_limit")
    if isinstance(limit, int) and not isinstance(limit, bool):
        out["token_daily_limit"] = limit
    elif limit is None:
        out["token_daily_limit"] = None
    if isinstance(row.get("is_free_tier"), bool):
        out["free_tier"] = row["is_free_tier"]
    return out


def _shared(
    ctx: Ctx,
    name: str,
    tenant: str,
    status: int,
    body: Mapping[str, Any],
    now: float,
) -> dict[str, Any]:
    title = "Tenant key is not shared"
    key_id = _id_field(ctx.engagement, "key_id")
    if not key_id:
        return _check(
            "K7.03",
            title,
            "high",
            "SKIPPED",
            {"reason": "no_key_id"},
            "",
            SOURCE_USAGE,
        )
    if status == 0:
        return _check(
            "K7.03",
            title,
            "high",
            "SKIPPED",
            {"reason": "openvault_unreachable", "masked_key": mask_id(key_id)},
            "",
            SOURCE_USAGE,
        )
    if status != 200:
        return _check(
            "K7.03",
            title,
            "high",
            "SKIPPED",
            {"reason": f"usage_http_{status}", "masked_key": mask_id(key_id)},
            "",
            SOURCE_USAGE,
        )
    events = body.get("events")
    if not isinstance(events, list):
        return _check(
            "K7.03",
            title,
            "high",
            "SKIPPED",
            {"reason": "usage_ledger_shape", "masked_key": mask_id(key_id)},
            "",
            SOURCE_USAGE,
        )
    caller_id = _id_field(ctx.engagement, "caller_id")
    ours = _ours(name, tenant, key_id, caller_id)
    foreign: list[str] = []
    matched = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("api_key_id") != key_id and event.get("vault_key_id") != key_id:
            continue
        created = _number(event.get("created_at"))
        if created is None or created < now - HOUR_S:
            continue
        matched += 1
        for field in ("identity", "service_id", "run_label"):
            other = _foreign(event.get(field), ours)
            if other:
                foreign.append(other)
    # ponytail: one page of 1000 events. The usage route has no cursor. A full
    # page that shows no other identity is SKIPPED, not a pass, until it does.
    truncated = len(events) >= USAGE_LIMIT
    evidence = {
        "masked_key": mask_id(key_id),
        "events": matched,
        "foreign": len({item for item in foreign}),
    }
    if foreign:
        shown = sorted({_show_identity(item) for item in foreign})
        evidence["identities"] = shown[:8]
        return _check(
            "K7.03",
            title,
            "high",
            "FAIL",
            evidence,
            "This key has usage from another identity in the last hour. "
            "Give the tenant its own key before an accuracy run. This kit does not rotate keys.",
            SOURCE_USAGE,
        )
    if truncated:
        evidence["reason"] = "usage_window_truncated"
        return _check("K7.03", title, "high", "SKIPPED", evidence, "", SOURCE_USAGE)
    return _check("K7.03", title, "high", "PASS", evidence, "", SOURCE_USAGE)


def _projection(ctx: Ctx, view: dict[str, Any] | None) -> dict[str, Any]:
    title = "Projected cost from the accuracy report"
    report = _latest_accuracy(ctx.engagement_dir)
    if report is None:
        return _check(
            "K7.04",
            title,
            "info",
            "SKIPPED",
            {"reason": "no_accuracy_report"},
            "",
            SOURCE_USAGE,
        )
    cost = report.get("cost") if isinstance(report.get("cost"), dict) else {}
    usd = _number(cost.get("usd"))
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    questions = _question_count(metrics)
    if usd is None or questions is None or questions <= 0:
        return _check(
            "K7.04",
            title,
            "info",
            "SKIPPED",
            {"reason": "accuracy_cost_unread"},
            "",
            SOURCE_USAGE,
        )
    per_question = round(usd / questions, 6)
    per_day_n, day_state = _questions_per_day(ctx.engagement)
    evidence: dict[str, Any] = {
        "per_question_usd": per_question,
        "questions": questions,
        "measured_usd": usd,
    }
    if day_state != "ok" or per_day_n is None:
        evidence["reason"] = (
            "questions_per_day_unset" if day_state == "unset" else "questions_per_day_invalid"
        )
        return _check("K7.04", title, "info", "SKIPPED", evidence, "", SOURCE_USAGE)
    per_day = round(per_question * per_day_n, 6)
    evidence["per_day_usd"] = per_day
    evidence["questions_per_day"] = per_day_n
    key_id = _id_field(ctx.engagement, "key_id")
    caller_id = _id_field(ctx.engagement, "caller_id")
    row = None
    if view is not None and key_id:
        row = view["key_caps"].get(key_id)
    if not _cap_present(row) and view is not None and caller_id:
        row = view["caller_caps"].get(caller_id)
    over = _over_cap(per_day, row)
    if over:
        evidence["cap"] = over
        return _check(
            "K7.04",
            title,
            "high",
            "FAIL",
            evidence,
            "The projected daily cost is above the USD cap. Lower the question volume "
            "or ask the founder to raise the cap. This kit does not change the cap.",
            SOURCE_POLICY,
        )
    return _check("K7.04", title, "info", "PASS", evidence, "", SOURCE_USAGE)


def _question_count(metrics: Mapping[str, Any]) -> int | None:
    for key in ("questions", "n_questions", "graded", "n"):
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        return value
    return None


def _over_cap(per_day: float, row: Mapping[str, Any] | None) -> dict[str, float] | None:
    if not row:
        return None
    daily = row.get("daily_usd")
    monthly = row.get("monthly_usd")
    if isinstance(daily, (int, float)) and not isinstance(daily, bool) and per_day > float(daily):
        return {"daily_usd": float(daily), "projected_usd": per_day}
    if (
        isinstance(monthly, (int, float))
        and not isinstance(monthly, bool)
        and per_day * 30 > float(monthly)
    ):
        return {"monthly_usd": float(monthly), "projected_month_usd": round(per_day * 30, 6)}
    return None


def _latest_accuracy(directory: Path) -> dict[str, Any] | None:
    runs = directory / "runs"
    if not runs.is_dir():
        return None
    found: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(runs.glob("*/report.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("kit") == "accuracy" or data.get("stage") == "K5":
            found.append((str(data.get("finished_at") or path.parent.name), data))
    if not found:
        return None
    found.sort(key=lambda item: item[0])
    return found[-1][1]


def _seat(status: int, body: Mapping[str, Any]) -> dict[str, Any]:
    title = "Seat plane"
    if status == 404 or status == 0:
        reason = "seat_plane_absent" if status == 404 else "openvault_unreachable"
        return _check(
            "K7.05",
            title,
            "info",
            "SKIPPED",
            {"reason": reason},
            "",
            SOURCE_SEAT,
        )
    if status != 200:
        return _check(
            "K7.05",
            title,
            "info",
            "SKIPPED",
            {"reason": f"seat_http_{status}"},
            "",
            SOURCE_SEAT,
        )
    error = body.get("policy_error")
    if error:
        return _check(
            "K7.05",
            title,
            "high",
            "FAIL",
            {"policy_error": "present"},
            "The seat policy did not parse. Fix that file on the OpenVault host. "
            "This kit does not write it.",
            SOURCE_SEAT,
        )
    using = body.get("using")
    safe = isinstance(using, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", using)
    shown = using if safe else ""
    return _check(
        "K7.05",
        title,
        "info",
        "PASS",
        {"policy_error": "absent", "using": shown or "unreported"},
        "",
        SOURCE_SEAT,
    )


def _ship_cap(status: int, body: Mapping[str, Any]) -> float | None:
    if status != 200:
        return None
    return _number(body.get("monthly_cap_usd"))


def _usage_usd(
    status: int, body: Mapping[str, Any], key_id: str, now: float
) -> tuple[float, str, int]:
    if status != 200:
        return 0.0, "unread", 0
    events = body.get("events")
    if not isinstance(events, list):
        return 0.0, "unread", 0
    total = 0.0
    measured = False
    count = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        created = _number(event.get("created_at"))
        if created is None or created < now - HOUR_S:
            continue
        if key_id and event.get("api_key_id") != key_id and event.get("vault_key_id") != key_id:
            continue
        count += 1
        usd = _number(event.get("est_cost_usd"))
        if usd is not None:
            measured = True
            total += usd
    if not measured:
        return 0.0, "not_on_ledger", count
    return round(total, 6), "measured", count


def write_report(directory: Path, report: dict[str, Any], now: float) -> Path:
    """Write JSON, then markdown rendered from that JSON."""
    stamp = datetime.fromtimestamp(now, UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = directory / "runs" / f"{stamp}-budget"
    folder.mkdir(parents=True, exist_ok=True)
    body = dict(report)
    rel_json = f"runs/{stamp}-budget/report.json"
    rel_md = f"runs/{stamp}-budget/report.md"
    body["artifacts"] = [rel_json, rel_md]
    raw = json.dumps(body, indent=2) + "\n"
    text = _scrub(raw)
    try:
        loaded = json.loads(text)
    except ValueError:
        text = raw
        loaded = json.loads(text)
    markdown = _scrub(render_markdown(loaded))
    (folder / "report.json").write_text(
        text if text.endswith("\n") else text + "\n", encoding="utf-8"
    )
    (folder / "report.md").write_text(markdown, encoding="utf-8")
    return folder


class Ctx:
    """What ``run`` needs. ``transport`` is GET-shaped: path and query only."""

    def __init__(
        self,
        engagement_dir: Path,
        engagement: Mapping[str, Any],
        transport: Transport,
        environ: Mapping[str, str],
        clock: Clock = time.time,
    ) -> None:
        self.engagement_dir = engagement_dir
        self.engagement = engagement
        self.transport = transport
        self.environ = environ
        self.clock = clock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fde-budget")
    parser.add_argument("--engagement", required=True)
    args = parser.parse_args(argv)
    directory = Path(args.engagement)
    if not kit_enabled(os.environ):
        print("fde_kit_disabled", file=sys.stderr)
        return 2
    if not directory.is_dir():
        print("engagement_dir_missing", file=sys.stderr)
        return 2
    data, error = load_engagement(directory)
    if error:
        print(error, file=sys.stderr)
        return 2
    now = time.time()
    ctx = Ctx(
        engagement_dir=directory,
        engagement=data,
        transport=live_transport(_base_url(data, os.environ)),
        environ=os.environ,
        clock=lambda: now,
    )
    report = run(ctx)
    write_report(directory, report, now)
    status = str(report.get("status") or "SKIPPED")
    print(status)
    if status == "PASS":
        return 0
    if status == "BLOCKED":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
