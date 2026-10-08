"""One clarifying question when an ask is structurally ambiguous.

Ambiguity is retrieval score gaps, a planner payload that already lists
competing interpretations, or a missing time range on a candidate that has
a temporal column. The question text is never matched with a phrase list.

The model writes the question and 2-4 options. Each option must bind a
granted table, column, measure, or time range. Invalid options are dropped.
Fewer than two valid options falls through to the existing ask path.

Flag ``DMS_ASK_CLARIFY`` defaults off. Off means this module is not called.

ponytail: a candidate is clearly ahead when its score is at least twice the
runner-up. Ceiling: near-ties that should still clarify. Upgrade: a calibrated
margin from the retrieval service, not a new scorer.

ponytail: token counts fall back to whitespace splits when the model omits
usage. Ceiling: that is not tokenizer-accurate. Upgrade: the provider usage
block only.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dms_core.clarify_stats import record_clarify_tokens
from dms_core.pii import column_is_pii, fail_closed_mask_payload

from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.semantic_retrieve import _safe_ident, _score, question_tokens

#: Same lifetime as ``SessionContext.ttl_seconds``. One store, one TTL.
CLARIFY_TTL_S = 900
_CLEAR_RATIO = 2.0
_MIN_SECRET = 4
_MAX_OPTIONS = 4
_MIN_OPTIONS = 2
_SAMPLE_CAP = 8

#: information_schema data_type names. Not question phrases.
_TEMPORAL = frozenset(
    {
        "DATE",
        "TIME",
        "TIMESTAMP",
        "TIMESTAMP WITH TIME ZONE",
        "TIMESTAMP_NS",
        "TIMESTAMP_S",
        "TIMESTAMP_MS",
        "DATETIME",
        "TIMESTAMPTZ",
    }
)
_NUMERIC = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "DECIMAL",
        "NUMERIC",
    }
)

_EXTRA_CALLS = 0


def clarify_enabled() -> bool:
    raw = os.environ.get("DMS_ASK_CLARIFY", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def extra_model_calls() -> int:
    return _EXTRA_CALLS


def reset_extra_model_calls() -> None:
    global _EXTRA_CALLS
    _EXTRA_CALLS = 0


def _now() -> float:
    return time.time()


@dataclass
class Gate:
    envelope: dict[str, Any] | None = None
    payload: Any = None
    prefetched: bool = False
    check_planner: bool = False


class ClarifyWriterUnavailable(Exception):
    """OpenVault could not write the question. The ask abstains. It does not serve."""


@dataclass
class _Col:
    table: str
    name: str
    type_name: str
    score: int
    cleared: bool


def _note_call() -> None:
    global _EXTRA_CALLS
    _EXTRA_CALLS += 1


def _temporal(type_name: str) -> bool:
    return type_name.strip().upper() in _TEMPORAL


def _numeric(type_name: str) -> bool:
    head = type_name.strip().upper().split("(", 1)[0].strip()
    return head in _NUMERIC


def _load_columns(warehouse: Path | None) -> list[tuple[str, str, str]]:
    if warehouse is None or not Path(warehouse).is_file():
        return []
    from dms_executor.demo_warehouse import connect_file

    con = connect_file(Path(warehouse))
    try:
        rows = con.execute(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns"
        ).fetchall()
    except Exception:  # noqa: BLE001 -- no schema, treat the ask as clear
        return []
    finally:
        con.close()
    out: list[tuple[str, str, str]] = []
    for table_name, column_name, data_type in rows:
        table = _safe_ident(str(table_name))
        col = _safe_ident(str(column_name))
        if table and col:
            out.append((table, col, str(data_type or "")))
    return out


def _samples(
    warehouse: Path | None, table: str, column: str
) -> list[str]:
    if warehouse is None or not _safe_ident(table) or not _safe_ident(column):
        return []
    from dms_executor.demo_warehouse import connect_file

    con = connect_file(Path(warehouse))
    try:
        fetched = con.execute(
            f"SELECT DISTINCT CAST({column} AS VARCHAR) FROM {table} "
            f"WHERE {column} IS NOT NULL LIMIT {_SAMPLE_CAP}"
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    finally:
        con.close()
    return [str(r[0]) for r in fetched if r and r[0] is not None]


def _schema_table_scores(ctx: dict[str, Any] | None) -> dict[str, int]:
    scores: dict[str, int] = {}
    if not isinstance(ctx, dict):
        return scores
    for item in ctx.get("schema") or []:
        if not isinstance(item, dict):
            continue
        table = _safe_ident(str(item.get("table") or ""))
        if not table:
            continue
        try:
            scores[table] = int(item.get("score") or 0)
        except (TypeError, ValueError):
            continue
    return scores


def _candidates(
    question: str,
    columns: list[_Col],
    table_scores: dict[str, int],
    measures: dict[str, Any],
) -> list[dict[str, Any]]:
    toks = question_tokens(question)
    found: list[dict[str, Any]] = []
    for col in columns:
        if not col.cleared or not _numeric(col.type_name):
            continue
        score = _score(col.table, toks) + _score(col.name, toks)
        if score <= 0 and table_scores.get(col.table, 0) <= 0:
            continue
        if score <= 0:
            score = table_scores.get(col.table, 0)
        found.append(
            {
                "kind": "measure",
                "name": col.name,
                "table": col.table,
                "score": score,
            }
        )
    if isinstance(measures, dict):
        for name, spec in measures.items():
            ident = _safe_ident(str(name))
            if not ident:
                continue
            score = _score(ident, toks)
            desc = ""
            if isinstance(spec, dict):
                desc = str(spec.get("description") or "")
            score += _score(desc, toks)
            if score <= 0:
                continue
            grain = ""
            if isinstance(spec, dict):
                grain = str(spec.get("grain") or "")
            found.append(
                {
                    "kind": "measure",
                    "name": ident,
                    "table": _safe_ident(grain) or "",
                    "score": score,
                }
            )
    return found


def _clearly_ahead(scored: list[dict[str, Any]]) -> bool:
    ranked = sorted((int(c["score"]) for c in scored if int(c["score"]) > 0), reverse=True)
    if len(ranked) <= 1:
        return True
    best, second = ranked[0], ranked[1]
    return best >= _CLEAR_RATIO * second


def _time_missing(question_columns: list[_Col], scored: list[dict[str, Any]]) -> bool:
    """True when a plausible candidate's table has a temporal column and no range.

    No scored candidate means a time range is not needed. A range is present
    only as a structured binding already on the ask. The question string is
    not scanned.
    """
    if not scored:
        return False
    if _clearly_ahead(scored):
        top = max(scored, key=lambda c: int(c["score"]))
        tables = {str(top.get("table") or "")} if top.get("table") else set()
    else:
        tables = {str(c.get("table") or "") for c in scored if c.get("table")}
    tables.discard("")
    if not tables:
        return False
    return any(col.table in tables and _temporal(col.type_name) for col in question_columns)


def _name_pii(table: str, col: str) -> tuple[bool, bool]:
    """(is_pii, detector_failed). A raise clears nothing and adds no value."""
    try:
        return bool(column_is_pii(col, (), table=table)), False
    except Exception:  # noqa: BLE001 -- fail closed, do not serve
        return True, True


def _granted_columns(
    warehouse: Path | None,
    grantable: set[str],
) -> tuple[list[_Col], set[str], bool]:
    """Metadata and name-only mask. No ``SELECT DISTINCT``.

    Ungranted tables stay on the denylist by name. A detector raise marks
    that column uncleared and sets the failure flag. It does not sample it.
    """
    granted = {t for t in grantable if _safe_ident(t)}
    granted_cols: list[_Col] = []
    secrets: set[str] = set()
    pii_failed = False
    by_table: dict[str, list[tuple[str, str]]] = {}
    for table, col, typ in _load_columns(warehouse):
        by_table.setdefault(table, []).append((col, typ))
        if table not in granted:
            if len(table) >= _MIN_SECRET:
                secrets.add(table)
            if len(col) >= _MIN_SECRET:
                secrets.add(col)
    for table, pairs in by_table.items():
        if table not in granted:
            continue
        for col, typ in pairs:
            pii, failed = _name_pii(table, col)
            if failed:
                pii_failed = True
            granted_cols.append(
                _Col(
                    table=table,
                    name=col,
                    type_name=typ,
                    score=0,
                    cleared=not pii,
                )
            )
    return granted_cols, secrets, pii_failed


def structural_signal(
    question: str,
    *,
    warehouse: Path | None,
    grantable: set[str],
    ctx: dict[str, Any] | None,
) -> tuple[bool, list[_Col], set[str], bool]:
    """Return (clear, granted columns, secret tokens, detector_failed).

    ``clear`` means one candidate is ahead and a time range is present or not
    needed. That path makes zero clarify model calls and zero sample queries.
    Samples run only when the ask is not clear, and only on granted columns
    the name check already cleared. A detector raise is not clear: the ask
    must not be served.
    """
    granted_cols, secrets, pii_failed = _granted_columns(warehouse, grantable)
    table_scores = _schema_table_scores(ctx)
    measures: dict[str, Any] = {}
    if isinstance(ctx, dict) and isinstance(ctx.get("measures"), dict):
        measures = ctx["measures"]
    scored = _candidates(question, granted_cols, table_scores, measures)
    clear = _clearly_ahead(scored) and not _time_missing(granted_cols, scored)
    if pii_failed:
        clear = False
    if not clear:
        for col in granted_cols:
            if not col.cleared:
                continue
            samples = _samples(warehouse, col.table, col.name)
            try:
                value_pii = column_is_pii(col.name, samples, table=col.table)
            except Exception:  # noqa: BLE001 -- this column contributes nothing
                pii_failed = True
                col.cleared = False
                continue
            if value_pii:
                col.cleared = False
                for val in samples:
                    if len(val) >= _MIN_SECRET:
                        secrets.add(val)
        scored = _candidates(question, granted_cols, table_scores, measures)
        clear = _clearly_ahead(scored) and not _time_missing(granted_cols, scored)
        if pii_failed:
            clear = False
    # Drop secrets that are also granted identifiers the user may say.
    allowed_names = {c.table for c in granted_cols} | {c.name for c in granted_cols}
    allowed_names |= {str(m) for m in measures}
    folded = {n.casefold() for n in allowed_names}
    secrets = {s for s in secrets if s.casefold() not in folded}
    return clear, granted_cols, secrets, pii_failed


def _iso_day(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        if len(text) == 10:
            date.fromisoformat(text)
            return True
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _binding_ok(binding: dict[str, Any], cols: list[_Col], measures: set[str]) -> bool:
    kind = str(binding.get("kind") or "")
    tables = {c.table for c in cols}
    if kind == "table":
        return str(binding.get("name") or "") in tables
    if kind == "column":
        table = str(binding.get("table") or "")
        name = str(binding.get("name") or "")
        return any(c.table == table and c.name == name and c.cleared for c in cols)
    if kind == "measure":
        name = str(binding.get("name") or "")
        table = str(binding.get("table") or "")
        if name in measures and (not table or table in tables):
            return True
        return any(
            c.name == name
            and c.cleared
            and _numeric(c.type_name)
            and (not table or c.table == table)
            for c in cols
        )
    if kind == "time_range":
        table = str(binding.get("table") or "")
        name = str(binding.get("column") or "")
        if not _iso_day(binding.get("start")) or not _iso_day(binding.get("end")):
            return False
        return any(
            c.table == table and c.name == name and c.cleared and _temporal(c.type_name)
            for c in cols
        )
    return False


def _leaks(blob: str, secrets: set[str]) -> bool:
    folded = blob.casefold()
    for secret in secrets:
        if len(secret) < _MIN_SECRET:
            continue
        if secret.casefold() in folded:
            return True
    return False


def _parse_model(raw: Any) -> tuple[dict[str, Any] | None, int, int]:
    prompt_tokens = 0
    completion_tokens = 0
    payload: Any = raw
    if isinstance(raw, dict) and ("question" in raw or "options" in raw or "raw" in raw):
        prompt_tokens = int(raw.get("prompt_tokens") or 0)
        completion_tokens = int(raw.get("completion_tokens") or 0)
        if "raw" in raw and "options" not in raw:
            payload = raw.get("raw")
        elif "options" in raw or "question" in raw:
            payload = {k: raw[k] for k in ("question", "options") if k in raw}
    elif hasattr(raw, "raw"):
        prompt_tokens = int(getattr(raw, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(raw, "completion_tokens", 0) or 0)
        payload = getattr(raw, "raw")
    if isinstance(payload, dict):
        data = payload
        text = json.dumps(data)
    else:
        text = str(payload or "")
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None, prompt_tokens, completion_tokens
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None, prompt_tokens, completion_tokens
    if not isinstance(data, dict):
        return None, prompt_tokens, completion_tokens
    if prompt_tokens <= 0:
        prompt_tokens = 0
    if completion_tokens <= 0:
        completion_tokens = len(text.split())
    return data, prompt_tokens, completion_tokens


def _prompt(question: str, cols: list[_Col], measures: dict[str, Any]) -> str:
    tables: dict[str, list[dict[str, str]]] = {}
    for col in cols:
        if not col.cleared:
            continue
        role = "column"
        if _numeric(col.type_name):
            role = "measure"
        elif _temporal(col.type_name):
            role = "time"
        tables.setdefault(col.table, []).append(
            {"name": col.name, "type": col.type_name, "role": role}
        )
    measure_names: list[str] = []
    if isinstance(measures, dict):
        measure_names = [str(n) for n in measures if _safe_ident(str(n))]
    body = {
        "question": question,
        "tables": [
            {"name": name, "columns": cols_}
            for name, cols_ in sorted(tables.items())
        ],
        "measures": measure_names,
    }
    return (
        "The ask is ambiguous. Write one short clarifying question and 2 to 4 "
        "options. Every option binding must name a table, column, measure, or "
        "time range from the schema JSON. Do not invent names or quote stored "
        "cell values. Reply with JSON "
        '{"question":"...","options":[{"id":"...","label":"...","binding":{...}}]}.\n'
        + json.dumps(body, sort_keys=True)
    )


def _validate_options(
    data: dict[str, Any],
    cols: list[_Col],
    measures: set[str],
    secrets: set[str],
) -> tuple[str, list[dict[str, Any]]]:
    question = str(data.get("question") or "").strip()
    raw_opts = data.get("options")
    if not question or not isinstance(raw_opts, list):
        return "", []
    if _leaks(question, secrets):
        return "", []
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_opts:
        if len(kept) >= _MAX_OPTIONS:
            break
        if not isinstance(item, dict):
            continue
        binding = item.get("binding")
        label = str(item.get("label") or "").strip()
        if not label or not isinstance(binding, dict):
            continue
        if not _binding_ok(binding, cols, measures):
            continue
        # Binding keys are ours. Only the label and the values can leak a
        # secret; a key named "name" must not drop every option.
        blob = label + "\n" + "\n".join(str(v) for v in binding.values())
        if _leaks(blob, secrets):
            continue
        oid = str(item.get("id") or "").strip() or f"opt_{len(kept) + 1}"
        if oid in seen:
            continue
        seen.add(oid)
        kept.append({"id": oid, "label": label, "binding": binding})
    if len(kept) < _MIN_OPTIONS:
        return "", []
    return question, kept


def _mask_stored_options(
    options: list[dict[str, Any]], cols: list[_Col]
) -> list[dict[str, Any]]:
    """Mask each option once. The store and the envelope keep this list.

    A time-range boundary on a cleared temporal column is a period, not a
    birth date, so the same ISO string is what the UI shows and what binds.
    """
    masked: list[dict[str, Any]] = []
    for opt in options:
        binding = opt.get("binding")
        if not isinstance(binding, dict):
            continue
        label = str(opt.get("label") or "")
        row: dict[str, Any] = {}
        sources: dict[str, frozenset[str]] = {}
        date_src: frozenset[str] | None = None
        if str(binding.get("kind") or "") == "time_range":
            table = str(binding.get("table") or "")
            column = str(binding.get("column") or "")
            typed = any(
                c.table == table
                and c.name == column
                and c.cleared
                and _temporal(c.type_name)
                for c in cols
            )
            if typed:
                date_src = frozenset({f"{table}.{column}"})
        for key, val in binding.items():
            if key == "kind":
                continue
            row[key] = val
            if date_src is not None and key in {"start", "end"}:
                sources[key] = date_src
        got = fail_closed_mask_payload(
            text=label,
            rows=[row] if row else [],
            column_sources=sources or None,
        )
        new_binding = dict(binding)
        rows = got.get("rows") if isinstance(got, dict) else None
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            for key, val in rows[0].items():
                new_binding[key] = val
        new_label = str(got.get("text") if isinstance(got, dict) else "").strip()
        if not new_label:
            continue
        masked.append({"id": opt["id"], "label": new_label, "binding": new_binding})
    if len(masked) < _MIN_OPTIONS:
        return []
    return masked


def _envelope(
    *,
    clarify_id: str,
    clarify_question: str,
    original: str,
    options: list[dict[str, Any]],
    space_id: str | None,
    session_id: str | None,
    prompt_tokens: int,
    completion_tokens: int,
) -> dict[str, Any]:
    env = build_answer_envelope(
        answer_id=f"ans_{clarify_id}",
        text=clarify_question,
        badge="ABSTAIN",
        abstained=True,
        values=[],
        rows=[],
        sql_used=None,
        assumptions=["clarify"],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="clarify",
        question=original,
    )
    env["status"] = "clarify"
    env["clarify_id"] = clarify_id
    env["question"] = clarify_question
    env["original_question"] = original
    env["options"] = options
    env["rows"] = []
    env["values"] = []
    env["clarify_prompt_tokens"] = int(prompt_tokens)
    env["clarify_completion_tokens"] = int(completion_tokens)
    assert_envelope_valid(env)
    return env


def _store_attempt(
    store: dict[str, Any],
    *,
    clarify_id: str,
    original: str,
    options: list[dict[str, Any]],
    space_id: str | None,
    session_id: str | None,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    store[clarify_id] = {
        "clarify_id": clarify_id,
        "question": original,
        "options": options,
        "space_id": space_id,
        "session_id": session_id,
        "created": _now(),
        "ttl": CLARIFY_TTL_S,
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "round": 1,
    }
    record_clarify_tokens(prompt_tokens, completion_tokens)


def _finish(
    data: dict[str, Any],
    *,
    prompt_tokens: int,
    completion_tokens: int,
    prompt: str,
    cols: list[_Col],
    measures: set[str],
    secrets: set[str],
    store: dict[str, Any],
    original: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    if prompt_tokens <= 0:
        prompt_tokens = len(prompt.split())
    question, options = _validate_options(data, cols, measures, secrets)
    if not options:
        return None
    options = _mask_stored_options(options, cols)
    if not options:
        return None
    clarify_id = "clr_" + uuid.uuid4().hex[:16]
    _store_attempt(
        store,
        clarify_id=clarify_id,
        original=original,
        options=options,
        space_id=space_id,
        session_id=session_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    return _envelope(
        clarify_id=clarify_id,
        clarify_question=question,
        original=original,
        options=options,
        space_id=space_id,
        session_id=session_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _measures(ctx: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(ctx, dict) and isinstance(ctx.get("measures"), dict):
        return ctx["measures"]
    return {}


def _column_unreadable(
    *,
    space_id: str | None,
    session_id: str | None,
    question: str,
) -> Gate:
    return Gate(
        envelope=named_abstain(
            "clarify_column_unreadable",
            "A column could not be checked, so this ask was not answered.",
            space_id=space_id,
            session_id=session_id,
            question=question,
        )
    )


def _writer_unavailable(
    *,
    space_id: str | None,
    session_id: str | None,
    question: str,
) -> Gate:
    return Gate(
        envelope=named_abstain(
            "clarify_writer_unavailable",
            "The clarifying question could not be written. Ask again.",
            space_id=space_id,
            session_id=session_id,
            question=question,
        )
    )


def consider_clarify(
    question: str,
    *,
    original: str,
    warehouse: Path | None,
    grantable: set[str],
    ctx: dict[str, Any] | None,
    store: dict[str, Any],
    model: Any,
    space_id: str | None,
    session_id: str | None,
    compute: Any,
) -> Gate:
    """Decide whether to clarify. At most one extra model call.

    A clear retrieval returns without calling ``model`` or ``compute``.
    An ambiguous ask calls ``model`` once when it is set. Otherwise the
    existing ``compute`` call is reused and not repeated by the caller.
    """
    try:
        clear, cols, secrets, pii_failed = structural_signal(
            question, warehouse=warehouse, grantable=grantable, ctx=ctx
        )
    except Exception:  # noqa: BLE001 -- do not serve an unreadable schema
        return _column_unreadable(
            space_id=space_id, session_id=session_id, question=original
        )
    if clear:
        return Gate(check_planner=True)
    measure_names = {str(n) for n in _measures(ctx)}
    if pii_failed and model is None:
        return _column_unreadable(
            space_id=space_id, session_id=session_id, question=original
        )
    if model is not None:
        _note_call()
        prompt = _prompt(question, cols, _measures(ctx))
        try:
            raw = model(prompt)
        except ClarifyWriterUnavailable:
            return _writer_unavailable(
                space_id=space_id, session_id=session_id, question=original
            )
        except Exception:  # noqa: BLE001
            if pii_failed:
                return _column_unreadable(
                    space_id=space_id, session_id=session_id, question=original
                )
            return Gate()
        data, prompt_tokens, completion_tokens = _parse_model(raw)
        if data is None:
            if pii_failed:
                return _column_unreadable(
                    space_id=space_id, session_id=session_id, question=original
                )
            return Gate()
        env = _finish(
            data,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            prompt=prompt,
            cols=cols,
            measures=measure_names,
            secrets=secrets,
            store=store,
            original=original,
            space_id=space_id,
            session_id=session_id,
        )
        if env is None and pii_failed:
            return _column_unreadable(
                space_id=space_id, session_id=session_id, question=original
            )
        return Gate(envelope=env)
    try:
        payload = compute(ctx)
    except Exception:  # noqa: BLE001
        payload = None
    env = None
    if isinstance(payload, dict):
        env = _from_planner_payload(
            payload,
            cols=cols,
            measures=measure_names,
            secrets=secrets,
            store=store,
            original=original,
            space_id=space_id,
            session_id=session_id,
        )
    kept = payload if isinstance(payload, dict) else None
    return Gate(envelope=env, payload=kept, prefetched=True)


def _usage(payload: dict[str, Any]) -> tuple[int, int]:
    usage = payload.get("usage")
    raw: dict[str, Any] = usage if isinstance(usage, dict) else {}
    prompt_tokens = int(raw.get("prompt_tokens") or payload.get("prompt_tokens") or 0)
    completion_tokens = int(
        raw.get("completion_tokens") or payload.get("completion_tokens") or 0
    )
    return prompt_tokens, completion_tokens


def _planner_block(payload: dict[str, Any]) -> dict[str, Any] | None:
    block = payload.get("clarify")
    if isinstance(block, dict) and isinstance(block.get("options"), list):
        return block
    competing = payload.get("competing_interpretations")
    if isinstance(competing, list) and len(competing) >= _MIN_OPTIONS:
        question = str(payload.get("clarify_question") or payload.get("question") or "").strip()
        return {"question": question, "options": competing}
    return None


def _from_planner_payload(
    payload: dict[str, Any],
    *,
    cols: list[_Col],
    measures: set[str],
    secrets: set[str],
    store: dict[str, Any],
    original: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    block = _planner_block(payload)
    if block is None:
        return None
    prompt_tokens, completion_tokens = _usage(payload)
    text = json.dumps(block)
    if completion_tokens <= 0:
        completion_tokens = len(text.split())
    if prompt_tokens <= 0:
        prompt_tokens = len(original.split())
    return _finish(
        block,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt=original,
        cols=cols,
        measures=measures,
        secrets=secrets,
        store=store,
        original=original,
        space_id=space_id,
        session_id=session_id,
    )


def clarify_from_planner(
    payload: dict[str, Any] | None,
    *,
    question: str,
    original: str,
    warehouse: Path | None,
    grantable: set[str],
    ctx: dict[str, Any] | None,
    store: dict[str, Any],
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    """Reuse a planner payload that already lists competing interpretations."""
    if not isinstance(payload, dict) or _planner_block(payload) is None:
        return None
    try:
        _clear, cols, secrets, pii_failed = structural_signal(
            question, warehouse=warehouse, grantable=grantable, ctx=ctx
        )
    except Exception:  # noqa: BLE001 -- do not serve an unreadable schema
        return named_abstain(
            "clarify_column_unreadable",
            "A column could not be checked, so this ask was not answered.",
            space_id=space_id,
            session_id=session_id,
            question=original,
        )
    planned = _from_planner_payload(
        payload,
        cols=cols,
        measures={str(n) for n in _measures(ctx)},
        secrets=secrets,
        store=store,
        original=original,
        space_id=space_id,
        session_id=session_id,
    )
    if planned is None and pii_failed:
        return named_abstain(
            "clarify_column_unreadable",
            "A column could not be checked, so this ask was not answered.",
            space_id=space_id,
            session_id=session_id,
            question=original,
        )
    return planned


def named_abstain(
    reason: str,
    text: str,
    *,
    space_id: str | None,
    session_id: str | None,
    question: str,
) -> dict[str, Any]:
    env = build_answer_envelope(
        answer_id=f"ans_{reason}",
        text=text,
        badge="ABSTAIN",
        abstained=True,
        values=[],
        rows=[],
        assumptions=[reason],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="abstain",
        question=question,
    )
    env["abstain_reason"] = reason
    assert_envelope_valid(env)
    return env


def _expired(attempt: dict[str, Any], now: float) -> bool:
    created = float(attempt.get("created") or 0)
    ttl = float(attempt.get("ttl") or CLARIFY_TTL_S)
    return now > created + ttl


def _scope_ok(attempt: dict[str, Any], space_id: str | None, session_id: str | None) -> bool:
    stored_space = attempt.get("space_id")
    stored_session = attempt.get("session_id")
    if stored_space and space_id and stored_space != space_id:
        return False
    if stored_session and session_id and stored_session != session_id:
        return False
    return True


def apply_binding(question: str, binding: dict[str, Any]) -> str:
    payload = json.dumps(binding, sort_keys=True, separators=(",", ":"), default=str)
    return f"{question.rstrip()}\n{payload}"


def _match_reply(text: str, options: list[Any]) -> dict[str, Any] | None:
    """One stored option, by id or by label. Zero or many is not a pick."""
    folded = text.strip().casefold()
    if not folded:
        return None
    hits: list[dict[str, Any]] = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        oid = str(opt.get("id") or "").strip()
        label = str(opt.get("label") or "").strip()
        if folded == oid.casefold() or (label and folded == label.casefold()):
            if opt not in hits:
                hits.append(opt)
    if len(hits) == 1:
        return hits[0]
    return None


def _binding_still_cleared(
    binding: dict[str, Any],
    cols: list[_Col],
    secrets: set[str],
    warehouse: Path | None,
) -> bool:
    """Every table and column in the binding is granted and mask-cleared."""
    measures = {c.name for c in cols if c.cleared and _numeric(c.type_name)}
    if not _binding_ok(binding, cols, measures):
        return False
    blob = "\n".join(str(v) for v in binding.values())
    if _leaks(blob, secrets):
        return False
    targets: list[tuple[str, str]] = []
    kind = str(binding.get("kind") or "")
    if kind in {"column", "measure"}:
        table = str(binding.get("table") or "")
        name = str(binding.get("name") or "")
        if table and name:
            targets.append((table, name))
    elif kind == "time_range":
        table = str(binding.get("table") or "")
        name = str(binding.get("column") or "")
        if table and name:
            targets.append((table, name))
    for table, name in targets:
        samples = _samples(warehouse, table, name)
        try:
            if column_is_pii(name, samples, table=table):
                return False
        except Exception:  # noqa: BLE001 -- do not re-enter on an unreadable column
            return False
        if _leaks("\n".join(samples), secrets):
            return False
    return True


def resolve_clarify(
    store: dict[str, Any],
    *,
    clarify_id: str,
    option_id: str | None,
    clarify_text: str | None,
    space_id: str | None,
    session_id: str | None,
    fallback_question: str,
    warehouse: Path | None = None,
    grantable: set[str] | None = None,
) -> dict[str, Any] | str:
    """Return the rewritten question, or a named abstain envelope.

    A pick is one stored option id, or free text that is exactly one stored
    id or label. The binding is checked against the Space grant and the mask
    again before the ask re-enters. Unknown and expired ids abstain.
    """
    attempt = store.get(clarify_id)
    if not isinstance(attempt, dict) or not _scope_ok(attempt, space_id, session_id):
        return named_abstain(
            "clarify_unknown",
            "That clarifying question is not known. Ask the question again.",
            space_id=space_id,
            session_id=session_id,
            question=fallback_question,
        )
    if _expired(attempt, _now()):
        store.pop(clarify_id, None)
        return named_abstain(
            "clarify_expired",
            "That clarifying question expired. Ask the question again.",
            space_id=space_id,
            session_id=session_id,
            question=fallback_question,
        )
    original = str(attempt.get("question") or fallback_question)
    raw_options = attempt.get("options")
    options = raw_options if isinstance(raw_options, list) else []
    cols, secrets, _pii_failed = _granted_columns(warehouse, set(grantable or ()))

    def _accept(chosen: dict[str, Any]) -> dict[str, Any] | str:
        binding = chosen.get("binding")
        if not isinstance(binding, dict) or not _binding_still_cleared(
            binding, cols, secrets, warehouse
        ):
            return named_abstain(
                "clarify_binding_ungranted",
                "That option is not granted on this Space. Ask again.",
                space_id=space_id,
                session_id=session_id,
                question=original,
            )
        store.pop(clarify_id, None)
        return apply_binding(original, binding)

    if option_id:
        chosen = next(
            (opt for opt in options if isinstance(opt, dict) and opt.get("id") == option_id),
            None,
        )
        if not isinstance(chosen, dict) or not isinstance(chosen.get("binding"), dict):
            return named_abstain(
                "clarify_option_unknown",
                "That option is not on the clarifying question. Ask again.",
                space_id=space_id,
                session_id=session_id,
                question=original,
            )
        return _accept(chosen)
    text = (clarify_text or "").strip()
    if not text:
        return named_abstain(
            "clarify_option_unknown",
            "Pick an option or reply with a short answer.",
            space_id=space_id,
            session_id=session_id,
            question=original,
        )
    chosen = _match_reply(text, options)
    if chosen is None or not isinstance(chosen.get("binding"), dict):
        return named_abstain(
            "clarify_pick_not_in_options",
            "That reply is not one of the options. Ask again.",
            space_id=space_id,
            session_id=session_id,
            question=original,
        )
    return _accept(chosen)


_DEMO_VIEWER_KEY = "dms-demo-viewer-key"
_CHAT_PATH = "/v1/chat/completions"


def _relay_bearer() -> str | None:
    """Relay a configured ov_ key. Never the demo viewer key and never a provider key."""
    for name in ("OPENVAULT_API_KEY", "CORTEX_API_KEY"):
        raw = os.environ.get(name, "").strip()
        if not raw or raw == _DEMO_VIEWER_KEY:
            continue
        return raw
    return None


def openvault_clarify_writer(base_url: str | None) -> Any:
    """One completion on the generative OpenVault route.

    Same path (``/v1/chat/completions``), ``free+normal`` preference, and pin
    stamp as Insights generate. OpenVault holds the provider key. If the vault
    cannot be reached, the callable raises ``ClarifyWriterUnavailable``.
    """
    root = (base_url or "").strip().rstrip("/")

    def _write(prompt: str) -> dict[str, Any]:
        if not root:
            raise ClarifyWriterUnavailable("openvault_url_missing")
        from cortex_client.compute import FREEROUTE_PREFERENCE
        from cortex_client.strict_pin import stamp_generate_body, stamp_generate_headers

        body = stamp_generate_body(
            {
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 512,
                "stream": False,
                "model_preference": FREEROUTE_PREFERENCE,
            }
        )
        if body.get("pin_refusal"):
            raise ClarifyWriterUnavailable(str(body["pin_refusal"]))
        headers: dict[str, str] = {}
        bearer = _relay_bearer()
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        stamped = stamp_generate_headers(headers) or {}
        import httpx

        try:
            with httpx.Client(timeout=5.0) as http:
                res = http.post(root + _CHAT_PATH, json=body, headers=stamped)
        except httpx.HTTPError as exc:
            raise ClarifyWriterUnavailable("openvault_unreachable") from exc
        if res.status_code >= 400:
            raise ClarifyWriterUnavailable(f"openvault_http_{res.status_code}")
        try:
            payload = res.json()
        except ValueError as exc:
            raise ClarifyWriterUnavailable("openvault_bad_body") from exc
        if not isinstance(payload, dict):
            raise ClarifyWriterUnavailable("openvault_bad_body")
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ClarifyWriterUnavailable("openvault_no_content") from exc
        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return {
            "raw": content,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
        }

    return _write
