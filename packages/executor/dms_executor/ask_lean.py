"""Prompt trim for DMS_ASK_LEAN_TRIM. Off leaves the catalog object untouched.

The cap is a token budget (default 1200, chars/4) on the schema, ontology,
and governed-definition block. Chunks are ranked with ``rank_texts`` (the
#558 embedder, or BM25 when no embedder is passed). No word list and no
scan of the question.
"""

from __future__ import annotations

import json
from typing import Any

from cortex_client.ask_clock import ask_lean_trim, est_tokens, trim_token_budget
from cortex_client.insights import SCHEMA_CONTEXT_FIELD

from dms_executor.verified_queries import rank_texts

_FIXED_ONTOLOGY = frozenset(
    {"source", "tables", "schema", "verified", "space_truncated", "intent_slots"}
)


def apply_prompt_trim(
    question: str,
    defs: str,
    examples: str,
    onto: dict[str, Any] | None,
) -> tuple[str, str, dict[str, Any] | None]:
    """Return the same objects when the trim cut is off."""
    if not ask_lean_trim():
        return defs, examples, onto
    budget = trim_token_budget()
    schema = ""
    rest: dict[str, Any] | None = onto
    if isinstance(onto, dict) and isinstance(onto.get(SCHEMA_CONTEXT_FIELD), str):
        schema = str(onto.get(SCHEMA_CONTEXT_FIELD) or "")
        rest = {key: value for key, value in onto.items() if key != SCHEMA_CONTEXT_FIELD}
    fixed_schema, schema_entries = _split_schema(schema)
    chunks: list[tuple[str, int]] = []
    texts: list[str] = []
    for index, line in enumerate(schema_entries):
        chunks.append(("schema", index))
        texts.append(line)
    def_parts = _paragraphs(defs)
    for index, part in enumerate(def_parts):
        chunks.append(("defs", index))
        texts.append(part)
    example_parts = _paragraphs(examples)
    for index, part in enumerate(example_parts):
        chunks.append(("examples", index))
        texts.append(part)
    onto_chunks = _ontology_chunks(rest)
    for index, text in enumerate(onto_chunks):
        chunks.append(("onto", index))
        texts.append(text)
    fixed_cost = est_tokens(fixed_schema) + _fixed_ontology_tokens(rest)
    room = max(0, budget - fixed_cost)
    chosen = _choose(question, texts, room)
    keep_schema = {chunks[i][1] for i in chosen if chunks[i][0] == "schema"}
    keep_defs = {chunks[i][1] for i in chosen if chunks[i][0] == "defs"}
    keep_examples = {chunks[i][1] for i in chosen if chunks[i][0] == "examples"}
    keep_onto = {chunks[i][1] for i in chosen if chunks[i][0] == "onto"}
    schema_out = _join_schema(fixed_schema, schema_entries, keep_schema)
    defs_out = _join_kept(def_parts, keep_defs)
    examples_out = _join_kept(example_parts, keep_examples)
    onto_out = _rebuild_ontology(rest, onto_chunks, keep_onto, schema_out)
    defs_out, examples_out, onto_out = _enforce_budget(
        schema_out, defs_out, examples_out, onto_out, budget
    )
    return defs_out, examples_out, onto_out


def block_tokens(
    defs: str,
    examples: str,
    onto: dict[str, Any] | None,
) -> int:
    """Estimated tokens of the schema / ontology / definition block."""
    parts = [defs or "", examples or ""]
    if isinstance(onto, dict):
        schema = onto.get(SCHEMA_CONTEXT_FIELD)
        if isinstance(schema, str):
            parts.append(schema)
        rest = {key: value for key, value in onto.items() if key != SCHEMA_CONTEXT_FIELD}
        try:
            parts.append(json.dumps(rest, default=str, sort_keys=True))
        except TypeError:
            pass
    return sum(est_tokens(part) for part in parts if part)


def _paragraphs(text: str) -> list[str]:
    if not text.strip():
        return []
    return [part for part in text.split("\n\n") if part.strip()]


def _split_schema(text: str) -> tuple[str, list[str]]:
    if not text:
        return "", []
    fixed: list[str] = []
    entries: list[str] = []
    for line in text.split("\n"):
        if line.startswith("- "):
            entries.append(line)
        else:
            fixed.append(line)
    return "\n".join(fixed).strip("\n"), entries


def _join_schema(fixed: str, entries: list[str], keep: set[int]) -> str:
    kept = [line for index, line in enumerate(entries) if index in keep]
    parts = [part for part in (fixed, "\n".join(kept)) if part]
    return "\n".join(parts)


def _join_kept(parts: list[str], keep: set[int]) -> str:
    return "\n\n".join(part for index, part in enumerate(parts) if index in keep)


def _ontology_chunks(onto: dict[str, Any] | None) -> list[str]:
    if not isinstance(onto, dict):
        return []
    chunks: list[str] = []
    for key, value in onto.items():
        if key in _FIXED_ONTOLOGY or key == SCHEMA_CONTEXT_FIELD:
            continue
        if isinstance(value, dict):
            for name, spec in value.items():
                chunks.append(_dump({key: {name: spec}}))
        elif isinstance(value, list):
            for item in value:
                chunks.append(_dump({key: [item]}))
        else:
            chunks.append(_dump({key: value}))
    return chunks


def _fixed_ontology_tokens(onto: dict[str, Any] | None) -> int:
    if not isinstance(onto, dict):
        return 0
    fixed = {key: onto[key] for key in onto if key in _FIXED_ONTOLOGY}
    if not fixed:
        return 0
    return est_tokens(_dump(fixed))


def _rebuild_ontology(
    onto: dict[str, Any] | None,
    chunks: list[str],
    keep: set[int],
    schema: str,
) -> dict[str, Any] | None:
    if not isinstance(onto, dict) and not schema:
        return onto
    base: dict[str, Any] = {}
    if isinstance(onto, dict):
        for key in onto:
            if key in _FIXED_ONTOLOGY:
                base[key] = onto[key]
    for index in sorted(keep):
        if index < 0 or index >= len(chunks):
            continue
        try:
            piece = json.loads(chunks[index])
        except ValueError:
            continue
        if not isinstance(piece, dict):
            continue
        for key, value in piece.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                base[key].update(value)
            elif isinstance(value, list) and isinstance(base.get(key), list):
                base[key].extend(value)
            else:
                base[key] = value
    if schema:
        base[SCHEMA_CONTEXT_FIELD] = schema
    elif isinstance(onto, dict) and SCHEMA_CONTEXT_FIELD in onto and not schema:
        base.pop(SCHEMA_CONTEXT_FIELD, None)
    return base


def _choose(question: str, texts: list[str], budget: int) -> list[int]:
    if not texts or budget <= 0:
        return []
    ranked = rank_texts(question, texts, None, limit=len(texts), floor=0.0)
    # ponytail: a zero BM25 score is dropped by rank_texts. If nothing scores,
    # keep document order and let the budget cut. Upgrade: pass the ask embedder.
    order = [index for _score, index in ranked] or list(range(len(texts)))
    kept: list[int] = []
    used = 0
    for index in order:
        cost = est_tokens(texts[index])
        if cost <= 0:
            continue
        if used + cost > budget:
            if not kept:
                kept.append(index)
            break
        kept.append(index)
        used += cost
    return kept


def _enforce_budget(
    schema: str,
    defs: str,
    examples: str,
    onto: dict[str, Any] | None,
    budget: int,
) -> tuple[str, str, dict[str, Any] | None]:
    """Last cut: shorten the longest string field until the block fits.

    The cut is on a newline under the remaining chars. It does not look at
    the question.
    """
    if block_tokens(defs, examples, onto if schema == "" else _with_schema(onto, schema)) <= budget:
        if schema and isinstance(onto, dict):
            onto = dict(onto)
            onto[SCHEMA_CONTEXT_FIELD] = schema
        elif schema:
            onto = {SCHEMA_CONTEXT_FIELD: schema}
        return defs, examples, onto
    if isinstance(onto, dict):
        onto = dict(onto)
        if schema:
            onto[SCHEMA_CONTEXT_FIELD] = schema
    elif schema:
        onto = {SCHEMA_CONTEXT_FIELD: schema}
    guard = 0
    while block_tokens(defs, examples, onto) > budget and guard < 8:
        guard += 1
        defs, examples, onto = _shrink_once(defs, examples, onto, budget)
    return defs, examples, onto


def _with_schema(onto: dict[str, Any] | None, schema: str) -> dict[str, Any]:
    out = dict(onto or {})
    out[SCHEMA_CONTEXT_FIELD] = schema
    return out


def _shrink_once(
    defs: str,
    examples: str,
    onto: dict[str, Any] | None,
    budget: int,
) -> tuple[str, str, dict[str, Any] | None]:
    fields: list[tuple[int, str]] = []
    if defs:
        fields.append((len(defs), "defs"))
    if examples:
        fields.append((len(examples), "examples"))
    schema = ""
    if isinstance(onto, dict) and isinstance(onto.get(SCHEMA_CONTEXT_FIELD), str):
        schema = str(onto.get(SCHEMA_CONTEXT_FIELD) or "")
        if schema:
            fields.append((len(schema), "schema"))
    if not fields:
        return "", "", onto
    _size, which = max(fields)
    room = max(1, budget // 2)
    if which == "defs":
        return _fit(defs, room), examples, onto
    if which == "examples":
        return defs, _fit(examples, room), onto
    if isinstance(onto, dict):
        onto = dict(onto)
        onto[SCHEMA_CONTEXT_FIELD] = _fit(schema, room)
    return defs, examples, onto


def _fit(text: str, tokens: int) -> str:
    if est_tokens(text) <= tokens:
        return text
    chars = max(0, tokens * 4)
    cut = text[:chars]
    newline = cut.rfind("\n")
    if newline > 0:
        cut = cut[:newline]
    return cut


def _dump(obj: Any) -> str:
    try:
        return json.dumps(obj, default=str, sort_keys=True)
    except TypeError:
        return ""
