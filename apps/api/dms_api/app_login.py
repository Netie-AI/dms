"""App sign-in state for ``DMS_APP_LOGIN`` (default off).

Swap: nginx HTTP basic auth on studio.netie.ai, or this page. The flag stays
off until a founder turns it on. Nothing here is read when the flag is off.

Passwords are stored only as ``pbkdf2_sha256`` hashes. The account file and
the environment value are never logged. Sessions live in this process.

ponytail: one process. A restart signs everyone out and drops access requests.
Upgrade path is the existing ``dms.sessions`` table, which this flag does not
write. The rate limit is also process-local, and behind a reverse proxy every
peer may be the proxy address.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

FLAG = "DMS_APP_LOGIN"
HASH_PREFIX = "pbkdf2_sha256"
HASH_ROUNDS = 200_000
SESSION_COOKIE = "dms_session"
CSRF_COOKIE = "dms_csrf"
SESSION_SECONDS = 12 * 60 * 60
ROLES = frozenset({"viewer", "steward", "admin"})
_REQUEST_CAP = 200


def enabled() -> bool:
    """``DMS_APP_LOGIN=1`` exactly. Unset, empty, and any other value are off."""
    return os.environ.get(FLAG, "0").strip() == "1"


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Hash a password. The plaintext is not retained."""
    if not isinstance(password, str) or password == "":
        raise ValueError("password_empty")
    raw = secrets.token_bytes(16) if salt is None else salt
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), raw, HASH_ROUNDS)
    return f"{HASH_PREFIX}${raw.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """True when ``stored`` is the hash of ``password``. Malformed hashes are false."""
    try:
        prefix, salt_hex, hash_hex = stored.split("$")
        if prefix != HASH_PREFIX:
            return False
        salt = bytes.fromhex(salt_hex)
        expect = bytes.fromhex(hash_hex)
    except (TypeError, ValueError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, HASH_ROUNDS)
    return hmac.compare_digest(digest, expect)


def cookie_secure(scheme: str) -> bool:
    """Secure when the request is https, or when the operator forces it.

    Nginx often terminates TLS, so the app still sees http. Set
    ``DMS_APP_LOGIN_COOKIE_SECURE=1`` on that host. Default off, so a local
    http test can hold the cookie.
    """
    if os.environ.get("DMS_APP_LOGIN_COOKIE_SECURE", "").strip() == "1":
        return True
    return scheme == "https"


def attempt_limit() -> int:
    raw = os.environ.get("DMS_APP_LOGIN_ATTEMPT_LIMIT", "").strip()
    if not raw:
        return 5
    try:
        value = int(raw)
    except ValueError:
        return 5
    return min(20, max(1, value))


def load_account_text() -> str:
    """File when ``DMS_APP_LOGIN_ACCOUNTS_FILE`` is set, otherwise the env string.

    A missing file is an empty list, not an error page. The text is not logged.
    """
    path = os.environ.get("DMS_APP_LOGIN_ACCOUNTS_FILE", "").strip()
    if path:
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            logger.info("app login account file unreadable")
            return ""
    return os.environ.get("DMS_APP_LOGIN_ACCOUNTS", "")


@dataclass(frozen=True)
class Account:
    email: str
    role: str
    password_hash: str
    user_id: str


def parse_accounts(text: str) -> dict[str, Account]:
    """``email role pbkdf2_sha256$salt$hash`` per line. Anything else is skipped.

    A plaintext password on a line is skipped, not stored.
    """
    found: dict[str, Account] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) != 3:
            logger.info("app login account skipped line %s", lineno)
            continue
        email, role, secret = parts[0].strip().lower(), parts[1].strip(), parts[2].strip()
        if "@" not in email or role not in ROLES or not secret.startswith(f"{HASH_PREFIX}$"):
            logger.info("app login account skipped line %s", lineno)
            continue
        user_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dms-app-login:{email}"))
        found[email] = Account(email=email, role=role, password_hash=secret, user_id=user_id)
    return found


def membership_for_email(
    database_url: str | None, tenant_id: str | None, email: str
) -> tuple[str, str] | None:
    """User id and role from ``dms.users`` plus membership, when Postgres is up.

    A miss, a down database, or a role outside viewer/steward/admin returns
    None. The caller keeps the account-file role. The connection string is
    not logged.
    """
    if not database_url or not tenant_id:
        return None
    try:
        import psycopg
        from dms_core.control_plane.session import set_tenant_context

        with psycopg.connect(database_url, connect_timeout=2) as conn:
            set_tenant_context(conn, tenant_id, role="admin")
            row = conn.execute(
                """
                SELECT u.id::text, r.name
                  FROM dms.users u
                  JOIN dms.memberships m ON m.user_id = u.id
                  JOIN dms.roles r ON r.id = m.role_id
                 WHERE m.tenant_id = %s::uuid
                   AND lower(u.email) = %s
                 ORDER BY m.created_at
                 LIMIT 1
                """,
                (tenant_id, email),
            ).fetchone()
    except Exception:  # noqa: BLE001
        logger.info("app login membership lookup unavailable")
        return None
    if row is None:
        return None
    user_id, role = str(row[0]), str(row[1])
    if role not in ROLES:
        return None
    return user_id, role


@dataclass
class Session:
    token: str
    email: str
    role: str
    user_id: str
    expires_at: float


@dataclass
class AccessRequest:
    id: str
    name: str
    email: str
    company: str
    note: str
    filed_at: str


class AttemptWindow:
    """Count recent hits. ``allow`` is false once the window is full."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            recent = [stamp for stamp in self._hits.get(key, []) if moment - stamp < self.window_s]
            if len(recent) >= self.limit:
                self._hits[key] = recent
                return False
            recent.append(moment)
            self._hits[key] = recent
            return True


@dataclass
class LoginBook:
    accounts: dict[str, Account] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    requests: list[AccessRequest] = field(default_factory=list)
    sign_in_attempts: AttemptWindow = field(
        default_factory=lambda: AttemptWindow(attempt_limit(), 15 * 60)
    )
    request_attempts: AttemptWindow = field(default_factory=lambda: AttemptWindow(5, 60 * 60))
    _dummy: str = ""

    @classmethod
    def load(cls) -> LoginBook:
        book = cls(accounts=parse_accounts(load_account_text()))
        book._dummy = hash_password(secrets.token_urlsafe(16))
        return book

    def open_session(
        self,
        email: str,
        password: str,
        *,
        database_url: str | None,
        tenant_id: str | None,
    ) -> Session | None:
        account = self.accounts.get(email)
        stored = account.password_hash if account is not None else self._dummy
        if account is None or not verify_password(password, stored):
            return None
        user_id, role = account.user_id, account.role
        found = membership_for_email(database_url, tenant_id, email)
        if found is not None:
            user_id, role = found
        token = secrets.token_urlsafe(32)
        session = Session(
            token=token,
            email=account.email,
            role=role,
            user_id=user_id,
            expires_at=time.time() + SESSION_SECONDS,
        )
        self.sessions[token] = session
        return session

    def session_from(self, token: str | None) -> Session | None:
        if not token:
            return None
        row = self.sessions.get(token)
        if row is None:
            return None
        if row.expires_at <= time.time():
            self.sessions.pop(token, None)
            return None
        return row

    def drop_session(self, token: str | None) -> None:
        if token:
            self.sessions.pop(token, None)

    def file_request(self, *, name: str, email: str, company: str, note: str) -> AccessRequest:
        # ponytail: cap 200, drop the oldest. A steward export can replace this.
        if len(self.requests) >= _REQUEST_CAP:
            del self.requests[: len(self.requests) - (_REQUEST_CAP - 1)]
        row = AccessRequest(
            id=str(uuid.uuid4()),
            name=name,
            email=email,
            company=company,
            note=note,
            filed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.requests.append(row)
        return row


def csrf_matches(cookie: str, header: str) -> bool:
    if not cookie or not header or len(cookie) != len(header):
        return False
    return hmac.compare_digest(cookie, header)


def new_csrf() -> str:
    return secrets.token_urlsafe(32)
