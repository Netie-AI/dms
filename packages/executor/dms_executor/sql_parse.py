"""One sqlglot parse per (sql, dialect), copied out so callers can mutate.

The serving dialect is loaded at import. A later ask does not pay the lazy
dialect import, and a second parse of the same text does not enter sqlglot.
``live_ask`` binds a per-ask slot so every gate in that ask copies the first
tree instead of parsing again.

ponytail: the cache holds 512 statements and the copies share one lock.
Ceiling: a process that parses more than 512 distinct statements evicts the
oldest. Upgrade: a larger cache, or a tree passed on the ask object, if
eviction shows up in a profile.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextvars import ContextVar, Token
from functools import lru_cache
from typing import Any

import sqlglot
from sqlglot.errors import SqlglotError

_ASK: ContextVar[dict[tuple[Any, ...], tuple[str, Any]] | None] = ContextVar(
    "dms_ask_parse", default=None
)
_COPY = threading.Lock()
_CACHE = 512
_WARMED: set[str] = set()


def bind_ask_parse() -> Token[dict[tuple[Any, ...], tuple[str, Any]] | None]:
    """Remember trees for this ask. The caller resets the token."""
    return _ASK.set({})


def reset_ask_parse(
    token: Token[dict[tuple[Any, ...], tuple[str, Any]] | None],
) -> None:
    _ASK.reset(token)


def release_ask_parse() -> None:
    """Drop a slot left behind by a failed ask. Tests use this."""
    if _ASK.get() is not None:
        _ASK.set(None)


@lru_cache(maxsize=_CACHE)
def _cached_parse(sql: str, dialect: str) -> tuple[str, tuple[Any, ...]]:
    try:
        trees = tuple(sqlglot.parse(sql, read=dialect))
    except SqlglotError as exc:
        return ("err", (str(exc),))
    return ("ok", trees)


@lru_cache(maxsize=_CACHE)
def _cached_one(sql: str, dialect: str) -> tuple[str, Any]:
    try:
        return ("ok", sqlglot.parse_one(sql, read=dialect))
    except SqlglotError as exc:
        return ("err", str(exc))


def _copy_out(found: tuple[str, Any], *, many: bool) -> Any:
    if found[0] == "err":
        message = found[1][0] if many else found[1]
        raise SqlglotError(str(message))
    with _COPY:
        if many:
            return [None if tree is None else tree.copy() for tree in found[1]]
        return found[1].copy()


def _remember(key: tuple[Any, ...], found: tuple[str, Any]) -> None:
    slot = _ASK.get()
    if slot is not None:
        slot[key] = found


def parse_cached(sql: str, dialect: str) -> list[Any]:
    """Copies of ``sqlglot.parse``. Raises ``SqlglotError`` on a bad statement."""
    warm_serving_dialect(dialect)
    key = ("many", sql, dialect)
    slot = _ASK.get()
    if slot is not None and key in slot:
        return _copy_out(slot[key], many=True)
    found = _cached_parse(sql, dialect)
    _remember(key, found)
    return _copy_out(found, many=True)


def parse_one_cached(sql: str, dialect: str) -> Any:
    """A copy of ``sqlglot.parse_one``. Raises ``SqlglotError`` on a bad statement."""
    warm_serving_dialect(dialect)
    key = ("one", sql, dialect)
    slot = _ASK.get()
    if slot is not None and key in slot:
        return _copy_out(slot[key], many=False)
    found = _cached_one(sql, dialect)
    _remember(key, found)
    return _copy_out(found, many=False)


def one_statement(sql: str, dialect: str) -> Any | None:
    """The one statement in ``sql``, or None when it does not parse as one."""
    try:
        roots = [tree for tree in parse_cached(sql or "", dialect) if tree is not None]
    except SqlglotError:
        return None
    if len(roots) != 1:
        return None
    return roots[0]


def cache_info() -> tuple[Any, Any]:
    """``(parse, parse_one)`` cache stats. Tests read hits and misses."""
    return _cached_parse.cache_info(), _cached_one.cache_info()


def clear_parse_cache() -> None:
    _cached_parse.cache_clear()
    _cached_one.cache_clear()


def warm_serving_dialect(dialect: str) -> None:
    """Load ``dialect`` so the first ask does not import it under the lock.

    The caller passes the serving dialect. This module does not spell it.
    """
    if dialect in _WARMED:
        return
    sqlglot.Dialect.get_or_raise(dialect)
    _WARMED.add(dialect)


def warmed_dialects() -> Iterator[str]:
    return iter(tuple(_WARMED))
