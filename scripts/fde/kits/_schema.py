"""Report shape ``netie.fde.report/1`` (plan section 4.3).

If ``scripts/fde/report.py`` exists and publishes the same schema, that module
wins so a later T1 merge replaces this copy. The dict keys stay the 4.3 set.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kits._boot import boot

boot()

SCHEMA = "netie.fde.report/1"
CHECK_STATUS = frozenset({"PASS", "FAIL", "BLOCKED", "SKIPPED", "MANUAL"})
RUN_STATUS = frozenset({"PASS", "FAIL", "BLOCKED", "SKIPPED"})
SEVERITY = frozenset({"critical", "high", "medium", "low", "info"})
_SECRET_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "authorization",
        "api_key",
        "bearer",
        "passwd",
        "pwd",
    }
)


class KitDisabled(RuntimeError):
    """``DMS_FDE_KIT`` is not the digit 1. The caller writes nothing."""


def kit_enabled() -> bool:
    """On only for the exact digit 1. ``true``, ``01``, and `` 1 `` stay off."""
    return os.environ.get("DMS_FDE_KIT", "") == "1"


def product_enabled(name: str) -> bool:
    """Match the connect route: 1, true, yes, or on. Anything else is off."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Check:
    id: str
    title: str
    severity: str
    status: str
    evidence: dict[str, Any] = field(default_factory=dict)
    fix: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITY:
            raise ValueError(f"bad severity: {self.severity}")
        if self.status not in CHECK_STATUS:
            raise ValueError(f"bad status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "status": self.status,
            "evidence": self.evidence,
            "fix": self.fix,
            "source": self.source,
        }


_T1: Any = None
_T1_READY = False


def _t1() -> Any:
    global _T1, _T1_READY
    if not _T1_READY:
        _T1 = _external()
        _T1_READY = True
    return _T1


def rollup(checks: list[Check] | list[dict[str, Any]]) -> str:
    """FAIL on any failed check. BLOCKED when a human still has to answer.

    An empty list is SKIPPED. PASS only when every check is PASS.
    """
    foreign = getattr(_t1(), "rollup", None)
    if callable(foreign) and foreign is not rollup:
        return foreign(checks)
    statuses: list[str] = []
    severities: list[str] = []
    for check in checks:
        if isinstance(check, Check):
            statuses.append(check.status)
            severities.append(check.severity)
        else:
            statuses.append(str(check.get("status", "")))
            severities.append(str(check.get("severity", "")))
    if not statuses:
        return "SKIPPED"
    if any(
        status == "FAIL" and severity in {"critical", "high"}
        for status, severity in zip(statuses, severities, strict=True)
    ):
        return "FAIL"
    if "FAIL" in statuses:
        return "FAIL"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    if "MANUAL" in statuses:
        return "BLOCKED"
    if all(status == "PASS" for status in statuses):
        return "PASS"
    return "SKIPPED"


def render_md(report: dict[str, Any]) -> str:
    """Markdown from a report dict. Callers pass the JSON object, not a second model."""
    foreign = getattr(_t1(), "render_md", None)
    if callable(foreign) and foreign is not render_md:
        return foreign(report)
    checks = list(report.get("checks") or [])
    failed = [item for item in checks if item.get("status") == "FAIL"]
    rest = [item for item in checks if item.get("status") != "FAIL"]
    lines = [
        f"# {report.get('kit', '')} {report.get('status', '')}",
        "",
        f"Engagement: {report.get('engagement', '')}",
        f"Tenant: {report.get('tenant_id', '')}",
        f"DMS sha: {report.get('dms_sha', '')}",
        "",
        "| id | title | severity | status |",
        "|---|---|---|---|",
    ]
    for item in failed + rest:
        lines.append(
            f"| {item.get('id', '')} | {item.get('title', '')} | "
            f"{item.get('severity', '')} | {item.get('status', '')} |"
        )
    lines.extend(["", "## Failed items"])
    if not failed:
        lines.extend(["", "None."])
    for item in failed:
        title = item.get("title", "")
        status = item.get("status", "")
        lines.extend(
            ["", f"### {item.get('id', '')} {title}: {status}", "", str(item.get("fix") or "")]
        )
    return "\n".join(lines) + "\n"


def _external():
    path = Path(__file__).resolve().parents[1] / "report.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("fde_report_t1", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if getattr(module, "SCHEMA", "") != SCHEMA:
        return None
    if not callable(getattr(module, "render_md", None)):
        return None
    if not callable(getattr(module, "rollup", None)):
        return None
    return module


def scrub_text(text: str, secrets: list[str]) -> str:
    try:
        from dms_core.control_plane.ask_audit_scrub import scrub
    except Exception:
        scrub = None
    out = scrub(text) if scrub is not None else text
    for secret in secrets:
        if secret and len(secret) >= 8 and secret in out:
            out = out.replace(secret, "[redacted]")
    return out


def scrub_obj(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return scrub_text(value, secrets)
    if isinstance(value, list):
        return [scrub_obj(item, secrets) for item in value]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                continue
            cleaned[str(key)] = scrub_obj(item, secrets)
        return cleaned
    return value


def write_run(directory: Path, report: dict[str, Any], secrets: list[str]) -> None:
    """Write ``report.json``, then ``report.md`` rendered from that JSON."""
    directory.mkdir(parents=True, exist_ok=True)
    body = scrub_obj(report, secrets)
    raw = json.dumps(body, indent=2) + "\n"
    parsed = json.loads(raw)
    (directory / "report.json").write_text(json.dumps(parsed, indent=2) + "\n", encoding="utf-8")
    markdown = scrub_text(render_md(parsed), secrets)
    (directory / "report.md").write_text(markdown, encoding="utf-8")


def now_z() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_sha() -> str:
    root = Path(__file__).resolve().parents[3]
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return out.strip()


def flag_snapshot() -> dict[str, str]:
    root = os.environ.get("DMS_SQLITE_SOURCE_ROOT", "").strip()
    return {
        "DMS_FDE_KIT": os.environ.get("DMS_FDE_KIT", ""),
        "DMS_CONNECT_API": os.environ.get("DMS_CONNECT_API", ""),
        "DMS_CONNECT_V2": os.environ.get("DMS_CONNECT_V2", ""),
        "DMS_CONNECT_ORACLE": os.environ.get("DMS_CONNECT_ORACLE", ""),
        "DMS_CONNECT_GAUSS": os.environ.get("DMS_CONNECT_GAUSS", ""),
        "DMS_SQLITE_SOURCE_ROOT": "set" if root else "",
    }


def exit_code(status: str) -> int:
    if status == "PASS":
        return 0
    if status == "BLOCKED":
        return 3
    if status in {"FAIL", "SKIPPED"}:
        return 1
    return 2
