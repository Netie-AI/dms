"""DMS_ONTO_LINK_PROPOSE. Default off.

After derive, value inclusion (``link_inclusion``) lists join candidates.
OpenVault role ``ontology`` ranks and names them from column names, types,
and samples. The coverage gate decides what is eligible. A pair under the
gate is not stored, even when the model names it. Stored rows are proposed
items with payload status ``unverified``. The ask path does not use them
until the existing item approve route marks them approved.

Off, this module is not called. No model id and no provider id is sent.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any
from uuid import UUID

from dms_executor.link_inclusion import (
    COVERAGE_MIN,
    infer_fk_candidates,
    quote_ident,
    relation_sql,
)

FLAG = "DMS_ONTO_LINK_PROPOSE"
ROLE = "ontology"
SCHEMA_NAME = "LinkRank"
SOURCE = "link-propose"
# In-memory and Postgres both store created_by. A missing actor still has a uuid.
_WRITER = "11111111-1111-4111-8111-111111111111"
# ponytail: one model call, 64 candidates. Upgrade path: page the rest.
_MODEL_CAP = 64
_SAMPLE_LIMIT = 5
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CHAT_PATH = "/v1/chat/completions"
API_KEY_ENVS = ("OPENVAULT_API_KEY", "CORTEX_API_KEY")
API_KEY_PREFIX = "ov_"
URL_ENV = "OPENVAULT_URL"
_PROVENANCE_COLUMNS = frozenset({"_src", "_ingest_id"})
_MODEL_KEYS = frozenset(
    {
        "model",
        "model_id",
        "model_name",
        "served_model",
        "served_provider",
        "provider",
        "strict",
    }
)
_SYSTEM = (
    "Rank these join candidates and assign each an identifier name. "
    "Use only the column names, types, and sample values in the user message. "
    "Reply with a JSON object and nothing else. "
    "The key ranked is a list of objects with child, column, parent, "
    "parent_column, name, and rank. "
    "name is one identifier of letters, digits, and underscores. "
    "rank is an integer starting at 1. "
    "Include only pairs from the candidate list. "
    "Do not name a model or a provider."
)

Transport = Callable[[dict[str, Any]], Any]
_TRANSPORT: ContextVar[Transport | None] = ContextVar(
    "dms_link_propose_transport", default=None
)


class LinkCallError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def enabled() -> bool:
    return os.environ.get(FLAG, "0").strip() == "1"


def bind_link_transport(transport: Transport) -> Token[Transport | None]:
    return _TRANSPORT.set(transport)


def reset_link_transport(token: Token[Transport | None]) -> None:
    _TRANSPORT.reset(token)


def propose_links(
    catalog: Mapping[str, Any],
    *,
    warehouse: Path,
    space_id: str,
    actor: UUID | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Profile ``warehouse``, ask for names, store eligible links.

    Returns ``(view rows, error code)``. An error stores nothing. The view
    rows are what the ontology snapshot shows as unverified.
    """
    from dms_executor.demo_warehouse import connect_file

    con = connect_file(Path(warehouse))
    try:
        return _propose(con, catalog, space_id=space_id, actor=actor)
    finally:
        con.close()


def apply_approved_links(onto: Any, space_id: str) -> None:
    """Add approved link-propose items for this Space. Proposed items stay off."""
    from dms_core.control_plane.onto_data import ontology_data_store

    try:
        rows = ontology_data_store().approved_items()
    except Exception:
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("source") or "") != SOURCE:
            continue
        if str(row.get("kind") or "") != "link":
            continue
        if str(row.get("status") or "") != "approved":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if not _same_space(str(payload.get("space_id") or ""), space_id):
            continue
        _add_link(onto, payload)


def _propose(
    con: Any,
    catalog: Mapping[str, Any],
    *,
    space_id: str,
    actor: UUID | None,
) -> tuple[list[dict[str, Any]], str | None]:
    pk = _primary_keys(catalog)
    if not pk:
        return [], None
    prof = _profile(con, list(pk))
    hits = infer_fk_candidates(con, prof, pk, coverage_min=0.0)
    declared = _declared(catalog)
    hits = [hit for hit in hits if tuple(hit[:4]) not in declared]
    if not hits:
        return [], None
    sent = _cap(hits)
    try:
        ranked = _ask(con, prof, sent)
    except LinkCallError as exc:
        return [], exc.code
    if ranked is None:
        return [], "link_rank_invalid"
    chosen = _eligible(ranked, hits)
    if not chosen:
        return [], None
    return _store(chosen, space_id=space_id, actor=actor), None


def _primary_keys(catalog: Mapping[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name, cols in (catalog.get("primary_keys") or {}).items():
        if isinstance(cols, list) and cols:
            out[str(name)] = [str(col) for col in cols]
    return out


def _declared(catalog: Mapping[str, Any]) -> set[tuple[str, str, str, str]]:
    found: set[tuple[str, str, str, str]] = set()
    for fk in catalog.get("foreign_keys") or []:
        if not isinstance(fk, dict):
            continue
        found.add(
            (
                str(fk.get("from_table") or ""),
                str(fk.get("from_column") or ""),
                str(fk.get("to_table") or ""),
                str(fk.get("to_column") or ""),
            )
        )
    return found


def _profile(con: Any, relations: Sequence[str]) -> dict[str, Any]:
    from dms_executor.link_inclusion import INTEGER

    prof: dict[str, Any] = {}
    for rel in relations:
        rel_sql = relation_sql(rel)
        rows = int(con.execute(f"SELECT COUNT(*) FROM {rel_sql}").fetchone()[0])
        columns: dict[str, Any] = {}
        for described in con.execute(f"DESCRIBE SELECT * FROM {rel_sql}").fetchall():
            name, ctype = str(described[0]), str(described[1])
            if name in _PROVENANCE_COLUMNS:
                continue
            quoted = quote_ident(name)
            nulls, distinct = con.execute(
                f"SELECT COUNT(*) - COUNT({quoted}), COUNT(DISTINCT {quoted}) FROM {rel_sql}"
            ).fetchone()
            info: dict[str, Any] = {
                "type": ctype,
                "nulls": int(nulls),
                "distinct": int(distinct),
            }
            if INTEGER.match(ctype):
                lo, hi = con.execute(
                    f"SELECT MIN({quoted}), MAX({quoted}) FROM {rel_sql}"
                ).fetchone()
                info["min"], info["max"] = lo, hi
            columns[name] = info
        prof[rel] = {"rows": rows, "columns": columns}
    return prof


def _cap(hits: list[list[Any]]) -> list[list[Any]]:
    strong = [hit for hit in hits if float(hit[4]) >= COVERAGE_MIN]
    weak = [hit for hit in hits if float(hit[4]) < COVERAGE_MIN]
    strong.sort(key=lambda hit: float(hit[4]), reverse=True)
    weak.sort(key=lambda hit: float(hit[4]), reverse=True)
    return (strong + weak)[:_MODEL_CAP]


def _ask(
    con: Any, prof: dict[str, Any], hits: Sequence[Sequence[Any]]
) -> list[dict[str, Any]] | None:
    candidates = [_candidate(con, prof, hit) for hit in hits]
    user = json.dumps({"candidates": candidates}, ensure_ascii=True)
    body: dict[str, Any] = {
        "role": ROLE,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        "schema": SCHEMA_NAME,
    }
    _reject_model_keys(body)
    transport = _TRANSPORT.get() or default_transport
    payload = transport(body)
    return _parse_ranked(payload)


def _candidate(con: Any, prof: dict[str, Any], hit: Sequence[Any]) -> dict[str, Any]:
    child, col, parent, pcol = str(hit[0]), str(hit[1]), str(hit[2]), str(hit[3])
    return {
        "child": child,
        "column": col,
        "column_type": str(prof[child]["columns"][col]["type"]),
        "parent": parent,
        "parent_column": pcol,
        "parent_type": str(prof[parent]["columns"][pcol]["type"]),
        "child_samples": _samples(con, child, col),
        "parent_samples": _samples(con, parent, pcol),
    }


def _samples(con: Any, relation: str, column: str) -> list[str]:
    quoted = quote_ident(column)
    try:
        rows = con.execute(
            f"SELECT DISTINCT {quoted} FROM {relation_sql(relation)} "
            f"WHERE {quoted} IS NOT NULL LIMIT {_SAMPLE_LIMIT}"
        ).fetchall()
    except Exception:
        return []
    out: list[str] = []
    for row in rows:
        text = str(row[0])
        out.append(text[:40])
    return out


def _parse_ranked(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(_strip_fence(payload))
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict) or _has_model_key(payload):
        return None
    raw = payload.get("ranked")
    if not isinstance(raw, list):
        return None
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or _has_model_key(item):
            return None
        child, column = item.get("child"), item.get("column")
        parent, pcol = item.get("parent"), item.get("parent_column")
        if not all(isinstance(part, str) and part for part in (child, column, parent, pcol)):
            continue
        rank = item.get("rank")
        if isinstance(rank, bool) or not isinstance(rank, int):
            rank = 0
        name = item.get("name")
        out.append(
            {
                "child": child,
                "column": column,
                "parent": parent,
                "parent_column": pcol,
                "name": name.strip() if isinstance(name, str) else "",
                "rank": rank,
            }
        )
    return out


def _eligible(
    ranked: Sequence[Mapping[str, Any]], hits: Sequence[Sequence[Any]]
) -> list[dict[str, Any]]:
    by_key = {tuple(hit[:4]): hit for hit in hits}
    chosen: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    order = sorted(ranked, key=lambda row: int(row.get("rank") or 0))
    for row in order:
        key = (
            str(row["child"]),
            str(row["column"]),
            str(row["parent"]),
            str(row["parent_column"]),
        )
        hit = by_key.get(key)
        if hit is None or key in seen:
            continue
        if float(hit[4]) < COVERAGE_MIN:
            continue
        name = str(row.get("name") or "")
        if _IDENT.fullmatch(name) is None:
            continue
        seen.add(key)
        chosen.append(
            {
                "child": key[0],
                "column": key[1],
                "parent": key[2],
                "parent_column": key[3],
                "name": name,
                "coverage": hit[4],
                "fanout": hit[5],
                "rank": int(row.get("rank") or 0),
            }
        )
    return chosen


def _store(
    chosen: Sequence[Mapping[str, Any]],
    *,
    space_id: str,
    actor: UUID | None,
) -> list[dict[str, Any]]:
    from dms_core.control_plane.onto_data import ontology_data_store

    joins = [
        {
            "source_table": row["child"],
            "source_column": row["column"],
            "target_table": row["parent"],
            "target_column": row["parent_column"],
            "name": row["name"],
            "status": "unverified",
            "coverage": row["coverage"],
            "fanout": row["fanout"],
            "space_id": space_id,
        }
        for row in chosen
    ]
    body = {
        "status": "proposed",
        "served": False,
        "source": SOURCE,
        "vendor_sha": SOURCE,
        "provenance": {
            "model_stamp": {"role": ROLE, "schema": SCHEMA_NAME},
            "validation": {"ok": True, "checks": ["value_inclusion", "coverage", "fanout"]},
        },
        "joins": joins,
    }
    created = str(actor) if actor is not None else _WRITER
    stored = ontology_data_store().store_proposal(
        body,
        created_by=created,
        space_id=space_id,
    )
    if stored.get("served") or stored.get("status") != "proposed":
        return []
    view: list[dict[str, Any]] = []
    for item in stored.get("items") or []:
        if not isinstance(item, dict) or item.get("kind") != "link":
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        view.append(
            {
                "name": payload.get("name") or "",
                "from": payload.get("from_object") or "",
                "from_columns": list(payload.get("from_columns") or []),
                "to": payload.get("to_object") or "",
                "to_columns": list(payload.get("to_columns") or []),
                "cardinality": None,
                "max_fanout": payload.get("fanout"),
                "status": "unverified",
                "coverage": payload.get("coverage"),
                "fanout": payload.get("fanout"),
                "item_id": item.get("id"),
                "violations": [],
            }
        )
    return view


def _add_link(onto: Any, payload: Mapping[str, Any]) -> None:
    name = str(payload.get("name") or "")
    child = str(payload.get("from_object") or "")
    parent = str(payload.get("to_object") or "")
    from_cols = payload.get("from_columns")
    to_cols = payload.get("to_columns")
    if not name or not isinstance(from_cols, list) or not isinstance(to_cols, list):
        return
    if child not in onto.objects or parent not in onto.objects:
        return
    wanted_from = tuple(str(col).casefold() for col in from_cols)
    wanted_to = tuple(str(col).casefold() for col in to_cols)
    for link in onto.links.values():
        if link.from_object != child or link.to_object != parent:
            continue
        have_from = tuple(str(col).casefold() for col in link.from_columns)
        have_to = tuple(str(col).casefold() for col in link.to_columns)
        if have_from == wanted_from and have_to == wanted_to:
            return
    unique = name
    n = 2
    while unique in onto.links:
        unique = f"{name}_{n}"
        n += 1
    try:
        onto.add_link(
            unique,
            child,
            [str(col) for col in from_cols],
            parent,
            [str(col) for col in to_cols],
            one_to_one=False,
        )
    except (KeyError, ValueError):
        return


def _same_space(left: str, right: str) -> bool:
    try:
        return UUID(left) == UUID(right)
    except ValueError:
        return False


def _strip_fence(text: str) -> str:
    raw = text.strip()
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _has_model_key(value: Mapping[str, Any]) -> bool:
    return any(str(key).casefold() in _MODEL_KEYS for key in value)


def _reject_model_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _MODEL_KEYS:
                raise LinkCallError("model_key_refused")
            _reject_model_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_model_keys(item)


def load_api_key() -> str:
    for name in API_KEY_ENVS:
        raw = os.environ.get(name, "").strip()
        if raw.startswith(API_KEY_PREFIX):
            return raw
    return ""


def endpoint_url() -> str:
    base = os.environ.get(URL_ENV, "").strip().rstrip("/")
    if not base:
        raise LinkCallError("openvault_url_missing")
    return base + CHAT_PATH


def default_transport(body: dict[str, Any]) -> Any:
    """POST the body. The bearer is an ov_ key. The code never carries the key."""
    import httpx

    url = endpoint_url()
    key = load_api_key()
    if not key:
        raise LinkCallError("ov_api_key_missing")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        response = httpx.post(url, headers=headers, json=body, timeout=60.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise LinkCallError("ov_unauthorized") from None
        raise LinkCallError(f"ov_http_{status}") from None
    except httpx.HTTPError:
        raise LinkCallError("ov_unreachable") from None
    data = response.json()
    if isinstance(data, dict) and isinstance(data.get("choices"), list) and data["choices"]:
        message = data["choices"][0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return json.loads(_strip_fence(content))
        if isinstance(content, dict):
            return content
    return data
