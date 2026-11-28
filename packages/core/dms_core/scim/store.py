"""Directory users, groups, mappings, and the provisioning audit.

The bearer itself is not stored. ``remember_token`` keeps a sha256 and the
OpenVault ``cred_ref``. Postgres is a write-through when it is configured.
A failure stays on memory and records the exception type only.

Mapping is exact case-insensitive equality against a saved source string.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

ROLES = frozenset({"admin", "steward", "analyst", "viewer"})
GOV_ROLES = frozenset({"manager", "member"})
_LIMIT = 120
_WINDOW_S = 60.0
_AUDIT_CAP = 2000
_SECRETISH = re.compile(r"(bearer\s+\S+|sk-|gsk_|ov_)", re.I)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_detail(detail: str) -> str:
    text = detail or ""
    if _SECRETISH.search(text):
        return "redacted"
    return text[:240]


@dataclass
class User:
    id: str
    tenant_id: str
    user_name: str
    external_id: str = ""
    active: bool = True
    display_name: str = ""
    given_name: str = ""
    family_name: str = ""
    emails: list[dict[str, Any]] = field(default_factory=list)
    department: str = ""
    manager: str = ""
    role: str = "viewer"
    gov_role: str = "member"
    space_grant: tuple[str, ...] | None = None
    sso_user_id: str = ""
    created: str = ""
    updated: str = ""


@dataclass
class Group:
    id: str
    tenant_id: str
    display_name: str
    external_id: str = ""
    members: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""


@dataclass
class MapRule:
    id: str
    tenant_id: str
    kind: str
    source: str
    priority: int
    role: str
    gov_role: str
    daily_usd: float | None
    monthly_usd: float | None
    daily_tokens: int | None
    monthly_tokens: int | None
    allow_ask: bool
    allow_create: bool
    allow_connectors: bool
    spaces: tuple[str, ...]


@dataclass(frozen=True)
class Audit:
    id: str
    tenant_id: str
    at: str
    action: str
    resource_id: str
    actor: str
    detail: str


@dataclass
class _Token:
    cred_ref: str
    sha256: str
    rotated_at: str


class ScimStore:
    """Process memory, optionally mirrored to ``dms.scim_*``."""

    def __init__(self) -> None:
        self.database_url: str | None = None
        self.persist_error = ""
        self._lock = threading.Lock()
        self._users: dict[tuple[str, str], User] = {}
        self._groups: dict[tuple[str, str], Group] = {}
        self._maps: dict[tuple[str, str], MapRule] = {}
        self._tokens: dict[str, _Token] = {}
        self._audit: list[Audit] = []
        self._hits: dict[str, list[float]] = {}

    def bind(self, database_url: str | None, tenant_id: str) -> None:
        url = (database_url or "").strip()
        self.database_url = url or None
        if not url:
            return
        try:
            UUID(tenant_id)
        except (TypeError, ValueError):
            self.persist_error = "tenant"
            return
        try:
            self._load(tenant_id)
            self.persist_error = ""
        except Exception as exc:  # noqa: BLE001 - the type is the log, not the DSN
            self.persist_error = type(exc).__name__
            logger.warning("scim store load failed (%s)", type(exc).__name__)

    def allow(self, key: str, *, limit: int | None = None, now: float | None = None) -> bool:
        cap = _LIMIT if limit is None else limit
        moment = time.time() if now is None else now
        with self._lock:
            hits = [item for item in self._hits.get(key, []) if item > moment - _WINDOW_S]
            if len(hits) >= cap:
                self._hits[key] = hits
                return False
            hits.append(moment)
            self._hits[key] = hits
            return True

    def remember_token(self, tenant_id: str, *, cred_ref: str, token: str) -> None:
        """Store the hash and the vault reference. ``token`` is not kept."""
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        row = _Token(cred_ref=cred_ref, sha256=digest, rotated_at=_now())
        with self._lock:
            self._tokens[tenant_id] = row
        self._exec(
            tenant_id,
            """
            INSERT INTO dms.scim_token (tenant_id, cred_ref, token_sha256, rotated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tenant_id) DO UPDATE SET
              cred_ref = EXCLUDED.cred_ref,
              token_sha256 = EXCLUDED.token_sha256,
              rotated_at = EXCLUDED.rotated_at
            """,
            (tenant_id, cred_ref, digest, row.rotated_at),
        )

    def token_status(self, tenant_id: str) -> dict[str, str] | None:
        with self._lock:
            row = self._tokens.get(tenant_id)
            if row is None:
                return None
            return {"cred_ref": row.cred_ref, "rotated_at": row.rotated_at}

    def tenant_for_token(self, token: str) -> str | None:
        if not token:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        found: str | None = None
        with self._lock:
            for tenant, row in self._tokens.items():
                if hmac.compare_digest(row.sha256, digest):
                    found = tenant
        return found

    def user(self, tenant_id: str, user_id: str) -> User | None:
        with self._lock:
            return self._users.get((tenant_id, user_id))

    def user_by_name(self, tenant_id: str, user_name: str) -> User | None:
        want = user_name.casefold()
        with self._lock:
            for user in self._users.values():
                if user.tenant_id == tenant_id and user.user_name.casefold() == want:
                    return user
        return None

    def users(self, tenant_id: str) -> list[User]:
        with self._lock:
            rows = [user for user in self._users.values() if user.tenant_id == tenant_id]
        rows.sort(key=lambda user: user.user_name.casefold())
        return rows

    def create_user(self, tenant_id: str, user_name: str) -> User:
        now = _now()
        user = User(
            id=str(uuid4()),
            tenant_id=tenant_id,
            user_name=user_name,
            created=now,
            updated=now,
        )
        with self._lock:
            self._users[(tenant_id, user.id)] = user
        self._write_user(user)
        return user

    def save_user(self, user: User) -> None:
        user.updated = _now()
        with self._lock:
            self._users[(user.tenant_id, user.id)] = user
        self._write_user(user)

    def group(self, tenant_id: str, group_id: str) -> Group | None:
        with self._lock:
            return self._groups.get((tenant_id, group_id))

    def groups(self, tenant_id: str) -> list[Group]:
        with self._lock:
            rows = [row for row in self._groups.values() if row.tenant_id == tenant_id]
        rows.sort(key=lambda row: row.display_name.casefold())
        return rows

    def group_names(self, tenant_id: str, user_id: str) -> list[str]:
        with self._lock:
            return [
                row.display_name
                for row in self._groups.values()
                if row.tenant_id == tenant_id and user_id in row.members
            ]

    def create_group(self, tenant_id: str, display_name: str) -> Group:
        now = _now()
        row = Group(
            id=str(uuid4()),
            tenant_id=tenant_id,
            display_name=display_name,
            created=now,
            updated=now,
        )
        with self._lock:
            self._groups[(tenant_id, row.id)] = row
        self._write_group(row)
        return row

    def save_group(self, row: Group) -> None:
        row.updated = _now()
        with self._lock:
            self._groups[(row.tenant_id, row.id)] = row
        self._write_group(row)

    def delete_group(self, tenant_id: str, group_id: str) -> Group | None:
        with self._lock:
            row = self._groups.pop((tenant_id, group_id), None)
        if row is not None:
            self._exec(
                tenant_id,
                "DELETE FROM dms.scim_member WHERE tenant_id = %s AND group_id = %s",
                (tenant_id, group_id),
            )
            self._exec(
                tenant_id,
                "DELETE FROM dms.scim_group WHERE tenant_id = %s AND id = %s",
                (tenant_id, group_id),
            )
        return row

    def maps(self, tenant_id: str) -> list[MapRule]:
        with self._lock:
            rows = [row for row in self._maps.values() if row.tenant_id == tenant_id]
        rows.sort(key=lambda row: (row.priority, row.source.casefold(), row.id))
        return rows

    def save_map(self, row: MapRule) -> None:
        with self._lock:
            self._maps[(row.tenant_id, row.id)] = row
        spaces = json.dumps(list(row.spaces))
        self._exec(
            row.tenant_id,
            """
            INSERT INTO dms.scim_map (
              tenant_id, id, kind, source_value, priority, role_name, gov_role,
              daily_usd, monthly_usd, daily_tokens, monthly_tokens,
              allow_ask, allow_create, allow_connectors, spaces_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id, id) DO UPDATE SET
              kind = EXCLUDED.kind,
              source_value = EXCLUDED.source_value,
              priority = EXCLUDED.priority,
              role_name = EXCLUDED.role_name,
              gov_role = EXCLUDED.gov_role,
              daily_usd = EXCLUDED.daily_usd,
              monthly_usd = EXCLUDED.monthly_usd,
              daily_tokens = EXCLUDED.daily_tokens,
              monthly_tokens = EXCLUDED.monthly_tokens,
              allow_ask = EXCLUDED.allow_ask,
              allow_create = EXCLUDED.allow_create,
              allow_connectors = EXCLUDED.allow_connectors,
              spaces_json = EXCLUDED.spaces_json
            """,
            (
                row.tenant_id,
                row.id,
                row.kind,
                row.source,
                row.priority,
                row.role,
                row.gov_role,
                row.daily_usd,
                row.monthly_usd,
                row.daily_tokens,
                row.monthly_tokens,
                row.allow_ask,
                row.allow_create,
                row.allow_connectors,
                spaces,
            ),
        )

    def delete_map(self, tenant_id: str, map_id: str) -> bool:
        with self._lock:
            found = self._maps.pop((tenant_id, map_id), None)
        if found is None:
            return False
        self._exec(
            tenant_id,
            "DELETE FROM dms.scim_map WHERE tenant_id = %s AND id = %s",
            (tenant_id, map_id),
        )
        return True

    def match(self, tenant_id: str, *, department: str, groups: list[str]) -> MapRule | None:
        """Lowest priority number wins. Equality is case-insensitive, not a prefix."""
        dept = department.casefold().strip()
        names = {name.casefold().strip() for name in groups if name.strip()}
        hits: list[MapRule] = []
        for rule in self.maps(tenant_id):
            source = rule.source.casefold().strip()
            if rule.kind == "department" and dept and source == dept:
                hits.append(rule)
            elif rule.kind == "group" and source in names:
                hits.append(rule)
        if not hits:
            return None
        hits.sort(key=lambda rule: (rule.priority, rule.id))
        return hits[0]

    def assign(self, user: User, rule: MapRule | None) -> None:
        if rule is None:
            user.role = "viewer"
            user.gov_role = "member"
            user.space_grant = None
        else:
            user.role = rule.role
            user.gov_role = rule.gov_role
            user.space_grant = tuple(rule.spaces)
        self.save_user(user)

    def space_grant(self, tenant_id: str, sso_user_id: str) -> tuple[str, ...] | None:
        """None when this person was not provisioned, so the catalog stays."""
        if not sso_user_id:
            return None
        with self._lock:
            for user in self._users.values():
                if user.tenant_id == tenant_id and user.sso_user_id == sso_user_id:
                    return user.space_grant
        return None

    def audit(
        self, tenant_id: str, action: str, resource_id: str, actor: str, detail: str
    ) -> Audit:
        row = Audit(
            id=str(uuid4()),
            tenant_id=tenant_id,
            at=_now(),
            action=action,
            resource_id=resource_id,
            actor=actor,
            detail=_clean_detail(detail),
        )
        with self._lock:
            self._audit.append(row)
            # ponytail: 2000 events in process. Older rows stay in Postgres
            # when it is configured. Upgrade: page the audit read from Postgres.
            if len(self._audit) > _AUDIT_CAP:
                del self._audit[: len(self._audit) - _AUDIT_CAP]
        self._exec(
            tenant_id,
            """
            INSERT INTO dms.scim_audit (tenant_id, id, at, action, resource_id, actor, detail)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (tenant_id, row.id, row.at, row.action, row.resource_id, row.actor, row.detail),
        )
        return row

    def audits(self, tenant_id: str) -> list[Audit]:
        with self._lock:
            rows = [row for row in self._audit if row.tenant_id == tenant_id]
        rows.sort(key=lambda row: row.at, reverse=True)
        return rows

    def _write_user(self, user: User) -> None:
        spaces = None if user.space_grant is None else json.dumps(list(user.space_grant))
        self._exec(
            user.tenant_id,
            """
            INSERT INTO dms.scim_user (
              tenant_id, id, user_name, external_id, active, display_name,
              given_name, family_name, department, manager_ref, emails_json,
              role_name, gov_role, spaces_json, sso_user_id, created_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id, id) DO UPDATE SET
              user_name = EXCLUDED.user_name,
              external_id = EXCLUDED.external_id,
              active = EXCLUDED.active,
              display_name = EXCLUDED.display_name,
              given_name = EXCLUDED.given_name,
              family_name = EXCLUDED.family_name,
              department = EXCLUDED.department,
              manager_ref = EXCLUDED.manager_ref,
              emails_json = EXCLUDED.emails_json,
              role_name = EXCLUDED.role_name,
              gov_role = EXCLUDED.gov_role,
              spaces_json = EXCLUDED.spaces_json,
              sso_user_id = EXCLUDED.sso_user_id,
              updated_at = EXCLUDED.updated_at
            """,
            (
                user.tenant_id,
                user.id,
                user.user_name,
                user.external_id,
                user.active,
                user.display_name,
                user.given_name,
                user.family_name,
                user.department,
                user.manager,
                json.dumps(user.emails),
                user.role,
                user.gov_role,
                spaces,
                user.sso_user_id,
                user.created,
                user.updated,
            ),
        )

    def _write_group(self, row: Group) -> None:
        self._exec(
            row.tenant_id,
            """
            INSERT INTO dms.scim_group (
              tenant_id, id, display_name, external_id, created_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id, id) DO UPDATE SET
              display_name = EXCLUDED.display_name,
              external_id = EXCLUDED.external_id,
              updated_at = EXCLUDED.updated_at
            """,
            (row.tenant_id, row.id, row.display_name, row.external_id, row.created, row.updated),
        )
        self._exec(
            row.tenant_id,
            "DELETE FROM dms.scim_member WHERE tenant_id = %s AND group_id = %s",
            (row.tenant_id, row.id),
        )
        for uid in row.members:
            self._exec(
                row.tenant_id,
                """
                INSERT INTO dms.scim_member (tenant_id, group_id, user_id)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (row.tenant_id, row.id, uid),
            )

    def _exec(self, tenant_id: str, sql: str, params: tuple[Any, ...]) -> None:
        if not self.database_url:
            return
        try:
            UUID(tenant_id)
        except (TypeError, ValueError):
            return
        try:
            with self._connect() as conn:
                conn.execute(sql, params)
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            self.persist_error = type(exc).__name__
            logger.warning("scim persist failed (%s)", type(exc).__name__)

    def _connect(self) -> Any:
        import psycopg

        return psycopg.connect(self.database_url or "", connect_timeout=3)

    def _load(self, tenant_id: str) -> None:
        with self._connect() as conn:
            users = conn.execute(
                """
                SELECT id, user_name, external_id, active, display_name, given_name,
                       family_name, department, manager_ref, emails_json, role_name,
                       gov_role, spaces_json, sso_user_id, created_at, updated_at
                  FROM dms.scim_user WHERE tenant_id = %s
                """,
                (tenant_id,),
            ).fetchall()
            for raw in users:
                spaces = None if raw[12] is None else tuple(json.loads(raw[12]))
                user = User(
                    id=raw[0],
                    tenant_id=tenant_id,
                    user_name=raw[1],
                    external_id=raw[2] or "",
                    active=bool(raw[3]),
                    display_name=raw[4] or "",
                    given_name=raw[5] or "",
                    family_name=raw[6] or "",
                    department=raw[7] or "",
                    manager=raw[8] or "",
                    emails=json.loads(raw[9] or "[]"),
                    role=raw[10] or "viewer",
                    gov_role=raw[11] or "member",
                    space_grant=spaces,
                    sso_user_id=raw[13] or "",
                    created=str(raw[14]),
                    updated=str(raw[15]),
                )
                self._users[(tenant_id, user.id)] = user
            groups = conn.execute(
                """
                SELECT id, display_name, external_id, created_at, updated_at
                  FROM dms.scim_group WHERE tenant_id = %s
                """,
                (tenant_id,),
            ).fetchall()
            members = conn.execute(
                "SELECT group_id, user_id FROM dms.scim_member WHERE tenant_id = %s",
                (tenant_id,),
            ).fetchall()
            by_group: dict[str, list[str]] = {}
            for group_id, user_id in members:
                by_group.setdefault(group_id, []).append(user_id)
            for raw in groups:
                row = Group(
                    id=raw[0],
                    tenant_id=tenant_id,
                    display_name=raw[1],
                    external_id=raw[2] or "",
                    members=by_group.get(raw[0], []),
                    created=str(raw[3]),
                    updated=str(raw[4]),
                )
                self._groups[(tenant_id, row.id)] = row
            maps = conn.execute(
                """
                SELECT id, kind, source_value, priority, role_name, gov_role,
                       daily_usd, monthly_usd, daily_tokens, monthly_tokens,
                       allow_ask, allow_create, allow_connectors, spaces_json
                  FROM dms.scim_map WHERE tenant_id = %s
                """,
                (tenant_id,),
            ).fetchall()
            for raw in maps:
                rule = MapRule(
                    id=raw[0],
                    tenant_id=tenant_id,
                    kind=raw[1],
                    source=raw[2],
                    priority=int(raw[3]),
                    role=raw[4],
                    gov_role=raw[5],
                    daily_usd=raw[6],
                    monthly_usd=raw[7],
                    daily_tokens=None if raw[8] is None else int(raw[8]),
                    monthly_tokens=None if raw[9] is None else int(raw[9]),
                    allow_ask=bool(raw[10]),
                    allow_create=bool(raw[11]),
                    allow_connectors=bool(raw[12]),
                    spaces=tuple(json.loads(raw[13] or "[]")),
                )
                self._maps[(tenant_id, rule.id)] = rule
            token = conn.execute(
                """
                SELECT cred_ref, token_sha256, rotated_at
                  FROM dms.scim_token WHERE tenant_id = %s
                """,
                (tenant_id,),
            ).fetchone()
            if token is not None:
                self._tokens[tenant_id] = _Token(token[0], token[1], str(token[2]))
            audits = conn.execute(
                """
                SELECT id, at, action, resource_id, actor, detail
                  FROM dms.scim_audit WHERE tenant_id = %s
                """,
                (tenant_id,),
            ).fetchall()
            for raw in audits:
                self._audit.append(
                    Audit(
                        raw[0],
                        tenant_id,
                        str(raw[1]),
                        raw[2],
                        raw[3] or "",
                        raw[4] or "",
                        raw[5] or "",
                    )
                )
            conn.commit()


_STORE = ScimStore()


def get_store() -> ScimStore:
    return _STORE


def reset_store() -> ScimStore:
    """Test helper. Drops process rows, the rate limit, and any database binding."""
    global _STORE, _LIMIT
    _STORE = ScimStore()
    _LIMIT = 120
    return _STORE


def set_rate_limit_for_tests(limit: int) -> None:
    global _LIMIT
    _LIMIT = limit


def rate_limit() -> int:
    return _LIMIT
