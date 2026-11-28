"""FDE-SCAN-01: data quality and PII report for one landed Space.

Standalone until the launcher (T1) registers it:

    DMS_FDE_KIT=1 python scripts/fde/kits/scan.py --engagement acme/

``DMS_FDE_KIT`` must be the digit ``1``. Any other value, including unset,
exits 2 with ``fde_kit_disabled`` and writes nothing.

The report body is ``netie.fde.report/1`` (plan section 4.3). When
``scripts/fde/report.py`` defines ``render_md``, that function renders the
markdown. Until then the renderer below is the same contract: markdown comes
only from the report dict.

No cell value is written. Counts, classes, and column names only. A password
named by ``password_env`` and secret fields on a connect receipt are scrubbed
with ``ask_audit_scrub`` plus a direct replace. Model routing is not used.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

SOURCE = "scripts/fde/kits/scan.py"
PII_SOURCE = "packages/core/dms_core/pii.py"
LAKE_SOURCE = "scripts/validate_lake.py"
CAP_SOURCE = "packages/executor/dms_executor/db_connector.py:45"
SCHEMA = "netie.fde.report/1"
DECISIONS_SCHEMA = "netie.fde.decisions/1"

# Text columns at or under this size are classified in full. Above it, a
# seeded 200-row hash sample is used.
# ponytail: a rare personal value outside that sample is missed. Upgrade:
# classify every non-null value, or push the count into SQL beside pii.py.
SAMPLE_CAP = 5000
SAMPLE_N = 200

# Extra copies at or above this share of the table are a high duplicate-load
# finding. Below it the finding stays low so a one-row collision is not high.
DUP_HIGH_RATE = 0.05

# Median ratio at or above this, across a short code column, is a mixed scale.
SCALE_RATIO = 3.0
SCALE_MIN_GROUP = 8

STATUS_RANK = {"FAIL": 0, "BLOCKED": 1, "MANUAL": 2, "SKIPPED": 3, "PASS": 4}
SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
SENSITIVE = frozenset({"email", "nric", "phone", "account", "passport", "address"})
BANNED_KEYS = frozenset(
    {"password", "passwd", "secret", "api_key", "access_key", "authorization", "token"}
)

INTEGER = re.compile(
    r"^(TINYINT|SMALLINT|INTEGER|INT|BIGINT|UBIGINT|UINTEGER|HUGEINT)", re.IGNORECASE
)
NUMERIC = re.compile(r"^(DECIMAL|NUMERIC|DOUBLE|FLOAT|REAL)", re.IGNORECASE)
TEMPORAL = re.compile(r"^(DATE|TIME|TIMESTAMP|DATETIME)", re.IGNORECASE)
TEXT = re.compile(r"^(VARCHAR|TEXT|STRING|CHAR|BPCHAR|NVARCHAR)", re.IGNORECASE)
_EMAIL = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
_NRIC = re.compile(r"\b\d{6}-?\d{2}-?\d{4}\b")
_CARD = re.compile(r"\b\d{13,16}\b")
_CAP_LINE = re.compile(r"^DEFAULT_MAX_ROWS = (\d[\d_]*)", re.M)

ROOT = Path(__file__).resolve().parents[3]


class KitDisabled(Exception):
    """``DMS_FDE_KIT`` is not the digit 1. The caller must write nothing."""


class KitConfig(Exception):
    """The engagement file cannot be scanned. The caller must write nothing."""


class Report(dict):
    """Section 4.3 body, plus decisions and secret strings kept off the JSON."""

    def __init__(
        self,
        body: dict[str, Any],
        *,
        engagement_dir: Path,
        decisions: dict[str, Any],
        secrets: list[str],
    ) -> None:
        super().__init__(body)
        self.engagement_dir = str(engagement_dir)
        self.decisions = decisions
        self.secrets = secrets


def kit_enabled() -> bool:
    """True only for the exact digit 1. Spaces, ``01`` and ``true`` stay off."""
    return os.environ.get("DMS_FDE_KIT") == "1"


def quote_ident(name: str) -> str:
    if "\x00" in name:
        raise ValueError("bad_ident")
    return '"' + name.replace('"', '""') + '"'


def default_max_rows() -> int:
    """The per-table extract cap. Read from the connector, not a second copy."""
    try:
        from dms_executor.db_connector import DEFAULT_MAX_ROWS

        return int(DEFAULT_MAX_ROWS)
    except Exception:
        text = (ROOT / "packages/executor/dms_executor/db_connector.py").read_text(encoding="utf-8")
        match = _CAP_LINE.search(text)
        if not match:
            return 500_000
        return int(match.group(1).replace("_", ""))


def git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        return out.strip() or "unknown"
    except Exception:
        return "unknown"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit_scrub():
    path = ROOT / "packages/core/dms_core/control_plane/ask_audit_scrub.py"
    spec = importlib.util.spec_from_file_location("fde_scan_ask_audit_scrub", path)
    if spec is None or spec.loader is None:
        return lambda text, sql=False: text
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod.scrub
    except Exception:
        return lambda text, sql=False: text


def scrub_str(text: str, secrets: list[str], scrubber: Any) -> str:
    out = text
    try:
        if len(out) <= 8000:
            out = scrubber(out)
    except Exception:
        pass
    for secret in secrets:
        if secret and len(secret) >= 8 and secret in out:
            out = out.replace(secret, "[redacted]")
    out = _EMAIL.sub("[redacted]", out)
    out = _NRIC.sub("[redacted]", out)
    out = _CARD.sub("[redacted]", out)
    return out


def scrub_obj(obj: Any, secrets: list[str], scrubber: Any) -> Any:
    """Walk a report and drop anything that is not a plain JSON value."""
    if isinstance(obj, str):
        return scrub_str(obj, secrets, scrubber)
    if isinstance(obj, dict):
        return {str(k): scrub_obj(v, secrets, scrubber) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_obj(v, secrets, scrubber) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return None
        return obj
    return None


def _shared_render():
    path = Path(__file__).resolve().parents[1] / "report.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("fde_report_schema", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    fn = getattr(mod, "render_md", None)
    return fn if callable(fn) else None


def render_md(report: dict[str, Any]) -> str:
    """Markdown from the report dict only. Failed checks keep the order given."""
    shared = _shared_render()
    if shared is not None:
        try:
            text = shared(report)
            if isinstance(text, str) and text.strip():
                return text if text.endswith("\n") else text + "\n"
        except Exception:
            pass
    lines = [
        "# FDE scan",
        "",
        f"- schema: {report.get('schema')}",
        f"- kit: {report.get('kit')}",
        f"- stage: {report.get('stage')}",
        f"- engagement: {report.get('engagement')}",
        f"- tenant_id: {report.get('tenant_id')}",
        f"- status: {report.get('status')}",
        f"- dms_sha: {report.get('dms_sha')}",
        f"- started_at: {report.get('started_at')}",
        f"- finished_at: {report.get('finished_at')}",
        "",
        "| id | severity | status | title |",
        "| --- | --- | --- | --- |",
    ]
    checks = list(report.get("checks") or [])
    for check in checks:
        title = str(check.get("title") or "").replace("|", "/")
        lines.append(
            f"| {check.get('id')} | {check.get('severity')} | {check.get('status')} | {title} |"
        )
    lines.extend(["", "## Failed items", ""])
    failed = [c for c in checks if c.get("status") == "FAIL"]
    if not failed:
        lines.append("No failed checks.")
        lines.append("")
    for check in failed:
        lines.append(f"### {check.get('id')} {check.get('title')}")
        lines.append("")
        lines.append(str(check.get("fix") or ""))
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(check.get("evidence") or {}, indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def rollup(checks: list[dict[str, Any]]) -> str:
    """Critical or high FAIL, then any FAIL, then BLOCKED. Never a bare PASS."""
    if not checks:
        return "SKIPPED"
    if any(c["status"] == "FAIL" and c["severity"] in ("critical", "high") for c in checks):
        return "FAIL"
    if any(c["status"] == "BLOCKED" for c in checks):
        return "BLOCKED"
    if any(c["status"] == "FAIL" for c in checks):
        return "FAIL"
    if any(c["status"] == "PASS" for c in checks):
        return "PASS"
    if any(c["status"] == "MANUAL" for c in checks):
        return "BLOCKED"
    return "SKIPPED"


def exit_for(status: str) -> int:
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 3, "SKIPPED": 2}.get(status, 2)


def sort_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        checks,
        key=lambda c: (
            STATUS_RANK.get(str(c.get("status")), 9),
            SEV_RANK.get(str(c.get("severity")), 9),
            str(c.get("id")),
        ),
    )


def make_check(
    cid: str,
    title: str,
    severity: str,
    status: str,
    evidence: dict[str, Any],
    fix: str,
    source: str,
) -> dict[str, Any]:
    return {
        "id": cid,
        "title": title,
        "severity": severity,
        "status": status,
        "evidence": evidence,
        "fix": fix,
        "source": source,
    }


def skipped(cid: str, title: str, reason: str, fix: str, source: str = SOURCE) -> dict[str, Any]:
    return make_check(cid, title, "info", "SKIPPED", {"reason": reason, "items": []}, fix, source)


def failed(
    cid: str, title: str, items: list[dict[str, Any]], fix: str, source: str = SOURCE
) -> dict[str, Any]:
    worst = min(items, key=lambda item: SEV_RANK.get(str(item.get("level")), 9))
    level = str(worst.get("level") or "high")
    return make_check(cid, title, level, "FAIL", {"items": items}, fix, source)


def passed_check(
    cid: str, title: str, evidence: dict[str, Any], fix: str, source: str = SOURCE
) -> dict[str, Any]:
    evidence.setdefault("items", [])
    return make_check(cid, title, "info", "PASS", evidence, fix, source)


def guard(cid: str, title: str, fix: str, fn: Any, source: str = SOURCE) -> dict[str, Any]:
    try:
        return fn()
    except Exception as exc:
        return make_check(
            cid,
            title,
            "high",
            "FAIL",
            {"exception": type(exc).__name__, "items": []},
            fix,
            source,
        )


FIX = {
    "pii": "Keep raw values out of reports. Decide filter or accept before a demo.",
    "nulls": "A column is mostly empty. Confirm the extract before a measure uses it.",
    "distinct": "Distinct counts are the spread of each column. No value is copied.",
    "orphans": "Child rows point at a missing parent. Map the key or accept the loss.",
    "duplicate_load": "Rows match once unique keys and time columns are set aside.",
    "mixed_unit": "A numeric column changes scale across a short code. Convert to one unit.",
    "casing": "The same code is stored in more than one case. Map it to one spelling.",
    "untyped_numeric": "A text column holds only numbers. Convert it before it is summed.",
    "lake": "A declared key or link failed on the lake. Fix it before a metric uses it.",
    "truncation": "Raise max_rows or agree a filtered extract before any accuracy claim.",
    "secret": "A secret-shaped value was counted. Remove it from the extract.",
    "secret_skip": "Secret-rule scan is not on this tree. Install it and rerun.",
}


def _banned_key(obj: Any) -> bool:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in BANNED_KEYS:
                return True
            if _banned_key(value):
                return True
    elif isinstance(obj, list):
        return any(_banned_key(item) for item in obj)
    return False


def _collect_secrets(obj: Any, secrets: list[str]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in BANNED_KEYS and isinstance(value, str):
                secrets.append(value)
            else:
                _collect_secrets(value, secrets)
    elif isinstance(obj, list):
        for item in obj:
            _collect_secrets(item, secrets)


def _resolve(path: Path) -> Path:
    if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}:
        return path.parent
    return path


def _as_path(root: Path, raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw.strip())
    if not path.is_absolute():
        path = root / path
    return path


def _first_warehouse(data: dict[str, Any], root: Path) -> Path | None:
    found: list[Any] = []
    space = data.get("space")
    if isinstance(space, dict):
        found.append(space.get("warehouse"))
    found.append(data.get("warehouse"))
    spaces = data.get("spaces")
    if isinstance(spaces, list):
        for item in spaces:
            if isinstance(item, dict):
                found.append(item.get("warehouse"))
    for raw in found:
        path = _as_path(root, raw)
        if path is not None:
            return path
    return None


def _receipt_path(data: dict[str, Any], root: Path) -> Path | None:
    receipts = data.get("receipts")
    named = None
    if isinstance(receipts, dict):
        named = receipts.get("connect")
    if named is None:
        named = data.get("connect_receipt")
    path = _as_path(root, named)
    if path is not None:
        return path
    direct = root / "receipts" / "connect.json"
    if direct.is_file():
        return direct
    runs = root / "runs"
    if not runs.is_dir():
        return None
    found = sorted(runs.glob("*/receipts/connect.json"))
    found += sorted(runs.glob("*-connect/report.json"))
    return found[-1] if found else None


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise KitConfig()
    if _banned_key(data):
        raise KitConfig()
    return data


def _rel(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return path.name


def _num(obj: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        val = obj.get(key)
        if isinstance(val, bool) or val is None:
            continue
        if isinstance(val, (int, float)):
            return int(val)
        if isinstance(val, str) and val.strip().isdigit():
            return int(val.strip())
    return None


def _table_of(obj: dict[str, Any]) -> str | None:
    for key in ("table", "bronze_table", "name"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip() and len(val) <= 256:
            return val.strip()
    src = obj.get("source")
    if (
        isinstance(src, str)
        and src.strip()
        and "://" not in src
        and "@" not in src
        and len(src) <= 128
    ):
        return src.strip()
    return None


def _is_truncated(obj: dict[str, Any]) -> bool:
    val = obj.get("truncated")
    if val is True:
        return True
    return isinstance(val, str) and val.strip().lower() == "true"


def _walk_trunc(obj: Any, acc: list[dict[str, Any]], check_id: str | None) -> None:
    if isinstance(obj, dict):
        cid = check_id
        raw = obj.get("id")
        if isinstance(raw, str) and raw.startswith("K"):
            cid = raw
        if _is_truncated(obj):
            name = _table_of(obj)
            if name:
                acc.append(
                    {
                        "table": name,
                        "rows": _num(obj, ("source_rows", "rows", "declared_rows", "row_count")),
                        "cap": _num(obj, ("cap", "max_rows")),
                        "source_check": cid,
                    }
                )
        for value in obj.values():
            _walk_trunc(value, acc, cid)
    elif isinstance(obj, list):
        for item in obj:
            _walk_trunc(item, acc, check_id)


def truncation_items(payload: Any, cap: int) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    _walk_trunc(payload, found, None)
    best: dict[str, dict[str, Any]] = {}
    for item in found:
        name = item["table"]
        rows = item["rows"]
        prev = best.get(name)
        if prev is None or (rows or 0) > (prev["rows"] or 0):
            item["cap"] = cap if item["cap"] is None else item["cap"]
            item["level"] = "critical"
            item["kind"] = "truncation"
            item["column"] = None
            best[name] = item
    return [best[key] for key in sorted(best)]


def _links(raw: Any) -> list[tuple[str, str, str, str]]:
    out: list[tuple[str, str, str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 4:
            parts = [str(x) for x in item[:4]]
            out.append((parts[0], parts[1], parts[2], parts[3]))
        elif isinstance(item, dict):
            child = item.get("from_table") or item.get("child")
            col = item.get("from_column") or item.get("column")
            parent = item.get("to_table") or item.get("parent")
            pcol = item.get("to_column") or item.get("parent_column")
            if all(isinstance(x, str) and x for x in (child, col, parent, pcol)):
                out.append((str(child), str(col), str(parent), str(pcol)))
    return out


class Rel:
    def __init__(self, schema: str, name: str, display: str, sql: str) -> None:
        self.schema = schema
        self.name = name
        self.display = display
        self.sql = sql


def list_tables(con: Any) -> list[Rel]:
    rows = con.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type IN ('BASE TABLE', 'VIEW')
          AND table_schema NOT IN ('information_schema', 'pg_catalog')
        """
    ).fetchall()
    out: list[Rel] = []
    for schema, name in rows:
        schema, name = str(schema), str(name)
        if name.startswith("_") or schema.startswith("pg_"):
            continue
        display = name if schema in ("main", "public") else f"{schema}.{name}"
        sql = f"{quote_ident(schema)}.{quote_ident(name)}"
        out.append(Rel(schema, name, display, sql))
    return out


def column_stats(con: Any, rel: Rel) -> dict[str, Any]:
    described = con.execute(f"DESCRIBE SELECT * FROM {rel.sql}").fetchall()
    rows = int(con.execute(f"SELECT COUNT(*) FROM {rel.sql}").fetchone()[0])
    cols: dict[str, Any] = {"__rows": rows}
    for desc in described:
        name, ctype = str(desc[0]), str(desc[1])
        qn = quote_ident(name)
        nulls, distinct = con.execute(
            f"SELECT COUNT(*) - COUNT({qn}), COUNT(DISTINCT {qn}) FROM {rel.sql}"
        ).fetchone()
        st: dict[str, Any] = {
            "type": ctype,
            "nulls": int(nulls or 0),
            "distinct": int(distinct or 0),
            "temporal": bool(TEMPORAL.match(ctype)),
            "integer": bool(INTEGER.match(ctype)),
            "numeric": bool(INTEGER.match(ctype) or NUMERIC.match(ctype)),
            "text": bool(TEXT.match(ctype)),
        }
        st["unique"] = rows > 0 and st["nulls"] == 0 and st["distinct"] == rows
        if st["integer"] and rows:
            lo = con.execute(f"SELECT MIN({qn}) FROM {rel.sql}").fetchone()[0]
            if isinstance(lo, int) and not isinstance(lo, bool):
                st["min"] = int(lo)
        cols[name] = st
    return cols


def _sample(con: Any, rel: Rel, column: str, non_null: int, seed: int) -> list[Any]:
    qn = quote_ident(column)
    if non_null <= SAMPLE_CAP:
        sql = f"SELECT {qn} FROM {rel.sql} WHERE {qn} IS NOT NULL"
        return [row[0] for row in con.execute(sql).fetchall()]
    sql = (
        f"SELECT {qn} FROM {rel.sql} WHERE {qn} IS NOT NULL "
        f"ORDER BY hash(CAST({qn} AS VARCHAR) || '|' || '{int(seed)}') "
        f"LIMIT {SAMPLE_N}"
    )
    return [row[0] for row in con.execute(sql).fetchall()]


def count_dup(con: Any, rel: Rel, keep: list[str]) -> tuple[int, int]:
    if not keep:
        rows = int(con.execute(f"SELECT COUNT(*) FROM {rel.sql}").fetchone()[0])
        if rows > 1:
            return rows - 1, 1
        return 0, 0
    cols = ", ".join(quote_ident(c) for c in keep)
    extra, groups = con.execute(
        f"""
        SELECT COALESCE(SUM(n - 1), 0), COUNT(*)
        FROM (
          SELECT COUNT(*) AS n
          FROM {rel.sql}
          GROUP BY {cols}
          HAVING COUNT(*) > 1
        ) s
        """
    ).fetchone()
    return int(extra or 0), int(groups or 0)


def pii_check(con: Any, rels: list[Rel], stats: dict[str, dict[str, Any]], seed: int) -> dict:
    from dms_core.pii import classify_column

    items: list[dict[str, Any]] = []
    scanned = 0
    for rel in rels:
        cols = stats[rel.display]
        for name, st in cols.items():
            if name.startswith("__"):
                continue
            scanned += 1
            non_null = cols["__rows"] - st["nulls"]
            values: list[Any] = []
            if st["text"] and non_null > 0:
                values = _sample(con, rel, name, non_null, seed)
            kind = classify_column(name, values, table=rel.display)
            hits = 0
            sampled = len(values)
            if kind:
                hits = sum(
                    1
                    for value in values
                    if classify_column(name, (value,), table=rel.display) == kind
                )
            values.clear()
            if not kind:
                continue
            items.append(
                {
                    "table": rel.display,
                    "column": name,
                    "class": kind,
                    "non_null": non_null,
                    "sample_rows": sampled,
                    "sample_hits": hits,
                    "level": "high" if kind in SENSITIVE else "medium",
                    "kind": "pii",
                }
            )
    items.sort(key=lambda item: (item["table"], item["column"], item["class"]))
    if items:
        return failed("K4.01", "PII class per column", items, FIX["pii"], PII_SOURCE)
    return passed_check(
        "K4.01",
        "PII class per column",
        {"columns_scanned": scanned, "pii_columns": 0},
        FIX["pii"],
        PII_SOURCE,
    )


def null_check(rels: list[Rel], stats: dict[str, dict[str, Any]]) -> dict:
    items = []
    max_rate = 0.0
    seen = 0
    for rel in rels:
        cols = stats[rel.display]
        rows = int(cols["__rows"])
        for name, st in cols.items():
            if name.startswith("__") or rows <= 0:
                continue
            seen += 1
            rate = st["nulls"] / rows
            if rate > max_rate:
                max_rate = rate
            if rate >= 0.5:
                items.append(
                    {
                        "table": rel.display,
                        "column": name,
                        "nulls": st["nulls"],
                        "rows": rows,
                        "null_rate": round(rate, 4),
                        "level": "high" if rate >= 0.9 else "medium",
                        "kind": "nulls",
                    }
                )
    items.sort(key=lambda item: (item["table"], item["column"]))
    if items:
        return failed("K4.02", "Null rates", items, FIX["nulls"])
    return passed_check(
        "K4.02",
        "Null rates",
        {"columns": seen, "max_null_rate": round(max_rate, 4)},
        FIX["nulls"],
    )


def distinct_check(rels: list[Rel], stats: dict[str, dict[str, Any]]) -> dict:
    distincts = [
        st["distinct"]
        for cols in stats.values()
        for name, st in cols.items()
        if not name.startswith("__")
    ]
    evidence: dict[str, Any] = {
        "columns": len(distincts),
        "tables": len(rels),
        "items": [],
    }
    if distincts:
        evidence["min_distinct"] = min(distincts)
        evidence["max_distinct"] = max(distincts)
    return passed_check("K4.03", "Distinct counts", evidence, FIX["distinct"])


def _infer(con: Any, rels: list[Rel], stats: dict[str, dict[str, Any]]) -> tuple[list, str | None]:
    try:
        import fde_pipeline as fp
    except Exception as exc:
        return [], type(exc).__name__
    prof: dict[str, Any] = {}
    pk: dict[str, list[str]] = {}
    for rel in rels:
        if rel.schema not in ("main", "public"):
            continue
        cols = stats[rel.display]
        columns = {}
        for name, st in cols.items():
            if name.startswith("__"):
                continue
            columns[name] = {
                "type": st["type"],
                "nulls": st["nulls"],
                "distinct": st["distinct"],
                "min": st.get("min"),
            }
        prof[rel.name] = {"rows": cols["__rows"], "columns": columns}
        unique_ints = [
            name
            for name, st in cols.items()
            if not name.startswith("__") and st["unique"] and st["integer"]
        ]
        if unique_ints:
            pk[rel.name] = [unique_ints[0]]
    try:
        found = fp._infer_fks(con, prof, pk)
    except Exception as exc:
        return [], type(exc).__name__
    out = []
    for row in found:
        if len(row) >= 4:
            out.append((str(row[0]), str(row[1]), str(row[2]), str(row[3])))
    return out, None


def orphan_check(
    con: Any,
    rels: list[Rel],
    stats: dict[str, dict[str, Any]],
    verified: list[tuple[str, str, str, str]],
    proposed: list[tuple[str, str, str, str]],
) -> dict:
    if not verified and not proposed:
        proposed, err = _infer(con, rels, stats)
        if err:
            return skipped(
                "K4.04",
                "Orphan rate on links",
                f"no links declared; inference {err}",
                FIX["orphans"],
            )
    if not verified and not proposed:
        return skipped(
            "K4.04",
            "Orphan rate on links",
            "no verified or proposed links",
            FIX["orphans"],
        )
    by_name = {}
    for rel in rels:
        by_name.setdefault(rel.display, rel)
        by_name.setdefault(rel.name, rel)
    items = []
    for link, origin in [(link, "verified") for link in verified] + [
        (link, "proposed") for link in proposed
    ]:
        child, col, parent, pcol = link
        child_rel = by_name.get(child)
        parent_rel = by_name.get(parent)
        if child_rel is None or parent_rel is None:
            items.append(
                {
                    "table": child,
                    "column": col,
                    "parent": parent,
                    "parent_column": pcol,
                    "link": origin,
                    "level": "medium",
                    "kind": "orphans",
                    "missing": True,
                }
            )
            continue
        child_cols = stats.get(child_rel.display, {})
        parent_cols = stats.get(parent_rel.display, {})
        if col not in child_cols or pcol not in parent_cols:
            items.append(
                {
                    "table": child_rel.display,
                    "column": col,
                    "parent": parent_rel.display,
                    "parent_column": pcol,
                    "link": origin,
                    "level": "medium",
                    "kind": "orphans",
                    "missing": True,
                }
            )
            continue
        qc, qp = quote_ident(col), quote_ident(pcol)
        non_null, orphans = con.execute(
            f"""
            SELECT
              SUM(CASE WHEN c.{qc} IS NOT NULL THEN 1 ELSE 0 END),
              SUM(CASE WHEN c.{qc} IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM {parent_rel.sql} p WHERE p.{qp} = c.{qc}
                  ) THEN 1 ELSE 0 END)
            FROM {child_rel.sql} c
            """
        ).fetchone()
        non_null = int(non_null or 0)
        orphans = int(orphans or 0)
        if orphans <= 0:
            continue
        rate = orphans / non_null if non_null else 0.0
        level = "high" if origin == "verified" else "medium"
        items.append(
            {
                "table": child_rel.display,
                "column": col,
                "parent": parent_rel.display,
                "parent_column": pcol,
                "orphans": orphans,
                "child_rows": non_null,
                "rate": round(rate, 4),
                "link": origin,
                "level": level,
                "kind": "orphans",
            }
        )
    items.sort(key=lambda item: (item["table"], str(item["column"]), item["link"]))
    if items:
        return failed("K4.04", "Orphan rate on links", items, FIX["orphans"])
    return passed_check(
        "K4.04",
        "Orphan rate on links",
        {"verified": len(verified), "proposed": len(proposed)},
        FIX["orphans"],
    )


def duplicate_check(con: Any, rels: list[Rel], stats: dict[str, dict[str, Any]]) -> dict:
    # ponytail: a load key is a unique integer, and a load time is a time type.
    # A unique text column stays in the row, so a name is not treated as a load id.
    # A repeated load that shares one batch id, with no per-row time column, is
    # not separated. Upgrade: drop one extra low-cardinality integer when that
    # drop creates a duplicate group this pass missed.
    items = []
    for rel in rels:
        cols = stats[rel.display]
        rows = int(cols["__rows"])
        if rows < 2:
            continue
        names = [name for name in cols if not name.startswith("__")]
        exempt = [
            name
            for name in names
            if cols[name]["temporal"] or (cols[name]["unique"] and cols[name]["integer"])
        ]
        keep = [name for name in names if name not in exempt]
        extra, groups = count_dup(con, rel, keep)
        if extra <= 0:
            continue
        rate = extra / rows
        items.append(
            {
                "table": rel.display,
                "column": None,
                "kind": "duplicate_load",
                "level": "high" if rate >= DUP_HIGH_RATE else "low",
                "extra_rows": extra,
                "rows": rows,
                "groups": groups,
                "exempt_columns": len(exempt),
            }
        )
    items.sort(key=lambda item: item["table"])
    if items:
        return failed("K4.05", "Duplicate-load candidates", items, FIX["duplicate_load"])
    return passed_check(
        "K4.05", "Duplicate-load candidates", {"tables": len(rels)}, FIX["duplicate_load"]
    )


def _unit_code(con: Any, rel: Rel, column: str) -> bool:
    qn = quote_ident(column)
    lo, hi, n, matched = con.execute(
        f"""
        SELECT MIN(length(trim(CAST({qn} AS VARCHAR)))),
               MAX(length(trim(CAST({qn} AS VARCHAR)))),
               COUNT(*),
               SUM(CASE WHEN regexp_full_match(trim(CAST({qn} AS VARCHAR)), '^[A-Za-z]{{2,4}}$')
                        THEN 1 ELSE 0 END)
        FROM {rel.sql}
        WHERE {qn} IS NOT NULL AND trim(CAST({qn} AS VARCHAR)) <> ''
        """
    ).fetchone()
    if not n or lo is None or hi is None:
        return False
    return int(lo) == int(hi) and 2 <= int(lo) <= 4 and int(matched or 0) == int(n)


def _scale(con: Any, rel: Rel, code: str, num: str) -> tuple[float | None, int]:
    qc, qn = quote_ident(code), quote_ident(num)
    rows = con.execute(
        f"""
        SELECT median({qn}), COUNT(*)
        FROM {rel.sql}
        WHERE {qc} IS NOT NULL AND {qn} IS NOT NULL
        GROUP BY {qc}
        """
    ).fetchall()
    usable: list[float] = []
    for med, cnt in rows:
        if cnt is None or int(cnt) < SCALE_MIN_GROUP or med is None:
            continue
        try:
            usable.append(float(med))
        except (TypeError, ValueError):
            continue
    if len(usable) < 2 or min(usable) <= 0:
        return None, len(usable)
    return max(usable) / min(usable), len(usable)


def _embedded(con: Any, rel: Rel, column: str) -> tuple[int, int, int]:
    qn = quote_ident(column)
    pat = r"^-?[0-9]+([.][0-9]+)?[ ]+[A-Za-z]{1,12}$"
    n, matched = con.execute(
        f"""
        SELECT COUNT(*),
               SUM(CASE WHEN regexp_full_match(trim(CAST({qn} AS VARCHAR)), '{pat}')
                        THEN 1 ELSE 0 END)
        FROM {rel.sql}
        WHERE {qn} IS NOT NULL
        """
    ).fetchone()
    tokens = con.execute(
        f"""
        SELECT COUNT(DISTINCT upper(regexp_extract(trim(CAST({qn} AS VARCHAR)),
                                                   '([A-Za-z]{{1,12}})$', 1)))
        FROM {rel.sql}
        WHERE {qn} IS NOT NULL
          AND regexp_full_match(trim(CAST({qn} AS VARCHAR)), '{pat}')
        """
    ).fetchone()[0]
    return int(n or 0), int(matched or 0), int(tokens or 0)


def mixed_check(con: Any, rels: list[Rel], stats: dict[str, dict[str, Any]]) -> dict:
    items: list[dict[str, Any]] = []
    for rel in rels:
        cols = stats[rel.display]
        rows = int(cols["__rows"])
        if rows < SCALE_MIN_GROUP:
            continue
        texts: list[str] = []
        numbers: list[str] = []
        for name, st in cols.items():
            if name.startswith("__"):
                continue
            non_null = rows - st["nulls"]
            if (
                st["text"]
                and 2 <= st["distinct"] <= 16
                and non_null >= SCALE_MIN_GROUP
                and st["distinct"] <= 0.5 * non_null
            ):
                texts.append(name)
            numeric = st["numeric"] and not st["unique"] and st["distinct"] >= 2
            if numeric and non_null >= SCALE_MIN_GROUP:
                numbers.append(name)
        unit_codes = [name for name in texts if _unit_code(con, rel, name)]
        for code in unit_codes:
            for num in numbers:
                ratio, groups = _scale(con, rel, code, num)
                if groups < 2:
                    continue
                if ratio is not None and ratio >= SCALE_RATIO:
                    signal, level_ratio = "scale", round(ratio, 3)
                else:
                    signal, level_ratio = "shape", None if ratio is None else round(ratio, 3)
                items.append(
                    {
                        "table": rel.display,
                        "column": num,
                        "code_column": code,
                        "kind": "mixed_unit",
                        "signal": signal,
                        "ratio": level_ratio,
                        "level": "high",
                    }
                )
        for name in texts:
            siblings = [other for other in texts if other != name]
            if not siblings:
                continue
            try:
                n, matched, tokens = _embedded(con, rel, name)
            except Exception:
                continue
            if n >= SCALE_MIN_GROUP and matched / n >= 0.8 and tokens >= 2:
                items.append(
                    {
                        "table": rel.display,
                        "column": name,
                        "code_column": siblings[0],
                        "kind": "mixed_unit",
                        "signal": "embedded",
                        "level": "high",
                    }
                )
    items.sort(key=lambda item: (item["table"], str(item["column"]), str(item.get("code_column"))))
    if items:
        return failed("K4.06", "Mixed unit scale", items, FIX["mixed_unit"])
    return passed_check("K4.06", "Mixed unit scale", {"tables": len(rels)}, FIX["mixed_unit"])


def casing_check(con: Any, rels: list[Rel], stats: dict[str, dict[str, Any]]) -> dict:
    items = []
    for rel in rels:
        cols = stats[rel.display]
        for name, st in cols.items():
            if name.startswith("__") or not st["text"]:
                continue
            if not 2 <= st["distinct"] <= 40:
                continue
            qn = quote_ident(name)
            raw, folded = con.execute(
                f"""
                SELECT COUNT(DISTINCT trim(CAST({qn} AS VARCHAR))),
                       COUNT(DISTINCT lower(trim(CAST({qn} AS VARCHAR))))
                FROM {rel.sql}
                WHERE {qn} IS NOT NULL AND trim(CAST({qn} AS VARCHAR)) <> ''
                """
            ).fetchone()
            if raw and folded and int(raw) > int(folded):
                items.append(
                    {
                        "table": rel.display,
                        "column": name,
                        "kind": "casing",
                        "level": "high",
                        "distinct": int(raw),
                        "distinct_folded": int(folded),
                    }
                )
    items.sort(key=lambda item: (item["table"], item["column"]))
    if items:
        return failed("K4.07", "Code casing drift", items, FIX["casing"])
    return passed_check("K4.07", "Code casing drift", {"tables": len(rels)}, FIX["casing"])


def _registry_untyped(con: Any) -> list[dict[str, Any]]:
    try:
        present = con.execute(
            """
            SELECT table_schema
            FROM information_schema.columns
            WHERE table_name = '_ingest_registry' AND column_name = 'untyped_numeric'
            """
        ).fetchone()
    except Exception:
        return []
    if not present:
        return []
    rel = f"{quote_ident(str(present[0]))}.{quote_ident('_ingest_registry')}"
    try:
        rows = con.execute(
            f"SELECT table_name, untyped_numeric FROM {rel} WHERE untyped_numeric IS NOT NULL"
        ).fetchall()
    except Exception:
        return []
    items = []
    for table_name, raw_cols in rows:
        for col in str(raw_cols).split(","):
            col = col.strip()
            if not col:
                continue
            items.append(
                {
                    "table": str(table_name),
                    "column": col,
                    "kind": "untyped_numeric",
                    "level": "high",
                    "signal": "registry",
                }
            )
    return items


def untyped_check(con: Any, rels: list[Rel], stats: dict[str, dict[str, Any]]) -> dict:
    items = _registry_untyped(con)
    for rel in rels:
        cols = stats[rel.display]
        rows = int(cols["__rows"])
        for name, st in cols.items():
            if name.startswith("__") or not st["text"]:
                continue
            non_null = rows - st["nulls"]
            if non_null < 10:
                continue
            qn = quote_ident(name)
            bad, n = con.execute(
                f"""
                SELECT
                  SUM(CASE WHEN NOT regexp_full_match(trim(CAST({qn} AS VARCHAR)),
                        '^-?[0-9]+([.][0-9]+)?$') THEN 1 ELSE 0 END),
                  COUNT(*)
                FROM {rel.sql}
                WHERE {qn} IS NOT NULL AND trim(CAST({qn} AS VARCHAR)) <> ''
                """
            ).fetchone()
            if int(n or 0) >= 10 and int(bad or 0) == 0:
                items.append(
                    {
                        "table": rel.display,
                        "column": name,
                        "kind": "untyped_numeric",
                        "level": "high",
                        "non_null": int(n),
                        "signal": "values",
                    }
                )
    items.sort(key=lambda item: (str(item["table"]), str(item["column"])))
    if items:
        return failed("K4.08", "Untyped numeric columns", items, FIX["untyped_numeric"])
    return passed_check(
        "K4.08",
        "Untyped numeric columns",
        {"tables": len(rels)},
        FIX["untyped_numeric"],
    )


def _load_validate_lake() -> Any:
    path = ROOT / "scripts" / "validate_lake.py"
    spec = importlib.util.spec_from_file_location("fde_scan_validate_lake", path)
    if spec is None or spec.loader is None:
        raise ImportError("validate_lake")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def lake_check(manifest: Path | None, lake_root: Path | None) -> dict:
    if manifest is None:
        return skipped("K4.09", "Lake promise checks", "no lake manifest", FIX["lake"], LAKE_SOURCE)
    if not manifest.is_file():
        return skipped(
            "K4.09",
            "Lake promise checks",
            "lake manifest file is missing",
            FIX["lake"],
            LAKE_SOURCE,
        )
    import duckdb

    mod = _load_validate_lake()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return skipped(
            "K4.09",
            "Lake promise checks",
            "lake manifest is not a list",
            FIX["lake"],
            LAKE_SOURCE,
        )
    root = lake_root or manifest.parent
    con = duckdb.connect()
    items: list[dict[str, Any]] = []
    try:
        for db in data:
            report = mod.validate_database(con, db, root=root)
            for finding in report.get("findings") or []:
                items.append(
                    {
                        "table": finding.get("subject"),
                        "column": None,
                        "kind": "lake",
                        "level": "high",
                        "lake_check": finding.get("check"),
                    }
                )
    finally:
        con.close()
    items.sort(key=lambda item: (str(item["table"]), str(item["lake_check"])))
    if items:
        return failed("K4.09", "Lake promise checks", items, FIX["lake"], LAKE_SOURCE)
    return passed_check(
        "K4.09",
        "Lake promise checks",
        {"databases": len(data)},
        FIX["lake"],
        LAKE_SOURCE,
    )


def truncation_check(payload: Any, receipt: str | None, present: bool) -> dict:
    if not present:
        return skipped(
            "K4.10",
            "Row-cap truncation",
            "no connect receipt",
            FIX["truncation"],
            CAP_SOURCE,
        )
    cap = default_max_rows()
    items = truncation_items(payload, cap)
    for item in items:
        item["receipt"] = receipt
    if items:
        return failed("K4.10", "Row-cap truncation", items, FIX["truncation"], CAP_SOURCE)
    return passed_check(
        "K4.10",
        "Row-cap truncation",
        {"truncated_tables": 0, "receipt": receipt},
        FIX["truncation"],
        CAP_SOURCE,
    )


def secret_check(con: Any, rels: list[Rel], stats: dict[str, dict[str, Any]], seed: int) -> dict:
    try:
        import importlib

        mod = importlib.import_module("dms_core.governance.scan")
        scan_text = mod.scan_text
    except Exception:
        return skipped(
            "K4.11",
            "Secret-rule scan",
            "governance scan module is not on this tree",
            FIX["secret_skip"],
        )
    items: list[dict[str, Any]] = []
    scanned = 0
    for rel in rels:
        cols = stats[rel.display]
        rows = int(cols["__rows"])
        for name, st in cols.items():
            if name.startswith("__") or not st["text"]:
                continue
            non_null = rows - st["nulls"]
            if non_null <= 0:
                continue
            scanned += 1
            values = _sample(con, rel, name, non_null, seed)
            counts: dict[str, int] = {}
            for value in values:
                try:
                    types, _redacted = scan_text("" if value is None else str(value))
                except Exception:
                    types = ()
                for kind in types:
                    if str(kind).startswith("secret:"):
                        counts[str(kind)] = counts.get(str(kind), 0) + 1
            values.clear()
            for kind in sorted(counts):
                items.append(
                    {
                        "table": rel.display,
                        "column": name,
                        "kind": "secret",
                        "secret_type": kind,
                        "hits": counts[kind],
                        "level": "high",
                    }
                )
    if items:
        return failed("K4.11", "Secret-rule scan", items, FIX["secret"])
    return passed_check(
        "K4.11",
        "Secret-rule scan",
        {"columns_scanned": scanned, "secret_hits": 0},
        FIX["secret"],
    )


def _skip_data(reason: str) -> list[dict[str, Any]]:
    titles = [
        ("K4.01", "PII class per column", FIX["pii"], PII_SOURCE),
        ("K4.02", "Null rates", FIX["nulls"], SOURCE),
        ("K4.03", "Distinct counts", FIX["distinct"], SOURCE),
        ("K4.04", "Orphan rate on links", FIX["orphans"], SOURCE),
        ("K4.05", "Duplicate-load candidates", FIX["duplicate_load"], SOURCE),
        ("K4.06", "Mixed unit scale", FIX["mixed_unit"], SOURCE),
        ("K4.07", "Code casing drift", FIX["casing"], SOURCE),
        ("K4.08", "Untyped numeric columns", FIX["untyped_numeric"], SOURCE),
    ]
    return [skipped(cid, title, reason, fix, source) for cid, title, fix, source in titles]


def _high_findings(checks: list[dict[str, Any]]) -> int:
    n = 0
    for check in checks:
        if check["status"] != "FAIL":
            continue
        items = check["evidence"].get("items") if isinstance(check["evidence"], dict) else None
        if items:
            n += sum(1 for item in items if item.get("level") in ("high", "critical"))
        elif check["severity"] in ("high", "critical"):
            n += 1
    return n


def action_for(item: dict[str, Any]) -> str:
    kind = item.get("kind")
    if kind == "pii":
        return "accept" if item.get("class") in ("name", "dob") else "filter"
    return {
        "nulls": "accept",
        "orphans": "map",
        "duplicate_load": "filter",
        "mixed_unit": "convert",
        "casing": "map",
        "untyped_numeric": "convert",
        "truncation": "filter",
        "lake": "map",
        "secret": "filter",
    }.get(str(kind), "filter")


def build_decisions(
    *,
    engagement: str,
    tenant_id: str,
    seed: int,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = []
    for check in checks:
        if check["status"] != "FAIL":
            continue
        items = []
        if isinstance(check["evidence"], dict):
            items = list(check["evidence"].get("items") or [])
        if not items:
            rows.append(
                {
                    "id": check["id"],
                    "table": None,
                    "column": None,
                    "kind": "error",
                    "action": "filter",
                    "level": check["severity"],
                }
            )
            continue
        for item in items:
            row = {
                "id": check["id"],
                "table": item.get("table"),
                "column": item.get("column"),
                "kind": item.get("kind") or check["id"],
                "action": action_for(item),
                "level": item.get("level"),
            }
            if item.get("class"):
                row["class"] = item["class"]
            rows.append(row)
    rows.sort(
        key=lambda row: (str(row["id"]), str(row["table"]), str(row["column"]), str(row["kind"]))
    )
    return {
        "schema": DECISIONS_SCHEMA,
        "engagement": engagement,
        "tenant_id": tenant_id,
        "seed": seed,
        "rows": rows,
    }


def _flags(data: dict[str, Any]) -> dict[str, str]:
    flags = {"DMS_FDE_KIT": "1"}
    listed = data.get("flags_on")
    if isinstance(listed, list):
        for name in listed:
            if not isinstance(name, str) or not name.strip():
                continue
            key = name.strip()
            flags[key] = "1" if os.environ.get(key) == "1" else "off"
    return flags


def _password_secrets(data: dict[str, Any]) -> list[str]:
    names = []
    if isinstance(data.get("password_env"), str):
        names.append(data["password_env"])
    source = data.get("source")
    if isinstance(source, dict) and isinstance(source.get("password_env"), str):
        names.append(source["password_env"])
    secrets = []
    for name in names:
        value = os.environ.get(name)
        if value:
            secrets.append(value)
    return secrets


def _finish(
    *,
    engagement_dir: Path,
    engagement: str,
    tenant_id: str,
    seed: int,
    started: str,
    flags: dict[str, str],
    checks: list[dict[str, Any]],
    secrets: list[str],
    tables: int,
    columns: int,
) -> Report:
    ordered = sort_checks(checks)
    status = rollup(ordered)
    body = {
        "schema": SCHEMA,
        "kit": "scan",
        "stage": "K4",
        "engagement": engagement,
        "tenant_id": tenant_id,
        "dms_sha": git_sha(),
        "started_at": started,
        "finished_at": _now(),
        "flags": flags,
        "status": status,
        "checks": ordered,
        "metrics": {
            "seed": seed,
            "tables": tables,
            "columns": columns,
            "high_findings": _high_findings(ordered),
        },
        "artifacts": ["decisions.yaml", "report.json", "report.md"],
        "cost": {"ov_calls": 0, "usd": 0.0, "source": "not read"},
    }
    decisions = build_decisions(
        engagement=engagement, tenant_id=tenant_id, seed=seed, checks=ordered
    )
    scrubber = _audit_scrub()
    clean = scrub_obj(body, secrets, scrubber)
    clean_decisions = scrub_obj(decisions, secrets, scrubber)
    return Report(
        clean,
        engagement_dir=engagement_dir,
        decisions=clean_decisions,
        secrets=secrets,
    )


def _scan_warehouse(
    con: Any,
    rels: list[Rel],
    stats: dict[str, dict[str, Any]],
    seed: int,
    verified: list[tuple[str, str, str, str]],
    proposed: list[tuple[str, str, str, str]],
) -> list[dict[str, Any]]:
    checks = [
        guard(
            "K4.01",
            "PII class per column",
            FIX["pii"],
            lambda: pii_check(con, rels, stats, seed),
            PII_SOURCE,
        ),
        guard("K4.02", "Null rates", FIX["nulls"], lambda: null_check(rels, stats)),
        guard("K4.03", "Distinct counts", FIX["distinct"], lambda: distinct_check(rels, stats)),
        guard(
            "K4.04",
            "Orphan rate on links",
            FIX["orphans"],
            lambda: orphan_check(con, rels, stats, verified, proposed),
        ),
        guard(
            "K4.05",
            "Duplicate-load candidates",
            FIX["duplicate_load"],
            lambda: duplicate_check(con, rels, stats),
        ),
        guard(
            "K4.06",
            "Mixed unit scale",
            FIX["mixed_unit"],
            lambda: mixed_check(con, rels, stats),
        ),
        guard("K4.07", "Code casing drift", FIX["casing"], lambda: casing_check(con, rels, stats)),
        guard(
            "K4.08",
            "Untyped numeric columns",
            FIX["untyped_numeric"],
            lambda: untyped_check(con, rels, stats),
        ),
        guard(
            "K4.11",
            "Secret-rule scan",
            FIX["secret"],
            lambda: secret_check(con, rels, stats, seed),
        ),
    ]
    return checks


def _ctx_path(ctx: Any) -> Path:
    if isinstance(ctx, dict):
        raw = ctx.get("engagement")
        seed = ctx.get("seed")
    else:
        raw = getattr(ctx, "engagement", None)
        seed = getattr(ctx, "seed", None)
    if raw is None:
        raise KitConfig()
    return _resolve(Path(str(raw))), seed


def run(ctx: Any) -> Report:
    """Scan one engagement. Raises ``KitDisabled`` or ``KitConfig`` before any write."""
    if not kit_enabled():
        raise KitDisabled()
    try:
        return _run(ctx)
    except (KitDisabled, KitConfig):
        raise
    except Exception as exc:
        engagement_dir, _seed = _safe_dir(ctx)
        return _finish(
            engagement_dir=engagement_dir,
            engagement=engagement_dir.name,
            tenant_id=engagement_dir.name,
            seed=1,
            started=_now(),
            flags={"DMS_FDE_KIT": "1"},
            checks=[
                make_check(
                    "K4.00",
                    "Scan",
                    "high",
                    "FAIL",
                    {"exception": type(exc).__name__, "items": []},
                    "The scan raised. Rerun it. Only the exception class is kept.",
                    SOURCE,
                )
            ],
            secrets=[],
            tables=0,
            columns=0,
        )


def _safe_dir(ctx: Any) -> tuple[Path, Any]:
    try:
        path, seed = _ctx_path(ctx)
        return path, seed
    except Exception:
        return Path("."), None


def _run(ctx: Any) -> Report:
    engagement_dir, seed_arg = _ctx_path(ctx)
    if not engagement_dir.is_dir():
        raise KitConfig()
    yaml_path = engagement_dir / "engagement.yaml"
    if not yaml_path.is_file():
        alt = engagement_dir / "engagement.yml"
        yaml_path = alt if alt.is_file() else yaml_path
    if not yaml_path.is_file():
        raise KitConfig()
    data = _load_yaml(yaml_path)
    started = _now()
    engagement = str(data.get("name") or data.get("engagement") or engagement_dir.name)
    tenant_id = str(data.get("tenant_id") or data.get("tenant") or engagement)
    seed = int(seed_arg if seed_arg is not None else data.get("seed") or 1)
    secrets = _password_secrets(data)
    flags = _flags(data)
    link_doc = data.get("links") if isinstance(data.get("links"), dict) else {}
    verified = _links(link_doc.get("verified"))
    proposed = _links(link_doc.get("proposed"))
    warehouse = _first_warehouse(data, engagement_dir)
    manifest = _as_path(engagement_dir, data.get("lake_manifest"))
    lake_root = _as_path(engagement_dir, data.get("lake_root"))
    receipt = _receipt_path(data, engagement_dir)
    receipt_payload: Any = None
    receipt_ok = False
    if receipt is not None and receipt.is_file():
        if receipt.stat().st_size > 5_000_000:
            receipt_ok = False
            receipt_payload = None
        else:
            receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
            _collect_secrets(receipt_payload, secrets)
            receipt_ok = True
    elif receipt is not None:
        receipt_ok = False

    checks: list[dict[str, Any]] = []
    tables = 0
    columns = 0
    if warehouse is None or not warehouse.is_file():
        reason = "warehouse path is not set" if warehouse is None else "warehouse file is missing"
        checks.extend(_skip_data(reason))
    else:
        import duckdb

        con = duckdb.connect(str(warehouse), read_only=True)
        try:
            rels = list_tables(con)
            stats = {rel.display: column_stats(con, rel) for rel in rels}
            tables = len(rels)
            columns = sum(
                1 for cols in stats.values() for name in cols if not str(name).startswith("__")
            )
            if not rels:
                checks.extend(_skip_data("no tables in the warehouse"))
                checks.append(
                    skipped(
                        "K4.11",
                        "Secret-rule scan",
                        "no tables in the warehouse",
                        FIX["secret_skip"],
                    )
                )
            else:
                checks.extend(_scan_warehouse(con, rels, stats, seed, verified, proposed))
        finally:
            con.close()
    checks.append(
        guard(
            "K4.09",
            "Lake promise checks",
            FIX["lake"],
            lambda: lake_check(manifest, lake_root),
            LAKE_SOURCE,
        )
    )
    checks.append(
        guard(
            "K4.10",
            "Row-cap truncation",
            FIX["truncation"],
            lambda: truncation_check(receipt_payload, _rel(receipt, engagement_dir), receipt_ok),
            CAP_SOURCE,
        )
    )
    if warehouse is None or not warehouse.is_file():
        checks.append(
            skipped(
                "K4.11",
                "Secret-rule scan",
                "governance scan module is not on this tree"
                if _gov_missing()
                else "warehouse file is missing",
                FIX["secret_skip"],
            )
        )
    return _finish(
        engagement_dir=engagement_dir,
        engagement=engagement,
        tenant_id=tenant_id,
        seed=seed,
        started=started,
        flags=flags,
        checks=checks,
        secrets=secrets,
        tables=tables,
        columns=columns,
    )


def _gov_missing() -> bool:
    try:
        import importlib

        importlib.import_module("dms_core.governance.scan")
        return False
    except Exception:
        return True


def write_outputs(report: Report) -> Path:
    import yaml

    eng = Path(report.engagement_dir)
    body = dict(report)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = eng / "runs" / f"{stamp}-scan"
    try:
        run_dir.mkdir(parents=True)
    except FileExistsError:
        run_dir = eng / "runs" / f"{stamp}-scan-2"
        run_dir.mkdir(parents=True)
    text = json.dumps(body, indent=2, sort_keys=True) + "\n"
    (run_dir / "report.json").write_text(text, encoding="utf-8")
    loaded = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    (run_dir / "report.md").write_text(render_md(loaded), encoding="utf-8")
    dumped = yaml.safe_dump(report.decisions, sort_keys=True)
    (run_dir / "decisions.yaml").write_text(dumped, encoding="utf-8")
    (eng / "decisions.yaml").write_text(dumped, encoding="utf-8")
    return run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FDE data-quality and PII scan")
    parser.add_argument("--engagement", required=True)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args(argv)
    if not kit_enabled():
        print("fde_kit_disabled", file=sys.stderr)
        return 2
    try:
        report = run(SimpleNamespace(engagement=args.engagement, seed=args.seed))
    except KitDisabled:
        print("fde_kit_disabled", file=sys.stderr)
        return 2
    except KitConfig:
        print("fde_kit_config", file=sys.stderr)
        return 2
    write_outputs(report)
    return exit_for(str(report.get("status")))


if __name__ == "__main__":
    raise SystemExit(main())
