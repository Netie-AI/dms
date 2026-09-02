"""EPIC-016 prompt-pack cross-check and FRTR golden (DMS half).

DMS owns pack quality + schema sanity + extract store + number gate.
Pointer owns Excel Copilot paste. This module does not paste, does not
open Excel, and does not write a workbook via openpyxl.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

EXPECTED_SHEET_FAMILIES: dict[str, tuple[str, ...]] = {
    "cover": ("cover",),
    "ontime_export": (
        "ontime export",
        "on-time export",
        "ontime_export",
        "on time export",
    ),
    "analysis": ("analysis", "ontime analysis", "on-time analysis", "on time analysis"),
    "chart": ("presentation chart", "ppt chart", "chart"),
}

FORBIDDEN_PRODUCERS = frozenset(
    {
        "mcp_primary",
        "mcp_user_excel_primary",
        "mcp",
        "openpyxl_primary",
        "openpyxl-as-primary",
        "openpyxl",
    }
)
ALLOWED_PRODUCERS = frozenset(
    {"pointer", "pointer_copilot", "pointer_paste", "test_fixture"}
)

FRTR_AVG = 300.27
FRTR_ONTIME = 184005
FRTR_TOTAL = 200000
# Founder-agreed rounding for #32: avg to 2dp +/- 0.05; counts +/- 50.
AVG_TOL = 0.05
COUNT_TOL = 50

_THEATER_SHEET = re.compile(r"summary|kpi|dashboard|cover", re.I)
_ONTINE_COL = re.compile(r"ontime|on_time|on-time", re.I)
_COST_COL = re.compile(r"unit.?cost|^cost$|spend|amount|baserate|base_rate", re.I)

STRENGTHEN_CLAUSE = (
    "Do not use a Summary / KPI / dashboard sheet as the filter source. "
    "Do not invent categories or totals. Pointer pastes; DMS does not drive Copilot."
)


def _norm_producer(raw: str | None) -> str:
    return str(raw or "").strip().lower().replace(" ", "_")


def _sheet_family_hits(names: list[str]) -> dict[str, str | None]:
    lowered = [(n, n.strip().lower()) for n in names]
    out: dict[str, str | None] = {}
    for fam, aliases in EXPECTED_SHEET_FAMILIES.items():
        hit = next((orig for orig, low in lowered if any(a in low for a in aliases)), None)
        out[fam] = hit
    return out


def _find_col(columns: list[str], pat: re.Pattern[str]) -> str | None:
    for c in columns:
        if pat.search(str(c)):
            return str(c)
    return None


def _header_row(grid: list[list[Any]]) -> list[str]:
    if not grid:
        return []
    return [str(c) if c is not None else "" for c in grid[0]]


def _body_rows(grid: list[list[Any]]) -> list[list[Any]]:
    if len(grid) < 2:
        return []
    out = []
    for row in grid[1:]:
        cells = [("" if c is None else c) for c in row]
        if all(str(c).strip() == "" for c in cells):
            continue
        out.append(cells)
    return out


def _truthy(val: Any) -> bool:
    if val is True:
        return True
    s = str(val).strip().lower()
    return s in {"true", "1", "yes", "y"}


def _as_float(val: Any) -> float | None:
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def pack_issues(
    pack: dict[str, Any],
    *,
    sheets: list[tuple[str, list[list[Any]]]] | None,
) -> list[str]:
    """Named refusals. Empty list means the pack may be handed to Pointer."""
    issues: list[str] = []
    if not isinstance(pack, dict) or not pack:
        return ["pack_required"]
    wb_raw = pack.get("workbook")
    wb: dict[str, Any] = wb_raw if isinstance(wb_raw, dict) else {}
    steps_raw = pack.get("steps")
    steps: list[Any] = steps_raw if isinstance(steps_raw, list) else []
    expected = pack.get("expected_result_sheets")
    if not isinstance(expected, list):
        expected = []
    expected_l = [str(s).strip().lower() for s in expected]
    for fam in ("cover", "ontime export", "analysis", "presentation chart"):
        if not any(fam in e or e in fam for e in expected_l):
            issues.append(f"missing_expected_sheet:{fam}")
    if len(steps) < 4:
        issues.append("steps_lt_4")
    source_sheet = str(wb.get("source_sheet") or "")
    if source_sheet and _THEATER_SHEET.search(source_sheet) and not _ONTINE_COL.search(
        source_sheet
    ):
        issues.append("source_sheet_summary_theater")
    producer = _norm_producer(str(pack.get("paste_owner") or pack.get("producer") or "pointer"))
    if producer in FORBIDDEN_PRODUCERS:
        issues.append(f"forbidden_producer:{producer}")
    nd_raw = pack.get("not_doing")
    not_doing: list[Any] = nd_raw if isinstance(nd_raw, list) else []
    if "pointer_paste" in [str(x) for x in not_doing] and pack.get("paste_owner") == "airgpt":
        issues.append("airgpt_must_not_paste")

    if sheets is None:
        issues.append("workbook_unverified")
        return issues

    names = [n for n, _ in sheets]
    by_name = {n.lower(): grid for n, grid in sheets}
    src_key = source_sheet.lower()
    grid = by_name.get(src_key)
    if grid is None and sheets:
        # Prefer a fact sheet with OnTime/cost; refuse if only theater exists.
        scored: list[tuple[int, str, list[list[Any]]]] = []
        for n, g in sheets:
            header_text = " ".join(_header_row(g))
            score = 0
            if _ONTINE_COL.search(header_text):
                score += 8
            if _COST_COL.search(header_text):
                score += 4
            if _THEATER_SHEET.search(n):
                score -= 6
            scored.append((score, n, g))
        scored.sort(key=lambda t: t[0], reverse=True)
        if scored and scored[0][0] > 0:
            grid = scored[0][2]
            source_sheet = scored[0][1]
        else:
            issues.append("no_ontime_cost_sheet")
            return issues
    if grid is None:
        issues.append("source_sheet_missing")
        return issues
    cols = _header_row(grid)
    if not _find_col(cols, _ONTINE_COL):
        issues.append("ontime_col_missing")
    if not _find_col(cols, _COST_COL):
        issues.append("cost_col_missing")
    if names and all(_THEATER_SHEET.search(n) for n in names):
        issues.append("only_summary_sheets")
    return issues


def oracle_from_sheets(
    sheets: list[tuple[str, list[list[Any]]]],
    *,
    source_sheet: str = "",
) -> dict[str, Any]:
    """Compute OnTime=true average cost from the source grids. Not the Copilot golden."""
    by_name = {n.lower(): grid for n, grid in sheets}
    grid = by_name.get(source_sheet.lower()) if source_sheet else None
    if grid is None:
        for n, g in sheets:
            header_text = " ".join(_header_row(g))
            if (
                _ONTINE_COL.search(header_text)
                and _COST_COL.search(header_text)
                and not _THEATER_SHEET.search(n)
            ):
                grid = g
                break
    if grid is None:
        return {"ok": False, "reason": "no_ontime_cost_sheet"}
    cols = _header_row(grid)
    ontime = _find_col(cols, _ONTINE_COL)
    cost = _find_col(cols, _COST_COL)
    if not ontime or not cost:
        return {"ok": False, "reason": "ontime_or_cost_missing"}
    oi = cols.index(ontime)
    ci = cols.index(cost)
    body = _body_rows(grid)
    costs: list[float] = []
    for row in body:
        flag = row[oi] if oi < len(row) else ""
        if not _truthy(flag):
            continue
        num = _as_float(row[ci] if ci < len(row) else None)
        if num is not None:
            costs.append(num)
    if not costs:
        return {
            "ok": False,
            "reason": "no_ontime_rows",
            "ontime_count": 0,
            "total_count": len(body),
        }
    avg = sum(costs) / len(costs)
    return {
        "ok": True,
        "avg_cost": round(avg, 4),
        "ontime_count": len(costs),
        "total_count": len(body),
        "ontime_col": ontime,
        "cost_col": cost,
        "honesty": "source-grid oracle; not a Copilot result",
    }


def strengthen_pack(
    pack: dict[str, Any],
    *,
    oracle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = dict(pack)
    out["paste_owner"] = "pointer"
    out["cross_check_owner"] = "dms"
    steps = []
    for step in pack.get("steps") or []:
        if not isinstance(step, dict):
            continue
        item = dict(step)
        intent = str(item.get("intent") or "")
        if STRENGTHEN_CLAUSE not in intent:
            item["intent"] = (intent + " " + STRENGTHEN_CLAUSE).strip()
        steps.append(item)
    if steps:
        out["steps"] = steps
    expected = pack.get("expected_result_sheets")
    if not isinstance(expected, list) or len(expected) < 4:
        out["expected_result_sheets"] = [
            "Cover",
            "OnTime Export",
            "Analysis",
            "Presentation Chart",
        ]
    not_doing = list(pack.get("not_doing") or [])
    for extra in ("excel_copilot_drive", "mcp_user_excel_primary", "in_airgpt_chart_orchestrator"):
        if extra not in not_doing:
            not_doing.append(extra)
    out["not_doing"] = not_doing
    if oracle and oracle.get("ok"):
        out["dms_source_oracle"] = {
            "avg_cost": oracle.get("avg_cost"),
            "ontime_count": oracle.get("ontime_count"),
            "total_count": oracle.get("total_count"),
            "honesty": oracle.get("honesty"),
        }
    out["honesty"] = (
        "DMS cross-checked candidate pack. Status awaiting_pointer_receipt. "
        "Pointer owns Excel Copilot paste. DMS did not open Excel."
    )
    return out


def crosscheck_pack(
    pack: dict[str, Any],
    *,
    sheets: list[tuple[str, list[list[Any]]]] | None,
    workbook_path: str = "",
    pack_id: str | None = None,
) -> dict[str, Any]:
    pid = (pack_id or "").strip() or f"orch_{uuid.uuid4().hex[:12]}"
    issues = pack_issues(pack, sheets=sheets)
    if "pack_required" in issues:
        return {
            "ok": False,
            "status": "rejected",
            "reason": "pack_required",
            "error": "pack object required",
            "pack_id": pid,
            "issues": issues,
            "paste_owner": "pointer",
        }
    if sheets is None:
        return {
            "ok": False,
            "status": "rejected",
            "reason": "workbook_unverified",
            "error": "workbook_path required so DMS can schema-check before Pointer paste",
            "pack_id": pid,
            "issues": issues,
            "paste_owner": "pointer",
        }
    fatal = [i for i in issues if i != "workbook_unverified"]
    if fatal:
        return {
            "ok": False,
            "status": "rejected",
            "reason": fatal[0],
            "error": "; ".join(fatal),
            "pack_id": pid,
            "issues": issues,
            "paste_owner": "pointer",
        }
    wb_raw = pack.get("workbook")
    wb: dict[str, Any] = wb_raw if isinstance(wb_raw, dict) else {}
    oracle = oracle_from_sheets(sheets, source_sheet=str(wb.get("source_sheet") or ""))
    strengthened = strengthen_pack(pack, oracle=oracle if oracle.get("ok") else None)
    return {
        "ok": True,
        "status": "awaiting_pointer_receipt",
        "reason": "live",
        "pack_id": pid,
        "issues": [],
        "paste_owner": "pointer",
        "strengthened_pack": strengthened,
        "source_oracle": oracle if oracle.get("ok") else None,
        "workbook_path": workbook_path,
        "honesty": strengthened["honesty"],
    }


def inspect_result_grids(sheets: list[tuple[str, list[list[Any]]]]) -> dict[str, Any]:
    names = [n for n, _ in sheets]
    families = _sheet_family_hits(names)
    missing = [fam for fam, hit in families.items() if not hit]
    export_name = families.get("ontime_export")
    export_rows = 0
    if export_name:
        grid = next(g for n, g in sheets if n == export_name)
        export_rows = len(_body_rows(grid))
    analysis_name = families.get("analysis")
    analysis_nums: list[float] = []
    labeled: dict[str, float] = {}
    if analysis_name:
        grid = next(g for n, g in sheets if n == analysis_name)
        for row in grid:
            cells = [c for c in row if c is not None and str(c).strip() != ""]
            if len(cells) >= 2:
                key = str(cells[0]).strip().lower()
                num = _as_float(cells[1])
                if num is not None:
                    labeled[key] = num
            for c in row:
                num = _as_float(c)
                if num is not None:
                    analysis_nums.append(num)
    return {
        "ok": not missing,
        "sheetnames": names,
        "families": families,
        "missing": missing,
        "export_row_count": export_rows,
        "analysis_numbers": analysis_nums,
        "analysis_labeled": labeled,
        "error": None if not missing else f"missing sheet families: {missing}",
    }


def _pick_labeled(labeled: dict[str, float], *keys: str) -> float | None:
    for k, v in labeled.items():
        if any(key in k for key in keys):
            return v
    return None


def _nearest(nums: list[float], target: float, tol: float) -> float | None:
    if not nums:
        return None
    best = min(nums, key=lambda n: abs(n - target))
    return best if abs(best - target) <= tol else None


def evaluate_frtr_golden(
    sheets: list[tuple[str, list[list[Any]]]],
    *,
    producer: str | None,
    require_export_row_match: bool = True,
) -> dict[str, Any]:
    """Assert FRTR ballpark on Analysis/Export. Refuses MCP/openpyxl as producer."""
    prod = _norm_producer(producer)
    if not prod:
        return {
            "ok": False,
            "reason": "producer_required",
            "error": "golden needs producer=pointer_copilot (Pointer paste). Not assumed.",
        }
    if prod in FORBIDDEN_PRODUCERS:
        return {
            "ok": False,
            "reason": f"forbidden_producer:{prod}",
            "error": "XLSX-ORCH-12 refuses MCP-primary or openpyxl-as-primary as Demo-2 source",
        }
    if prod not in ALLOWED_PRODUCERS:
        return {
            "ok": False,
            "reason": f"unknown_producer:{prod}",
            "error": "producer must be pointer_copilot (or test_fixture for the gate)",
        }
    inspected = inspect_result_grids(sheets)
    if inspected["missing"]:
        return {
            "ok": False,
            "reason": "missing_result_sheets",
            "error": inspected["error"],
            "inspected": inspected,
        }
    labeled = inspected["analysis_labeled"]
    nums = inspected["analysis_numbers"]
    avg = _pick_labeled(labeled, "avg", "average", "mean") or _nearest(nums, FRTR_AVG, AVG_TOL)
    ontime = _pick_labeled(labeled, "ontime", "on-time", "on_time count")
    total = _pick_labeled(labeled, "total", "all rows", "row count")
    if ontime is None:
        hit = _nearest(nums, float(FRTR_ONTIME), float(COUNT_TOL))
        ontime = hit
    if total is None:
        total = _nearest(nums, float(FRTR_TOTAL), float(COUNT_TOL))
    misses: list[str] = []
    if avg is None or abs(float(avg) - FRTR_AVG) > AVG_TOL:
        misses.append(f"avg_cost wanted {FRTR_AVG} got {avg}")
    if ontime is None or abs(float(ontime) - FRTR_ONTIME) > COUNT_TOL:
        misses.append(f"ontime_count wanted {FRTR_ONTIME} got {ontime}")
    if total is None or abs(float(total) - FRTR_TOTAL) > COUNT_TOL:
        misses.append(f"total_count wanted {FRTR_TOTAL} got {total}")
    export_n = int(inspected["export_row_count"] or 0)
    if require_export_row_match and ontime is not None:
        if abs(export_n - float(ontime)) > COUNT_TOL:
            misses.append(
                f"export_row_count {export_n} disagrees with Analysis ontime {ontime} "
                "(Summary theater / wrong filter)"
            )
    if misses:
        return {
            "ok": False,
            "reason": "golden_miss",
            "error": "; ".join(misses),
            "avg_cost": avg,
            "ontime_count": ontime,
            "total_count": total,
            "export_row_count": export_n,
            "inspected": inspected,
            "tolerance": {"avg": AVG_TOL, "count": COUNT_TOL},
        }
    return {
        "ok": True,
        "reason": "frtr_golden",
        "avg_cost": avg,
        "ontime_count": ontime,
        "total_count": total,
        "export_row_count": export_n,
        "producer": prod,
        "tolerance": {"avg": AVG_TOL, "count": COUNT_TOL},
        "honesty": "numbers read from Analysis/Export grids. Not chat paraphrase.",
    }


def artifact_dir(root: Path, *, space_id: str, pack_id: str) -> Path:
    safe_space = re.sub(r"[^a-zA-Z0-9._-]+", "_", space_id or "company-default")[:80]
    safe_pack = re.sub(r"[^a-zA-Z0-9._-]+", "_", pack_id or "pack")[:80]
    return root / safe_space / "xlsx_orch" / safe_pack


def store_artifact(
    root: Path,
    *,
    data: bytes,
    filename: str,
    space_id: str,
    pack_id: str,
    producer: str,
    sheetnames: list[str],
) -> dict[str, Any]:
    if not data:
        return {"ok": False, "reason": "empty_xlsx", "error": "no bytes to store"}
    if not filename.lower().endswith((".xlsx", ".xlsm")):
        return {"ok": False, "reason": "not_xlsx", "error": f"not an xlsx: {filename}"}
    prod = _norm_producer(producer)
    if prod in FORBIDDEN_PRODUCERS:
        return {
            "ok": False,
            "reason": f"forbidden_producer:{prod}",
            "error": "extract refuses MCP-primary / openpyxl-as-primary as the producer",
        }
    if prod not in ALLOWED_PRODUCERS:
        return {
            "ok": False,
            "reason": f"unknown_producer:{prod}",
            "error": "producer must be pointer_copilot (Pointer paste) or test_fixture",
        }
    dest_dir = artifact_dir(root, space_id=space_id, pack_id=pack_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name or "result.xlsx"
    dest = dest_dir / safe_name
    dest.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    meta = {
        "artifact_id": f"art_{digest[:16]}",
        "pack_id": pack_id,
        "space_id": space_id,
        "producer": prod,
        "filename": safe_name,
        "path": str(dest.resolve()),
        "sha256": digest,
        "nbytes": len(data),
        "sheetnames": sheetnames,
        "honesty": (
            "byte-faithful copy of the posted xlsx. DMS did not generate this workbook."
        ),
    }
    (dest_dir / "artifact.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"ok": True, "status": "stored", **meta}


def load_artifact_meta(root: Path, *, space_id: str, pack_id: str) -> dict[str, Any] | None:
    fp = artifact_dir(root, space_id=space_id, pack_id=pack_id) / "artifact.json"
    if not fp.is_file():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
