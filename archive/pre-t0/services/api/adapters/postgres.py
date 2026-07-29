"""Control-plane store: Postgres (preferred) or SQLite fallback for local appliance."""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

SCHEMA_PG = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS orgs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL UNIQUE,
  password_salt TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  display_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memberships (
  org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('viewer','steward','admin')),
  PRIMARY KEY (org_id, user_id)
);

CREATE TABLE IF NOT EXISTS api_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('viewer','steward','admin')),
  key_hash TEXT NOT NULL,
  label TEXT NOT NULL,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ledger (
  seq BIGSERIAL PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  actor TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}',
  prev_hash TEXT,
  entry_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS spaces (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_by UUID REFERENCES users(id),
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','archived')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS space_members (
  space_id UUID NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (space_id, user_id)
);

CREATE TABLE IF NOT EXISTS space_sources (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  space_id UUID NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  scope TEXT NOT NULL CHECK (scope IN ('personal','team','company')),
  meta JSONB NOT NULL DEFAULT '{}'
);

ALTER TABLE spaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY spaces_org_isolation ON spaces
    USING (org_id::text = current_setting('dms.org_id', true));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE POLICY ledger_org_isolation ON ledger
    USING (org_id::text = current_setting('dms.org_id', true));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
"""

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS orgs (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_salt TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  display_name TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS memberships (
  org_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL,
  PRIMARY KEY (org_id, user_id)
);
CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  role TEXT NOT NULL,
  key_hash TEXT NOT NULL,
  label TEXT NOT NULL,
  revoked_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ledger (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  org_id TEXT NOT NULL,
  actor TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  prev_hash TEXT,
  entry_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS spaces (
  id TEXT PRIMARY KEY,
  org_id TEXT NOT NULL,
  name TEXT NOT NULL,
  created_by TEXT,
  state TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS space_members (
  space_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  PRIMARY KEY (space_id, user_id)
);
CREATE TABLE IF NOT EXISTS space_sources (
  id TEXT PRIMARY KEY,
  space_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  scope TEXT NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}'
);
"""


def use_sqlite() -> bool:
    dsn = os.environ.get("DATABASE_URL", "")
    if dsn.startswith("sqlite"):
        return True
    if os.environ.get("DMS_USE_SQLITE", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    if os.environ.get("DMS_FORCE_POSTGRES", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    try:
        import psycopg

        with psycopg.connect(
            os.environ.get("DATABASE_URL", "postgresql://dms:dms@127.0.0.1:5432/dms"),
            connect_timeout=1,
        ) as c:
            with c.cursor() as cur:
                cur.execute("SELECT 1")
        return False
    except Exception:
        return True


def _sqlite_path() -> str:
    dsn = os.environ.get("DATABASE_URL", "")
    if dsn.startswith("sqlite:///"):
        return dsn.replace("sqlite:///", "", 1)
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data"))
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "dms_control.sqlite3")


class _Cursor:
    def __init__(self, cur, sqlite: bool):
        self._cur = cur
        self._sqlite = sqlite

    def execute(self, sql: str, params: tuple | list | None = None):
        q = sql
        if self._sqlite:
            q = q.replace("%s", "?").replace("::jsonb", "").replace("::uuid", "")
            q = q.replace("gen_random_uuid()", "NULL")  # callers supply ids when needed
        self._cur.execute(q, params or ())
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if not self._sqlite:
            self._cur.close()


class _Conn:
    def __init__(self, conn, sqlite: bool):
        self._conn = conn
        self._sqlite = sqlite

    def cursor(self):
        return _Cursor(self._conn.cursor(), self._sqlite)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


@contextmanager
def get_conn() -> Iterator[Any]:
    if use_sqlite():
        conn = sqlite3.connect(_sqlite_path())
        try:
            yield _Conn(conn, True)
        finally:
            conn.close()
    else:
        import psycopg
        from psycopg.rows import tuple_row

        conn = psycopg.connect(
            os.environ.get("DATABASE_URL", "postgresql://dms:dms@127.0.0.1:5432/dms"),
            row_factory=tuple_row,
        )
        try:
            yield _Conn(conn, False)
        finally:
            conn.close()


def init_schema() -> None:
    if use_sqlite():
        with get_conn() as conn:
            with conn.cursor() as cur:
                for stmt in _SCHEMA_SQLITE.strip().split(";"):
                    s = stmt.strip()
                    if s:
                        cur.execute(s)
            conn.commit()
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_PG)
        conn.commit()


def ping_db() -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    return True


def set_rls_context(conn: Any, org_id: str, role: str) -> None:
    if use_sqlite():
        return
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('dms.org_id', %s, true)", (str(org_id),))
        cur.execute("SELECT set_config('dms.role', %s, true)", (str(role),))


def new_id() -> str:
    return str(uuid.uuid4())
