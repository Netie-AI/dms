"""Rule-based chart recommendation from answer rows (DMS-VIZ-01).

Pure and deterministic: no LLM, no engine import. The recommender reads the
*shape* of the rows the query returned and picks a chart kind by rule. It never
copies a number into the spec: the Vega-Lite dict references a named dataset
(``data: {name: "rows"}``) and column names only, so the rows on the envelope
stay the single source of every figure a chart draws. The one exception is
``bignum.value``, which is the one cell the tile shows and is required to be a
cell of ``rows`` and an entry of ``values`` (E14).

Abstentions never reach here with rows: ``build_answer_envelope`` forces
``chart=None`` on ABSTAIN, and E14 asserts it.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"

#: Columns profiled per recommendation. Enough to type a column honestly
#: without walking a 100k-row result on the ask path.
PROFILE_ROWS = 500

_TEMPORAL_NAME = re.compile(r"(?:^|_)(date|day|week|month|year|period|time|as_of)(?:_|$)", re.I)
#: A *numeric* column is temporal only when its name ends in a calendar unit
#: (``year``, ``fiscal_month``). ``lead_time_days`` stays a measure.
_TEMPORAL_INT_SUFFIX = re.compile(r"(?:^|_)(year|month|week|day|period)$", re.I)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?(?:[T ][0-9:.+\-Z]*)?$")
_ID_NAME = re.compile(r"(?:^id$|_id$)", re.I)
_CODE_NAME = re.compile(r"code", re.I)
_SHARE_WORDS = re.compile(
    r"\b(share|proportion|percent|percentage|breakdown|mix|composition)\b|%", re.I
)

MEASURE = "measure"
TEMPORAL = "temporal"
NOMINAL = "nominal"
ID = "id"
EMPTY = "empty"
OTHER = "other"


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _parses_as_date(v: Any) -> bool:
    if isinstance(v, (date, datetime)):
        return True
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not _ISO_DATE.match(s):
        return False
    if len(s) == 7:  # YYYY-MM
        try:
            return 1 <= int(s[5:7]) <= 12
        except ValueError:
            return False
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _profile_column(name: str, vals: list[Any]) -> str:
    present = [v for v in vals if v is not None]
    if not present:
        return EMPTY
    if all(_is_num(v) for v in present):
        all_int = all(isinstance(v, int) or float(v).is_integer() for v in present)
        if _ID_NAME.search(name):
            return ID
        if _CODE_NAME.search(name) and all_int and len(set(present)) == len(present):
            return ID
        if all_int and _TEMPORAL_INT_SUFFIX.search(name):
            return TEMPORAL
        return MEASURE
    if all(isinstance(v, (str, date, datetime)) for v in present):
        parsed = sum(1 for v in present if _parses_as_date(v))
        if parsed and (_TEMPORAL_NAME.search(name) or parsed / len(present) >= 0.9):
            return TEMPORAL
        if all(isinstance(v, str) for v in present):
            return NOMINAL
    return OTHER


def profile_columns(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Column name -> measure|temporal|nominal|id|empty|other, over every row."""
    sample = [r for r in rows[:PROFILE_ROWS] if isinstance(r, dict)]
    keys: list[str] = []
    for r in sample:
        for k in r:
            if k not in keys:
                keys.append(str(k))
    return {k: _profile_column(k, [r.get(k) for r in sample]) for k in keys}


def _title(title_hint: str | None, default: str) -> str:
    hint = (title_hint or "").strip()
    return hint or default


def _spec(mark: str, encoding: dict[str, Any], title: str) -> dict[str, Any]:
    return {
        "$schema": VEGA_LITE_SCHEMA,
        "title": title,
        "data": {"name": "rows"},
        "mark": mark,
        "encoding": encoding,
    }


def _temporal_type(col: str, rows: list[dict[str, Any]]) -> str:
    # An integer year is not a timestamp: Vega would read 2024 as 2024 ms.
    vals = [r.get(col) for r in rows[:PROFILE_ROWS] if r.get(col) is not None]
    return "ordinal" if vals and all(_is_num(v) for v in vals) else "temporal"


def recommend_chart(
    rows: list[dict[str, Any]] | None,
    question: str | None = "",
    title_hint: str | None = None,
) -> dict[str, Any] | None:
    """Pick a chart for ``rows`` by shape. None for no rows; never invents data."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return None
    sample = rows[:PROFILE_ROWS]
    kinds = profile_columns(rows)
    measures = [k for k, t in kinds.items() if t == MEASURE]
    temporals = [k for k, t in kinds.items() if t == TEMPORAL]
    nominals = [k for k, t in kinds.items() if t == NOMINAL]
    others = [k for k, t in kinds.items() if t == OTHER]
    n = len(rows)

    # (a) one row, one measure, everything else a label -> big number.
    if n == 1 and len(measures) == 1 and not others:
        m = measures[0]
        return {
            "kind": "bignum",
            "y": m,
            "value": rows[0][m],
            "label": m,
            "title": _title(title_hint, m),
            "vega_lite": _spec(
                "text",
                {"text": {"field": m, "type": "quantitative"}},
                _title(title_hint, m),
            ),
        }

    # (b) time series.
    if len(temporals) == 1 and measures and n >= 2 and not nominals and not others:
        x, y = temporals[0], measures[0]
        title = _title(title_hint, f"{y} over {x}")
        return {
            "kind": "line",
            "x": x,
            "y": y,
            "title": title,
            "vega_lite": _spec(
                "line",
                {
                    "x": {"field": x, "type": _temporal_type(x, rows), "sort": "ascending"},
                    "y": {"field": y, "type": "quantitative"},
                },
                title,
            ),
        }

    if len(nominals) == 1 and measures and not temporals and not others:
        x = nominals[0]
        cats = [r.get(x) for r in sample]
        distinct = {c for c in cats if c is not None}
        # One bar per row: repeated categories would silently stack.
        one_per_row = len(distinct) == len(cats) == n

        # (c) share-of-whole with a handful of positive slices -> pie.
        if (
            one_per_row
            and len(measures) == 1
            and _SHARE_WORDS.search(question or "")
            and 2 <= len(distinct) <= 6
        ):
            y = measures[0]
            ys = [r.get(y) for r in rows]
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in ys):
                title = _title(title_hint, f"Share of {y} by {x}")
                return {
                    "kind": "pie",
                    "x": x,
                    "y": y,
                    "title": title,
                    "vega_lite": _spec(
                        "arc",
                        {
                            "theta": {"field": y, "type": "quantitative"},
                            "color": {"field": x, "type": "nominal", "sort": None},
                        },
                        title,
                    ),
                }

        # (d) category comparison -> bar / hbar.
        if one_per_row and 2 <= len(distinct) <= 50:
            y = measures[0]
            longest = max(len(str(c)) for c in distinct)
            horizontal = len(distinct) > 8 or longest > 12
            title = _title(title_hint, f"{y} by {x}")
            if horizontal:
                enc = {
                    "y": {"field": x, "type": "nominal", "sort": None},
                    "x": {"field": y, "type": "quantitative"},
                }
            else:
                enc = {
                    "x": {"field": x, "type": "nominal", "sort": None},
                    "y": {"field": y, "type": "quantitative"},
                }
            return {
                "kind": "hbar" if horizontal else "bar",
                "x": x,
                "y": y,
                "title": title,
                "vega_lite": _spec("bar", enc, title),
            }

    # (e) two measures, no dimension -> scatter.
    if not nominals and not temporals and not others and len(measures) >= 2 and n >= 3:
        x, y = measures[0], measures[1]
        title = _title(title_hint, f"{y} vs {x}")
        return {
            "kind": "scatter",
            "x": x,
            "y": y,
            "title": title,
            "vega_lite": _spec(
                "point",
                {
                    "x": {"field": x, "type": "quantitative"},
                    "y": {"field": y, "type": "quantitative"},
                },
                title,
            ),
        }

    # (f) the rows table is the honest view.
    return {"kind": "table", "title": _title(title_hint, "Result")}


def chart_fields(chart: dict[str, Any]) -> list[str]:
    """Every column name a chart encodes (``x``, ``y``, vega_lite encoding fields)."""
    out: list[str] = []
    for key in ("x", "y"):
        v = chart.get(key)
        if v is not None:
            out.append(str(v))
    vl = chart.get("vega_lite")
    if isinstance(vl, dict):
        enc = vl.get("encoding")
        if isinstance(enc, dict):
            for ch in enc.values():
                if isinstance(ch, dict) and ch.get("field") is not None:
                    out.append(str(ch["field"]))
    return out


__all__ = ["PROFILE_ROWS", "VEGA_LITE_SCHEMA", "chart_fields", "profile_columns", "recommend_chart"]
