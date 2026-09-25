"""PII-01 — local deterministic personal-data detector and masker (dms#272).

No network, no model. Column-name signals plus value-pattern checks for person
names, Malaysian IC numbers, phones, emails and account numbers.

Swap: Cortex HTTP PII-MASK (#268) or a vendor DLP call behind these functions.
Not a sixth port: this is a local classifier, same class as xlsx_ooxml.

Placeholders are ``DMSMASK_<kind>_<nn>`` — letters, underscores, a 2-digit
counter. They are not email/phone/IC/account-shaped, so a second regex masker
(Cortex #268 kinds without NER) must leave them unchanged.

ponytail: names are column-name only (no NER). Ceiling: a free-text notes
column of person names. Upgrade: Cortex #268 NER at the FreeRoute choke.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

Kind = str
KINDS = frozenset({"name", "nric", "phone", "email", "account", "unknown"})

# Stable, non-PII-shaped. No '@', no 6-2-4 IC, no +60 / 01x phone, no 10-16 digit run.
MASK_TOKEN_RE = re.compile(r"DMSMASK_(?:name|nric|phone|email|account|unknown)_\d{2,}")

_METRIC_SKIP = re.compile(
    r"(amount|qty|quantity|cost|kg|myr|usd|score|load|capacity|date|id|"
    r"count|total|pct|percent|revenue|code|sku|category|region|country|"
    r"status|type|flag|bool)$",
    re.I,
)
_PERSON_TABLE = re.compile(
    r"(customer|person|people|employee|staff|user|users|contact|client|"
    r"patient|member|applicant)s?$",
    re.I,
)
_NAME_COL = re.compile(
    r"(?:^|_)(?:(?:customer|person|people|employee|staff|user|contact|client|"
    r"patient|member|holder|beneficiary|applicant|signatory|director|owner|"
    r"payee|payer|full|first|last|given|family|middle|maiden|preferred|legal|"
    r"pic)_?names?|nama(?:_penuh|_pemegang|_pengguna)?)$",
    re.I,
)
_EMAIL_COL = re.compile(r"(?:^|_)(?:e_?mails?|email_addr(?:ess)?s?)$", re.I)
_PHONE_COL = re.compile(
    r"(?:^|_)(?:phones?|mobiles?|telefons?|telephones?|whatsapps?|faxes|"
    r"fax|tels?|hp|contact_no|contact_num(?:ber)?s?)$",
    re.I,
)
_NRIC_COL = re.compile(
    r"(?:^|_)(?:nrics?|mykads?|ic_no|ic_num(?:ber)?s?|ic|"
    r"kad_pengenalan|passports?(?:_no|_num(?:ber)?)?)$",
    re.I,
)
_ACCOUNT_COL = re.compile(
    r"(?:^|_)(?:account_no|account_num(?:ber)?s?|acct_no|acct_num(?:ber)?s?|"
    r"bank_accounts?|bank_accts?|ibans?|acc_no|acc_num(?:ber)?s?)$",
    re.I,
)

_EMAIL_VALUE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)
_EMAIL_FIND = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
_NRIC_FIND = re.compile(r"\b(\d{6})-?(\d{2})-?(\d{4})\b")
_PHONE_FIND = re.compile(
    r"(?:(?<!\d)\+?60\s*1\d(?:[-\s]?\d){7,8}(?!\d)|"
    r"(?<!\d)01\d(?:[-\s]?\d){7,8}(?!\d)|"
    r"(?<!\d)\+[1-9]\d{9,14}(?!\d))"
)
_ACCOUNT_FIND = re.compile(r"(?<!\d)(\d{3,4}[-\s]\d{3,4}[-\s]\d{4,8})(?!\d)")
_DIGIT_RUN = re.compile(r"(?<!\d)(\d{10,16})(?!\d)")

_KEEP_KEYS = frozenset(
    {
        "answer_id",
        "badge",
        "abstained",
        "ask_mode",
        "as_of",
        "audit_id",
        "space_id",
        "session_id",
        "route",
        "plan_source",
        "drillthrough_token",
    }
)


def is_mask_token(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return MASK_TOKEN_RE.fullmatch(value.strip()) is not None


def _split_key(key: str) -> tuple[str | None, str]:
    raw = str(key or "")
    if "." in raw:
        table, col = raw.rsplit(".", 1)
        return table, col
    return None, raw


def _valid_yymmdd(text: str) -> bool:
    if len(text) != 6 or not text.isdigit():
        return False
    month = int(text[2:4])
    day = int(text[4:6])
    return 1 <= month <= 12 and 1 <= day <= 31


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or not (13 <= len(digits) <= 16):
        return False
    total = 0
    alt = False
    for ch in digits[::-1]:
        n = ord(ch) - 48
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def _kind_from_name(table: str | None, column: str) -> Kind | None:
    col = str(column or "").strip()
    if not col or _METRIC_SKIP.search(col):
        return None
    if _EMAIL_COL.search(col):
        return "email"
    if _NRIC_COL.search(col):
        return "nric"
    if _PHONE_COL.search(col):
        return "phone"
    if _ACCOUNT_COL.search(col):
        return "account"
    if _NAME_COL.search(col):
        return "name"
    if _PERSON_TABLE.search(str(table or "")) and re.fullmatch(r"name", col, re.I):
        return "name"
    return None


def _kind_from_one_value(raw: object) -> Kind | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and not raw.is_integer():
            return None
        digits = str(int(raw))
        if _valid_yymmdd(digits[:6]) and len(digits) == 12:
            return "nric"
        if 10 <= len(digits) <= 16 and (_luhn_ok(digits) or len(digits) >= 12):
            # Value-only account is conservative: Luhn cards or 12-16 digits.
            if len(digits) == 12 and _valid_yymmdd(digits[:6]):
                return "nric"
            if _luhn_ok(digits) or len(digits) >= 12:
                return "account"
        return None
    text = str(raw).strip()
    if not text or is_mask_token(text):
        return None
    if _EMAIL_VALUE.fullmatch(text):
        return "email"
    nric = _NRIC_FIND.fullmatch(text) or (
        _NRIC_FIND.fullmatch(_digits(text)) if _digits(text) == text else None
    )
    if nric and _valid_yymmdd(nric.group(1)):
        return "nric"
    digits = _digits(text)
    if re.fullmatch(r"\+?60\s*1\d(?:[-\s]?\d){7,8}", text) or re.fullmatch(
        r"01\d(?:[-\s]?\d){7,8}", text
    ):
        return "phone"
    if text.startswith("+") and 10 <= len(digits) <= 15:
        return "phone"
    if 10 <= len(digits) <= 11 and digits.startswith(("01", "60")):
        return "phone"
    if _ACCOUNT_FIND.fullmatch(text):
        return "account"
    if digits == text.replace(" ", "").replace("-", "") and 10 <= len(digits) <= 16:
        if len(digits) == 12 and _valid_yymmdd(digits[:6]):
            return "nric"
        if _luhn_ok(digits) or ("-" in text or " " in text):
            return "account"
    return None


def _kind_from_values(values: Sequence[object]) -> Kind | None:
    hits: dict[Kind, int] = {}
    for raw in values:
        kind = _kind_from_one_value(raw)
        if kind:
            hits[kind] = hits.get(kind, 0) + 1
    if not hits:
        return None
    return max(hits, key=lambda k: (hits[k], k))


def classify_column(
    column: str,
    values: Sequence[object] = (),
    *,
    table: str | None = None,
) -> Kind | None:
    """Return a PII kind, or None. Name signal wins; values catch misnamed columns."""
    named = _kind_from_name(table, column)
    if named:
        return named
    return _kind_from_values(values)


def column_is_pii(
    column: str,
    values: Sequence[object] = (),
    *,
    table: str | None = None,
) -> bool:
    """True when the column must not leave DMS as raw values. Errors fail closed."""
    try:
        return classify_column(column, values, table=table) is not None
    except Exception:
        return True


class Masker:
    """Stable tokens within one payload. Already-masked tokens pass through."""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], str] = {}
        self._n: dict[str, int] = {}

    def token(self, kind: str, raw: object) -> str:
        text = str(raw)
        if is_mask_token(text):
            return text.strip()
        use = kind if kind in KINDS else "unknown"
        key = (use, text.casefold().strip())
        got = self._seen.get(key)
        if got:
            return got
        self._n[use] = self._n.get(use, 0) + 1
        token = f"DMSMASK_{use}_{self._n[use]:02d}"
        self._seen[key] = token
        return token


def drop_pii_encodings(encodings: Mapping[str, Sequence[object]]) -> dict[str, list[str]]:
    """Drop sample lists for flagged columns. Detector errors drop that key."""
    out: dict[str, list[str]] = {}
    for key, vals in encodings.items():
        table, col = _split_key(str(key))
        sample = [v for v in vals if v is not None]
        if column_is_pii(col, sample, table=table):
            continue
        out[str(key)] = [str(v) for v in sample]
    return out


def drop_pii_bound_values(bound: Mapping[str, object]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, val in bound.items():
        if val is None:
            continue
        table, col = _split_key(str(key))
        if column_is_pii(col, [val], table=table):
            continue
        out[str(key)] = str(val)
    return out


def sanitize_retrieve_parts(parts: dict[str, Any]) -> dict[str, Any]:
    """Fail closed: a detector error wipes encodings / bound_values, not schema."""
    out = dict(parts)
    try:
        enc = out.get("encodings") or {}
        out["encodings"] = drop_pii_encodings(enc if isinstance(enc, dict) else {})
    except Exception:
        out["encodings"] = {}
    try:
        bound = out.get("bound_values") or {}
        out["bound_values"] = drop_pii_bound_values(bound if isinstance(bound, dict) else {})
    except Exception:
        out["bound_values"] = {}
    return out


def _column_kinds(rows: Sequence[Mapping[str, Any]]) -> dict[str, Kind]:
    kinds: dict[str, Kind] = {}
    if not rows:
        return kinds
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key in seen:
                continue
            seen.add(key)
            keys.append(key)
    for key in keys:
        sample = [row.get(key) for row in rows if key in row]
        table, col = _split_key(key)
        kind = None
        try:
            kind = classify_column(col, sample, table=table)
        except Exception:
            kind = "unknown"
        if kind:
            kinds[key] = kind
    return kinds


def _should_mask_cell(column: str, value: object, kinds: Mapping[str, Kind]) -> Kind | None:
    if value is None or isinstance(value, bool):
        return None
    if is_mask_token(value):
        return None
    kind = kinds.get(column)
    if kind:
        if isinstance(value, (int, float)) and kind == "name":
            return None
        return kind
    try:
        return _kind_from_one_value(value)
    except Exception:
        return "unknown"


def _apply_map(text: str, pairs: list[tuple[str, str]]) -> str:
    out = text
    for raw, token in sorted(pairs, key=lambda item: len(item[0]), reverse=True):
        if raw and raw in out:
            out = out.replace(raw, token)
    return out


def _scan_text(text: str, masker: Masker) -> str:
    out = text

    def _sub_email(match: re.Match[str]) -> str:
        return masker.token("email", match.group(0))

    out = _EMAIL_FIND.sub(_sub_email, out)

    def _sub_nric(match: re.Match[str]) -> str:
        if not _valid_yymmdd(match.group(1)):
            return match.group(0)
        return masker.token("nric", match.group(0))

    out = _NRIC_FIND.sub(_sub_nric, out)

    def _sub_phone(match: re.Match[str]) -> str:
        return masker.token("phone", match.group(0))

    out = _PHONE_FIND.sub(_sub_phone, out)

    def _sub_acct(match: re.Match[str]) -> str:
        return masker.token("account", match.group(0))

    out = _ACCOUNT_FIND.sub(_sub_acct, out)

    def _sub_run(match: re.Match[str]) -> str:
        digits = match.group(1)
        if len(digits) == 12 and _valid_yymmdd(digits[:6]):
            return masker.token("nric", match.group(0))
        if _luhn_ok(digits):
            return masker.token("account", match.group(0))
        return match.group(0)

    return _DIGIT_RUN.sub(_sub_run, out)


def _mask_walk(obj: Any, masker: Masker, kinds: Mapping[str, Kind]) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, val in obj.items():
            if key in _KEEP_KEYS:
                out[key] = val
                continue
            kind = _should_mask_cell(str(key), val, kinds)
            if kind and not isinstance(val, (dict, list)):
                out[key] = masker.token(kind, val)
            else:
                out[key] = _mask_walk(val, masker, kinds)
        return out
    if isinstance(obj, list):
        return [_mask_walk(item, masker, kinds) for item in obj]
    if isinstance(obj, str) and not is_mask_token(obj):
        kind = _kind_from_one_value(obj)
        if kind:
            return masker.token(kind, obj)
        return _scan_text(obj, masker)
    return obj


def mask_payload(
    *,
    text: str = "",
    rows: Sequence[Mapping[str, Any]] | None = None,
    values: Sequence[Mapping[str, Any]] | None = None,
    sources: Sequence[Mapping[str, Any]] | None = None,
    chart: Any = None,
    sql_used: str | None = None,
) -> dict[str, Any]:
    """Mask PII in customer-visible fields. Numeric aggregates stay numbers."""
    row_list = [dict(r) for r in (rows or []) if isinstance(r, dict)]
    kinds = _column_kinds(row_list)
    masker = Masker()
    masked_rows: list[dict[str, Any]] = []
    pairs: list[tuple[str, str]] = []
    for row in row_list:
        new_row: dict[str, Any] = {}
        for key, val in row.items():
            kind = _should_mask_cell(str(key), val, kinds)
            if kind:
                token = masker.token(kind, val)
                new_row[key] = token
                raw = str(val)
                if raw and not is_mask_token(raw):
                    pairs.append((raw, token))
            else:
                new_row[key] = val
        masked_rows.append(new_row)

    def _mask_str(raw: str) -> str:
        mapped = _apply_map(raw, pairs)
        return _scan_text(mapped, masker)

    masked_text = _mask_str(text or "")
    masked_sql = None if sql_used is None else _mask_str(str(sql_used))
    masked_values = _mask_walk(list(values or []), masker, kinds)
    masked_sources = _mask_walk(list(sources or []), masker, kinds)
    masked_chart = _mask_walk(chart, masker, kinds)
    return {
        "text": masked_text,
        "rows": masked_rows,
        "values": masked_values,
        "sources": masked_sources,
        "chart": masked_chart,
        "sql_used": masked_sql,
    }


def _blank_strings(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: v if k in _KEEP_KEYS else _blank_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_blank_strings(item) for item in obj]
    if isinstance(obj, str) and not is_mask_token(obj):
        return "DMSMASK_unknown_00"
    return obj


def mask_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with PII masked in text/rows/values/sources/chart/sql_used."""
    env = dict(envelope)
    got = mask_payload(
        text=str(env.get("text") or ""),
        rows=list(env.get("rows") or []) if isinstance(env.get("rows"), list) else [],
        values=list(env.get("values") or []) if isinstance(env.get("values"), list) else [],
        sources=(
            list(env.get("contributing_sources") or [])
            if isinstance(env.get("contributing_sources"), list)
            else []
        ),
        chart=env.get("chart"),
        sql_used=env.get("sql_used") if env.get("sql_used") is not None else None,
    )
    env["text"] = got["text"]
    env["rows"] = got["rows"]
    env["values"] = got["values"]
    env["contributing_sources"] = got["sources"]
    env["chart"] = got["chart"]
    if "sql_used" in env:
        env["sql_used"] = got["sql_used"]
    receipt = env.get("audit_receipt")
    if isinstance(receipt, dict):
        rec = dict(receipt)
        include = rec.get("include")
        if isinstance(include, dict) and isinstance(include.get("rows"), list):
            inc = dict(include)
            inc["rows"] = got["rows"]
            rec["include"] = inc
        env["audit_receipt"] = rec
    return env


def fail_closed_mask_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return mask_envelope(envelope)
    except Exception:
        env = dict(envelope)
        for key in ("text", "rows", "values", "contributing_sources", "chart", "sql_used"):
            if key in env:
                env[key] = _blank_strings(env[key])
        return env


def fail_closed_mask_payload(**kwargs: Any) -> dict[str, Any]:
    try:
        return mask_payload(**kwargs)
    except Exception:
        rows = [dict(r) for r in (kwargs.get("rows") or []) if isinstance(r, dict)]
        blank_rows = _blank_strings(rows)
        return {
            "text": "DMSMASK_unknown_00" if kwargs.get("text") else "",
            "rows": blank_rows,
            "values": _blank_strings(list(kwargs.get("values") or [])),
            "sources": _blank_strings(list(kwargs.get("sources") or [])),
            "chart": _blank_strings(kwargs.get("chart")),
            "sql_used": (
                "DMSMASK_unknown_00" if kwargs.get("sql_used") is not None else None
            ),
        }
