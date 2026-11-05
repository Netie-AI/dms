"""Connector schema listings the connect path reads.

A SQL source lists tables through ``list_source_tables`` (the connector's own
catalog). A harness with no database registers a callable here. Connect reads
that listing. The request body does not get to name tables the connector did
not expose.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock

from dms_core.control_plane.connect_grants import clean_tables

_lock = Lock()
# The callable receives the resolved credential and must not keep it.
_registry: dict[str, Callable[[str], list[str]]] = {}


def register_connector(connector_id: str, list_tables: Callable[[str], list[str]]) -> None:
    if not connector_id or not connector_id.strip():
        raise ValueError("connector_id_required")
    _registry_set(connector_id.strip(), list_tables)


def _registry_set(connector_id: str, list_tables: Callable[[str], list[str]]) -> None:
    with _lock:
        _registry[connector_id] = list_tables


def connector_tables(connector_id: str, credential: str) -> list[str]:
    """Names the connector exposes for this in-memory credential. No row samples.

    ``credential`` is not stored and is not part of any error message.
    """
    with _lock:
        listing = _registry.get(connector_id)
    if listing is None:
        raise KeyError("connector")
    try:
        names = listing(credential)
    except Exception:
        raise ValueError("connector_failed") from None
    return list(clean_tables(names))


def clear_connectors() -> None:
    with _lock:
        _registry.clear()
