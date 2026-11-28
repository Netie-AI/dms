"""Connector setup tests (K2). Read-only proof, no write, landing forecast.

    DMS_FDE_KIT=1 python scripts/fde/kits/connect.py --engagement acme/

``POST /v1/connect/test`` and ``GET /v1/connect/sources`` run only when
``DMS_CONNECT_API`` and ``DMS_CONNECT_V2`` are on. Off, that check is SKIPPED.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
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
    product_enabled,
    rollup,
    scrub_obj,
    write_run,
)
from kits._schema import KitDisabled as _KitDisabled  # noqa: E402
from kits.engage import identity, password_for, secret_values, sources_of  # noqa: E402
from kits.probe import CAP_FIX, CAP_SOURCE, KEY_SOURCE, Facts, collect  # noqa: E402

_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_SPECS = (
    ("K2.01", "Validate login", "critical", ""),
    ("K2.02", "TLS or encrypt", "high", ""),
    ("K2.03", "Read-only", "high", ""),
    ("K2.04", "Catalog visible", "high", ""),
    ("K2.05", "Keys visible", "high", KEY_SOURCE),
    ("K2.06", "Schema introspect", "medium", ""),
    ("K2.07", "Landing receipt truncated", "critical", CAP_SOURCE),
    ("K2.08", "Landing receipt untyped", "high", ""),
    ("K2.09", "Landing receipt skipped", "high", ""),
    ("K2.10", "Connect API test", "high", "apps/api/dms_api/routes/connect.py"),
)
_LOGIN_FIX = (
    "The login was refused. Check the user and the password env var. "
    "The password is not in this report."
)
_READONLY_FIX = (
    "not read-only: the login can change data. Ask the DBA for a SELECT-only "
    "role. No write was issued."
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


def _spec(check_id: str) -> tuple[str, str, str, str]:
    for item in _SPECS:
        if item[0] == check_id:
            return item
    raise KeyError(check_id)


def _skipped(check_id: str, reason: str) -> dict[str, Any]:
    spec = _spec(check_id)
    return _check(spec[0], spec[1], spec[2], "SKIPPED", {"reason": reason}, source=spec[3])


def _login(facts: Facts) -> dict[str, Any]:
    if not facts.accepted:
        return _skipped("K2.01", facts.skip_reason)
    if facts.login_code:
        return _check(
            "K2.01",
            "Validate login",
            "critical",
            "FAIL",
            {"code": facts.login_code},
            _LOGIN_FIX,
        )
    return _check("K2.01", "Validate login", "critical", "PASS", {"code": "ok"})


def _tls(facts: Facts) -> dict[str, Any]:
    if not facts.accepted:
        return _skipped("K2.02", facts.skip_reason)
    if facts.driver_kind == "sqlite":
        return _skipped("K2.02", "local file, TLS does not apply")
    if facts.login_code in {"auth", "network"} and facts.tls_on is None:
        return _skipped("K2.02", "login failed")
    if facts.requested_tls is None:
        return _check(
            "K2.02",
            "TLS or encrypt",
            "high",
            "MANUAL",
            {},
            "Record tls: true or false on the source.",
        )
    if facts.tls_on is None:
        return _skipped("K2.02", facts.tls_code or "tls unreadable")
    if facts.requested_tls and not facts.tls_on:
        return _check(
            "K2.02",
            "TLS or encrypt",
            "high",
            "FAIL",
            {"tls": False},
            "The session is not TLS. Turn encrypt on for this source.",
        )
    return _check("K2.02", "TLS or encrypt", "high", "PASS", {"tls": facts.tls_on})


def _readonly(facts: Facts) -> dict[str, Any]:
    if not facts.accepted:
        return _skipped("K2.03", facts.skip_reason)
    if facts.login_code:
        return _skipped("K2.03", "login failed")
    if facts.writes is True or facts.read_only_txn is False:
        return _check(
            "K2.03",
            "Read-only",
            "high",
            "FAIL",
            {"result": "not read-only", "write_privilege": facts.writes is True},
            _READONLY_FIX,
        )
    if facts.writes is None or facts.read_only_txn is not True:
        return _skipped("K2.03", facts.priv_code or "read-only not confirmed")
    return _check(
        "K2.03",
        "Read-only",
        "high",
        "PASS",
        {"result": "read-only", "write_privilege": False},
    )


def _catalog(facts: Facts) -> dict[str, Any]:
    if not facts.accepted:
        return _skipped("K2.04", facts.skip_reason)
    if facts.login_code:
        return _skipped("K2.04", "login failed")
    if not facts.tables:
        return _check(
            "K2.04",
            "Catalog visible",
            "high",
            "FAIL",
            {"tables": 0},
            "Grant USAGE and SELECT so the catalog lists the agreed tables.",
        )
    return _check("K2.04", "Catalog visible", "high", "PASS", {"tables": len(facts.tables)})


def _keys(facts: Facts) -> dict[str, Any]:
    if not facts.accepted:
        return _skipped("K2.05", facts.skip_reason)
    if facts.login_code:
        return _skipped("K2.05", "login failed")
    if facts.key_code or facts.pk_count is None:
        fix = "Key discovery could not read the catalog."
        if facts.key_path == "pg_catalog":
            fix = (
                "Key discovery reads pg_catalog (FDE-PG-KEYS-01). "
                "INFORMATION_SCHEMA hides keys from a SELECT-only login."
            )
        return _check(
            "K2.05",
            "Keys visible",
            "high",
            "FAIL",
            {"code": facts.key_code or "keys_unreadable", "path": facts.key_path},
            fix,
            KEY_SOURCE,
        )
    return _check(
        "K2.05",
        "Keys visible",
        "high",
        "PASS",
        {
            "path": facts.key_path,
            "primary_keys": facts.pk_count,
            "foreign_keys": facts.fk_count,
        },
        source=KEY_SOURCE,
    )


def _schema(facts: Facts) -> dict[str, Any]:
    if not facts.accepted:
        return _skipped("K2.06", facts.skip_reason)
    if facts.login_code:
        return _skipped("K2.06", "login failed")
    if facts.schema_code:
        return _check(
            "K2.06",
            "Schema introspect",
            "medium",
            "FAIL",
            {"code": facts.schema_code},
            "The column catalog could not be read.",
        )
    return _check(
        "K2.06",
        "Schema introspect",
        "medium",
        "PASS",
        {"columns": len(facts.columns)},
    )


def _truncated(facts: Facts) -> dict[str, Any]:
    from dms_executor.db_connector import DEFAULT_MAX_ROWS

    if not facts.accepted:
        return _skipped("K2.07", facts.skip_reason)
    if facts.login_code:
        return _skipped("K2.07", "login failed")
    over = [
        table for table in facts.tables if table.rows is not None and table.rows > DEFAULT_MAX_ROWS
    ]
    unknown = [table for table in facts.tables if table.rows is None]
    if over:
        first = over[0]
        return _check(
            "K2.07",
            "Landing receipt truncated",
            "critical",
            "FAIL",
            {
                "table": f"{first.schema}.{first.name}",
                "rows": first.rows,
                "cap": DEFAULT_MAX_ROWS,
                "method": "catalog_forecast",
            },
            CAP_FIX,
            CAP_SOURCE,
        )
    if unknown or not facts.tables:
        reason = "no tables" if not facts.tables else "catalog stats missing"
        return _skipped("K2.07", reason)
    return _check(
        "K2.07",
        "Landing receipt truncated",
        "critical",
        "PASS",
        {"truncated": 0, "cap": DEFAULT_MAX_ROWS, "method": "catalog_forecast"},
        source=CAP_SOURCE,
    )


def _untyped(facts: Facts) -> dict[str, Any]:
    from dms_executor.bronze import duckdb_type_for

    if not facts.accepted:
        return _skipped("K2.08", facts.skip_reason)
    if facts.login_code:
        return _skipped("K2.08", "login failed")
    if facts.schema_code:
        return _skipped("K2.08", facts.schema_code)
    names: list[str] = []
    for column in facts.columns:
        landed = duckdb_type_for(column.data_type, column.precision, column.scale)
        if landed is None:
            names.append(f"{column.schema}.{column.name}.{column.column}")
    if names:
        return _check(
            "K2.08",
            "Landing receipt untyped",
            "high",
            "FAIL",
            {"columns": names, "method": "catalog_forecast"},
            "Fix the source type, or accept that the column is not measurable.",
        )
    return _check(
        "K2.08",
        "Landing receipt untyped",
        "high",
        "PASS",
        {"untyped": 0, "method": "catalog_forecast"},
    )


def _skipped_tables(facts: Facts, source: dict[str, Any]) -> dict[str, Any]:
    if not facts.accepted:
        return _skipped("K2.09", facts.skip_reason)
    requested = source.get("tables")
    if not isinstance(requested, list):
        return _check(
            "K2.09",
            "Landing receipt skipped",
            "high",
            "MANUAL",
            {},
            "Name the tables the steward wants. A missing list is not an empty skip list.",
        )
    if facts.login_code:
        return _skipped("K2.09", "login failed")
    visible = {table.name.lower() for table in facts.tables}
    visible.update(f"{table.schema}.{table.name}".lower() for table in facts.tables)
    missing = [str(name) for name in requested if str(name).lower() not in visible]
    if missing:
        return _check(
            "K2.09",
            "Landing receipt skipped",
            "high",
            "FAIL",
            {"skipped": missing},
            "The login cannot see these tables. Ask the DBA to grant SELECT.",
        )
    return _check("K2.09", "Landing receipt skipped", "high", "PASS", {"skipped": []})


def _safe_code(value: Any) -> str:
    if isinstance(value, str) and _CODE.fullmatch(value):
        return value
    return "bad_response"


def http_exchange(
    url: str,
    *,
    method: str,
    body: dict[str, Any] | None,
    bearer: str,
    opener: Any = None,
) -> tuple[int, str]:
    """Return ``(status, code)``. The body is not returned."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("content-type", "application/json")
    request.add_header("authorization", f"Bearer {bearer}")
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(request, timeout=10) as response:
            raw = response.read(2048)
            status = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as exc:
        raw = exc.read(2048)
        status = int(exc.code)
    except Exception:
        return 0, "network"
    try:
        payload = json.loads(raw.decode("utf-8", "replace") or "{}")
    except (UnicodeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        return status, "bad_response"
    if payload.get("ok") is True and status < 400:
        return status, "ok"
    return status, _safe_code(payload.get("error") or payload.get("detail") or "")


def api_check(source: dict[str, Any], *, opener: Any = None) -> dict[str, Any]:
    if not product_enabled("DMS_CONNECT_API"):
        return _skipped("K2.10", "DMS_CONNECT_API off")
    if not product_enabled("DMS_CONNECT_V2"):
        return _skipped("K2.10", "DMS_CONNECT_V2 off")
    base = os.environ.get("DMS_API_BASE", "").strip().rstrip("/")
    if not base:
        return _skipped("K2.10", "DMS_API_BASE unset")
    ref = str(source.get("ov_ref") or "").strip()
    if not ref:
        return _skipped("K2.10", "no ov_ref; the route rejects a raw secret")
    bearer = os.environ.get("DMS_SERVICE_BEARER", "")
    if not bearer:
        return _skipped("K2.10", "DMS_SERVICE_BEARER unset")
    port = source.get("port")
    if not isinstance(port, int) or isinstance(port, bool):
        return _skipped("K2.10", "port missing")
    body = {
        "kind": str(source.get("kind") or ""),
        "host": str(source.get("host") or ""),
        "port": port,
        "db": str(source.get("database") or source.get("db") or ""),
        "cred_ref": ref,
        "tls": bool(source.get("tls")),
    }
    status, code = http_exchange(
        base + "/v1/connect/test",
        method="POST",
        body=body,
        bearer=bearer,
        opener=opener,
    )
    list_status, list_code = http_exchange(
        base + "/v1/connect/sources",
        method="GET",
        body=None,
        bearer=bearer,
        opener=opener,
    )
    if code != "ok" or list_status >= 400 or list_status == 0:
        return _check(
            "K2.10",
            "Connect API test",
            "high",
            "FAIL",
            {"test": code, "sources": list_code, "status": status},
            "The connect route did not accept this source.",
            _spec("K2.10")[3],
        )
    return _check(
        "K2.10",
        "Connect API test",
        "high",
        "PASS",
        {"test": "ok", "sources": "ok"},
        source=_spec("K2.10")[3],
    )


def source_checks(
    facts: Facts,
    source: dict[str, Any],
    *,
    opener: Any = None,
) -> list[dict[str, Any]]:
    return [
        _login(facts),
        _tls(facts),
        _readonly(facts),
        _catalog(facts),
        _keys(facts),
        _schema(facts),
        _truncated(facts),
        _untyped(facts),
        _skipped_tables(facts, source),
        api_check(source, opener=opener),
    ]


def _fold(check_id: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    spec = _spec(check_id)
    if not parts:
        return _skipped(check_id, "no source in engagement.yaml")
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


def _receipt(facts_list: list[Facts], sources: list[dict[str, Any]]) -> dict[str, Any]:
    from dms_executor.bronze import duckdb_type_for
    from dms_executor.db_connector import DEFAULT_MAX_ROWS

    truncated: list[str] = []
    untyped: list[str] = []
    skipped: list[str] = []
    for facts, source in zip(facts_list, sources, strict=True):
        for table in facts.tables:
            if table.rows is not None and table.rows > DEFAULT_MAX_ROWS:
                truncated.append(f"{table.schema}.{table.name}")
        for column in facts.columns:
            if duckdb_type_for(column.data_type, column.precision, column.scale) is None:
                untyped.append(f"{column.schema}.{column.name}.{column.column}")
        requested = source.get("tables")
        if isinstance(requested, list):
            visible = {table.name.lower() for table in facts.tables}
            visible.update(f"{table.schema}.{table.name}".lower() for table in facts.tables)
            skipped.extend(str(name) for name in requested if str(name).lower() not in visible)
    return {
        "truncated": truncated,
        "untyped": untyped,
        "skipped": skipped,
        "method": "catalog_forecast",
    }


def build_checks(
    data: dict[str, Any],
    *,
    collect_fn: Any = None,
    probe_network: bool = True,
    opener: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reader = collect_fn or collect
    sources = sources_of(data)
    grouped: dict[str, list[dict[str, Any]]] = {spec[0]: [] for spec in _SPECS}
    gathered: list[Facts] = []
    if not sources:
        for spec in _SPECS:
            grouped[spec[0]].append(_skipped(spec[0], "no source in engagement.yaml"))
    for source in sources:
        facts = reader(source, password_for(source), probe_network=probe_network)
        gathered.append(facts)
        for item in source_checks(facts, source, opener=opener):
            grouped[item["id"]].append(item)
    checks = [_fold(spec[0], grouped[spec[0]]) for spec in _SPECS]
    return checks, _receipt(gathered, sources)


def run(
    directory: Path,
    data: dict[str, Any],
    *,
    only: set[str] | None = None,
    collect_fn: Any = None,
    probe_network: bool = True,
    opener: Any = None,
) -> dict[str, Any]:
    if not kit_enabled():
        raise _KitDisabled("fde_kit_disabled")
    started = now_z()
    checks, receipt = build_checks(
        data,
        collect_fn=collect_fn,
        probe_network=probe_network,
        opener=opener,
    )
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
        "kit": "connect",
        "stage": "K2",
        "engagement": name,
        "tenant_id": tenant,
        "dms_sha": git_sha(),
        "started_at": started,
        "finished_at": now_z(),
        "flags": flag_snapshot(),
        "status": rollup(objects),
        "checks": [item.to_dict() for item in objects],
        "metrics": {},
        "artifacts": ["receipts/connect.json"],
        "cost": {"ov_calls": 0, "usd": 0.0, "source": "OV /api/usage"},
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = directory / "runs" / f"{stamp}-connect"
    suffix = 2
    while run_dir.exists():
        run_dir = directory / "runs" / f"{stamp}-connect-{suffix}"
        suffix += 1
    secrets = secret_values(data)
    write_run(run_dir, report, secrets)
    receipt_dir = run_dir / "receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    cleaned = scrub_obj(receipt, secrets)
    (receipt_dir / "connect.json").write_text(
        json.dumps(cleaned, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    from kits.engage import cli

    return cli(run, argv)


if __name__ == "__main__":
    sys.exit(main())
