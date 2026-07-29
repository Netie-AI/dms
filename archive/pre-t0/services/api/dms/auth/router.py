from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from adapters.postgres import get_conn, init_schema, new_id, use_sqlite
from settings import get_settings

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str
    org_slug: str = "default"


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    org_id: str
    user_id: str
    email: str


class MeResponse(BaseModel):
    user_id: str
    email: str
    role: str
    org_id: str
    org_slug: str


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return salt, digest.hex()


def _verify(password: str, salt: str, password_hash: str) -> bool:
    _, digest = _hash_password(password, salt)
    return secrets.compare_digest(digest, password_hash)


def ensure_seed() -> None:
    init_schema()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM orgs WHERE slug = %s", ("default",))
            row = cur.fetchone()
            if row:
                org_id = row[0]
            else:
                org_id = new_id() if use_sqlite() else None
                if use_sqlite():
                    cur.execute(
                        "INSERT INTO orgs (id, slug, name) VALUES (%s, %s, %s)",
                        (org_id, "default", "Default Org"),
                    )
                else:
                    cur.execute(
                        "INSERT INTO orgs (slug, name) VALUES (%s, %s) RETURNING id",
                        ("default", "Default Org"),
                    )
                    org_id = cur.fetchone()[0]
            cur.execute("SELECT id FROM users WHERE email = %s", ("admin@dms.local",))
            if not cur.fetchone():
                salt, pw = _hash_password("admin")
                user_id = new_id() if use_sqlite() else None
                if use_sqlite():
                    cur.execute(
                        """
                        INSERT INTO users (id, email, password_salt, password_hash, display_name)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (user_id, "admin@dms.local", salt, pw, "DMS Admin"),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO users (email, password_salt, password_hash, display_name)
                        VALUES (%s, %s, %s, %s) RETURNING id
                        """,
                        ("admin@dms.local", salt, pw, "DMS Admin"),
                    )
                    user_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO memberships (org_id, user_id, role)
                    VALUES (%s, %s, %s)
                    """,
                    (org_id, user_id, "admin"),
                )
            for role, key in (
                ("viewer", "dms-demo-viewer-key"),
                ("steward", "dms-demo-steward-key"),
                ("admin", "dms-demo-admin-key"),
            ):
                label = f"demo-{role}"
                cur.execute("SELECT id FROM api_keys WHERE org_id = %s AND label = %s", (org_id, label))
                if cur.fetchone():
                    continue
                kid = new_id() if use_sqlite() else None
                if use_sqlite():
                    cur.execute(
                        """
                        INSERT INTO api_keys (id, org_id, role, key_hash, label)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (kid, org_id, role, hashlib.sha256(key.encode()).hexdigest(), label),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO api_keys (org_id, role, key_hash, label)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (org_id, role, hashlib.sha256(key.encode()).hexdigest(), label),
                    )
        conn.commit()


def issue_token(*, user_id: str, org_id: str, role: str, email: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "email": email,
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_ttl_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def caller_from_headers(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    settings = get_settings()
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail="invalid token") from exc
        return {
            "user_id": payload["sub"],
            "org_id": payload["org"],
            "role": payload["role"],
            "email": payload.get("email"),
            "auth": "jwt",
        }
    if x_api_key:
        key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
        ensure_seed()
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT org_id, role FROM api_keys WHERE key_hash = %s AND revoked_at IS NULL",
                    (key_hash,),
                )
                row = cur.fetchone()
        if not row:
            # Fallback: parse settings demo keys without DB
            for part in settings.demo_api_keys.split(";"):
                if ":" not in part:
                    continue
                role, key = part.split(":", 1)
                if key.strip() == x_api_key:
                    return {
                        "user_id": "demo",
                        "org_id": "default",
                        "role": role.strip(),
                        "email": f"{role}@demo.local",
                        "auth": "api_key",
                    }
            raise HTTPException(status_code=401, detail="invalid api key")
        return {
            "user_id": "api-key",
            "org_id": str(row[0]),
            "role": row[1],
            "email": None,
            "auth": "api_key",
        }
    raise HTTPException(status_code=401, detail="authorization required")


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    ensure_seed()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id, u.password_salt, u.password_hash, m.role, o.id
                FROM users u
                JOIN memberships m ON m.user_id = u.id
                JOIN orgs o ON o.id = m.org_id
                WHERE u.email = %s AND o.slug = %s
                """,
                (body.email.lower().strip(), body.org_slug.strip()),
            )
            row = cur.fetchone()
    if not row or not _verify(body.password, row[1], row[2]):
        raise HTTPException(status_code=401, detail="invalid credentials")
    user_id, _, _, role, org_id = row
    token = issue_token(user_id=str(user_id), org_id=str(org_id), role=role, email=body.email)
    return LoginResponse(
        access_token=token,
        role=role,
        org_id=str(org_id),
        user_id=str(user_id),
        email=body.email.lower().strip(),
    )


@router.get("/me", response_model=MeResponse)
def me(caller: dict = Depends(caller_from_headers)) -> MeResponse:
    org_slug = "default"
    if caller.get("org_id") and caller["org_id"] != "default":
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT slug FROM orgs WHERE id = %s", (caller["org_id"],))
                r = cur.fetchone()
                if r:
                    org_slug = r[0]
    return MeResponse(
        user_id=str(caller["user_id"]),
        email=str(caller.get("email") or ""),
        role=str(caller["role"]),
        org_id=str(caller["org_id"]),
        org_slug=org_slug,
    )
