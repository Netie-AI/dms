"""Shared FDE report schema (netie.fde.report/1).

Every kit writes this shape. Markdown is rendered from the JSON object only.
Nothing here defaults a run to PASS: an empty check list is SKIPPED.

Passwords named by an engagement are removed by exact value. Secret-shaped
text is then passed through ``ask_audit_scrub`` when that module imports.
The governance scanner is used the same way when it is installed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
_CORE = ROOT / "packages" / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

SCHEMA = "netie.fde.report/1"
RUN_STATUSES = ("PASS", "FAIL", "BLOCKED", "SKIPPED")
CHECK_STATUSES = ("PASS", "FAIL", "BLOCKED", "SKIPPED", "MANUAL")
SEVERITIES = ("critical", "high", "medium", "low", "info")
_REQUIRED = (
    "schema",
    "kit",
    "stage",
    "engagement",
    "tenant_id",
    "dms_sha",
    "started_at",
    "finished_at",
    "flags",
    "status",
    "checks",
    "metrics",
    "artifacts",
    "cost",
)
_CHECK_REQUIRED = ("id", "title", "severity", "status", "evidence", "fix", "source")
_COST_REQUIRED = ("ov_calls", "usd", "source")
_STATUS_ORDER = {"FAIL": 0, "BLOCKED": 1, "MANUAL": 2, "SKIPPED": 3, "PASS": 4}
# A JSON key whose value is a secret. ``password_env`` is a variable name, not the secret.
_SECRET_KEY = re.compile(
    r"(?i)(?:^|[_\-.])(passwords?|passwds?|secrets?|api[-_]?keys?|tokens?|"
    r"bearers?|private[-_]?keys?|client[-_]?secrets?|authorizations?)(?:$|[_\-.])"
)
_SECRET_KEY_KEEP = frozenset({"password_env", "ov_ref"})
_TEXT_SUFFIXES = frozenset({".json", ".md", ".txt", ".yaml", ".yml", ".log"})
_MIN_SECRET_LEN = 6

_sha: str | None = None


def dms_sha() -> str:
    """Eight-character git SHA of this checkout, or ``unknown``."""
    global _sha
    if _sha is None:
        _sha = _read_sha()
    return _sha


def _read_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out or "unknown"


def utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_report(
    *,
    kit: str,
    stage: str,
    engagement: str,
    tenant_id: str,
    dms_sha_value: str | None = None,
    flags: dict[str, str] | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    """A report with no checks. Status is SKIPPED until ``rollup`` says otherwise."""
    started = started_at or utc_now()
    return {
        "schema": SCHEMA,
        "kit": kit,
        "stage": stage,
        "engagement": engagement,
        "tenant_id": tenant_id,
        "dms_sha": dms_sha_value if dms_sha_value is not None else dms_sha(),
        "started_at": started,
        "finished_at": started,
        "flags": dict(flags or {}),
        "status": "SKIPPED",
        "checks": [],
        "metrics": {},
        "artifacts": [],
        "cost": {"ov_calls": 0, "usd": 0.0, "source": "OV /api/usage"},
    }


def make_check(
    *,
    id: str,
    title: str,
    severity: str,
    status: str,
    evidence: dict[str, Any] | None = None,
    fix: str = "",
    source: str = "",
) -> dict[str, Any]:
    return {
        "id": id,
        "title": title,
        "severity": severity,
        "status": status,
        "evidence": dict(evidence or {}),
        "fix": fix,
        "source": source,
    }


def add_check(report: dict[str, Any], check: dict[str, Any]) -> None:
    report.setdefault("checks", []).append(check)
    report["status"] = rollup(report["checks"])


def rollup(checks: list[dict[str, Any]]) -> str:
    """Roll checks up to a run status.

    An empty list is SKIPPED. Any FAIL fails the run, including medium and low,
    because a critical or high FAIL is already enough and a lower FAIL is still
    a failure. BLOCKED, and a MANUAL item still waiting for a person, block the
    run when nothing failed. PASS mixed with SKIPPED stays SKIPPED: a check
    that did not run is not a pass.
    """
    if not checks:
        return "SKIPPED"
    statuses = [str(c.get("status") or "") for c in checks]
    if any(s == "FAIL" for s in statuses):
        return "FAIL"
    if any(s == "BLOCKED" for s in statuses) or any(s == "MANUAL" for s in statuses):
        return "BLOCKED"
    if all(s == "PASS" for s in statuses):
        return "PASS"
    return "SKIPPED"


def finish(report: dict[str, Any]) -> dict[str, Any]:
    report["finished_at"] = utc_now()
    report["status"] = rollup(list(report.get("checks") or []))
    return report


def validate(report: dict[str, Any]) -> list[str]:
    """Problems in a report object. An empty list means the 4.3 shape holds."""
    problems: list[str] = []
    if not isinstance(report, dict):
        return ["report is not an object"]
    for key in _REQUIRED:
        if key not in report:
            problems.append(f"missing {key}")
    if report.get("schema") != SCHEMA:
        problems.append("schema")
    if report.get("status") not in RUN_STATUSES:
        problems.append("status")
    flags = report.get("flags")
    if not isinstance(flags, dict):
        problems.append("flags")
    checks = report.get("checks")
    if not isinstance(checks, list):
        problems.append("checks")
    else:
        for i, check in enumerate(checks):
            if not isinstance(check, dict):
                problems.append(f"checks[{i}]")
                continue
            for key in _CHECK_REQUIRED:
                if key not in check:
                    problems.append(f"checks[{i}].{key}")
            if check.get("severity") not in SEVERITIES:
                problems.append(f"checks[{i}].severity")
            if check.get("status") not in CHECK_STATUSES:
                problems.append(f"checks[{i}].status")
            if not isinstance(check.get("evidence"), dict):
                problems.append(f"checks[{i}].evidence")
    if not isinstance(report.get("metrics"), dict):
        problems.append("metrics")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, list) or not all(isinstance(a, str) for a in artifacts or []):
        problems.append("artifacts")
    cost = report.get("cost")
    if not isinstance(cost, dict):
        problems.append("cost")
    else:
        for key in _COST_REQUIRED:
            if key not in cost:
                problems.append(f"cost.{key}")
        if not isinstance(cost.get("ov_calls"), int):
            problems.append("cost.ov_calls")
        if not isinstance(cost.get("usd"), (int, float)):
            problems.append("cost.usd")
    for key in ("started_at", "finished_at"):
        val = report.get(key)
        if not isinstance(val, str) or not val.endswith("Z"):
            problems.append(key)
    if "status" in report and isinstance(checks, list):
        expect = rollup(checks)
        if report.get("status") != expect:
            problems.append(f"status {report.get('status')} != rollup {expect}")
    return problems


def render_md(report: dict[str, Any]) -> str:
    """Markdown from a report dict. Callers pass the JSON object, not a side channel."""
    checks = [c for c in (report.get("checks") or []) if isinstance(c, dict)]
    def _sort_key(check: dict[str, Any]) -> tuple[int, str]:
        rank = _STATUS_ORDER.get(str(check.get("status")), 9)
        return rank, str(check.get("id"))

    ordered = sorted(checks, key=_sort_key)
    lines = [
        f"# {report.get('kit', '')} ({report.get('stage', '')})",
        "",
        f"Engagement: {report.get('engagement', '')}",
        f"Tenant: {report.get('tenant_id', '')}",
        f"Status: {report.get('status', '')}",
        f"DMS sha: {report.get('dms_sha', '')}",
        f"Started: {report.get('started_at', '')}",
        f"Finished: {report.get('finished_at', '')}",
        "",
        "## Checks",
        "",
        "| id | severity | status | title |",
        "|---|---|---|---|",
    ]
    for check in ordered:
        lines.append(
            f"| {check.get('id', '')} | {check.get('severity', '')} | "
            f"{check.get('status', '')} | {check.get('title', '')} |"
        )
    attention = [c for c in ordered if c.get("status") in ("FAIL", "BLOCKED", "MANUAL")]
    lines.extend(["", "## Needs attention", ""])
    if not attention:
        lines.append("None.")
    for check in attention:
        lines.extend(
            [
                "",
                f"### {check.get('id', '')} {check.get('title', '')}",
                "",
                f"Severity: {check.get('severity', '')}",
                f"Status: {check.get('status', '')}",
                "Evidence: `"
                + json.dumps(check.get("evidence") or {}, default=str, sort_keys=True)
                + "`",
                f"Fix: {check.get('fix', '')}",
                f"Source: {check.get('source', '')}",
            ]
        )
    skipped = [c for c in ordered if c.get("status") == "SKIPPED"]
    if skipped:
        lines.extend(["", "## Not run", ""])
        for check in skipped:
            reason = (check.get("evidence") or {}).get("reason") or check.get("fix") or ""
            lines.append(f"- {check.get('id', '')} {check.get('title', '')}: {reason}")
    cost = report.get("cost") or {}
    lines.extend(
        [
            "",
            "## Cost",
            "",
            f"ov_calls: {cost.get('ov_calls', 0)}",
            f"usd: {cost.get('usd', 0.0)}",
            f"source: {cost.get('source', '')}",
            "",
        ]
    )
    return "\n".join(lines)


def secret_values(names: list[str], environ: dict[str, str] | None = None) -> list[str]:
    """Env values named by the engagement. Short values are skipped (they collide)."""
    env = os.environ if environ is None else environ
    found: list[str] = []
    for name in names:
        value = env.get(name) or ""
        if len(value) >= _MIN_SECRET_LEN and value not in found:
            found.append(value)
    found.sort(key=len, reverse=True)
    return found


def _is_secret_key(key: str) -> bool:
    if key in _SECRET_KEY_KEEP:
        return False
    return _SECRET_KEY.search(key) is not None


def scrub_text(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = _audit_scrub(text)
    return _governance_scrub(text)


def scrub_obj(obj: Any, secrets: list[str]) -> Any:
    if isinstance(obj, str):
        return scrub_text(obj, secrets)
    if isinstance(obj, list):
        return [scrub_obj(item, secrets) for item in obj]
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and _is_secret_key(key):
                out[key] = "[redacted]"
            else:
                out[str(key)] = scrub_obj(value, secrets)
        return out
    return obj


def _audit_scrub(text: str) -> str:
    try:
        from dms_core.control_plane.ask_audit_scrub import scrub
    except Exception:
        return text
    try:
        return scrub(text)
    except Exception:
        return text


def _governance_scrub(text: str) -> str:
    """#671 secret rules when that module is installed. Absent is a no-op."""
    try:
        from dms_core.governance import scan as gov_scan
    except Exception:
        return text
    for name in ("redact", "scrub", "scrub_text"):
        fn = getattr(gov_scan, name, None)
        if not callable(fn):
            continue
        try:
            got = fn(text)
        except Exception:
            continue
        if isinstance(got, str):
            return got
    return text


def write_report(directory: Path, report: dict[str, Any], secrets: list[str]) -> None:
    """Write report.json and report.md. The markdown is rendered from the JSON text."""
    cleaned = scrub_obj(report, secrets)
    cleaned["status"] = rollup(list(cleaned.get("checks") or []))
    text = json.dumps(cleaned, indent=1, default=str) + "\n"
    scrubbed = scrub_text(text, secrets)
    try:
        json.loads(scrubbed)
        text = scrubbed
    except json.JSONDecodeError:
        text = json.dumps(cleaned, indent=1, default=str) + "\n"
    payload = json.loads(text)
    md = scrub_text(render_md(payload), secrets)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(text, encoding="utf-8")
    (directory / "report.md").write_text(md, encoding="utf-8")


def scrub_tree(root: Path, secrets: list[str]) -> None:
    """Replace secret values in text files under ``root``. Binary files are left alone."""
    if not secrets or not root.exists():
        return
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        raw = path.read_bytes()
        if b"\x00" in raw[:1024]:
            continue
        text = raw.decode("utf-8", errors="replace")
        cleaned = scrub_text(text, secrets)
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(cleaned)
            except json.JSONDecodeError:
                payload = None
            if payload is not None:
                payload = scrub_obj(payload, secrets)
                cleaned = json.dumps(payload, indent=1, default=str) + "\n"
                cleaned = scrub_text(cleaned, secrets)
                try:
                    json.loads(cleaned)
                except json.JSONDecodeError:
                    cleaned = json.dumps(payload, indent=1, default=str) + "\n"
        if cleaned != text:
            path.write_text(cleaned, encoding="utf-8")


def ingest_receipts(src: Path, dest: Path, secrets: list[str]) -> list[str]:
    """Copy engagement receipts into the run folder with secrets removed."""
    if not src.is_dir():
        return []
    dest.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for path in sorted(src.glob("*.json")):
        raw = path.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            text = scrub_text(raw, secrets)
        else:
            text = json.dumps(scrub_obj(payload, secrets), indent=1, default=str) + "\n"
            text = scrub_text(text, secrets)
        (dest / path.name).write_text(text, encoding="utf-8")
        names.append(f"receipts/{path.name}")
    return names
