"""Exclude scored rows from the verified-question store.

Hashes come only from operator environment (or a file path the environment
names). Real values are set through prove config by DevOps. This module
does not read a pack file of its own.

A row is excluded when any listed key matches:

- provenance pack hash (``DMS_SCORED_PACK_HASHES``)
- ``item_content_hash`` of its SQL (``DMS_SCORED_ITEM_HASHES`` or
  ``DMS_SCORED_ITEM_HASHES_FILE``)
- ``item_result_hash`` of its result rows (``DMS_SCORED_RESULT_HASHES``)

Question text is not an input. Unset variables mean no quarantine: retrieval
is unchanged. A variable that is set but empty, malformed, or unreadable
fails closed: retrieval returns no rows, writes are refused with
``skills_quarantine_config_invalid``, and answer envelopes carry that
stamp. The ask still runs.

When result hashes are configured, a row with no stored ``result_rows`` is
executed read-only on the Space warehouse after the chat SQL guard, with
external access off. Error or timeout excludes that row
(``scored_result_hash_uncomputable``). A hashing exception on one row
excludes that row (``scored_item_hash_failed``).

Limits (ponytail):
- Result hashes are cached by warehouse path, row id, and SQL, including
  failures, so a bad statement is not retried on every list.
- The read-only probe runs in a daemon thread. On timeout the connection is
  interrupted and closed; a thread that ignores the interrupt is abandoned.
  Upgrade: join to a hard deadline, then a process-wide query cap.
- Integers above 2**53 collide in ``item_result_hash`` because numbers go
  through float. Upgrade: Decimal quantize.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from sqlglot import exp, parse_one

from dms_executor.manifest import reject_hostile_chat_sql

logger = logging.getLogger(__name__)

SCORED_PACK_HASHES_ENV = "DMS_SCORED_PACK_HASHES"
SCORED_ITEM_HASHES_ENV = "DMS_SCORED_ITEM_HASHES"
SCORED_ITEM_HASHES_FILE_ENV = "DMS_SCORED_ITEM_HASHES_FILE"
SCORED_RESULT_HASHES_ENV = "DMS_SCORED_RESULT_HASHES"
WRITE_BLOCKED = "scored_pack_write_blocked"
CONFIG_STAMP = "skills_quarantine_config_invalid"
HASH_FAILED = "scored_item_hash_failed"
RESULT_UNCOMPUTABLE = "scored_result_hash_uncomputable"

_HASH = re.compile(r"^[0-9a-f]{64}$")
_RESULT_TIMEOUT_S = 2.0
_RESULT_CACHE: dict[tuple[str, str, str], str | BaseException] = {}


def canonical_pack_hash(value: object) -> str | None:
    """64-hex sha256, or None when ``value`` is not a hash."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text.startswith("sha256:"):
        text = text[7:].strip()
    if _HASH.fullmatch(text):
        return text
    return None


def item_content_hash(sql: str) -> str:
    """sha256 of canonical SQL. Parse failure still returns a hash."""
    return hashlib.sha256(_canonical_sql(sql).encode("utf-8")).hexdigest()


def _canonical_sql(sql: str) -> str:
    text = sql or ""
    tree = _parse_sql(text)
    if tree is None:
        return " ".join(text.casefold().split())
    for node in tree.find_all(exp.Identifier):
        name = node.name
        if isinstance(name, str):
            node.set("this", name.lower())
        node.set("quoted", False)
    emitted = tree.sql(dialect="duckdb", normalize=True, comments=False)
    return " ".join(str(emitted).split())


def _parse_sql(sql: str) -> Any | None:
    attempts = [sql]
    swapped = sql.replace("`", '"')
    if swapped != sql:
        attempts.append(swapped)
    for attempt in attempts:
        try:
            return parse_one(attempt, read="duckdb")
        except Exception:
            continue
    return None


def item_result_hash(rows: Iterable[Any]) -> str:
    """sha256 of result rows.

    Each value is type-tagged, then rendered as a stable string (numbers at
    10 decimal places, None as ``null``). Row strings are sorted. Values stay
    in returned column order; column names are not part of the hash.
    """
    lines = [
        "\x1f".join(_escape(_stable_value(value)) for value in _row_values(row))
        for row in rows
    ]
    lines.sort()
    return hashlib.sha256("\x1e".join(lines).encode("utf-8")).hexdigest()


def _row_values(row: Any) -> list[Any]:
    if isinstance(row, Mapping):
        return list(row.values())
    if isinstance(row, (list, tuple)):
        return list(row)
    return [row]


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\x1e", "\\x1e").replace("\x1f", "\\x1f")


def _stable_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool:true" if value else "bool:false"
    if isinstance(value, (int, float, Decimal)):
        return "num:" + _stable_number(value)
    if isinstance(value, str):
        return "str:" + value
    if hasattr(value, "isoformat"):
        try:
            return "time:" + str(value.isoformat())
        except Exception:
            return "other:" + str(value)
    return "other:" + str(value)


def _stable_number(value: int | float | Decimal) -> str:
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
    try:
        return f"{float(value):.10f}"
    except (OverflowError, ValueError):
        return str(value)


def _digest_tokens(raw: str) -> tuple[set[str], bool]:
    found: set[str] = set()
    invalid = False
    for part in raw.replace(",", " ").split():
        digest = canonical_pack_hash(part)
        if digest:
            found.add(digest)
        else:
            invalid = True
    return found, invalid


def _parse_hash_text(raw: str) -> tuple[set[str], bool]:
    """Hashes from one configured source. Empty or any bad token is invalid."""
    if not raw.strip():
        return set(), True
    found, invalid = _digest_tokens(raw)
    if invalid or not found:
        return set(), True
    return found, False


def _env_hashes(name: str) -> tuple[set[str], bool]:
    """``(hashes, invalid)``. Unset is valid and empty."""
    if name not in os.environ:
        return set(), False
    return _parse_hash_text(os.environ.get(name) or "")


def _file_hashes() -> tuple[set[str], bool]:
    """``(hashes, invalid)``. Unset is valid and empty."""
    if SCORED_ITEM_HASHES_FILE_ENV not in os.environ:
        return set(), False
    raw = (os.environ.get(SCORED_ITEM_HASHES_FILE_ENV) or "").strip()
    if not raw:
        return set(), True
    path = Path(raw)
    if not path.is_file():
        return set(), True
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set(), True
    return _parse_hash_text(text)


@dataclass(frozen=True)
class _Config:
    pack: frozenset[str]
    item: frozenset[str]
    result: frozenset[str]
    invalid: bool


def _load_config() -> _Config:
    invalid = False
    pack, bad = _env_hashes(SCORED_PACK_HASHES_ENV)
    invalid = invalid or bad
    item, bad = _env_hashes(SCORED_ITEM_HASHES_ENV)
    invalid = invalid or bad
    from_file, bad = _file_hashes()
    invalid = invalid or bad
    item.update(from_file)
    result, bad = _env_hashes(SCORED_RESULT_HASHES_ENV)
    invalid = invalid or bad
    return _Config(frozenset(pack), frozenset(item), frozenset(result), invalid)


def config_stamp() -> str | None:
    """Envelope stamp when quarantine config is set but unusable. Else None."""
    if _load_config().invalid:
        return CONFIG_STAMP
    return None


def provenance_pack_hash(row: Mapping[str, Any]) -> str | None:
    """Pack hash carried on the row, if it has one."""
    for key in ("pack_hash", "source_pack_hash"):
        digest = canonical_pack_hash(row.get(key))
        if digest:
            return digest
    for key in ("provenance", "source"):
        raw = row.get(key)
        if isinstance(raw, str):
            digest = canonical_pack_hash(raw)
            if digest:
                return digest
            continue
        if not isinstance(raw, dict):
            continue
        for sub in ("pack_hash", "hash", "pack"):
            digest = canonical_pack_hash(raw.get(sub))
            if digest:
                return digest
    return None


def _row_sql(row: Mapping[str, Any]) -> str:
    for key in ("sql", "sql_text"):
        raw = row.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw
    return ""


def _row_result_hash(row: Mapping[str, Any], warehouse: Path | None) -> str:
    if "result_rows" in row:
        raw = row.get("result_rows")
        if not isinstance(raw, (list, tuple)):
            raise TypeError(RESULT_UNCOMPUTABLE)
        return item_result_hash(raw)
    sql = _row_sql(row)
    if warehouse is None or not sql:
        raise RuntimeError(RESULT_UNCOMPUTABLE)
    return _cached_result_hash(Path(warehouse), str(row.get("asset_id") or ""), sql)


def _cached_result_hash(warehouse: Path, asset_id: str, sql: str) -> str:
    key = (str(warehouse.resolve()), asset_id, sql)
    cached = _RESULT_CACHE.get(key)
    if isinstance(cached, str):
        return cached
    if isinstance(cached, BaseException):
        raise cached
    try:
        digest = item_result_hash(_readonly_rows(warehouse, sql))
    except Exception as exc:
        _RESULT_CACHE[key] = exc
        raise
    _RESULT_CACHE[key] = digest
    return digest


def _readonly_rows(warehouse: Path, sql: str) -> list[Any]:
    """Read-only probe. Chat SQL guard first, then external access off."""
    reject_hostile_chat_sql(sql)
    con = duckdb.connect(
        str(warehouse),
        read_only=True,
        config={"enable_external_access": False},
    )
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            cur = con.execute(sql)
            desc = cur.description
            if not desc:
                box["rows"] = []
                return
            cols = [str(col[0]) for col in desc]
            box["rows"] = [dict(zip(cols, rec, strict=False)) for rec in cur.fetchall()]
        except Exception as exc:
            box["error"] = exc

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(_RESULT_TIMEOUT_S)
    if worker.is_alive():
        try:
            con.interrupt()
        except Exception:
            pass
        worker.join(0.5)
    try:
        con.close()
    except Exception:
        pass
    if worker.is_alive() or ("rows" not in box and "error" not in box):
        raise TimeoutError(RESULT_UNCOMPUTABLE)
    if "error" in box:
        raise box["error"]
    rows = box["rows"]
    if not isinstance(rows, list):
        raise TypeError(RESULT_UNCOMPUTABLE)
    return rows


def _excluded(row: Mapping[str, Any], cfg: _Config, warehouse: Path | None) -> bool:
    carried = provenance_pack_hash(row)
    if carried is not None and carried in cfg.pack:
        return True
    sql = _row_sql(row)
    if sql and item_content_hash(sql) in cfg.item:
        return True
    if not cfg.result:
        return False
    try:
        digest = _row_result_hash(row, warehouse)
    except Exception:
        logger.warning(RESULT_UNCOMPUTABLE)
        return True
    return digest in cfg.result


def filter_retrieved_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    warehouse: Path | None = None,
) -> list[dict[str, Any]]:
    """The only retrieval filter. Every store reader calls this.

    A set-but-unusable hash source returns no rows. Unset config returns the
    rows the hash lists do not exclude.
    """
    cfg = _load_config()
    if cfg.invalid:
        logger.warning(CONFIG_STAMP)
        return []
    kept: list[dict[str, Any]] = []
    for row in rows:
        try:
            drop = _excluded(row, cfg, warehouse)
        except Exception:
            logger.warning(HASH_FAILED)
            continue
        if not drop:
            kept.append(dict(row))
    return kept


def reject_scored_write(
    row: Mapping[str, Any],
    *,
    warehouse: Path | None = None,
) -> None:
    """Block a write of a listed scored row. Logs no question text.

    An unusable hash source refuses the write. A row is not stored when this
    process cannot tell whether it is scored.
    """
    cfg = _load_config()
    if cfg.invalid:
        logger.warning(CONFIG_STAMP)
        raise ValueError(CONFIG_STAMP)
    try:
        blocked = _excluded(row, cfg, warehouse)
    except Exception:
        logger.warning(HASH_FAILED)
        blocked = True
    if not blocked:
        return
    logger.warning(WRITE_BLOCKED)
    raise ValueError(WRITE_BLOCKED)
