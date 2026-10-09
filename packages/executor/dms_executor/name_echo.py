"""Drop ungranted relation names from user-visible abstain fields.

The ticket keeps the name. ``space_id``, ``session_id``, and the other
id fields are not rewritten. Matching is the whole identifier, any
length, so a name shorter than four characters is not a hole and a
name is not cut out of a longer identifier.

A delimited part is one non-identifier character, an identifier, and
one closing non-identifier character. The characters are not listed:
backticks, quotes, and brackets are the same shape. A qualified name
is those parts joined by dots. Only an ungranted tail is read, so a
quoted measure name or a filename is left alone. A name the
envelope's Space grants stays. ``ungranted:file`` and
``ungranted:unparsed`` are reason codes.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

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
_COMMA = re.compile(r"\s*,\s*")
# Dot, comma, colon, and parens separate names. They are not quotes.
_SEPARATOR = frozenset(".,:() \t\n\r")


class _Span(NamedTuple):
    name: str
    start: int
    end: int
    parts: tuple[str, ...]


def _wrapper(text: str, i: int) -> bool:
    """A delimiter, not a character that joins two identifier characters.

    A hyphen in ``boom-secret`` sits between identifier characters, so it
    is not a quote. A backtick, quote, or bracket does not.
    """
    ch = text[i]
    if ch.isalnum() or ch == "_" or ch in _SEPARATOR:
        return False
    prev = i > 0 and (text[i - 1].isalnum() or text[i - 1] == "_")
    nxt = i + 1 < len(text) and (text[i + 1].isalnum() or text[i + 1] == "_")
    return not (prev and nxt)


def _ident_at(text: str, i: int) -> int | None:
    if i >= len(text) or not (text[i].isalpha() or text[i] == "_"):
        return None
    j = i + 1
    while j < len(text) and (text[j].isalnum() or text[j] == "_"):
        j += 1
    return j


def _read_part(text: str, i: int) -> tuple[str, int] | None:
    """One bare or delimited part. The end index is past the closer."""
    if i >= len(text):
        return None
    if not _wrapper(text, i):
        end = _ident_at(text, i)
        if end is None:
            return None
        return text[i:end], end
    body = _ident_at(text, i + 1)
    if body is None or body >= len(text) or not _wrapper(text, body):
        return None
    return text[i + 1 : body], body + 1


def _read_qualified(text: str, i: int) -> _Span | None:
    part = _read_part(text, i)
    if part is None:
        return None
    parts = [part[0]]
    end = part[1]
    while end < len(text) and text[end] == ".":
        nxt = _read_part(text, end + 1)
        if nxt is None:
            break
        parts.append(nxt[0])
        end = nxt[1]
    return _Span(".".join(parts), i, end, tuple(parts))


def _tail_spans(text: str, pos: int) -> list[_Span]:
    spans: list[_Span] = []
    first = True
    while pos <= len(text):
        if not first:
            comma = _COMMA.match(text, pos)
            if not comma:
                break
            pos = comma.end()
        first = False
        span = _read_qualified(text, pos)
        if span is None:
            break
        spans.append(span)
        pos = span.end
    return spans


def _add_span(found: list[str], span: _Span) -> None:
    if not span.name or span.name.casefold() in _CODE_TAILS:
        return
    if span.name not in found:
        found.append(span.name)


def echoed_names(text: str) -> list[str]:
    """Relation identifiers the grant check named, quoting stripped.

    The tail may quote each part with any delimiter, or leave it bare,
    and may join parts with dots. A quoted word outside that tail is
    not a relation: measure names and refusals use the same shape.
    """
    found: list[str] = []
    raw = text or ""
    for match in _PREFIX.finditer(raw):
        for span in _tail_spans(raw, match.end()):
            _add_span(found, span)
    return found


def _relations(names: list[str]) -> list[str]:
    """Full relation, then the bare table ``table_is_granted`` compares.

    Leading dotted pieces are schema qualifiers. They are not relations.
    """
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        parts = [part for part in name.split(".") if part]
        bare = parts[-1] if parts else name
        for piece in (name, bare):
            key = piece.casefold()
            if key in _CODE_TAILS or key in seen:
                continue
            seen.add(key)
            out.append(piece)
    out.sort(key=len, reverse=True)
    return out


def _should_drop(span: _Span, dropping: set[str]) -> bool:
    if span.name.casefold() in _CODE_TAILS:
        return False
    if span.name.casefold() in dropping:
        return True
    return span.parts[-1].casefold() in dropping


def scrub_text(text: str, names: list[str]) -> str:
    """Remove each ungranted name, including its delimiters."""
    if not text or not names:
        return text
    idents = _relations(names)
    dropping = {ident.casefold() for ident in idents}
    kept: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        span = _read_qualified(text, i)
        if span is None:
            kept.append(text[i])
            i += 1
            continue
        if _should_drop(span, dropping):
            i = span.end
            continue
        kept.append(text[i : span.end])
        i = span.end
    text = "".join(kept)
    for ident in idents:
        text = re.sub(
            rf"(?i)(?<![A-Za-z0-9_]){re.escape(ident)}(?![A-Za-z0-9_])",
            "",
            text,
        )
    return text


def _scrub_value(value: Any, names: list[str]) -> Any:
    if isinstance(value, str):
        return scrub_text(value, names)
    if isinstance(value, list):
        return [_scrub_value(item, names) for item in value]
    if isinstance(value, dict):
        return {
            str(key): item if str(key) in _ID_KEYS else _scrub_value(item, names)
            for key, item in value.items()
        }
    return value


def _collect(reason: str, env: dict[str, Any]) -> list[str]:
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
    found: list[str] = []
    for blob in blobs:
        for name in echoed_names(blob):
            if name not in found:
                found.append(name)
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


def _ungranted(names: list[str], grants: set[str]) -> list[str]:
    from dms_executor.ontology import table_is_granted

    out: list[str] = []
    for name in names:
        if name.casefold() in _CODE_TAILS or table_is_granted(name, grants):
            continue
        if name not in out:
            out.append(name)
    return out


def hide_echo(env: dict[str, Any], reason: str) -> dict[str, Any]:
    """Scrub name-carrying fields. Id fields are left as they are."""
    names = _ungranted(_collect(reason, env), _grant_names(env))
    if not names:
        return env
    for key in _NAME_KEYS:
        if key not in env or key in _ID_KEYS:
            continue
        env[key] = _scrub_value(env[key], names)
    loop = env.get("loop")
    if isinstance(loop, list):
        for item in loop:
            if isinstance(item, dict) and isinstance(item.get("outcome"), str):
                item["outcome"] = scrub_text(item["outcome"], names)
    receipt = env.get("audit_receipt")
    if isinstance(receipt, dict):
        unsure = receipt.get("unsure")
        if isinstance(unsure, dict) and isinstance(unsure.get("why"), str):
            unsure["why"] = scrub_text(unsure["why"], names)
    return env
