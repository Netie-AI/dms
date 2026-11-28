"""Report shape ``netie.fde.report/1`` for one FDE kit.

T1 owns ``scripts/fde/report.py``. This module is the same document so a kit
can run before that file exists. ``upgrade.py`` loads T1's module when it
defines this schema, ``rollup``, and ``render_md``.

``report.md`` is rendered from the JSON document only.
"""

from __future__ import annotations

import json
from typing import Any

SCHEMA = "netie.fde.report/1"

STATUSES = ("PASS", "FAIL", "BLOCKED", "SKIPPED", "MANUAL")
SEVERITIES = ("critical", "high", "medium", "low", "info")
RUN_STATUSES = ("PASS", "FAIL", "BLOCKED", "SKIPPED")

DOCUMENT_KEYS = (
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

CHECK_KEYS = ("id", "title", "severity", "status", "evidence", "fix", "source")

_RANK = {"FAIL": 0, "BLOCKED": 1, "MANUAL": 2, "SKIPPED": 3, "PASS": 4}


def _field(check: Any, name: str) -> Any:
    if isinstance(check, dict):
        return check.get(name)
    return getattr(check, name)


def rollup(checks: list[Any]) -> str:
    """Nothing defaults to PASS. An empty list is SKIPPED.

    Any FAIL fails the run, including medium and low: exit 0 requires every
    check to be PASS. BLOCKED and MANUAL need a person. A mix of PASS and
    SKIPPED stays SKIPPED.
    """
    if not checks:
        return "SKIPPED"
    statuses = [_field(c, "status") for c in checks]
    if any(status == "FAIL" for status in statuses):
        return "FAIL"
    if any(status in {"BLOCKED", "MANUAL"} for status in statuses):
        return "BLOCKED"
    if all(status == "PASS" for status in statuses):
        return "PASS"
    return "SKIPPED"


def render_md(doc: dict[str, Any]) -> str:
    """Human report. Reads the document dict and nothing else."""
    checks = list(doc.get("checks") or [])
    ordered = sorted(checks, key=lambda c: (_RANK.get(str(c.get("status")), 9), str(c.get("id"))))
    lines = [
        f"# {doc.get('kit')} {doc.get('stage')} {doc.get('status')}",
        "",
        f"Engagement: {doc.get('engagement')}",
        f"Tenant: {doc.get('tenant_id')}",
        f"DMS sha: {doc.get('dms_sha')}",
        f"Started: {doc.get('started_at')}",
        f"Finished: {doc.get('finished_at')}",
        "",
        "| id | severity | status | title |",
        "| --- | --- | --- | --- |",
    ]
    for check in ordered:
        title = str(check.get("title", "")).replace("|", "/")
        lines.append(
            f"| {check.get('id')} | {check.get('severity')} | {check.get('status')} | {title} |"
        )
    lines.append("")
    for check in ordered:
        if check.get("status") == "PASS":
            continue
        lines.append(f"## {check.get('id')} {check.get('title')}")
        lines.append("")
        lines.append(f"Status: {check.get('status')} ({check.get('severity')})")
        lines.append("")
        lines.append(str(check.get("fix") or ""))
        lines.append("")
        evidence = json.dumps(check.get("evidence") or {}, sort_keys=True)
        lines.append(f"Evidence: `{evidence}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def document(
    *,
    kit: str,
    stage: str,
    engagement: str,
    tenant_id: str,
    dms_sha: str,
    started_at: str,
    finished_at: str,
    flags: dict[str, str],
    status: str,
    checks: list[dict[str, Any]],
    metrics: dict[str, Any],
    artifacts: list[str],
    cost: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc = {
        "schema": SCHEMA,
        "kit": kit,
        "stage": stage,
        "engagement": engagement,
        "tenant_id": tenant_id,
        "dms_sha": dms_sha,
        "started_at": started_at,
        "finished_at": finished_at,
        "flags": flags,
        "status": status,
        "checks": checks,
        "metrics": metrics,
        "artifacts": list(artifacts),
        "cost": cost or {"ov_calls": 0, "usd": 0.0, "source": "none"},
    }
    missing = [key for key in DOCUMENT_KEYS if key not in doc]
    if missing or doc["schema"] != SCHEMA:
        raise RuntimeError("report document is incomplete")
    if status not in RUN_STATUSES:
        raise RuntimeError("report status is not in the schema")
    for check in checks:
        if tuple(check.keys()) != CHECK_KEYS:
            raise RuntimeError("check keys do not match the schema")
        if check["status"] not in STATUSES or check["severity"] not in SEVERITIES:
            raise RuntimeError("check status or severity is not in the schema")
    return doc
