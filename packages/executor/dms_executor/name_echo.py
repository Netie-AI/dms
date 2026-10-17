"""Drop ungranted relation names from the fields a caller can read.

The ticket keeps the name. ``space_id``, ``session_id``, and the other
id fields are not rewritten. Matching is the whole identifier, any
length, so a name shorter than four characters is not a hole and a
name is not cut out of a longer identifier.

An ungranted tail is tokenized once in the serving dialect and parsed
once with that dialect's parser. The identifier parts come from that
parse. They are compared with the grant set. A granted name stays. A
name the grant set does not hold is removed. ``ungranted:file`` and
``ungranted:unparsed`` are reason codes. A tail that does not tokenize
is fail-closed: the field is cleared.

The grant set is the one ``Executor.grantable_tables`` resolved for
this turn (``note_turn_grants``). Nothing here derives a grant or opens
a connection. Outside a turn, or before the turn resolved its grants,
nothing is granted, so every named relation is removed. Cortex's
``warehouse_<table>`` alias of a demo-lake table is granted when the
table is.

On an abstain, the relations ``sql_used`` and ``loop[].sql`` read are
also checked. They come from the audit parser
(``sql_currency.tables_read_checked``), so a CTE in scope is not a
relation. ``loop[].sql`` and ``raw_reply`` are left for a later change.

An answer keeps its ``text`` and ``sql_used``. A served relation the
grant set does not hold is refused before this runs
(``served_ungranted`` and ``abstain.refuse_ungranted_answer``). It is
never hidden under a validated badge.
"""

from __future__ import annotations

import contextvars
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
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


@dataclass
class _Turn:
    owner: int
    space_id: str | None
    grants: frozenset[str] | None = None


_TURN: contextvars.ContextVar[_Turn | None] = contextvars.ContextVar(
    "dms_name_echo_turn", default=None
)


def begin_grant_turn(
    owner: object, space_id: str | None
) -> contextvars.Token[_Turn | None]:
    """Start recording the grant set ``owner`` resolves for ``space_id``."""
    return _TURN.set(_Turn(owner=id(owner), space_id=space_id))


def end_grant_turn(token: contextvars.Token[_Turn | None]) -> None:
    _TURN.reset(token)


def note_turn_grants(owner: object, space_id: str | None, grants: Iterable[str]) -> None:
    """Record what the turn's own grant function returned. Other calls are ignored."""
    turn = _TURN.get()
    if turn is None or turn.owner != id(owner) or turn.space_id != space_id:
        return
    turn.grants = frozenset(grants)


def turn_grants() -> frozenset[str] | None:
    """The grant set this turn resolved, or None when it resolved none."""
    turn = _TURN.get()
    return None if turn is None else turn.grants


def _granted(grants: Collection[str] | None) -> frozenset[str]:
    if grants is not None:
        return frozenset(grants)
    return turn_grants() or frozenset()


def _answered(env: dict[str, Any]) -> bool:
    return env.get("abstained") is False


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
    """Relations one statement reads, from the audit parser.

    A name sqlglot's scope resolves to a CTE or derived table is not a
    relation. SQL the parser cannot scope gets the audit's bounded scan,
    which counts more names, never fewer.
    """
    from dms_executor.sql_currency import tables_read_checked

    raw = (sql or "").strip()
    if not raw or raw.startswith("--"):
        return []
    labels, _approximate = tables_read_checked(raw)
    found: list[tuple[str, ...]] = []
    for label in labels:
        parts = tuple(part for part in label.split(".") if part)
        if parts and label.casefold() not in _CODE_TAILS:
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


_LAKE_ALIAS = "warehouse_"


def _lake_alias_granted(parts: tuple[str, ...], allowed: set[str]) -> bool:
    """True when ``parts`` is Cortex's ``warehouse_<t>`` alias of a granted lake table.

    ``table_is_granted`` reads the alias on the grant side only. Cortex
    also writes it in served SQL (``warehouse_inventory`` for a Space that
    grants ``inventory``). Only an unqualified alias of a demo-lake table
    maps, and only onto a grant that names that table or its alias
    exactly, so ``warehouse_<upload>``, ``hr.warehouse_<t>`` and a
    ``bronze.<t>`` upload grant open nothing.
    """
    from dms_executor.demo_warehouse import DEMO_TABLES

    if len(parts) != 1:
        return False
    name = parts[0].casefold()
    if not name.startswith(_LAKE_ALIAS):
        return False
    lake = name[len(_LAKE_ALIAS) :]
    if lake not in {t.casefold() for t in DEMO_TABLES}:
        return False
    names = {str(g).strip().casefold() for g in allowed}
    return lake in names or name in names


def _ungranted(
    parts_list: list[tuple[str, ...]], grants: Collection[str]
) -> list[tuple[str, ...]]:
    from dms_executor.ontology import table_is_granted

    allowed = set(grants)
    out: list[tuple[str, ...]] = []
    for parts in parts_list:
        name = ".".join(parts)
        if (
            name.casefold() in _CODE_TAILS
            or table_is_granted(name, allowed)
            or _lake_alias_granted(parts, allowed)
        ):
            continue
        out.append(parts)
    return out


def _sql_parts(sources: list[str]) -> list[tuple[str, ...]]:
    parsed: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for sql in sources:
        for parts in _tables_in_sql(sql):
            key = ".".join(parts).casefold()
            if key in seen:
                continue
            seen.add(key)
            parsed.append(parts)
    return parsed


def served_ungranted(
    env: dict[str, Any], grants: Collection[str] | None = None
) -> list[tuple[str, ...]]:
    """Relations an answer's ``sql_used`` reads that the grant set does not hold.

    Empty for an abstain. ``grants`` is the turn's set; None means the
    running turn's, and no recorded set grants nothing.
    """
    if not _answered(env):
        return []
    sql = env.get("sql_used")
    if not isinstance(sql, str) or not sql.strip() or sql.strip().startswith("--"):
        return []
    return _ungranted(_sql_parts([sql]), _granted(grants))


def _scrub_field(value: Any, parts_list: list[tuple[str, ...]]) -> Any:
    cleared = _clear_closed(value)
    if not parts_list:
        return cleared
    return _scrub_value(cleared, parts_list)


def hide_echo(
    env: dict[str, Any],
    reason: str,
    *,
    grants: Collection[str] | None = None,
) -> dict[str, Any]:
    """Scrub name-carrying fields. Id fields, loop SQL, and raw replies stay.

    Pure: no warehouse read, no connection. ``grants`` is the set
    ``Executor.grantable_tables`` resolved for this turn; None means the
    running turn's recorded set, and no recorded set grants nothing.

    On an abstain, relations read by ``sql_used`` and ``loop[].sql`` are
    checked too, and ``sql_used`` is scrubbed. On an answer, ``text`` and
    ``sql_used`` are not touched (``served_ungranted`` refuses those).
    """
    granted = _granted(grants)
    answered = _answered(env)
    parts_list = _ungranted(_collect_parts(reason, env), granted)
    if not answered:
        parts_list = _merge_parts(
            parts_list, _ungranted(_sql_parts(_sql_sources(env)), granted)
        )
    for key in _NAME_KEYS:
        if key not in env or key in _ID_KEYS or (answered and key == "text"):
            continue
        env[key] = _scrub_field(env[key], parts_list)
    if not answered and isinstance(env.get("sql_used"), str):
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
