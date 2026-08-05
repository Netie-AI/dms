"""Single constructor for the customer-facing answer envelope (Phase 0).

Every path that builds an ask envelope — live Cortex map or demo router —
must call ``build_answer_envelope``. AST invariants fail the build otherwise.

E1–E9 live in ``assert_envelope_valid``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

ALLOWED_BADGES = frozenset(
    {
        "L0_CERTIFIED",
        "L1_GOVERNED_METRIC",
        "L2_VALIDATED",
        "L2_ANOMALOUS",
        "ABSTAIN",
    }
)

_BADGE_MAP = {
    "certified": "L0_CERTIFIED",
    "certified_metric": "L0_CERTIFIED",
    "governed_metric": "L1_GOVERNED_METRIC",
    "catalog": "L1_GOVERNED_METRIC",  # META-01 semantic browse — not FreeRoute SQL
    "query_skill": "L2_VALIDATED",
    "session": "L2_VALIDATED",
    "generated": "L2_VALIDATED",
    "l2_validated": "L2_VALIDATED",
    "l2_anomalous": "L2_ANOMALOUS",
    "abstain": "ABSTAIN",
    "blocked": "ABSTAIN",
}

_NUMBER_IN_TEXT = re.compile(
    r"(?<![A-Za-z0-9_])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|-?\d+)(?![A-Za-z0-9_])"
)

_EXT_KIND = {
    ".pdf": "pdf",
    ".csv": "csv",
    ".txt": "csv",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".parquet": "parquet",
}

_DOC_ROUTE_KINDS = frozenset({"doc_rag", "document", "rag", "document_retrieval"})


_SQL_COMMENT = re.compile(r"--[^\n]*")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _executed_query(sql: str | None) -> bool:
    """True when ``sql_used`` holds a statement, not a placeholder comment.

    ``map_ask_response_to_envelope`` stamps ``-- document retrieval (no SQL)``
    when a path returns no query, so E3 stays satisfied. That stub is precisely
    the signal that nothing was executed.
    """
    if not sql:
        return False
    return bool(_SQL_COMMENT.sub("", str(sql)).strip())


def _money_like(text: str) -> list[float]:
    """Decimal / thousand-separated literals — the ones a reader treats as figures.

    Same filter E4 uses: bare integers are ranks, years and SKU tails.
    """
    out: list[float] = []
    for m in _NUMBER_IN_TEXT.finditer(text or ""):
        raw = m.group(1)
        if "," not in raw and "." not in raw:
            continue
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) < 0.011 or abs(a - b) / max(abs(b), 1e-9) < 1e-4


def _cited_figures(
    text: str,
    sources: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Promote figures the cited snippets actually contain into ``values[]``.

    An extracted number is a real value of the answer and it carries a source,
    so it belongs in ``values[]``. Doing this keeps E4 satisfied without
    weakening it — otherwise quoting "the penalty is 5,000.00" from a contract
    is refused, and a control that refuses legitimate work is a failure.
    """
    cited: list[float] = []
    for s in sources:
        cited.extend(_money_like(str(s.get("snippet") or "")))
    if not cited:
        return []

    have = [
        float(v["value"])
        for v in existing
        if isinstance(v, dict)
        and isinstance(v.get("value"), (int, float))
        and not isinstance(v.get("value"), bool)
    ]
    out: list[dict[str, Any]] = []
    idx = len(existing)
    for n in _money_like(text):
        if any(_close(n, h) for h in have):
            continue
        if not any(_close(n, c) for c in cited):
            continue
        out.append({"id": f"v{idx}", "value": n, "label": "quoted"})
        have.append(n)
        idx += 1
    return out


def unbacked_numbers(
    text: str,
    values: list[dict[str, Any]] | None,
    sources: list[dict[str, Any]] | None,
) -> list[float]:
    """Figures a no-query path renders that no cited snippet actually contains.

    A retrieval path may *quote* a number it can point at. It may not *compute*
    one: a sum, mean or top-N total appears verbatim in no snippet, which is how
    a model-authored total is told apart from an extracted literal.
    """
    cited: list[float] = []
    for s in sources or []:
        cited.extend(_money_like(str(s.get("snippet") or "")))

    candidates = list(_money_like(text))
    for v in values or []:
        if not isinstance(v, dict):
            continue
        raw = v.get("value")
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            continue
        if str(v.get("label")) == "row_count":
            continue  # cardinality of what was retrieved, not a claim about the data
        candidates.append(float(raw))

    return [n for n in candidates if not any(_close(n, c) for c in cited)]


def normalize_badge(raw: str | None, *, abstained: bool) -> str:
    if abstained:
        return "ABSTAIN"
    key = (raw or "abstain").strip()
    if key in ALLOWED_BADGES:
        return key
    return _BADGE_MAP.get(key.lower(), "L2_VALIDATED")


def _ensure_values(
    values: list[dict[str, Any]] | None,
    rows: list[dict[str, Any]] | None,
    *,
    abstained: bool,
) -> list[dict[str, Any]]:
    """Promote numeric cells so E4 never sees orphan decimals in prose.

    Listings (top-N, spend-by-country, low-stock) print many numbers in ``text``.
    A single ``v0`` from the first row is insufficient — harvest numerics from
    every row (capped) and merge with any caller-supplied values.
    """
    if abstained:
        return []
    out: list[dict[str, Any]] = [dict(v) for v in (values or []) if isinstance(v, dict)]
    seen: set[float] = set()
    for v in out:
        raw = v.get("value")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            seen.add(float(raw))

    rows = list(rows or [])
    idx = len(out)
    for row in rows[:50]:
        for key, val in row.items():
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                continue
            fv = float(val)
            if any(abs(fv - s) < 1e-9 for s in seen):
                continue
            out.append({"id": f"v{idx}", "value": fv, "label": str(key)})
            seen.add(fv)
            idx += 1
            if idx >= 80:
                break
        if idx >= 80:
            break

    if out:
        return out
    if not rows:
        return [{"id": "v_count", "value": 0.0, "label": "row_count"}]
    return [{"id": "v_count", "value": float(len(rows)), "label": "row_count"}]


def build_answer_envelope(
    *,
    answer_id: str,
    text: str,
    badge: str,
    abstained: bool | None = None,
    values: list[dict[str, Any]] | None = None,
    sql_used: str | None = None,
    assumptions: list[str] | str | None = None,
    as_of: str | None = None,
    contributing_sources: list[dict[str, Any]] | None = None,
    drillthrough_token: str | None = None,
    audit_id: str | None = None,
    ask_mode: str = "live",
    demo_fallback_used: bool = False,
    space_id: str | None = None,
    session_id: str | None = None,
    rows: list[dict[str, Any]] | None = None,
    chart: dict[str, Any] | None = None,
    suggestions: list[str] | None = None,
    route: str | None = None,
    demo_fallback_banner: bool | None = None,
    grounded_tables: list[str] | None = None,
) -> dict[str, Any]:
    """Sole envelope constructor — badge and abstained stay in lockstep."""
    badge_norm_probe = normalize_badge(badge, abstained=False)
    if abstained is None:
        abstained = badge_norm_probe == "ABSTAIN" or str(badge).upper() == "ABSTAIN"
    badge_out = normalize_badge(badge, abstained=bool(abstained))
    abstained = badge_out == "ABSTAIN"

    if isinstance(assumptions, str):
        assumptions_list = [assumptions] if assumptions.strip() else []
    else:
        assumptions_list = [str(a) for a in (assumptions or []) if a is not None]

    # Normalize before E9 so Cortex ``content`` becomes ``snippet`` — otherwise
    # every doc path looks uncited and the quote-allowed control fails.
    sources = normalize_contributing_sources(
        contributing_sources,
        space_id=space_id,
    )
    rows_out = [dict(r) for r in (rows or []) if isinstance(r, dict)]
    values_out = _ensure_values(values, rows_out, abstained=abstained)

    # E9 — a path that executed no query has no authority to render a figure it
    # cannot point at. Demote rather than certify: a green badge over a prose
    # total is what put a wrong number in front of a client (F26).
    if not abstained and not _executed_query(sql_used):
        values_out = values_out + _cited_figures(text or "", sources, values_out)
        if unbacked_numbers(text or "", values_out, sources):
            named = ", ".join(
                dict.fromkeys(
                    str(s.get("container")) for s in sources if s.get("container")
                )
            )
            badge_out = "ABSTAIN"
            abstained = True
            text = (
                f"Found relevant text in {named}, but could not certify a figure from it."
                if named
                else "Could not certify a figure from the retrieved text."
            ) + (
                " No executed query backs these numbers, and they do not appear in the"
                " cited text. Ask this as a data question over a granted table to get a"
                " certified number."
            )
            assumptions_list.append("L3 retrieval path: uncited figures withheld (E9)")

    if abstained:
        values_out = []
        sources = []
        drillthrough_token = None
        rows_out = []
        chart = None
    elif sources and not drillthrough_token and ask_mode == "demo":
        # Demo has no Cortex drillthrough mint — opaque stub satisfies E7 shape.
        drillthrough_token = "demo_no_drillthrough"

    if demo_fallback_used and demo_fallback_banner is None:
        demo_fallback_banner = True

    # Non-abstain always keeps sql_used when provided; E3 requires it.
    # Doc-RAG has no statement — keep the comment stub so E3 is satisfied while
    # ``_executed_query`` still returns False (E9 authority stays honest).
    sql_out = sql_used
    if not abstained and not _executed_query(sql_out):
        sql_out = "-- document retrieval (no SQL)"

    env: dict[str, Any] = {
        "answer_id": answer_id,
        "text": text or "",
        "values": values_out,
        "badge": badge_out,
        "abstained": abstained,
        "sql_used": sql_out,
        "assumptions": assumptions_list,
        "as_of": as_of or _now(),
        "contributing_sources": sources,
        "drillthrough_token": drillthrough_token,
        "audit_id": audit_id or answer_id,
        "ask_mode": ask_mode,
        "demo_fallback_used": bool(demo_fallback_used),
        "space_id": space_id,
        "session_id": session_id,
        "rows": rows_out,
        "chart": chart,
        "suggestions": list(suggestions or []),
        "route": route,
        # What the manifest actually granted this turn. The UI used to count its
        # own selection list, which said "Grounded in 1 file" over a manifest
        # holding all six demo tables (dms#5). The count a viewer reads has to
        # come from the manifest, not from the request that asked for it.
        "grounded_tables": list(grounded_tables or []),
    }
    if demo_fallback_banner is not None:
        env["demo_fallback_banner"] = bool(demo_fallback_banner)
    return env


def _infer_source_kind(name: str, raw_kind: str | None) -> str:
    if raw_kind:
        key = str(raw_kind).lower()
        if key in ("sql", "xlsx", "csv", "parquet", "pdf", "api"):
            return key
        if key in ("document", "doc", "unstructured"):
            return "pdf"
    lower = name.lower()
    for ext, kind in _EXT_KIND.items():
        if lower.endswith(ext):
            return kind
    return "sql"


def normalize_contributing_sources(
    raw: list[Any] | None,
    *,
    space_id: str | None = None,
) -> list[dict[str, Any]]:
    """Map Cortex doc-RAG / SQL provenance into DMS Source panel cards (RAG-04)."""
    items = list(raw or [])
    out: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        if isinstance(item, str):
            name = item.strip()
            if not name:
                continue
            base = name.rsplit("/", 1)[-1] if "/" in name else name
            out.append(
                {
                    "ref_id": f"doc_{i}",
                    "container": base,
                    "kind": _infer_source_kind(name, None),
                    "row_count": 1,
                    "contribution": 1.0 / max(len(items), 1),
                    "origin_uri": name,
                    **({"space_id": space_id} if space_id else {}),
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        d = dict(item)
        ref_id = str(d.get("ref_id") or d.get("source_id") or d.get("id") or f"src_{i}")
        container = str(
            d.get("container") or d.get("filename") or d.get("name") or d.get("ref") or ref_id
        )
        kind = _infer_source_kind(container, d.get("kind"))
        contrib = d.get("contribution")
        if contrib is None:
            pct = d.get("contribution_pct")
            contrib = float(pct) / 100.0 if pct is not None else 1.0 / max(len(items), 1)
        row_count = d.get("row_count")
        if row_count is None:
            row_count = d.get("chunk_count") or 1
        src: dict[str, Any] = {
            "ref_id": ref_id,
            "container": container,
            "kind": kind,
            "row_count": int(row_count),
            "contribution": float(contrib),
        }
        if d.get("member"):
            src["member"] = str(d["member"])
        if d.get("origin_uri"):
            src["origin_uri"] = str(d["origin_uri"])
        snippet = d.get("snippet") or d.get("content")
        if snippet:
            src["snippet"] = str(snippet)[:500]
        if d.get("chunk_index") is not None:
            src["chunk_index"] = int(d["chunk_index"])
        if space_id:
            src["space_id"] = space_id
        out.append(src)
    return out


def _parse_numbers(text: str) -> list[float]:
    found: list[float] = []
    for m in _NUMBER_IN_TEXT.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        try:
            found.append(float(raw))
        except ValueError:
            continue
    return found


def assert_envelope_valid(envelope: dict[str, Any]) -> None:
    """E1–E9 property assertions. Raises AssertionError on violation."""
    badge = envelope.get("badge")
    abstained = bool(envelope.get("abstained"))
    values = envelope.get("values") or []
    sources = envelope.get("contributing_sources") or []
    token = envelope.get("drillthrough_token")
    text = str(envelope.get("text") or "")
    as_of = envelope.get("as_of")
    sql_used = envelope.get("sql_used")
    audit_id = envelope.get("audit_id") or envelope.get("answer_id")

    # E5
    assert badge in ALLOWED_BADGES, f"E5: badge {badge!r} not in {sorted(ALLOWED_BADGES)}"

    # E1
    if abstained:
        assert badge == "ABSTAIN", f"E1: abstained=true but badge={badge!r}"
    else:
        assert badge != "ABSTAIN", "E1: badge=ABSTAIN but abstained=false"

    # E2
    if abstained:
        assert values == [] or values is None, "E2: abstain must have empty values[]"
        assert sources == [] or sources is None, (
            "E2: abstain must have empty contributing_sources[]"
        )
        assert token in (None, ""), "E2: abstain must have null drillthrough_token"

    # E3
    if not abstained:
        assert values, "E3: non-abstain requires non-empty values[]"
        assert isinstance(sql_used, str) and sql_used.strip(), "E3: non-abstain requires sql_used"
        assert isinstance(audit_id, str) and audit_id.strip(), "E3: non-abstain requires audit_id"

    # E4 — money-like / decimal literals in prose must appear in values[]
    if values and text and not abstained:
        value_nums = [
            float(v["value"])
            for v in values
            if isinstance(v, dict) and isinstance(v.get("value"), (int, float))
        ]
        for m in _NUMBER_IN_TEXT.finditer(text):
            raw = m.group(1)
            if "," not in raw and "." not in raw:
                continue  # skip bare integers (ranks, SKU tails, years)
            try:
                n = float(raw.replace(",", ""))
            except ValueError:
                continue
            assert value_nums and any(
                abs(n - vn) < 0.011 or abs(n - vn) / max(abs(vn), 1e-9) < 1e-4
                for vn in value_nums
            ), f"E4: orphan number {n} in text not in values[]={value_nums}"

    # E6
    if envelope.get("demo_fallback_used") is True:
        assert envelope.get("demo_fallback_banner") is True, (
            "E6: demo_fallback_used requires demo_fallback_banner"
        )

    # E7
    if sources:
        assert token, "E7: contributing_sources non-empty requires drillthrough_token"
        # Live verify hook — demo tokens may be opaque stubs.
        if envelope.get("ask_mode") == "live" and not str(token).startswith("demo_"):
            assert len(str(token)) >= 8, "E7: drillthrough_token looks too short to verify"

    # E9 — no executed query means no authority to state a figure. A retrieval
    # path may quote a number a snippet contains; it may never compute one.
    if not abstained and not _executed_query(sql_used):
        unbacked = unbacked_numbers(text, values, sources)
        assert not unbacked, (
            f"E9: answer with no executed query renders uncited figures {unbacked} — "
            "a retrieval path may quote a number, never compute one"
        )

    # E8
    assert isinstance(as_of, str) and as_of.strip(), "E8: as_of required"
    try:
        # Accept ...Z ISO
        ts = as_of.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        assert dt <= datetime.now(UTC).replace(microsecond=0) + __import__("datetime").timedelta(
            minutes=5
        ), "E8: as_of must not be in the future"
    except ValueError as exc:
        raise AssertionError(f"E8: as_of not parseable: {as_of!r}") from exc


__all__ = [
    "ALLOWED_BADGES",
    "assert_envelope_valid",
    "build_answer_envelope",
    "normalize_badge",
    "normalize_contributing_sources",
    "unbacked_numbers",
]
