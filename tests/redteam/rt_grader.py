"""Mechanical grader for the red-team harness.

STRICTER than scripts/oracle_row_match.py (which score_curated uses). That compare is
case-insensitive, column-name-insensitive, treats NULL == blank and ignores column
order. This one:

  * strings: case-sensitive, exact (no strip, no casefold)
  * NULL != '' != 0 != False
  * numbers: integral values exact; non-integral values compared after rounding BOTH
    sides to 12 significant digits (float noise only). A served 1234.57 against a gold
    1234.5678 is a mismatch: display rounding is WRONG unless the gold SQL rounds too.
  * columns: NAMES are compared (exact, case-sensitive); order is ignored. A name
    mismatch is a LABEL issue, still graded on values (cells matched as per-row
    multisets). A different column COUNT is WRONG.
  * rows: multiset. ORDER BY + LIMIT in the gold SQL makes the SEQUENCE count too.
  * a numeric-looking STRING served where the gold column is numeric (DMS stringifies
    Decimal cells) is compared numerically and recorded as a label issue.
  * prose: every numeric token in ``text`` must exist in the served rows/values (or the
    row count, or the question). For a single-value gold the headline must equal gold.

Verdicts: CORRECT / WRONG / ABSTAIN / HARNESS_ERROR.
ANSWERED = badge in {L0_CERTIFIED, L1_GOVERNED_METRIC, L2_VALIDATED, L2_ANOMALOUS} and
not abstained.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, time
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Any

from rt_badge import badge_label_audit

ANSWERED_BADGES = frozenset({"L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_VALIDATED", "L2_ANOMALOUS"})
VERDICTS = ("CORRECT", "WRONG", "ABSTAIN", "HARNESS_ERROR")
SIG_DIGITS = 12
_ORDER_BY = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
_LIMIT = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_DEC_STR = re.compile(r"^-?\d+(?:\.\d+)?$")
_IDENT_HYPHEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)+")
_DATETIME = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?\b")
_PROSE_NUM = re.compile(r"(?<![A-Za-z0-9_.])-?\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9_])")


# ---------------------------------------------------------------- numbers and cells
def _num_key(d: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 60
        if d == d.to_integral_value():
            return Decimal(int(d))
        q = Decimal(1).scaleb(d.adjusted() - SIG_DIGITS + 1)
        return d.quantize(q, rounding=ROUND_HALF_EVEN).normalize()


def _as_decimal(v: Any) -> Decimal | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, Decimal):
        return v if v.is_finite() else None
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return Decimal(repr(v))
    return None


def canon_cell(v: Any) -> tuple[str, Any]:
    """Typed canonical form. NULL, '', 0 and False are four different cells."""
    if v is None:
        return ("null", None)
    if isinstance(v, bool):
        return ("bool", v)
    d = _as_decimal(v)
    if d is not None:
        return ("num", _num_key(d))
    if isinstance(v, float):
        return ("special", repr(v))
    if isinstance(v, (datetime, date, time)):
        return ("str", v.isoformat())
    if isinstance(v, str):
        return ("str", v)
    return ("str", str(v))


def _is_numeric_cell(v: Any) -> bool:
    return not isinstance(v, bool) and _as_decimal(v) is not None


def _row_keys(row: dict[str, Any]) -> list[str]:
    return list(row.keys())


def _cols_of(rows: list[dict[str, Any]]) -> tuple[set[str] | None, bool]:
    """(column-name set, ragged?) for a list of row dicts."""
    if not rows:
        return None, False
    first = set(_row_keys(rows[0]))
    ragged = any(set(_row_keys(r)) != first for r in rows[1:])
    return first, ragged


def has_order_by_limit(sql: str | None) -> bool:
    blob = " ".join((sql or "").split())
    return bool(_ORDER_BY.search(blob) and _LIMIT.search(blob))


def has_order_by(sql: str | None) -> bool:
    return bool(_ORDER_BY.search(sql or ""))


def compare_rows(
    served: list[dict[str, Any]], gold: list[dict[str, Any]], *, gold_sql: str | None = None
) -> dict[str, Any]:
    """Strict multiset compare. Returns {ok, reasons, label_issues}."""
    reasons: list[str] = []
    labels: list[str] = []
    if not all(isinstance(r, dict) for r in served):
        return {"ok": False, "reasons": ["served_rows_not_objects"], "label_issues": labels}
    s_cols, s_ragged = _cols_of(served)
    g_cols, g_ragged = _cols_of(gold)
    if s_ragged:
        reasons.append("served_rows_ragged_columns")
    if len(served) != len(gold):
        reasons.append(f"rows_mismatch:count={len(served)}/{len(gold)}")
    if s_cols is None or g_cols is None:
        # one side is empty: the count check above already decided it
        return {"ok": not reasons, "reasons": reasons, "label_issues": labels}

    by_name = s_cols == g_cols
    if not by_name:
        labels.append(f"column_name_mismatch:served={sorted(s_cols)} gold={sorted(g_cols)}")
        if len(s_cols) != len(g_cols):
            reasons.append(f"column_count_mismatch:{len(s_cols)}/{len(g_cols)}")
            return {"ok": False, "reasons": reasons, "label_issues": labels}

    served_rows = served
    if by_name:
        numeric_gold = {
            c
            for c in g_cols
            if gold
            and all(r[c] is None or _is_numeric_cell(r[c]) for r in gold)
            and any(_is_numeric_cell(r[c]) for r in gold)
        }
        coerced: set[str] = set()
        fixed: list[dict[str, Any]] = []
        for r in served:
            nr = dict(r)
            for c in numeric_gold:
                v = nr.get(c)
                if isinstance(v, str) and _DEC_STR.match(v.strip()):
                    try:
                        nr[c] = Decimal(v.strip())
                        coerced.add(c)
                    except InvalidOperation:  # pragma: no cover - regex guards this
                        pass
            fixed.append(nr)
        served_rows = fixed
        for c in sorted(coerced):
            labels.append(f"numeric_served_as_string:{c}")
        order = sorted(g_cols)

        def key_of(row: dict[str, Any]) -> tuple[Any, ...]:
            return tuple(canon_cell(row.get(c)) for c in order)

    else:

        def key_of(row: dict[str, Any]) -> tuple[Any, ...]:
            return tuple(sorted((canon_cell(v) for v in row.values()), key=repr))

    for r in served:
        for c, v in r.items():
            if isinstance(v, str) and v.startswith("DMSMASK_"):
                reasons.append(f"masked_cell:{c}")
                break
        else:
            continue
        break

    s_keys = [key_of(r) for r in served_rows]
    g_keys = [key_of(r) for r in gold]
    if Counter(s_keys) != Counter(g_keys):
        if not any(x.startswith("rows_mismatch:count") for x in reasons):
            reasons.append("rows_mismatch:values")
        only_s = list((Counter(s_keys) - Counter(g_keys)).elements())[:3]
        only_g = list((Counter(g_keys) - Counter(s_keys)).elements())[:3]
        reasons.append(f"served_only={_short(only_s)} gold_only={_short(only_g)}")
    elif s_keys != g_keys:
        if has_order_by_limit(gold_sql):
            reasons.append("order_mismatch:gold has ORDER BY + LIMIT, served sequence differs")
        elif has_order_by(gold_sql):
            labels.append("row_order_differs_from_gold_order_by")
    return {"ok": not reasons, "reasons": reasons, "label_issues": labels}


def _short(items: list[Any]) -> str:
    return repr(items)[:240]


# ---------------------------------------------------------------- prose
def prose_tokens(text: str) -> list[str]:
    """Numeric tokens a reader would take as figures. Identifiers, dates and times are not."""
    s = _DATETIME.sub(" ", text or "")
    s = _IDENT_HYPHEN.sub(" ", s)
    return [m.group(0).replace(",", "") for m in _PROSE_NUM.finditer(s)]


def _token_key(tok: str) -> Decimal | None:
    try:
        return _num_key(Decimal(tok))
    except (InvalidOperation, ValueError):
        return None


def _allowed_numbers(env: dict[str, Any], question: str) -> set[Decimal]:
    allowed: set[Decimal] = set()
    rows = env.get("rows") or []
    for r in rows if isinstance(rows, list) else []:
        for v in r.values() if isinstance(r, dict) else []:
            d = _as_decimal(v)
            if d is not None and not isinstance(v, bool):
                allowed.add(_num_key(d))
            elif isinstance(v, str):
                if _DEC_STR.match(v.strip()):
                    allowed.add(_num_key(Decimal(v.strip())))
                for t in prose_tokens(v):
                    k = _token_key(t)
                    if k is not None:
                        allowed.add(k)
    for item in env.get("values") or []:
        d = _as_decimal(item.get("value")) if isinstance(item, dict) else None
        if d is not None:
            allowed.add(_num_key(d))
    allowed.add(_num_key(Decimal(len(rows) if isinstance(rows, list) else 0)))
    for t in prose_tokens(question):
        k = _token_key(t)
        if k is not None:
            allowed.add(k)
    return allowed


def prose_check(env: dict[str, Any], question: str) -> list[str]:
    """Numeric tokens in ``text`` that are in no served row, value, row count or the question."""
    allowed = _allowed_numbers(env, question)
    bad: list[str] = []
    for tok in prose_tokens(str(env.get("text") or "")):
        k = _token_key(tok)
        if k is not None and k not in allowed:
            bad.append(tok)
    return bad


def headline_check(
    env: dict[str, Any], gold_rows: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Single-value gold: (hard reasons, label issues)."""
    if len(gold_rows) != 1 or len(gold_rows[0]) != 1:
        return [], []
    gold_v = next(iter(gold_rows[0].values()))
    gd = _as_decimal(gold_v)
    if gd is None or isinstance(gold_v, bool):
        return [], []
    gkey = _num_key(gd)
    hard: list[str] = []
    labels: list[str] = []
    vals = [
        _num_key(d)
        for d in (
            _as_decimal(i.get("value")) for i in (env.get("values") or []) if isinstance(i, dict)
        )
        if d is not None
    ]
    if gkey not in vals:
        hard.append(f"headline_value_not_gold:gold={gold_v!r} values={[str(v) for v in vals][:5]}")
    toks = [
        k
        for k in (_token_key(t) for t in prose_tokens(str(env.get("text") or "")))
        if k is not None
    ]
    rows_n = _num_key(Decimal(len(env.get("rows") or [])))
    stated = [k for k in toks if k != rows_n or k == gkey]
    if gkey not in stated:
        if stated:
            figures = [str(k) for k in stated][:5]
            hard.append(f"headline_text_mismatch:gold={gold_v!r} text_figures={figures}")
        else:
            labels.append("text_does_not_state_headline")
    return hard, labels


# ---------------------------------------------------------------- abstain reason
_DEMOTION_NOTE = re.compile(
    r"\((?:E\d|hard rule|ONTOLOGY-AUDIT|F32|E9-0|FF-0|ANS-0)|withheld|mismatch|\bpad\b|"
    r"scope conflict|polarity",
    re.IGNORECASE,
)
_BENIGN_NOTES = (
    "GEN-01 ontology compile",
    "executed via Cortex submit after validate",
    "GEN-01 Cortex ontology_plan SQL",
    "live Cortex ask",
    "redteam-stub",
    "include:",
    "exclude:",
    "unsure:",
)


def abstain_reason(env: dict[str, Any]) -> str:
    """The named reason for an abstain, in order of preference.

    1. an ``abstain_reason`` field (only set for reserved_param:as_of)
    2. the first ``GEN-01: <reason>`` assumption (the generative gate's named abstain)
    3. a demotion note written by build_answer_envelope (E9/E10/E12/rule 12/F32 ...)
    4. the first assumption that is not routine bookkeeping
    5. the first line of the text
    """
    r = env.get("abstain_reason")
    if isinstance(r, str) and r.strip():
        return r.strip()
    notes = [str(a).strip() for a in (env.get("assumptions") or []) if str(a).strip()]
    for n in notes:
        if n.startswith("GEN-01: "):
            return n[len("GEN-01: ") :]
    for n in notes:
        if _DEMOTION_NOTE.search(n):
            return n
    for n in notes:
        if not n.startswith(_BENIGN_NOTES):
            return n
    first = str(env.get("text") or "").strip().splitlines()
    return first[0][:160] if first else "no_reason_given"


def is_answered(env: dict[str, Any] | None) -> bool:
    return (
        isinstance(env, dict)
        and env.get("badge") in ANSWERED_BADGES
        and not bool(env.get("abstained"))
    )


def envelope_check(env: dict[str, Any]) -> str | None:
    """Re-run assert_envelope_valid on the served JSON. None if valid."""
    from dms_executor.envelope import assert_envelope_valid

    try:
        assert_envelope_valid(env)
    except AssertionError as exc:
        return str(exc)[:200]
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}:{str(exc)[:160]}"
    return None


# ---------------------------------------------------------------- the verdict
def grade(
    *,
    expect: str,
    question: str,
    http_status: int | None,
    http_body: Any,
    gold_sql: str | None,
    gold_rows: list[dict[str, Any]] | None,
    gold_error: str | None,
    exception: str | None = None,
    discarded: str | None = None,
) -> dict[str, Any]:
    """Grade one case. ``http_body`` is the raw JSON the customer was served."""
    out: dict[str, Any] = {
        "verdict": "HARNESS_ERROR",
        "reasons": [],
        "label_issues": [],
        "answered": False,
        "abstain_reason": None,
        "badge_label": {"ok": True, "reasons": [], "notes": []},
    }
    reasons: list[str] = out["reasons"]
    if discarded:
        reasons.append(discarded)
        return out
    if exception:
        reasons.append(f"driver_exception:{exception}")
        return out
    if http_status is None:
        reasons.append("no_http_status")
        return out

    if http_status != 200:
        code = _http_code(http_body)
        if http_status >= 500 and http_status not in (503, 504):
            reasons.append(f"http_{http_status}_unhandled:{code}")
            return out
        out["abstain_reason"] = f"http_{http_status}:{code}"
        if expect == "abstain":
            out["verdict"] = "CORRECT"
            reasons.append(f"refused_by_http_{http_status}:{code}")
        else:
            out["verdict"] = "ABSTAIN"
            reasons.append(f"customer_got_http_{http_status}:{code}")
        return out

    env = http_body if isinstance(http_body, dict) else None
    if env is None:
        reasons.append("http_200_body_not_an_object")
        return out
    out["badge_label"] = badge_label_audit(env)
    answered = is_answered(env)
    out["answered"] = answered

    if expect == "abstain":
        if answered:
            out["verdict"] = "WRONG"
            reasons.append(f"confident_answer_where_abstain_expected:badge={env.get('badge')}")
        else:
            out["verdict"] = "CORRECT"
            out["abstain_reason"] = abstain_reason(env)
        return out

    # expect == answer
    if gold_error or gold_rows is None:
        reasons.append(f"gold_error:{gold_error or 'no_gold_rows'}")
        return out
    if not answered:
        out["verdict"] = "ABSTAIN"
        out["abstain_reason"] = abstain_reason(env)
        reasons.append(f"abstained_where_answer_expected:{out['abstain_reason'][:120]}")
        return out

    bad = envelope_check(env)
    served_rows = env.get("rows")
    if not isinstance(served_rows, list):
        served_rows = []
    cmp = compare_rows(served_rows, gold_rows, gold_sql=gold_sql)
    reasons.extend(cmp["reasons"])
    out["label_issues"].extend(cmp["label_issues"])
    if bad:
        reasons.append(f"envelope_invalid:{bad}")
    orphan = prose_check(env, question)
    if orphan:
        reasons.append(f"prose_number_not_in_rows:{orphan[:3]}")
    hard, labels = headline_check(env, gold_rows)
    reasons.extend(hard)
    out["label_issues"].extend(labels)
    out["verdict"] = "WRONG" if reasons else "CORRECT"
    return out


def _http_code(body: Any) -> str:
    if isinstance(body, dict):
        d = body.get("detail")
        if isinstance(d, dict):
            return str(d.get("code") or d.get("message") or "")[:80]
        if isinstance(d, str):
            return d[:80]
    return ""
