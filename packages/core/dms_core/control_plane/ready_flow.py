"""READY-04. A new company can connect, confirm, and read one count.

Every flag is the digit 1 only. Unset is off. Off, callers see the same
bytes they see today: health stays ``ok``, the data map grows no key, and
these routes are not mounted.

On, the flow is:

1. Create the company (tenant, steward user, membership, Space).
2. Connect a source and list the tables it can read.
3. An admin confirms a read-only grant. Nothing is granted before that.
4. Joins are inferred from a shared column name or from value inclusion,
   then a steward confirms them. A declared foreign key stays declared.
5. Five questions are asked of OpenVault role ``questions``. One granted
   table returns its row count.

No question text is matched. No model id is chosen. OpenVault routes
``questions``. Cell values are used only to test inclusion and are not
returned.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID, uuid4, uuid5

FLAG_HEALTH = "DMS_READY_HEALTH"
FLAG_TENANT = "DMS_READY_TENANT"
FLAG_GRANT = "DMS_READY_GRANT"
FLAG_JOINS = "DMS_READY_JOINS"
FLAG_GUIDE = "DMS_READY_GUIDE"
FLAG_DEMO = "DMS_READY_DEMO"

ROLE_QUESTIONS = "questions"
SCHEMA_NAME = "ReadyQuestions"
# Fixed so a rerun inserts the same steward user for one tenant.
_NS = UUID("6f0e2a1c-4b7d-4e1a-9c3d-111111111111")

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_SAMPLE_CAP = 40
_ACTOR_FK_SUFFIXES = ("_created_by_fkey", "_user_id_fkey")
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
_STEPS = (
    ("company", "Create the company", "A tenant and a Space exist, and the call did not fail."),
    ("connect", "Connect a source", "The source listed the tables the login can read."),
    (
        "grant",
        "Confirm read-only tables",
        "Only the tables an admin confirmed are granted, and only for read.",
    ),
    (
        "joins",
        "Confirm joins",
        "A join was inferred from a shared column name or from matching values, "
        "then a steward confirmed it.",
    ),
    (
        "answer",
        "First answer",
        "Five questions were written from the schema, and a granted table returned its row count.",
    ),
)
_SYSTEM = (
    "Write five questions a person could ask about this schema. "
    "You receive table names, column names, and types. "
    "You do not receive cell values. "
    "Reply with a JSON object and nothing else. "
    "The only key is questions. "
    "questions is an array of five short strings. "
    "Do not write SQL."
)

_lock = Lock()
_sources: dict[str, _ReadySource] = {}
_candidates: list[_Candidate] = []
_questions: list[str] = []
_questions_status = ""
_measured: dict[str, int] = {}
_company = False
_grants_done = False
_joins_done = False
_demo = False


def _digit(name: str) -> bool:
    """True only when the variable is the digit 1. ``true`` and ``yes`` are off."""
    return os.environ.get(name, "0").strip() == "1"


def health_enabled() -> bool:
    return _digit(FLAG_HEALTH)


def tenant_enabled() -> bool:
    return _digit(FLAG_TENANT)


def grant_enabled() -> bool:
    return _digit(FLAG_GRANT)


def joins_enabled() -> bool:
    return _digit(FLAG_JOINS)


def guide_enabled() -> bool:
    return _digit(FLAG_GUIDE)


def demo_enabled() -> bool:
    return _digit(FLAG_DEMO)


def any_route_enabled() -> bool:
    return (
        tenant_enabled()
        or grant_enabled()
        or joins_enabled()
        or guide_enabled()
        or demo_enabled()
    )


def health_status(persistent: bool) -> str:
    """``degraded`` when the flag is on and the store will not survive a restart.

    Flag off always returns ``ok``, which is the byte the health route has today.
    """
    if health_enabled() and not persistent:
        return "degraded"
    return "ok"


class ReadyError(Exception):
    """A typed refusal. ``code`` is the only text a response may carry."""

    def __init__(self, code: str, status: int = 400) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def is_actor_fk(name: object) -> bool:
    """True for a foreign key onto ``dms.users``, which is not a missing tenant."""
    return isinstance(name, str) and any(name.endswith(suffix) for suffix in _ACTOR_FK_SUFFIXES)


@dataclass
class ColumnProfile:
    name: str
    type_name: str
    primary: bool
    unique: bool
    values: tuple[str, ...]


@dataclass
class TableProfile:
    name: str
    columns: tuple[ColumnProfile, ...]


@dataclass
class _Candidate:
    id: str
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    origin: str
    needs_confirm: bool
    value_support: bool
    status: str = "proposed"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from_table": self.from_table,
            "from_column": self.from_column,
            "to_table": self.to_table,
            "to_column": self.to_column,
            "origin": self.origin,
            "status": self.status,
            "needs_confirm": self.needs_confirm,
            "value_support": self.value_support,
        }


@dataclass
class _ReadySource:
    source_id: str
    space_id: str
    path: str
    tables: tuple[str, ...]
    profile: tuple[TableProfile, ...] = field(default_factory=tuple)


def reset() -> None:
    """Test isolation. Production never calls this."""
    global _company, _grants_done, _joins_done, _demo, _questions_status
    with _lock:
        _sources.clear()
        _candidates.clear()
        _questions.clear()
        _measured.clear()
        _company = False
        _grants_done = False
        _joins_done = False
        _demo = False
        _questions_status = ""


def mark_company() -> None:
    global _company
    with _lock:
        _company = True


def grant_proposal(tables: list[str], source_id: str) -> dict[str, Any]:
    """The read-only list an admin still has to confirm. Nothing is granted here."""
    return {
        "privilege": "read",
        "tables": list(tables),
        "confirmed": False,
        "source_id": source_id,
    }


def note_connected(source_id: str, space_id: str, tables: list[str]) -> None:
    """A studio land registered tables. They stay ungranted until confirm."""
    with _lock:
        _sources.setdefault(
            source_id,
            _ReadySource(
                source_id=source_id,
                space_id=space_id,
                path="",
                tables=tuple(tables),
            ),
        )


def candidate_public() -> list[dict[str, Any]]:
    with _lock:
        return [row.public() for row in _candidates]


def _cid(origin: str, child_t: str, child_c: str, parent_t: str, parent_c: str) -> str:
    raw = f"{origin}|{child_t}|{child_c}|{parent_t}|{parent_c}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def infer_joins(tables: list[TableProfile]) -> list[_Candidate]:
    """Name equality or value inclusion. Both-primary pairs are not joins.

    A declared foreign key is not invented here. The caller passes only the
    columns it read. Two primary keys can share small integers; that overlap
    identifies each table's own rows, so it is not a candidate.

    ponytail: pairwise over columns. Ceiling is a few dozen tables. Upgrade
    is a hash of (column name, sample) instead of the nested loops.
    """
    found: list[_Candidate] = []
    seen: set[str] = set()
    columns: list[tuple[str, ColumnProfile]] = []
    for table in tables:
        for column in table.columns:
            columns.append((table.name, column))
    for i, (left_table, left) in enumerate(columns):
        for right_table, right in columns[i + 1 :]:
            if left_table == right_table:
                continue
            both_primary = left.primary and right.primary
            name_match = left.name.casefold() == right.name.casefold() and not both_primary
            parent, child_table, child = _value_parent(left_table, left, right_table, right)
            value_match = parent is not None and not both_primary
            if not name_match and not value_match:
                continue
            if value_match and parent is not None and child is not None:
                origin = "name_and_values" if name_match else "values"
                from_table, from_column = child_table, child.name
                to_table, to_column = parent
            else:
                origin = "name"
                pair = sorted(
                    ((left_table, left.name), (right_table, right.name)),
                )
                from_table, from_column = pair[0]
                to_table, to_column = pair[1]
            key = _cid(origin, from_table, from_column, to_table, to_column)
            if key in seen:
                continue
            seen.add(key)
            found.append(
                _Candidate(
                    id=key,
                    from_table=from_table,
                    from_column=from_column,
                    to_table=to_table,
                    to_column=to_column,
                    origin=origin,
                    needs_confirm=True,
                    value_support=value_match,
                )
            )
    return found


def _value_parent(
    left_table: str,
    left: ColumnProfile,
    right_table: str,
    right: ColumnProfile,
) -> tuple[tuple[str, str] | None, str, ColumnProfile | None]:
    """Parent is the unique side that contains the other side's samples."""
    left_values = set(left.values)
    right_values = set(right.values)
    if not left_values or not right_values:
        return None, "", None
    left_in_right = left_values <= right_values and (
        right.unique or len(right_values) > len(left_values)
    )
    right_in_left = right_values <= left_values and (
        left.unique or len(left_values) > len(right_values)
    )
    if left_in_right and not right_in_left:
        return (right_table, right.name), left_table, left
    if right_in_left and not left_in_right:
        return (left_table, left.name), right_table, right
    return None, "", None


def profile_sqlite(path: Path) -> list[TableProfile]:
    """Read names, types, keys, and a capped distinct sample. Read-only."""
    uri = f"{path.resolve().as_posix()}"
    con = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        tables: list[TableProfile] = []
        for (name,) in rows:
            if not isinstance(name, str) or not _IDENT.fullmatch(name):
                continue
            tables.append(_profile_table(con, name))
        return tables
    finally:
        con.close()


def _profile_table(con: sqlite3.Connection, name: str) -> TableProfile:
    info = con.execute(f'PRAGMA table_info("{name}")').fetchall()
    columns: list[ColumnProfile] = []
    for _cid, col_name, col_type, _notnull, _default, pk in info:
        if not isinstance(col_name, str) or not _IDENT.fullmatch(col_name):
            continue
        values = _sample(con, name, col_name)
        distinct = len(set(values))
        columns.append(
            ColumnProfile(
                name=col_name,
                type_name=str(col_type or ""),
                primary=bool(pk),
                unique=bool(pk) or (len(values) > 1 and distinct == len(values)),
                values=tuple(values),
            )
        )
    return TableProfile(name=name, columns=tuple(columns))


def _sample(con: sqlite3.Connection, table: str, column: str) -> list[str]:
    # Names already matched _IDENT, so the quotes cannot close.
    # Raw rows, not DISTINCT: a repeated key must not look unique.
    sql = (
        f'SELECT "{column}" FROM "{table}" '
        f'WHERE "{column}" IS NOT NULL LIMIT {int(_SAMPLE_CAP)}'
    )
    out: list[str] = []
    for (value,) in con.execute(sql):
        text = str(value)
        if text:
            out.append(text)
    return out


def count_granted(path: Path, table: str) -> int:
    """COUNT(*) of one catalog table. The name is not a caller SQL string."""
    if not _IDENT.fullmatch(table):
        raise ReadyError("bad_table_name")
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        names = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            if isinstance(row[0], str)
        }
        if table not in names:
            raise ReadyError("table_not_found", 404)
        row = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    finally:
        con.close()
    if row is None:
        raise ReadyError("count_failed", 503)
    return int(row[0])


def _store_source(source: _ReadySource, tables: list[TableProfile]) -> None:
    global _candidates
    inferred = infer_joins(tables)
    with _lock:
        _sources[source.source_id] = source
        _candidates.clear()
        _candidates.extend(inferred)


def connect_profiled(
    *,
    space_id: str,
    path: Path,
    actor: str,
    token_id: str,
) -> dict[str, Any]:
    """Register every readable table as ungranted, and infer joins."""
    from dms_core.control_plane.connect_grants import register_source

    if not grant_enabled():
        raise ReadyError("ready_grant_disabled", 404)
    tables = profile_sqlite(path)
    names = [table.name for table in tables]
    if not names:
        raise ReadyError("source_has_no_tables")
    source_id = str(uuid4())
    register_source(
        source_id=source_id,
        space_id=space_id,
        connector_id="sqlite",
        exposed=names,
        actor=actor,
        token_id=token_id,
    )
    source = _ReadySource(
        source_id=source_id,
        space_id=space_id,
        path=str(path.resolve()),
        tables=tuple(names),
        profile=tuple(tables),
    )
    _store_source(source, tables)
    body: dict[str, Any] = {
        "source_id": source_id,
        "space_id": space_id,
        "exposed_tables": names,
        "granted_tables": [],
        "grant_proposal": grant_proposal(names, source_id),
    }
    if joins_enabled():
        body["join_candidates"] = candidate_public()
    return body


def confirm_grants(source_id: str, tables: list[str], *, actor: str) -> dict[str, Any]:
    """Grant the confirmed subset. Privilege is read. Unlisted tables stay off."""
    global _grants_done
    from dms_core.control_plane.connect_grants import grant_tables

    if not grant_enabled():
        raise ReadyError("ready_grant_disabled", 404)
    try:
        granted = grant_tables(
            source_id=source_id,
            tables=tables,
            actor=actor,
            token_id="ready",
        )
    except KeyError:
        raise ReadyError("source_not_found", 404) from None
    except ValueError as exc:
        code = str(exc)
        if code not in {"bad_table_name", "table_not_exposed"}:
            code = "bad_request"
        raise ReadyError(code) from None
    with _lock:
        _grants_done = True
    return {"source_id": source_id, "privilege": "read", "granted_tables": list(granted)}


def confirm_joins(ids: list[str]) -> dict[str, Any]:
    """Mark the steward's ids confirmed. Unknown ids are refused."""
    global _joins_done
    if not joins_enabled():
        raise ReadyError("ready_joins_disabled", 404)
    with _lock:
        known = {row.id: row for row in _candidates if row.needs_confirm}
        if not known and not ids:
            _joins_done = True
            return {"confirmed": [], "status": "nothing_to_confirm"}
        missing = [item for item in ids if item not in known]
        if missing or not ids:
            raise ReadyError("join_unknown")
        for item in ids:
            known[item].status = "confirmed"
            known[item].needs_confirm = False
        _joins_done = True
        confirmed = [known[item].public() for item in ids]
    return {"confirmed": confirmed, "status": "confirmed"}


def questions_request(profile: list[TableProfile]) -> dict[str, Any]:
    """OpenVault body. Role ``questions``. No model id and no cell values."""
    tables: list[dict[str, Any]] = []
    for table in profile:
        columns = [
            {"name": column.name, "type": column.type_name, "primary": column.primary}
            for column in table.columns
        ]
        tables.append({"name": table.name, "columns": columns})
    user = json.dumps({"tables": tables}, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    body: dict[str, Any] = {
        "role": ROLE_QUESTIONS,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        "schema": SCHEMA_NAME,
    }
    overlap = _MODEL_KEYS.intersection(body)
    if overlap:
        raise ReadyError("ready_route_field")
    return body


def parse_questions(payload: Any) -> list[str] | None:
    """Five non-empty strings, or None. No lexicon check."""
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("```"):
            lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("questions")
    if not isinstance(raw, list) or len(raw) != 5:
        return None
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            return None
        question = item.strip()
        if not question or len(question) > 200:
            return None
        out.append(question)
    return out


def store_questions(questions: list[str], status: str) -> None:
    global _questions_status
    with _lock:
        _questions.clear()
        _questions.extend(questions)
        _questions_status = status


def record_answer(table: str) -> dict[str, Any]:
    """Row count of one granted table on the connected file."""
    from dms_core.control_plane.connect_grants import granted_tables

    if not guide_enabled():
        raise ReadyError("ready_guide_disabled", 404)
    with _lock:
        if not _grants_done:
            raise ReadyError("grants_required", 409)
        source = next(iter(_sources.values()), None)
        if source is None or not source.path:
            raise ReadyError("source_not_connected", 409)
        space_id = source.space_id
        path = Path(source.path)
    granted = set(granted_tables(space_id))
    if table not in granted:
        raise ReadyError("table_not_granted", 403)
    count = count_granted(path, table)
    with _lock:
        _measured[table] = count
    return {
        "table": table,
        "measured_rows": count,
        "proves": "the granted table can be read",
    }


def guide_body() -> dict[str, Any]:
    if not guide_enabled():
        raise ReadyError("ready_guide_disabled", 404)
    with _lock:
        company = _company
        connected = bool(_sources)
        grants = _grants_done
        joins = _joins_done
        questions = list(_questions)
        measured = dict(_measured)
        demo = _demo
        candidates = [row.public() for row in _candidates]
        q_status = _questions_status
    answered = len(questions) == 5 and bool(measured)
    done_map = {
        "company": company,
        "connect": connected,
        "grant": grants,
        "joins": joins,
        "answer": answered,
    }
    steps = []
    done = 0
    for number, (step_id, title, proves) in enumerate(_STEPS, start=1):
        ready = bool(done_map[step_id])
        if ready:
            done += 1
        steps.append(
            {
                "n": number,
                "id": step_id,
                "title": title,
                "proves": proves,
                "done": ready,
            }
        )
    return {
        "steps": steps,
        "progress": {"done": done, "total": len(_STEPS)},
        "demo": demo,
        "demo_enabled": demo_enabled(),
        "join_candidates": candidates,
        "questions": questions,
        "questions_status": q_status,
        "measured_rows": measured,
    }


def write_demo_sqlite(directory: Path) -> Path:
    """Synthetic company. No declared foreign key. No secret."""
    path = directory / "ready_demo.sqlite"
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
        con.execute(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, sku TEXT)"
        )
        con.execute("CREATE TABLE products (sku TEXT PRIMARY KEY, title TEXT)")
        con.executemany(
            "INSERT INTO customers (id, name) VALUES (?, ?)",
            ((1, "Ada"), (2, "Ben")),
        )
        con.executemany(
            "INSERT INTO orders (id, customer_id, sku) VALUES (?, ?, ?)",
            ((1, 1, "A"), (2, 2, "B"), (3, 1, "A")),
        )
        con.executemany(
            "INSERT INTO products (sku, title) VALUES (?, ?)",
            (("A", "Bolt"), ("B", "Nut"), ("C", "Washer")),
        )
        con.commit()
    finally:
        con.close()
    return path


def demo_directory() -> Path:
    path = Path(tempfile.mkdtemp(prefix="dms-ready-"))
    return path


def mark_demo() -> None:
    global _demo
    with _lock:
        _demo = True


def current_profile() -> list[TableProfile]:
    with _lock:
        for source in _sources.values():
            if source.profile:
                return list(source.profile)
    return []


def provision_postgres(conninfo: str, tenant_id: str, name: str) -> dict[str, Any]:
    """Insert the tenant, a steward user, the membership, and the Space.

    The space insert used to name a user that was not a row. That foreign key
    left the API as HTTP 500. The user row is written first, so the key holds.
    The driver message is not returned.
    """
    import psycopg

    from dms_core.control_plane.session import set_tenant_context

    if not tenant_enabled():
        raise ReadyError("ready_tenant_disabled", 404)
    try:
        tenant = str(UUID(str(tenant_id).strip()))
    except (ValueError, AttributeError, TypeError):
        raise ReadyError("tenant_id_invalid") from None
    clean = name.strip() if isinstance(name, str) else ""
    if not clean or len(clean) > 120:
        raise ReadyError("tenant_name_invalid")
    user_id = str(uuid5(_NS, tenant + ":steward"))
    email = f"ready-{tenant[:8]}@example.com"
    slug = "t-" + tenant
    try:
        with psycopg.connect(conninfo, connect_timeout=5) as conn:
            conn.execute(
                """
                INSERT INTO dms.tenants (id, slug, name)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (tenant, slug, clean),
            )
            conn.execute(
                """
                INSERT INTO dms.users (id, email, display_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (user_id, email, clean),
            )
            role = conn.execute(
                "SELECT id FROM dms.roles WHERE name = 'steward'"
            ).fetchone()
            if role is None:
                raise ReadyError("tenant_create_failed", 503)
            set_tenant_context(conn, tenant, role="admin")
            conn.execute(
                """
                INSERT INTO dms.memberships (tenant_id, user_id, role_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (tenant_id, user_id) DO NOTHING
                """,
                (tenant, user_id, role[0]),
            )
            set_tenant_context(conn, tenant, role="steward")
            who = conn.execute("SELECT current_user").fetchone()
            if who is None or str(who[0]) != "dms_steward":
                raise ReadyError("tenant_create_role_refused", 503)
            conn.execute(
                """
                INSERT INTO dms.compute_pool (tenant_id, name, kind)
                VALUES (%s, 'default', 'duckdb')
                ON CONFLICT (tenant_id, name) DO NOTHING
                """,
                (tenant,),
            )
            try:
                row = conn.execute(
                    """
                    INSERT INTO dms.spaces (tenant_id, name, created_by)
                    VALUES (%s, %s, %s)
                    RETURNING id::text
                    """,
                    (tenant, clean, user_id),
                ).fetchone()
            except psycopg.errors.UniqueViolation:
                raise ReadyError("space_name_taken", 409) from None
            if row is None:
                raise ReadyError("tenant_create_failed", 503)
            conn.execute(
                """
                INSERT INTO dms.space_members (space_id, tenant_id, user_id)
                VALUES (%s, %s, %s)
                """,
                (row[0], tenant, user_id),
            )
            conn.commit()
    except ReadyError:
        raise
    except psycopg.Error:
        raise ReadyError("tenant_create_failed", 503) from None
    mark_company()
    return {
        "tenant_id": tenant,
        "space_id": row[0],
        "name": clean,
        "persisted": True,
        "created_user": True,
    }


Transport = Callable[[dict[str, Any]], Any]
_TRANSPORT: ContextVar[Transport | None] = ContextVar("dms_ready_questions", default=None)


def bind_questions_transport(transport: Transport) -> Token[Transport | None]:
    return _TRANSPORT.set(transport)


def reset_questions_transport(token: Token[Transport | None]) -> None:
    _TRANSPORT.reset(token)


def call_questions(body: dict[str, Any]) -> Any:
    """One OpenVault call. Tests bind a transport. No model id is added."""
    fn = _TRANSPORT.get()
    if fn is None:
        fn = default_questions_transport
    return fn(body)


def default_questions_transport(body: dict[str, Any]) -> Any:
    """POST role ``questions``. The code never carries the key or the reply."""
    import httpx

    overlap = _MODEL_KEYS.intersection(body)
    if overlap:
        raise ReadyError("ready_route_field")
    base = os.environ.get("OPENVAULT_URL", "").strip().rstrip("/")
    if not base:
        raise ReadyError("openvault_url_missing", 503)
    key = ""
    for name in ("OPENVAULT_API_KEY", "CORTEX_API_KEY"):
        raw = os.environ.get(name, "").strip()
        if raw.startswith("ov_"):
            key = raw
            break
    if not key:
        raise ReadyError("ov_api_key_missing", 503)
    try:
        response = httpx.post(
            base + "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body,
            timeout=8.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise ReadyError("ov_unauthorized", 503) from None
        raise ReadyError("ov_unreachable", 503) from None
    except httpx.HTTPError:
        raise ReadyError("ov_unreachable", 503) from None
    try:
        data = response.json()
    except ValueError:
        raise ReadyError("model_output_invalid") from None
    if isinstance(data, dict) and isinstance(data.get("choices"), list) and data["choices"]:
        message = data["choices"][0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            return content
    return data


def schema_for_questions() -> list[dict[str, Any]]:
    """Names and types only, for a caller that logs the prompt."""
    return [
        {
            "name": table.name,
            "columns": [
                {"name": column.name, "type": column.type_name} for column in table.columns
            ],
        }
        for table in current_profile()
    ]
