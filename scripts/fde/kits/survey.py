"""Site survey (K1). Catalog stats, cap forecast, keys, TLS. No customer write.

DMS_FDE_KIT=1 python scripts/fde/kits/survey.py --engagement acme/
"""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_FDE = Path(__file__).resolve().parents[1]
if str(_FDE) not in sys.path:
    sys.path.insert(0, str(_FDE))

from kits._boot import boot  # noqa: E402

boot()

from kits._schema import (  # noqa: E402
    Check,
    flag_snapshot,
    git_sha,
    kit_enabled,
    now_z,
    rollup,
    write_run,
)
from kits._schema import KitDisabled as _KitDisabled  # noqa: E402
from kits.engage import identity, password_for, secret_values, sources_of  # noqa: E402
from kits.probe import CAP_FIX, CAP_SOURCE, KEY_SOURCE, Facts, collect  # noqa: E402

_PLACE = re.compile(r"^(TODO|\(fill\).*|_.*|<[^>]+>)\s*$", re.IGNORECASE)
_DB = (
    ("K1.04", "Rows vs per-table cap", "critical", CAP_SOURCE),
    ("K1.05", "Engine and version on the support matrix", "high", ""),
    ("K1.06", "Table count", "info", ""),
    ("K1.07", "Size estimate", "info", ""),
    ("K1.08", "Key visibility", "high", KEY_SOURCE),
    ("K1.09", "Network reachability", "high", ""),
    ("K1.10", "TLS", "high", ""),
)


def _check(
    check_id: str,
    title: str,
    severity: str,
    status: str,
    evidence: dict[str, Any] | None = None,
    fix: str = "",
    source: str = "",
) -> dict[str, Any]:
    return Check(
        id=check_id,
        title=title,
        severity=severity,
        status=status,
        evidence=evidence or {},
        fix=fix,
        source=source,
    ).to_dict()


def _sections(text: str) -> dict[str, str]:
    current = ""
    body: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("#"):
            current = line.lstrip("#").strip().lower()
            body.setdefault(current, [])
            continue
        if current:
            body[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in body.items()}


def _body(sections: dict[str, str], *needles: str) -> str:
    for key, value in sections.items():
        if any(needle in key for needle in needles):
            return value
    return ""


def _filled(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    return any(_PLACE.match(line) is None for line in lines)


def _named(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and _PLACE.match(text) is None


def intake_checks(directory: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = ""
    path = directory / "intake.md"
    if path.is_file():
        raw = path.read_text(encoding="utf-8")
    sections = _sections(raw)
    use_ok = _filled(_body(sections, "use case"))
    decision_ok = _filled(_body(sections, "decision"))
    people_ok = _filled(_body(sections, "stakeholder"))
    pii_ok = _filled(_body(sections, "pii"))
    defs_ok = _filled(_body(sections, "definition"))
    names_ok = _named(data.get("steward")) and _named(data.get("dba")) and _named(data.get("owner"))
    manual = "Record this on intake.md. An empty section stays MANUAL."
    return [
        _check(
            "K1.01",
            "Use case recorded",
            "info",
            "PASS" if use_ok else "MANUAL",
            fix="" if use_ok else manual,
        ),
        _check(
            "K1.02",
            "Decision recorded",
            "info",
            "PASS" if decision_ok else "MANUAL",
            fix="" if decision_ok else manual,
        ),
        _check(
            "K1.03",
            "Steward, DBA, and owner named",
            "high",
            "PASS" if names_ok and people_ok else "MANUAL",
            fix=""
            if names_ok and people_ok
            else "Record the steward, the DBA, and the owner in engagement.yaml and intake.md.",
        ),
        _check(
            "K1.11",
            "PII list recorded",
            "high",
            "PASS" if pii_ok else "MANUAL",
            fix="" if pii_ok else manual,
        ),
        _check(
            "K1.12",
            "Definitions sheet recorded",
            "info",
            "PASS" if defs_ok else "MANUAL",
            fix="" if defs_ok else manual,
        ),
    ]


def _skipped(spec: tuple[str, str, str, str], reason: str) -> dict[str, Any]:
    return _check(spec[0], spec[1], spec[2], "SKIPPED", {"reason": reason}, source=spec[3])


def _cap(facts: Facts) -> dict[str, Any]:
    from dms_executor.db_connector import DEFAULT_MAX_ROWS

    spec = _DB[0]
    if not facts.accepted:
        return _skipped(spec, facts.skip_reason)
    if facts.login_code:
        return _skipped(spec, "login failed")
    over = [
        table for table in facts.tables if table.rows is not None and table.rows > DEFAULT_MAX_ROWS
    ]
    unknown = [table for table in facts.tables if table.rows is None]
    if over:
        first = over[0]
        return _check(
            spec[0],
            spec[1],
            spec[2],
            "FAIL",
            {
                "table": f"{first.schema}.{first.name}",
                "rows": first.rows,
                "cap": DEFAULT_MAX_ROWS,
                "over_cap": [f"{table.schema}.{table.name}" for table in over],
            },
            CAP_FIX,
            spec[3],
        )
    if unknown or not facts.tables:
        reason = "no tables" if not facts.tables else "catalog stats missing"
        return _skipped(spec, reason + "; a missing estimate is not under the cap")
    return _check(
        spec[0],
        spec[1],
        spec[2],
        "PASS",
        {"tables": len(facts.tables), "cap": DEFAULT_MAX_ROWS},
        source=spec[3],
    )


def _engine(facts: Facts) -> dict[str, Any]:
    spec = _DB[1]
    if not facts.accepted:
        return _skipped(spec, facts.skip_reason)
    if facts.login_code and not facts.version:
        return _skipped(spec, "login failed")
    if not facts.version:
        return _skipped(spec, "version unreadable")
    return _check(
        spec[0],
        spec[1],
        spec[2],
        "PASS",
        {"kind": facts.kind, "driver_kind": facts.driver_kind, "version": facts.version},
    )


def _tables(facts: Facts) -> dict[str, Any]:
    spec = _DB[2]
    if not facts.accepted:
        return _skipped(spec, facts.skip_reason)
    if facts.login_code:
        return _skipped(spec, "login failed")
    return _check(spec[0], spec[1], spec[2], "PASS", {"tables": len(facts.tables)})


def _size(facts: Facts) -> dict[str, Any]:
    spec = _DB[3]
    if not facts.accepted:
        return _skipped(spec, facts.skip_reason)
    if facts.login_code:
        return _skipped(spec, "login failed")
    known = [table.nbytes for table in facts.tables if table.nbytes is not None]
    total = facts.db_bytes if facts.db_bytes is not None else (sum(known) if known else None)
    if total is None:
        return _skipped(spec, "size not in the catalog")
    return _check(spec[0], spec[1], spec[2], "PASS", {"bytes": total})


def _keys(facts: Facts) -> dict[str, Any]:
    spec = _DB[4]
    if not facts.accepted:
        return _skipped(spec, facts.skip_reason)
    if facts.login_code:
        return _skipped(spec, "login failed")
    if facts.key_code or facts.pk_count is None:
        fix = "Key discovery could not read the catalog."
        if facts.key_path == "pg_catalog":
            fix = (
                "Key discovery reads pg_catalog (FDE-PG-KEYS-01). "
                "INFORMATION_SCHEMA hides keys from a SELECT-only login."
            )
        return _check(
            spec[0],
            spec[1],
            spec[2],
            "FAIL",
            {"code": facts.key_code or "keys_unreadable", "path": facts.key_path},
            fix,
            spec[3],
        )
    return _check(
        spec[0],
        spec[1],
        spec[2],
        "PASS",
        {
            "path": facts.key_path,
            "primary_keys": facts.pk_count,
            "foreign_keys": facts.fk_count,
        },
        source=spec[3],
    )


def _network(facts: Facts) -> dict[str, Any]:
    spec = _DB[5]
    if not facts.accepted:
        return _skipped(spec, facts.skip_reason)
    if facts.driver_kind == "sqlite":
        if facts.reachable:
            return _check(spec[0], spec[1], spec[2], "PASS", {"path": "local file"})
        return _check(
            spec[0],
            spec[1],
            spec[2],
            "FAIL",
            {"code": facts.reach_code or "unreachable"},
            "The SQLite file is not inside DMS_SQLITE_SOURCE_ROOT.",
        )
    if facts.reachable is False or facts.login_code == "network":
        return _check(
            spec[0],
            spec[1],
            spec[2],
            "FAIL",
            {"code": facts.reach_code or "unreachable"},
            "Agree a network path (VPN, allow-listed IP, or bastion) before the visit.",
        )
    if facts.reachable:
        return _check(spec[0], spec[1], spec[2], "PASS", {"code": "reachable"})
    return _skipped(spec, "reachability not measured")


def _tls(facts: Facts) -> dict[str, Any]:
    spec = _DB[6]
    if not facts.accepted:
        return _skipped(spec, facts.skip_reason)
    if facts.driver_kind == "sqlite":
        return _skipped(spec, "local file, TLS does not apply")
    if facts.login_code in {"auth", "network"} and facts.tls_on is None:
        return _skipped(spec, "login failed")
    if facts.requested_tls is None:
        return _check(
            spec[0],
            spec[1],
            spec[2],
            "MANUAL",
            {},
            "Record tls: true or false on the source.",
        )
    if facts.tls_on is None:
        return _skipped(spec, facts.tls_code or "tls unreadable")
    if facts.requested_tls and not facts.tls_on:
        return _check(
            spec[0],
            spec[1],
            spec[2],
            "FAIL",
            {"tls": False},
            "The session is not TLS. Agree an encrypted path before the visit.",
        )
    return _check(spec[0], spec[1], spec[2], "PASS", {"tls": facts.tls_on})


def db_checks(facts: Facts) -> list[dict[str, Any]]:
    return [
        _cap(facts),
        _engine(facts),
        _tables(facts),
        _size(facts),
        _keys(facts),
        _network(facts),
        _tls(facts),
    ]


def _fold(spec: tuple[str, str, str, str], parts: list[dict[str, Any]]) -> dict[str, Any]:
    if not parts:
        return _skipped(spec, "no source in engagement.yaml")
    rank = {"FAIL": 0, "BLOCKED": 1, "MANUAL": 2, "SKIPPED": 3, "PASS": 4}
    status = min((part["status"] for part in parts), key=lambda item: rank.get(item, 9))
    fix = ""
    for part in parts:
        if part["status"] == status and part.get("fix"):
            fix = str(part["fix"])
            break
    if len(parts) == 1:
        evidence = dict(parts[0].get("evidence") or {})
    else:
        evidence = {"items": [part.get("evidence") or {} for part in parts]}
    return _check(spec[0], spec[1], spec[2], status, evidence, fix, spec[3])


def build_checks(
    directory: Path,
    data: dict[str, Any],
    *,
    collect_fn: Any = None,
    probe_network: bool = True,
) -> list[dict[str, Any]]:
    reader = collect_fn or collect
    checks = intake_checks(directory, data)
    sources = sources_of(data)
    grouped: dict[str, list[dict[str, Any]]] = {spec[0]: [] for spec in _DB}
    if not sources:
        for spec in _DB:
            grouped[spec[0]].append(_skipped(spec, "no source in engagement.yaml"))
    for source in sources:
        facts = reader(source, password_for(source), probe_network=probe_network)
        for item in db_checks(facts):
            grouped[item["id"]].append(item)
    for spec in _DB:
        checks.append(_fold(spec, grouped[spec[0]]))
    return checks


def run(
    directory: Path,
    data: dict[str, Any],
    *,
    only: set[str] | None = None,
    collect_fn: Any = None,
    probe_network: bool = True,
) -> dict[str, Any]:
    if not kit_enabled():
        raise _KitDisabled("fde_kit_disabled")
    started = now_z()
    checks = build_checks(directory, data, collect_fn=collect_fn, probe_network=probe_network)
    if only:
        checks = [item for item in checks if item["id"] in only]
    objects = [
        Check(
            id=item["id"],
            title=item["title"],
            severity=item["severity"],
            status=item["status"],
            evidence=item.get("evidence") or {},
            fix=item.get("fix") or "",
            source=item.get("source") or "",
        )
        for item in checks
    ]
    name, tenant = identity(directory, data)
    report = {
        "schema": "netie.fde.report/1",
        "kit": "survey",
        "stage": "K1",
        "engagement": name,
        "tenant_id": tenant,
        "dms_sha": git_sha(),
        "started_at": started,
        "finished_at": now_z(),
        "flags": flag_snapshot(),
        "status": rollup(objects),
        "checks": [item.to_dict() for item in objects],
        "metrics": _metrics(objects),
        "artifacts": [],
        "cost": {"ov_calls": 0, "usd": 0.0, "source": "OV /api/usage"},
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = _run_dir(directory, stamp, "survey")
    write_run(run_dir, report, secret_values(data))
    return report


def _metrics(checks: list[Check]) -> dict[str, Any]:
    for check in checks:
        if check.id == "K1.04" and check.evidence:
            return {"cap": check.evidence}
    return {}


def _run_dir(directory: Path, stamp: str, kit: str) -> Path:
    run_dir = directory / "runs" / f"{stamp}-{kit}"
    suffix = 2
    while run_dir.exists():
        run_dir = directory / "runs" / f"{stamp}-{kit}-{suffix}"
        suffix += 1
    return run_dir


def main(argv: list[str] | None = None) -> int:
    from kits.engage import cli

    return cli(run, argv)


if __name__ == "__main__":
    sys.exit(main())
