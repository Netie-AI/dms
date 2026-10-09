"""Drop ungranted relation names from the fields a caller can read.

The ticket keeps the name. ``space_id``, ``session_id``, and the other
id fields are not rewritten. Matching is the whole identifier, any
length, so a name shorter than four characters is not a hole and a
name is not cut out of a longer identifier.

An ungranted tail is tokenized once in the serving dialect and parsed
once with that dialect's parser. The identifier parts come from that
parse. They are compared with the Space grant set, including tables
uploaded into the Space. A granted name stays. A name the Space does
not grant is removed. ``ungranted:file`` and ``ungranted:unparsed`` are
reason codes. A tail that does not tokenize is fail-closed: the field
is cleared.

``sql_used`` and ``loop[].sql`` are also parsed once, in the same
dialect. A table in that SQL that the validator would not grant is
removed from ``text``, ``assumptions``, ``abstain_reason``,
``loop[].outcome``, and ``sql_used``. ``loop[].sql`` and ``raw_reply``
are left for a later change. No Space is the personal context for
those SQL names (the demo set plus uploads). A prefix tail with no
Space still grants nothing, so the tail is removed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one, tokenize
from sqlglot.errors import SqlglotError
from sqlglot.tokens import Token, TokenType

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
# Colon form, ``ungranted table <name>``, ``(ungranted <name>)``, or the
# ontology sentence ``(object X cites ungranted T)``.
# ``ungranted_table`` is before ``ungranted`` so the suffix is not a name.
_PREFIX = re.compile(
    r"(?i)(?:(?<![A-Za-z0-9_])ungranted_table\s*:\s*"
    r"|(?<![A-Za-z0-9_])ungranted\s+table\s+"
    r"|(?<![A-Za-z0-9_])ungranted\s*:\s*"
    r"|\(\s*ungranted\s+"
    r"|(?<![A-Za-z0-9_])cites\s+ungranted\s+)"
)
_NAME_TOKENS = frozenset({TokenType.IDENTIFIER, TokenType.VAR, TokenType.STRING})


class _Closed(Exception):
    """An identifier delimiter the serving tokenizer opened did not close."""


def _dialect() -> str:
    from dms_executor.sql_loop import EXTRACT_DIALECT

    return EXTRACT_DIALECT


def _tokenize(text: str) -> list[Token] | None:
    try:
        return list(tokenize(text, read=_dialect()))
    except SqlglotError:
        return None


def _read_atom(tokens: list[Token], index: int) -> tuple[str, int, int, int] | None:
    """One identifier. ``(name, end, next_index, start)``.

    Raises ``_Closed`` when a delimiter the tokenizer did not consume as
    an identifier has no closer.
    """
    if index >= len(tokens):
        return None
    tok = tokens[index]
    if tok.start is None or tok.end is None:
        return None
    if tok.token_type in _NAME_TOKENS:
        return tok.text, tok.end + 1, index + 1, tok.start
    if tok.token_type == TokenType.L_BRACKET:
        return _read_bracket(tokens, index)
    if tok.token_type == TokenType.UNKNOWN and tok.text:
        return _read_delimited(tokens, index, tok.text)
    return None


def _read_bracket(tokens: list[Token], index: int) -> tuple[str, int, int, int]:
    if index + 2 >= len(tokens):
        raise _Closed
    inner = tokens[index + 1]
    close = tokens[index + 2]
    opener = tokens[index]
    if (
        inner.token_type not in _NAME_TOKENS
        or close.token_type != TokenType.R_BRACKET
        or inner.text is None
        or opener.start is None
        or close.end is None
    ):
        raise _Closed
    return inner.text, close.end + 1, index + 3, opener.start


def _read_delimited(
    tokens: list[Token], index: int, delim: str
) -> tuple[str, int, int, int]:
    opener = tokens[index]
    if opener.start is None:
        raise _Closed
    chunks: list[str] = []
    cursor = index + 1
    count = len(tokens)
    while cursor < count:
        tok = tokens[cursor]
        if tok.token_type == TokenType.UNKNOWN and tok.text == delim:
            nxt = cursor + 1
            if (
                nxt < count
                and tokens[nxt].token_type == TokenType.UNKNOWN
                and tokens[nxt].text == delim
            ):
                chunks.append(delim)
                cursor = nxt + 1
                continue
            if not chunks or tok.end is None:
                raise _Closed
            return "".join(chunks), tok.end + 1, cursor + 1, opener.start
        if tok.token_type in _NAME_TOKENS and tok.text:
            chunks.append(tok.text)
            cursor += 1
            continue
        raise _Closed
    raise _Closed


def _index_at(tokens: list[Token], offset: int) -> int:
    for index, tok in enumerate(tokens):
        if tok.start is not None and tok.start >= offset:
            return index
    return len(tokens)


def _leading_tables(
    tokens: list[Token], offset: int
) -> list[tuple[tuple[str, ...], int]]:
    """Tables at ``offset``. A dot joins parts only when nothing is between them."""
    index = _index_at(tokens, offset)
    count = len(tokens)
    found: list[tuple[tuple[str, ...], int]] = []
    while index < count:
        atom = _read_atom(tokens, index)
        if atom is None:
            break
        name, end, index, _start = atom
        parts = [name]
        while (
            index < count
            and tokens[index].token_type == TokenType.DOT
            and tokens[index].start == end
            and tokens[index].end is not None
        ):
            nxt = _read_atom(tokens, index + 1)
            if nxt is None:
                break
            nxt_name, nxt_end, nxt_index, nxt_start = nxt
            if nxt_start != tokens[index].end + 1:
                break
            parts.append(nxt_name)
            end = nxt_end
            index = nxt_index
        found.append((tuple(parts), end))
        if (
            index < count
            and tokens[index].token_type == TokenType.COMMA
            and tokens[index].start == end
        ):
            index += 1
            continue
        break
    return found


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _confirm(tables: list[tuple[tuple[str, ...], int]]) -> tuple[tuple[str, ...], ...]:
    """One parse in the serving dialect. The AST parts are the names."""
    if not tables:
        return ()
    sql = "SELECT * FROM " + ", ".join(
        ".".join(_quote(part) for part in parts) for parts, _end in tables
    )
    try:
        tree = parse_one(sql, read=_dialect())
    except SqlglotError:
        return tuple(parts for parts, _end in tables)
    found: list[tuple[str, ...]] = []
    for table in tree.find_all(exp.Table):
        idents = tuple(part.name for part in table.parts if part.name)
        if idents:
            found.append(idents)
    if len(found) != len(tables):
        return tuple(parts for parts, _end in tables)
    return tuple(found)


def _names_in(text: str) -> tuple[list[tuple[str, ...]], bool]:
    """Names after ungranted markers, and whether the field must be cleared.

    The text is tokenized once. Each tail is parsed once.
    """
    raw = text or ""
    if not raw or _PREFIX.search(raw) is None:
        return [], False
    tokens = _tokenize(raw)
    if tokens is None:
        return [], True
    found: list[tuple[str, ...]] = []
    seen: set[str] = set()
    try:
        for match in _PREFIX.finditer(raw):
            for parts in _confirm(_leading_tables(tokens, match.end())):
                name = ".".join(parts)
                key = name.casefold()
                if not name or key in _CODE_TAILS or key in seen:
                    continue
                seen.add(key)
                found.append(parts)
    except _Closed:
        return [], True
    return found, False


def echoed_names(text: str) -> list[str]:
    """Relation identifiers the grant check named, quoting stripped."""
    parts, failed = _names_in(text)
    if failed:
        return []
    return [".".join(item) for item in parts]


def _drop_keys(parts_list: list[tuple[str, ...]]) -> set[str]:
    keys: set[str] = set()
    for parts in parts_list:
        if not parts:
            continue
        keys.add(".".join(parts).casefold())
        keys.add(parts[-1].casefold())
    return keys


def _spans(tokens: list[Token], dropping: set[str]) -> list[tuple[int, int]] | None:
    """Spans to delete. ``None`` means the field did not tokenize closed."""
    spans: list[tuple[int, int]] = []
    index = 0
    count = len(tokens)
    try:
        while index < count:
            atom = _read_atom(tokens, index)
            if atom is None:
                index += 1
                continue
            name, end, index, start = atom
            parts = [name]
            while (
                index < count
                and tokens[index].token_type == TokenType.DOT
                and tokens[index].start == end
                and tokens[index].end is not None
            ):
                nxt = _read_atom(tokens, index + 1)
                if nxt is None:
                    break
                nxt_name, nxt_end, nxt_index, nxt_start = nxt
                if nxt_start != tokens[index].end + 1:
                    break
                parts.append(nxt_name)
                end = nxt_end
                index = nxt_index
            full = ".".join(parts).casefold()
            if full in dropping or parts[-1].casefold() in dropping:
                spans.append((start, end))
    except _Closed:
        return None
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
    """Remove each ungranted name. An unclosed tail clears the field."""
    if not text or not parts_list:
        return text
    tokens = _tokenize(text)
    if tokens is None:
        if _PREFIX.search(text):
            return ""
        return text
    spans = _spans(tokens, _drop_keys(parts_list))
    if spans is None:
        return "" if _PREFIX.search(text) else text
    return _apply(text, spans)


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


def _tail_failed(text: str) -> bool:
    """True when an ungranted tail does not tokenize. No confirming parse."""
    raw = text or ""
    if not raw or _PREFIX.search(raw) is None:
        return False
    tokens = _tokenize(raw)
    if tokens is None:
        return True
    try:
        for match in _PREFIX.finditer(raw):
            _leading_tables(tokens, match.end())
    except _Closed:
        return True
    return False


def _clear_closed(value: Any) -> Any:
    """Clear a value whose ungranted tail did not tokenize."""
    if isinstance(value, str):
        return "" if _tail_failed(value) else value
    if isinstance(value, list):
        return [_clear_closed(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): item if str(key) in _ID_KEYS else _clear_closed(item)
            for key, item in value.items()
        }
    return value


def _sql_sources(env: dict[str, Any]) -> list[str]:
    """Statements to read names from. ``loop[].sql`` is not rewritten."""
    found: list[str] = []
    sql_used = env.get("sql_used")
    if isinstance(sql_used, str) and sql_used.strip() and not sql_used.strip().startswith("--"):
        found.append(sql_used)
    loop = env.get("loop")
    if isinstance(loop, list):
        for item in loop:
            if not isinstance(item, dict):
                continue
            sql = item.get("sql")
            if isinstance(sql, str) and sql.strip():
                found.append(sql)
    return found


def _tables_in_sql(sql: str) -> list[tuple[str, ...]]:
    """Table identifiers in one statement. One parse, serving dialect."""
    raw = (sql or "").strip()
    if not raw or raw.startswith("--"):
        return []
    try:
        tree = parse_one(raw, read=_dialect())
    except SqlglotError:
        return []
    found: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for table in tree.find_all(exp.Table):
        parts = tuple(part.name for part in table.parts if part.name)
        if not parts:
            continue
        key = ".".join(parts).casefold()
        if key in _CODE_TAILS or key in seen:
            continue
        seen.add(key)
        found.append(parts)
    return found


def _merge_parts(*groups: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    found: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for group in groups:
        for parts in group:
            key = ".".join(parts).casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            found.append(parts)
    return found


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
    seen_blobs: set[str] = set()
    for blob in _blobs(reason, env):
        if blob in seen_blobs:
            continue
        seen_blobs.add(blob)
        parts, failed = _names_in(blob)
        if failed:
            continue
        for item in parts:
            key = ".".join(item).casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(item)
    return found


def _grant_names(env: dict[str, Any], warehouse: Path | None = None) -> set[str]:
    """Tables this Space may read, including its uploads.

    The same seed the session store gives the validator. No Space, or a
    Space that is not seeded, grants nothing.
    """
    from dms_executor.demo_grants import (
        DEMO_SPACE_GRANTS,
        canonical_space_id,
        ingested_bronze_tables,
    )

    raw = env.get("space_id")
    if not isinstance(raw, str) or not raw.strip():
        return set()
    sid = canonical_space_id(raw.strip())
    entry = DEMO_SPACE_GRANTS.get(sid)
    if not entry:
        return set()
    names = set(entry[1])
    names.update(ingested_bronze_tables(warehouse, space_id=sid))
    return names


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


def _sql_grant_names(env: dict[str, Any], warehouse: Path | None = None) -> set[str]:
    """Tables the validator would allow this turn to read.

    No Space is the personal context: the demo set plus uploads. A seeded
    Space is that seed plus its uploads. An id that is not a Space grants
    nothing. Prefix tails do not use this set.
    """
    from dms_executor.demo_grants import (
        DEMO_SPACE_GRANTS,
        canonical_space_id,
        ingested_bronze_tables,
    )
    from dms_executor.demo_warehouse import DEMO_TABLES

    raw = env.get("space_id")
    if not isinstance(raw, str) or not raw.strip():
        names = set(DEMO_TABLES)
        names.update(ingested_bronze_tables(warehouse))
        return names
    sid = canonical_space_id(raw.strip())
    entry = DEMO_SPACE_GRANTS.get(sid)
    if not entry:
        return set()
    names = set(entry[1])
    names.update(ingested_bronze_tables(warehouse, space_id=sid))
    return names


def _sql_parts(env: dict[str, Any], warehouse: Path | None) -> list[tuple[str, ...]]:
    sources = _sql_sources(env)
    if not sources:
        return []
    parsed: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for sql in sources:
        for parts in _tables_in_sql(sql):
            key = ".".join(parts).casefold()
            if key in seen:
                continue
            seen.add(key)
            parsed.append(parts)
    return _ungranted(parsed, _sql_grant_names(env, warehouse))


def _scrub_field(value: Any, parts_list: list[tuple[str, ...]]) -> Any:
    cleared = _clear_closed(value)
    if not parts_list:
        return cleared
    return _scrub_value(cleared, parts_list)


def hide_echo(
    env: dict[str, Any],
    reason: str,
    *,
    warehouse: Path | None = None,
) -> dict[str, Any]:
    """Scrub name-carrying fields. Id fields, loop SQL, and raw replies stay.

    Called at the HTTP boundary, after a loop has been re-attached.
    ``sql_used`` is included. ``loop[].sql`` and ``raw_reply`` are not.
    """
    parts_list = _merge_parts(
        _ungranted(_collect_parts(reason, env), _grant_names(env, warehouse)),
        _sql_parts(env, warehouse),
    )
    for key in _NAME_KEYS:
        if key not in env or key in _ID_KEYS:
            continue
        env[key] = _scrub_field(env[key], parts_list)
    if isinstance(env.get("sql_used"), str):
        env["sql_used"] = _scrub_field(env["sql_used"], parts_list)
    loop = env.get("loop")
    if isinstance(loop, list):
        for item in loop:
            if isinstance(item, dict) and isinstance(item.get("outcome"), str):
                item["outcome"] = _scrub_field(item["outcome"], parts_list)
    receipt = env.get("audit_receipt")
    if isinstance(receipt, dict):
        unsure = receipt.get("unsure")
        if isinstance(unsure, dict) and isinstance(unsure.get("why"), str):
            unsure["why"] = _scrub_field(unsure["why"], parts_list)
    return env
