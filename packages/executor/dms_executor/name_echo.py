"""Drop ungranted relation names from user-visible abstain fields.

The ticket keeps the name. ``space_id``, ``session_id``, and the other
id fields are not rewritten. Matching is the whole identifier, any
length, so a name shorter than four characters is not a hole and a
name is not cut out of a longer identifier.

An ungranted tail is parsed with sqlglot. The extract dialect is tried
first. A longer read from another registered dialect wins, so the
identifier parts come from that dialect's parser rather than from a
list of quote or joining characters. A name the envelope's Space
grants stays. ``ungranted:file`` and ``ungranted:unparsed`` are reason
codes.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from sqlglot import Dialects, exp, parse_one, tokenize
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

# Reason-code tails. Not a table list.
_CODE_TAILS = frozenset({"file", "unparsed"})
_ID_KEYS = frozenset(
    {
        "answer_id",
        "ask_id",
        "audit_id",
        "issuer_key_id",
        "org_id",
        "run_id",
        "session_id",
        "space_id",
        "ticket_id",
    }
)
_NAME_KEYS = frozenset(
    {
        "abstain_reason",
        "assumptions",
        "demote_note",
        "message",
        "messages",
        "text",
    }
)
# Colon form, ``ungranted table <name>``, or ``(ungranted <name>)``.
# ``ungranted_table`` is before ``ungranted`` so the suffix is not a name.
_PREFIX = re.compile(
    r"(?i)(?:(?<![A-Za-z0-9_])ungranted_table\s*:\s*"
    r"|(?<![A-Za-z0-9_])ungranted\s+table\s+"
    r"|(?<![A-Za-z0-9_])ungranted\s*:\s*"
    r"|\(\s*ungranted\s+)"
)
_WRAP = "SELECT * FROM "
_NAME_TOKENS = frozenset({TokenType.IDENTIFIER, TokenType.VAR, TokenType.STRING})


@lru_cache(maxsize=1)
def _dialect_names() -> tuple[str, ...]:
    from dms_executor.sql_loop import EXTRACT_DIALECT

    names = [item.value for item in Dialects if item.value]
    names.sort(key=lambda name: (name != EXTRACT_DIALECT, name))
    return tuple(names)


def _tables_at_start(tree: exp.Expression) -> list[tuple[tuple[str, ...], int]] | None:
    """Parts of each leading table, and how far the last part reaches.

    ``None`` when the first table does not start at the tail. The reach
    is the AST end, so an alias is not included.
    """
    base = len(_WRAP)
    found: list[tuple[tuple[str, ...], int]] = []
    for table in tree.find_all(exp.Table):
        idents = [part for part in table.parts if part.name]
        if not idents:
            continue
        start = idents[0].meta.get("start")
        end = idents[-1].meta.get("end")
        if start is None or end is None:
            continue
        if not found and start != base:
            return None
        if start < base:
            continue
        found.append((tuple(part.name for part in idents), end + 1 - base))
    if not found:
        return None
    return found


@lru_cache(maxsize=512)
def _parsed_tail(body: str) -> tuple[tuple[str, ...], ...]:
    """Identifier parts at the start of an ungranted tail.

    The longest successful read wins. A following word that the parser
    accepts only as an alias does not extend the name.
    """
    best: tuple[int, int, tuple[tuple[str, ...], ...]] | None = None
    for dialect in _dialect_names():
        try:
            tokens = list(tokenize(body, read=dialect))
        except SqlglotError:
            continue
        for tok in tokens:
            if tok.end is None:
                continue
            try:
                tree = parse_one(_WRAP + body[: tok.end + 1], read=dialect)
            except SqlglotError:
                continue
            tables = _tables_at_start(tree)
            if not tables:
                continue
            coverage = max(reach for _parts, reach in tables)
            parts = tuple(item[0] for item in tables)
            candidate = (coverage, len(parts), parts)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
    if best is None:
        return ()
    return best[2]


def _parts_in(text: str) -> list[tuple[str, ...]]:
    found: list[tuple[str, ...]] = []
    seen: set[str] = set()
    raw = text or ""
    for match in _PREFIX.finditer(raw):
        tail = raw[match.end() :]
        body = tail.lstrip()
        if not body:
            continue
        for parts in _parsed_tail(body):
            name = ".".join(parts)
            key = name.casefold()
            if not name or key in _CODE_TAILS or key in seen:
                continue
            seen.add(key)
            found.append(parts)
    return found


def echoed_names(text: str) -> list[str]:
    """Relation identifiers the grant check named, quoting stripped."""
    return [".".join(parts) for parts in _parts_in(text)]


def _drop_keys(parts_list: list[tuple[str, ...]]) -> set[str]:
    keys: set[str] = set()
    for parts in parts_list:
        if not parts:
            continue
        keys.add(".".join(parts).casefold())
        keys.add(parts[-1].casefold())
    return keys


def _token_spans(text: str, dropping: set[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for dialect in _dialect_names():
        try:
            tokens = list(tokenize(text, read=dialect))
        except SqlglotError:
            continue
        index = 0
        count = len(tokens)
        while index < count:
            tok = tokens[index]
            if tok.token_type not in _NAME_TOKENS or tok.start is None or tok.end is None:
                index += 1
                continue
            parts = [tok]
            cursor = index + 1
            while (
                cursor + 1 < count
                and tokens[cursor].token_type == TokenType.DOT
                and tokens[cursor + 1].token_type in _NAME_TOKENS
                and tokens[cursor].start == parts[-1].end + 1
                and tokens[cursor + 1].start == tokens[cursor].end + 1
                and tokens[cursor].start is not None
                and tokens[cursor + 1].start is not None
                and tokens[cursor + 1].end is not None
            ):
                parts.append(tokens[cursor + 1])
                cursor += 2
            names = [part.text for part in parts]
            full = ".".join(names).casefold()
            if full in dropping or names[-1].casefold() in dropping:
                spans.append((parts[0].start, parts[-1].end + 1))
            index = cursor
    return spans


def _apply(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if start < 0 or end > len(text) or start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    chunks: list[str] = []
    prev = 0
    for start, end in merged:
        chunks.append(text[prev:start])
        prev = end
    chunks.append(text[prev:])
    return "".join(chunks)


def scrub_text(text: str, parts_list: list[tuple[str, ...]]) -> str:
    """Remove each ungranted name, including the quoting sqlglot consumed."""
    if not text or not parts_list:
        return text
    return _apply(text, _token_spans(text, _drop_keys(parts_list)))


def _scrub_value(value: Any, parts_list: list[tuple[str, ...]]) -> Any:
    if isinstance(value, str):
        return scrub_text(value, parts_list)
    if isinstance(value, list):
        return [_scrub_value(item, parts_list) for item in value]
    if isinstance(value, dict):
        return {
            str(key): item if str(key) in _ID_KEYS else _scrub_value(item, parts_list)
            for key, item in value.items()
        }
    return value


def _blobs(reason: str, env: dict[str, Any]) -> list[str]:
    blobs = [reason]
    for key in _NAME_KEYS:
        value = env.get(key)
        if isinstance(value, str):
            blobs.append(value)
        elif isinstance(value, list):
            blobs.extend(str(item) for item in value if isinstance(item, str))
    loop = env.get("loop")
    if isinstance(loop, list):
        for item in loop:
            if isinstance(item, dict) and isinstance(item.get("outcome"), str):
                blobs.append(item["outcome"])
    receipt = env.get("audit_receipt")
    if isinstance(receipt, dict):
        unsure = receipt.get("unsure")
        if isinstance(unsure, dict) and isinstance(unsure.get("why"), str):
            blobs.append(unsure["why"])
    return blobs


def _collect_parts(reason: str, env: dict[str, Any]) -> list[tuple[str, ...]]:
    found: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for blob in _blobs(reason, env):
        for parts in _parts_in(blob):
            key = ".".join(parts).casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(parts)
    return found


def _grant_names(env: dict[str, Any]) -> set[str]:
    """Tables the envelope's Space may read. No Space, nothing granted."""
    from dms_executor.demo_grants import DEMO_SPACE_GRANTS, canonical_space_id

    raw = env.get("space_id")
    if not isinstance(raw, str) or not raw.strip():
        return set()
    entry = DEMO_SPACE_GRANTS.get(canonical_space_id(raw.strip()))
    if not entry:
        return set()
    return set(entry[1])


def _ungranted(
    parts_list: list[tuple[str, ...]], grants: set[str]
) -> list[tuple[str, ...]]:
    from dms_executor.ontology import table_is_granted

    out: list[tuple[str, ...]] = []
    for parts in parts_list:
        name = ".".join(parts)
        if name.casefold() in _CODE_TAILS or table_is_granted(name, grants):
            continue
        out.append(parts)
    return out


def hide_echo(env: dict[str, Any], reason: str) -> dict[str, Any]:
    """Scrub name-carrying fields. Id fields are left as they are."""
    parts_list = _ungranted(_collect_parts(reason, env), _grant_names(env))
    if not parts_list:
        return env
    for key in _NAME_KEYS:
        if key not in env or key in _ID_KEYS:
            continue
        env[key] = _scrub_value(env[key], parts_list)
    loop = env.get("loop")
    if isinstance(loop, list):
        for item in loop:
            if isinstance(item, dict) and isinstance(item.get("outcome"), str):
                item["outcome"] = scrub_text(item["outcome"], parts_list)
    receipt = env.get("audit_receipt")
    if isinstance(receipt, dict):
        unsure = receipt.get("unsure")
        if isinstance(unsure, dict) and isinstance(unsure.get("why"), str):
            unsure["why"] = scrub_text(unsure["why"], parts_list)
    return env
