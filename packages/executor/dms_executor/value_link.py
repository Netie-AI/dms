"""DMS_VALUE_LINK. Default off. The digit 1 only.

On, cleared columns are profiled once per database file: distinct text
values, and min/max for numeric and date columns. The profile is cached on
the file's mtime. Literals are matched with the value-match n-gram index
(PR #596). Hits and the named ranges are injected as a ``<values>`` block.

Person-classified columns are not profiled. A mask or classifier error
sends no block and the ask continues. A tie across columns flags the ask
ambiguous for the self-consistency lever.

Off, nothing is read and the block is empty.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

FLAG = "DMS_VALUE_LINK"
# ponytail: 40 distinct values. A wider column is omitted whole, same as the
# value index. Upgrade: INDEX_COLUMN_CAP when the prompt budget grows.
_DISTINCT_CAP = 40
_VALUE_CHARS = 80

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[int, Profile]] = {}
_MISSES = 0
_TIE = False


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only when ``DMS_VALUE_LINK`` is the digit 1."""
    source = os.environ if env is None else env
    return str(source.get(FLAG) or "").strip() == "1"


def reset() -> None:
    """Test hook. Drops the profile cache and the tie flag."""
    global _MISSES, _TIE
    with _LOCK:
        _CACHE.clear()
        _MISSES = 0
        _TIE = False


def cache_misses() -> int:
    with _LOCK:
        return _MISSES


def take_tie() -> bool:
    """True once after a cross-column tie. The flag then clears."""
    global _TIE
    with _LOCK:
        flag = _TIE
        _TIE = False
        return flag


@dataclass
class Profile:
    values: dict[str, tuple[str, ...]]
    ranges: dict[str, tuple[str, str]]


def _mark_tie(flag: bool) -> None:
    global _TIE
    with _LOCK:
        _TIE = flag


def _profile(warehouse: Path, grantable: set[str], dialect: str) -> Profile:
    global _MISSES
    path = Path(warehouse)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return Profile({}, {})
    key = str(path.resolve())
    with _LOCK:
        held = _CACHE.get(key)
        if held is not None and held[0] == stamp:
            return held[1]
        _MISSES += 1
    built = _read_profile(path, grantable, dialect)
    with _LOCK:
        _CACHE[key] = (stamp, built)
    return built


def _read_profile(path: Path, grantable: set[str], dialect: str) -> Profile:
    from dms_executor.schema_context import (
        _column_positively_cleared,
        _ident,
        _quote,
        _type_family,
        read_catalog,
    )
    from dms_executor.sql_loop import run_readonly

    values: dict[str, tuple[str, ...]] = {}
    ranges: dict[str, tuple[str, str]] = {}
    try:
        _fp, described = read_catalog(path, set(grantable), dialect)
    except Exception:
        return Profile({}, {})
    datasets = described.get("datasets") if isinstance(described, dict) else None
    if not isinstance(datasets, list):
        return Profile({}, {})
    granted = {item.lower() for item in grantable}
    for dataset in datasets:
        if not isinstance(dataset, dict):
            continue
        table = str(dataset.get("name") or "")
        if not _ident(table) or table.lower() not in granted:
            continue
        columns = dataset.get("columns")
        if not isinstance(columns, list):
            continue
        for column in columns:
            if not isinstance(column, dict):
                continue
            name = str(column.get("name") or "")
            if not _ident(name):
                continue
            family = _type_family(str(column.get("type") or ""))
            key = f"{table}.{name}"
            quoted_table = _quote(table)
            quoted_name = _quote(name)
            try:
                if family in {"numeric", "date"}:
                    sql = (
                        f"SELECT MIN({quoted_name}) AS lo, MAX({quoted_name}) AS hi "
                        f"FROM {quoted_table}"
                    )
                    rows, err = run_readonly(sql, path)
                    if err or not rows:
                        continue
                    lo = rows[0].get("lo")
                    hi = rows[0].get("hi")
                    if lo is None or hi is None:
                        continue
                    ranges[key] = (str(lo), str(hi))
                    continue
                if family != "text":
                    continue
                if not _column_positively_cleared(table, name, tagged=False):
                    continue
                sql = (
                    f"SELECT DISTINCT {quoted_name} AS v FROM {quoted_table} "
                    f"WHERE {quoted_name} IS NOT NULL LIMIT {_DISTINCT_CAP + 1}"
                )
                rows, err = run_readonly(sql, path)
                if err or not rows or len(rows) > _DISTINCT_CAP:
                    continue
                held: list[str] = []
                for row in rows:
                    raw = row.get("v")
                    if raw is None:
                        continue
                    text = str(raw).strip()
                    if not text or len(text) > _VALUE_CHARS:
                        continue
                    held.append(text)
                if held:
                    values[key] = tuple(held)
            except Exception:
                continue
    return Profile(values, ranges)


def values_context(
    question: str,
    *,
    warehouse: Path | None,
    grantable: set[str],
    dialect: str,
) -> str:
    """``<values>`` block. Empty when the flag is off or nothing matched."""
    if not enabled():
        _mark_tie(False)
        return ""
    if warehouse is None:
        _mark_tie(False)
        return ""
    try:
        profile = _profile(Path(warehouse), set(grantable), dialect)
    except Exception:
        _mark_tie(False)
        return ""
    return _render(question, profile)


def _render(question: str, profile: Profile) -> str:
    from dms_executor.value_match import ColumnInput, _fold, _spans, match_prompt

    columns = []
    for key, vals in profile.values.items():
        table, column = key.split(".", 1)
        columns.append(
            ColumnInput(table=table, column=column, values=tuple(vals), tagged=False)
        )
    try:
        matched = match_prompt(columns, question or "", active=True)
    except Exception:
        _mark_tie(False)
        return ""
    _mark_tie(bool(matched.readings))
    lines = [f"{item.table}.{item.column}={item.value}" for item in matched.candidates]
    spans = {folded for folded, _original in _spans(question or "")}
    for key, (lo, hi) in sorted(profile.ranges.items()):
        column = key.split(".", 1)[1]
        folded = _fold(column)
        if folded and folded in spans:
            lines.append(f"{key}={lo}..{hi}")
    if not lines:
        return ""
    return "<values>\n" + "\n".join(lines) + "\n</values>"
