"""Exclude scored-pack rows from the verified-question store.

A row is excluded when any one of these is listed:

- its provenance pack hash (``DMS_SCORED_PACK_HASHES``, plus the sha256 of
  ``tests/fixtures/curated_ceo/questions.yaml`` when that file is on disk)
- ``item_content_hash`` of its SQL (``DMS_SCORED_ITEM_HASHES``, an optional
  hash file, plus SQL strings in ``oracles.yaml`` when that file is on disk)
- ``item_result_hash`` of its result rows (``DMS_SCORED_RESULT_HASHES``)

Question text is not an input. A missing fixture adds nothing and logs
nothing, so an image without ``tests/`` still imports. Unset env vars log
nothing. An empty or malformed value logs a named warning and adds nothing.

When result hashes are configured, a row with no stored ``result_rows`` is
executed read-only against the Space warehouse. Error or timeout excludes
that row (``scored_result_hash_uncomputable``). A hashing exception on one
row excludes that row (``scored_item_hash_failed``) and the rest are still
filtered.

Limits (ponytail):
- File hashes are cached by resolved path for the process. A replaced file
  is picked up on restart.
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
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb
from sqlglot import exp, parse_one

logger = logging.getLogger(__name__)

SCORED_PACK_HASHES_ENV = "DMS_SCORED_PACK_HASHES"
SCORED_ITEM_HASHES_ENV = "DMS_SCORED_ITEM_HASHES"
SCORED_ITEM_HASHES_FILE_ENV = "DMS_SCORED_ITEM_HASHES_FILE"
SCORED_RESULT_HASHES_ENV = "DMS_SCORED_RESULT_HASHES"
WRITE_BLOCKED = "scored_pack_write_blocked"
CONFIG_EMPTY = "scored_item_hash_config_empty"
CONFIG_INVALID = "scored_item_hash_config_invalid"
FILE_UNREADABLE = "scored_item_hash_file_unreadable"
HASH_FAILED = "scored_item_hash_failed"
RESULT_UNCOMPUTABLE = "scored_result_hash_uncomputable"

_HASH = re.compile(r"^[0-9a-f]{64}$")
_RESULT_TIMEOUT_S = 2.0
_RESULT_CACHE: dict[tuple[str, str, str], str | BaseException] = {}


def curated_questions_path() -> Path:
    """In-repo scored pack file. Not read at import."""
    return _fixture("questions.yaml")


def curated_oracles_path() -> Path:
    """In-repo scored SQL file. Not read at import."""
    return _fixture("oracles.yaml")


def _fixture(name: str) -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "curated_ceo" / name


def pack_files() -> tuple[Path, ...]:
    path = curated_questions_path()
    if path.is_file():
        return (path,)
    return ()


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

    Each value becomes a stable string (numbers at 10 decimal places, None
    as ``null``). Row strings are sorted. Values stay in returned column
    order; column names are not part of the hash.
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
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        return _stable_number(value)
    if not isinstance(value, str) and hasattr(value, "isoformat"):
        try:
            return str(value.isoformat())
        except Exception:
            return str(value)
    return str(value)


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


def _env_hashes(name: str, warnings: list[str]) -> set[str]:
    if name not in os.environ:
        return set()
    raw = os.environ.get(name) or ""
    if not raw.strip():
        warnings.append(CONFIG_EMPTY)
        return set()
    found, invalid = _digest_tokens(raw)
    if invalid:
        warnings.append(CONFIG_INVALID)
    if not found and not invalid:
        warnings.append(CONFIG_EMPTY)
    return found


def _file_env_hashes(warnings: list[str]) -> set[str]:
    if SCORED_ITEM_HASHES_FILE_ENV not in os.environ:
        return set()
    raw = (os.environ.get(SCORED_ITEM_HASHES_FILE_ENV) or "").strip()
    path = Path(raw) if raw else None
    if path is None or not path.is_file():
        warnings.append(FILE_UNREADABLE)
        return set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        warnings.append(FILE_UNREADABLE)
        return set()
    if not text.strip():
        warnings.append(CONFIG_EMPTY)
        return set()
    found, invalid = _digest_tokens(text)
    if invalid:
        warnings.append(CONFIG_INVALID)
    return found


@lru_cache(maxsize=4)
def _file_hash(key: str) -> str:
    return hashlib.sha256(Path(key).read_bytes()).hexdigest()


def questions_file_hash(path: Path) -> str:
    return _file_hash(str(path.resolve()))


def _sql_strings(node: object) -> list[str]:
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "sql" and isinstance(child, str) and child.strip():
                    found.append(child)
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(node)
    return found


@lru_cache(maxsize=4)
def _cached_fixture_hashes(key: str) -> frozenset[str]:
    import yaml

    data = yaml.safe_load(Path(key).read_text(encoding="utf-8"))
    return frozenset(item_content_hash(sql) for sql in _sql_strings(data))


def _fixture_item_hashes(warnings: list[str]) -> set[str]:
    path = curated_oracles_path()
    if not path.is_file():
        return set()
    try:
        return set(_cached_fixture_hashes(str(path.resolve())))
    except Exception:
        warnings.append(FILE_UNREADABLE)
        return set()


@dataclass(frozen=True)
class _Config:
    pack: frozenset[str]
    item: frozenset[str]
    result: frozenset[str]
    warnings: tuple[str, ...]


def _load_config() -> _Config:
    warnings: list[str] = []
    pack = _env_hashes(SCORED_PACK_HASHES_ENV, warnings)
    for path in pack_files():
        try:
            pack.add(questions_file_hash(path))
        except OSError:
            warnings.append(FILE_UNREADABLE)
    item = _env_hashes(SCORED_ITEM_HASHES_ENV, warnings)
    item.update(_file_env_hashes(warnings))
    item.update(_fixture_item_hashes(warnings))
    result = _env_hashes(SCORED_RESULT_HASHES_ENV, warnings)
    return _Config(
        frozenset(pack),
        frozenset(item),
        frozenset(result),
        tuple(dict.fromkeys(warnings)),
    )


def _warn(cfg: _Config) -> None:
    for code in cfg.warnings:
        logger.warning(code)


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
    con = duckdb.connect(str(warehouse), read_only=True)
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
    """The only retrieval filter. Every store reader calls this."""
    cfg = _load_config()
    _warn(cfg)
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
    """Block a write of a listed scored row. Logs no question text."""
    cfg = _load_config()
    _warn(cfg)
    try:
        blocked = _excluded(row, cfg, warehouse)
    except Exception:
        logger.warning(HASH_FAILED)
        blocked = True
    if not blocked:
        return
    logger.warning(WRITE_BLOCKED)
    raise ValueError(WRITE_BLOCKED)
