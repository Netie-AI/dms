"""FEAT-VALUE-MATCH-01. Default off.

Retrieval is an index over stored values: character n-gram BM25, plus an
OpenVault embedding when the caller passes one. The question is split with
the schema-context span tokenizer. No word list and no regex on the question.

Only a column the masker positively clears is indexed. A person-classified,
masked, or row-filtered column is left out. Any error while checking a
column sends no candidates and the ask continues.

Caps are ``DMS_VALUE_MATCH_PER_COLUMN`` and ``DMS_VALUE_MATCH_TOKEN_BUDGET``.
Swap: a column with more near-spellings than the default, or a tighter
prompt budget, without a code change. Hitting a cap is written on the stamp.

Embeddings are one OpenVault call with role ``embed``. The body has no
provider and no model id. A missing or failed embedder keeps the BM25 order.

A string filter whose value is not in that column's granted values is
``value_not_found``. The envelope is a reconfirm with no rows and no SQL.
"""

from __future__ import annotations

import math
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

VALUE_MATCH_ENV = "DMS_VALUE_MATCH"
PER_COLUMN_ENV = "DMS_VALUE_MATCH_PER_COLUMN"
TOKEN_BUDGET_ENV = "DMS_VALUE_MATCH_TOKEN_BUDGET"
EMBED_ROLE = "embed"
# ponytail: 4 candidates, 80 tokens. Ceiling: a wider near-match set is cut
# and the stamp says so. Upgrade: raise the env, not the code.
PER_COLUMN_DEFAULT = 4
TOKEN_BUDGET_DEFAULT = 80
_NGRAM = 3
_MIN_CHARS = 2
_RECALL_FLOOR = 0.34
_TIE_RECALL = 0.8
_TIE_GAP = 0.02
_BM25_K1 = 1.2
_BM25_B = 0.75
_EMBED_CAP = 64
_READING_CAP = 4
_MODEL_KEYS = frozenset(
    {
        "model",
        "model_id",
        "model_name",
        "provider",
        "served_model",
        "served_provider",
        "strict",
    }
)

_GRANTED: ContextVar[Mapping[str, tuple[str, ...]] | None] = ContextVar(
    "dms_value_match_granted", default=None
)
_READINGS: ContextVar[tuple[tuple[str, str, str, str], ...] | None] = ContextVar(
    "dms_value_match_readings", default=None
)


class _Doubt(Exception):
    """A mask or classifier error. Send no candidates."""


def value_match_enabled() -> bool:
    """True only when ``DMS_VALUE_MATCH`` is the digit 1. Unset is off."""
    return os.environ.get(VALUE_MATCH_ENV, "0").strip() == "1"


def per_column_cap() -> int:
    return _positive_int(PER_COLUMN_ENV, PER_COLUMN_DEFAULT)


def token_budget() -> int:
    return _positive_int(TOKEN_BUDGET_ENV, TOKEN_BUDGET_DEFAULT)


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        number = int(raw)
    except ValueError:
        return default
    if number < 1:
        return default
    return number


def publish_match(
    granted: Mapping[str, tuple[str, ...]] | None,
    readings: Sequence[tuple[str, str, str, str]] | None,
) -> None:
    """Remember this ask's granted values and any tie readings. Flag off clears both."""
    if not value_match_enabled() or not granted:
        _GRANTED.set(None)
        _READINGS.set(None)
        return
    _GRANTED.set({key: tuple(vals) for key, vals in granted.items()})
    _READINGS.set(tuple(readings or ()))


def embed_body(texts: Sequence[str]) -> dict[str, Any]:
    """OpenVault embed body. Role ``embed``. No model id and no provider."""
    body: dict[str, Any] = {"role": EMBED_ROLE, "input": [str(text) for text in texts]}
    if _MODEL_KEYS.intersection(body):
        raise RuntimeError("model_key")
    return body


def role_embedder(
    transport: Callable[[dict[str, Any]], Any] | None,
) -> Callable[[list[str]], Any] | None:
    """Call ``transport`` with :func:`embed_body`. None when there is no transport."""
    if transport is None:
        return None

    def _embed(texts: list[str]) -> Any:
        return transport(embed_body(texts))

    return _embed


@dataclass(frozen=True)
class ColumnInput:
    table: str
    column: str
    values: tuple[str, ...]
    tagged: bool = False
    description: str = ""
    masked: bool = False
    row_filtered: bool = False


@dataclass(frozen=True)
class Candidate:
    table: str
    column: str
    value: str
    recall: float
    bm25: float
    span: str

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}"

    @property
    def rank(self) -> tuple[float, float, str, str]:
        return (-self.recall, -self.bm25, self.key, self.value)


@dataclass(frozen=True)
class MatchResult:
    block: str
    candidates: tuple[Candidate, ...]
    readings: tuple[tuple[str, str, str, str], ...]
    stamp: str
    columns: tuple[str, ...]
    granted: Mapping[str, tuple[str, ...]]


@dataclass
class _Corpus:
    table: str
    column: str
    values: tuple[str, ...]
    folded: tuple[str, ...]
    grams: tuple[tuple[str, ...], ...]
    df: dict[str, int]
    avgdl: float

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}"


def match_prompt(
    columns: Sequence[ColumnInput],
    asked: str,
    *,
    embedder: Callable[[list[str]], Any] | None = None,
    per_column: int | None = None,
    budget: int | None = None,
) -> MatchResult:
    """Candidate block, tie readings, and the granted values those columns hold.

    Flag off returns an empty result and does not read the columns.
    """
    empty = MatchResult("", (), (), "", (), {})
    if not value_match_enabled():
        return empty
    try:
        corpora = _corpora(columns)
    except _Doubt:
        return empty
    if not corpora:
        return empty
    spans = _spans(asked)
    hits = _hits(corpora, spans)
    if embedder is not None and hits:
        hits = _blend(hits, embedder)
    cap = per_column if per_column is not None else per_column_cap()
    room = budget if budget is not None else token_budget()
    chosen, per_hit = _cap_columns(hits, cap)
    ordered = tuple(sorted(chosen, key=lambda item: item.rank))
    fitted, token_hit = _fit_budget(ordered, room)
    parts: list[str] = []
    if per_hit:
        parts.append("value_match_cap:per_column")
    if token_hit:
        parts.append("value_match_cap:token_budget")
    granted = {corp.key: corp.values for corp in corpora}
    readings = _readings(hits)
    return MatchResult(
        block=_render(fitted),
        candidates=tuple(fitted),
        readings=readings,
        stamp=",".join(parts),
        columns=tuple(sorted(granted)),
        granted=granted,
    )


def value_not_found_reconfirm(
    statement: str,
    *,
    asked: str = "",
    granted: Mapping[str, tuple[str, ...]] | None = None,
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """Reconfirm when a string filter is not a granted value of its column.

    Flag off returns None. No rows and no SQL are attached. The literal is
    not copied onto the envelope.
    """
    if not value_match_enabled():
        return None
    index = granted if granted is not None else _GRANTED.get()
    if not index:
        return None
    column = _missing_column(statement, index)
    if column is None:
        return None
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid

    text = f"The filter on {column} is not a stored value. No figure was returned."
    env = build_abstain(
        reason="value_not_found",
        question=asked,
        sql=None,
        stage="generative",
        answer_id="ans_value_not_found",
        text=text,
        rows=[],
        values=[],
        sql_used=None,
        assumptions=["value_not_found"],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="generated",
    )
    env["rows"] = []
    env["values"] = []
    env["sql_used"] = None
    env["reconfirm"] = {
        "reason_code": "value_not_found",
        "reason_text": text,
        "suggested_question": asked,
        "closest_question": asked,
        "why": "value_not_found",
        "column": column,
    }
    assert_envelope_valid(env)
    return env


def take_clarify_envelope(
    asked: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """Clarify card when retrieval tied. None when the flag is off or there is no tie."""
    if not value_match_enabled():
        return None
    readings = _READINGS.get()
    if not readings:
        return None
    from dms_executor.abstain import build_abstain
    from dms_executor.envelope import assert_envelope_valid, attach_clarify_block

    clarify_id = "clr_" + uuid.uuid4().hex[:16]
    shown: list[dict[str, Any]] = []
    lines = ["Which reading should I answer?"]
    for item in readings[:_READING_CAP]:
        ident, text, _column, _value = item
        shown.append({"id": ident, "text": text, "sql_preview": ""})
        lines.append(f"{len(shown)}. {text}")
    env = build_abstain(
        reason="clarify_ambiguous",
        ticket_reason="clarify_ambiguous",
        question=asked,
        stage="generative",
        answer_id="ans_value_match_clarify",
        text="\n".join(lines),
        abstain_reason="clarify_ambiguous",
        rows=[],
        sql_used=None,
        assumptions=["clarify_ambiguous"],
        space_id=space_id,
        session_id=session_id,
        ask_mode="live",
        route="needs_clarification",
    )
    attach_clarify_block(
        env,
        question=asked,
        readings=shown,
        clarify_id=clarify_id,
    )
    assert_envelope_valid(env)
    return env


def _corpora(columns: Sequence[ColumnInput]) -> list[_Corpus]:
    out: list[_Corpus] = []
    for column in columns:
        if not _admit(column):
            continue
        values: list[str] = []
        folded: list[str] = []
        grams: list[tuple[str, ...]] = []
        for raw in column.values:
            text = " ".join(str(raw).split())
            if not text or text in values:
                continue
            if not _sendable(column.table, column.column, text):
                continue
            values.append(text)
            fold = _fold(text)
            folded.append(fold)
            grams.append(tuple(_grams(fold)))
        if not values:
            continue
        df: dict[str, int] = {}
        for row in grams:
            for tok in set(row):
                df[tok] = df.get(tok, 0) + 1
        total = sum(len(row) for row in grams)
        out.append(
            _Corpus(
                table=column.table,
                column=column.column,
                values=tuple(values),
                folded=tuple(folded),
                grams=tuple(grams),
                df=df,
                avgdl=total / len(grams),
            )
        )
    return out


def _admit(column: ColumnInput) -> bool:
    if column.masked or column.row_filtered:
        return False
    if not column.table or not column.column or not column.values:
        return False
    try:
        from dms_core.pii import classify_column

        from dms_executor.schema_context import _column_positively_cleared

        if classify_column(column.column, list(column.values), table=column.table) is not None:
            return False
        if column.description and classify_column(column.description, (), table=column.table):
            return False
        if not _column_positively_cleared(column.table, column.column, tagged=column.tagged):
            return False
    except Exception as exc:  # noqa: BLE001 -- doubt: send nothing
        raise _Doubt from exc
    return True


def _sendable(table: str, column: str, value: str) -> bool:
    try:
        from dms_core.pii import fail_closed_mask_payload, is_mask_token

        if is_mask_token(value):
            return False
        key = f"{table}.{column}"
        masked = fail_closed_mask_payload(
            text=value,
            rows=[{key: value}],
            values=[value],
        )
    except Exception as exc:  # noqa: BLE001 -- doubt: send nothing
        raise _Doubt from exc
    if not isinstance(masked, dict) or masked.get("text") != value:
        return False
    if is_mask_token(value):
        return False
    rows = masked.get("rows")
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], dict)
        or rows[0].get(key) != value
        or is_mask_token(str(rows[0].get(key)))
    ):
        return False
    vals = masked.get("values")
    return isinstance(vals, list) and len(vals) == 1 and vals[0] == value


def _spans(asked: str) -> list[tuple[str, str]]:
    from dms_executor.schema_context import _question_spans

    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for _width, folded, original, _at in _question_spans(asked or ""):
        if not folded or folded in seen:
            continue
        if len(folded.replace(" ", "")) < _MIN_CHARS:
            continue
        seen.add(folded)
        out.append((folded, original))
    return out


def _hits(corpora: Sequence[_Corpus], spans: Sequence[tuple[str, str]]) -> list[Candidate]:
    found: list[Candidate] = []
    for folded, _original in spans:
        query = _grams(folded)
        if not query:
            continue
        for corp in corpora:
            n = len(corp.values)
            for value, value_fold, grams in zip(corp.values, corp.folded, corp.grams, strict=True):
                recall = 1.0 if folded == value_fold else _recall(query, grams)
                if recall < _RECALL_FLOOR:
                    continue
                score = _bm25(query, grams, n=n, avgdl=corp.avgdl, df=corp.df)
                if folded == value_fold:
                    score = max(score, 1.0)
                if score <= 0.0 and recall < 1.0:
                    continue
                found.append(Candidate(corp.table, corp.column, value, recall, score, folded))
    return found


def _blend(
    hits: list[Candidate],
    embedder: Callable[[list[str]], Any],
) -> list[Candidate]:
    spans = list(dict.fromkeys(hit.span for hit in hits))
    values = list(dict.fromkeys(hit.value for hit in hits))
    texts = [*spans, *values]
    if len(texts) > _EMBED_CAP:
        return hits
    try:
        raw = embedder(texts)
    except Exception:
        return hits
    from dms_executor.verified_queries import _as_vectors, _cosine

    vectors = _as_vectors(raw, len(texts))
    if vectors is None:
        return hits
    span_at = {text: index for index, text in enumerate(spans)}
    value_at = {text: len(spans) + index for index, text in enumerate(values)}
    blended: list[Candidate] = []
    for hit in hits:
        left = vectors[span_at[hit.span]]
        right = vectors[value_at[hit.value]]
        extra = _cosine(left, right)
        if extra <= 0.0:
            blended.append(hit)
            continue
        blended.append(
            Candidate(
                hit.table,
                hit.column,
                hit.value,
                hit.recall,
                hit.bm25 + extra,
                hit.span,
            )
        )
    return blended


def _cap_columns(hits: Sequence[Candidate], cap: int) -> tuple[list[Candidate], bool]:
    grouped: dict[tuple[str, str], list[Candidate]] = {}
    for hit in hits:
        grouped.setdefault((hit.table, hit.column), []).append(hit)
    kept: list[Candidate] = []
    truncated = False
    for items in grouped.values():
        best: dict[str, Candidate] = {}
        for item in items:
            held = best.get(item.value)
            if held is None or item.rank < held.rank:
                best[item.value] = item
        ordered = sorted(best.values(), key=lambda item: item.rank)
        if len(ordered) > cap:
            truncated = True
            ordered = ordered[:cap]
        kept.extend(ordered)
    return kept, truncated


def _fit_budget(ordered: Sequence[Candidate], budget: int) -> tuple[list[Candidate], bool]:
    from dms_executor.schema_context import estimate_tokens

    kept: list[Candidate] = []
    hit = False
    for item in ordered:
        trial = [*kept, item]
        if estimate_tokens(_render(trial)) <= budget:
            kept = trial
            continue
        hit = True
    return kept, hit


def _readings(hits: Sequence[Candidate]) -> tuple[tuple[str, str, str, str], ...]:
    by_span: dict[str, list[Candidate]] = {}
    for hit in hits:
        by_span.setdefault(hit.span, []).append(hit)
    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[str, str, str, str]] = []
    for items in by_span.values():
        strong = [item for item in items if item.recall + 1e-9 >= _TIE_RECALL]
        if len({(item.key, item.value) for item in strong}) < 2:
            continue
        best = max(item.recall for item in strong)
        tied = [item for item in strong if best - item.recall <= _TIE_GAP]
        unique: dict[tuple[str, str, str], Candidate] = {}
        for item in tied:
            ident = (item.table, item.column, item.value)
            unique.setdefault(ident, item)
        if len(unique) < 2:
            continue
        for ident in sorted(unique):
            if ident in seen:
                continue
            seen.add(ident)
            item = unique[ident]
            text = f"{item.table}.{item.column} is {item.value}"
            out.append((f"r{len(out) + 1}", text, item.key, item.value))
            if len(out) == _READING_CAP:
                return tuple(out)
    return tuple(out)


def _render(candidates: Sequence[Candidate]) -> str:
    if not candidates:
        return ""
    lines = ["CANDIDATE VALUES"]
    for item in candidates:
        lines.append(f"- {item.table}.{item.column}: {item.value}")
    return "\n".join(lines)


def _fold(text: str) -> str:
    from dms_executor.schema_context import _normalize

    return _normalize(text)


def _grams(folded: str) -> list[str]:
    compact = folded.replace(" ", "")
    if len(compact) < _MIN_CHARS:
        return []
    if len(compact) < _NGRAM:
        return [compact]
    grams = [compact[i : i + _NGRAM] for i in range(len(compact) - _NGRAM + 1)]
    # Leading bigram: a two-letter span overlaps the stored value's start.
    grams.append(compact[:2])
    return grams


def _recall(query: Sequence[str], grams: Sequence[str]) -> float:
    if not query:
        return 0.0
    have = set(grams)
    need = set(query)
    return sum(1 for tok in need if tok in have) / len(need)


def _bm25(
    query: Sequence[str],
    grams: Sequence[str],
    *,
    n: int,
    avgdl: float,
    df: Mapping[str, int],
) -> float:
    if not query or not grams or n < 1:
        return 0.0
    tf: dict[str, int] = {}
    for tok in grams:
        tf[tok] = tf.get(tok, 0) + 1
    length = len(grams) or 1
    score = 0.0
    for tok in query:
        freq = tf.get(tok, 0)
        if not freq:
            continue
        n_q = df.get(tok, 0)
        idf = math.log(1.0 + (n - n_q + 0.5) / (n_q + 0.5))
        denom = freq + _BM25_K1 * (1.0 - _BM25_B + _BM25_B * length / (avgdl or 1.0))
        score += idf * (freq * (_BM25_K1 + 1.0)) / denom
    return score


def _missing_column(statement: str, index: Mapping[str, tuple[str, ...]]) -> str | None:
    from sqlglot import exp, parse_one

    from dms_executor.demo_warehouse import SERVING_DIALECT

    try:
        tree = parse_one(statement, read=SERVING_DIALECT)
    except Exception:
        return None
    if tree is None:
        return None
    cited = {table.name for table in tree.find_all(exp.Table) if table.name}
    folds = {key: {_fold(value) for value in vals if value} for key, vals in index.items()}
    for column, literal in _string_filters(tree):
        keys = _resolve(column, cited, folds)
        if not keys:
            continue
        folded = _fold(literal)
        if not folded:
            continue
        for key in keys:
            if folded not in folds[key]:
                return key
    return None


def _string_filters(tree: Any) -> list[tuple[Any, str]]:
    from sqlglot import exp

    found: list[tuple[Any, str]] = []
    for eq in tree.find_all(exp.EQ):
        pair = _column_string(eq.left, eq.right)
        if pair is not None:
            found.append(pair)
    for node in tree.find_all(exp.In):
        column = node.this
        if not isinstance(column, exp.Column):
            continue
        for expr in node.expressions:
            if isinstance(expr, exp.Literal) and expr.is_string and isinstance(expr.this, str):
                found.append((column, expr.this))
    return found


def _column_string(left: Any, right: Any) -> tuple[Any, str] | None:
    from sqlglot import exp

    if isinstance(left, exp.Column) and isinstance(right, exp.Literal) and right.is_string:
        if isinstance(right.this, str):
            return left, right.this
    if isinstance(right, exp.Column) and isinstance(left, exp.Literal) and left.is_string:
        if isinstance(left.this, str):
            return right, left.this
    return None


def _resolve(column: Any, cited: set[str], folds: Mapping[str, set[str]]) -> list[str]:
    name = str(getattr(column, "name", "") or "")
    table = str(getattr(column, "table", "") or "")
    if not name:
        return []
    if table:
        direct = f"{table}.{name}"
        if direct in folds:
            return [direct]
        hits = [key for key in folds if key.lower() == direct.lower()]
        return hits
    cited_l = {item.lower() for item in cited}
    hits = []
    for key in folds:
        rel, _, col = key.rpartition(".")
        if col.lower() != name.lower():
            continue
        if cited_l and rel.lower() not in cited_l:
            continue
        hits.append(key)
    return hits
