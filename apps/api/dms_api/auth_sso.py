"""Login, roles, and OIDC when ``DMS_SSO`` is the digit 1.

Off unless the variable is exactly ``1``. ``true``, ``yes``, and ``on`` stay
off, and the auth routes are not mounted. Identity headers stay refused
(DR-0004). A verified session or the existing OpenVault service bearer is the
only caller a request may be. The bearer is never logged and never copied
into an error.

Roles are ``admin``, ``steward``, ``analyst``, and ``viewer``. Analyst reads
and may POST ``/v1/chat``. Viewer reads. Steward and admin may mutate. The
service bearer keeps the deployment steward's calls.

OIDC providers are Google, Microsoft Entra, and a generic Okta issuer.
Client secrets are read by an OpenVault ``cred_ref`` (``load_vault_secret``).
Nothing here has a default secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

FLAG = "DMS_SSO"
ROLES = frozenset({"admin", "steward", "analyst", "viewer"})
OIDC_PROVIDERS = ("google", "entra", "okta")
PBKDF2_ROUNDS = 200_000
_SESSION_TTL_S = 12 * 60 * 60
_STATE_TTL_S = 10 * 60
_MAX_STATES = 1000
_TOKEN_MAX = 16_000

_ROLE_RANK = {"viewer": 0, "analyst": 1, "steward": 2, "admin": 3}
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

#: GETs that answer with no credential. The CI sweep fails when a GET outside
#: this set does. Login is POST, so it is not in this set.
ALLOWED_GET_PATHS = frozenset(
    [
        "/health",
        *[
            f"/v1/auth/oidc/{name}/{step}"
            for name in OIDC_PROVIDERS
            for step in ("start", "callback")
        ],
    ]
)

_ANALYST_POST = "/v1/chat"

_DUMMY_SALT = b"dms-sso-dummy-salt"


class AuthError(Exception):
    """A refusal. ``code`` is the only text a response may carry."""

    def __init__(self, code: str, status: int = 401) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    tenant_id: str
    role: str
    kind: str  # "user" or "service"


@dataclass
class _User:
    user_id: str
    email: str
    password_hash: str
    memberships: dict[str, str]


@dataclass
class _Session:
    user_id: str
    tenant_id: str
    role: str
    expires: float


@dataclass
class _State:
    provider: str
    redirect_uri: str
    nonce: str
    expires: float


@dataclass(frozen=True)
class OidcProvider:
    name: str
    issuer: str
    authorize_url: str
    token_url: str
    jwks_url: str
    client_id: str
    cred_ref: str


SecretLoader = Callable[[str], str]

_principal: ContextVar[Principal | None] = ContextVar("dms_sso_principal", default=None)
_tenants: dict[str, str] = {}
_users_by_email: dict[str, _User] = {}
_users_by_id: dict[str, _User] = {}
_sessions: dict[str, _Session] = {}
_states: dict[str, _State] = {}
_secret_loader: SecretLoader | None = None


def enabled() -> bool:
    """True only when ``DMS_SSO`` is exactly the digit ``1``.

    ``true``, ``yes``, ``on``, and a padded `` 1 `` stay off.
    """
    return os.environ.get(FLAG, "0") == "1"


def _now() -> float:
    return time.time()


def set_secret_loader(loader: SecretLoader | None) -> None:
    """Install the OpenVault reveal. Tests pass their own loader."""
    global _secret_loader
    _secret_loader = loader


def reset_for_tests() -> None:
    """Drop process logins. Does not change the flag or the secret loader."""
    _tenants.clear()
    _users_by_email.clear()
    _users_by_id.clear()
    _sessions.clear()
    _states.clear()
    _principal.set(None)


def current_principal() -> Principal | None:
    return _principal.get()


def bind_principal(principal: Principal) -> Token[Principal | None]:
    return _principal.set(principal)


def reset_principal(token: Token[Principal | None]) -> None:
    _principal.reset(token)


def bound_tenant_id(fallback: str) -> str:
    """Request tenant when SSO is on and a principal is bound, else ``fallback``.

    Flag off returns ``fallback`` unchanged, so a route that used
    ``settings.dms_tenant_id`` keeps that string.
    """
    if not enabled():
        return fallback
    principal = _principal.get()
    if principal is None:
        return fallback
    return principal.tenant_id


def verified_user_actor() -> str | None:
    """User id of a logged-in person. None for flag off and for the service bearer."""
    if not enabled():
        return None
    principal = _principal.get()
    if principal is None or principal.kind != "user":
        return None
    return principal.user_id


def export_actor_basis(default: str) -> str:
    """Audit export header. Flag off, and the service bearer, keep ``default``."""
    if verified_user_actor() is None:
        return default
    return "verified user (DMS_SSO); not a request header"


def unauthenticated_get_allowed(path: str) -> bool:
    return path in ALLOWED_GET_PATHS


def anonymous_allowed(method: str, path: str) -> bool:
    if method == "GET" and path in ALLOWED_GET_PATHS:
        return True
    return method == "POST" and path == "/v1/auth/login"


def _hash_password(password: str, salt: bytes | None = None) -> str:
    raw_salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), raw_salt, PBKDF2_ROUNDS)
    salt_b64 = base64.urlsafe_b64encode(raw_salt).decode("ascii")
    hash_b64 = base64.urlsafe_b64encode(digest).decode("ascii")
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt_b64}${hash_b64}"


def hash_password(password: str) -> str:
    """Hash for a bootstrap file. The password is not returned."""
    if len(password) < 12:
        raise AuthError("password_too_short", 422)
    return _hash_password(password)


_DUMMY_STORED = _hash_password("missing-user", _DUMMY_SALT)


def _verify_password(password: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _DUMMY_SALT, PBKDF2_ROUNDS)
        return False
    try:
        rounds = int(parts[1])
        salt = base64.urlsafe_b64decode(parts[2])
        expect = base64.urlsafe_b64decode(parts[3])
    except (ValueError, TypeError):
        return False
    if rounds < PBKDF2_ROUNDS:
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(got, expect)


def _check_role(role: str) -> str:
    name = role.strip().lower()
    if name not in ROLES:
        raise AuthError("role_unknown", 422)
    return name


def _check_tenant(tenant_id: str) -> str:
    if _UUID.fullmatch(tenant_id) is None or tenant_id not in _tenants:
        raise AuthError("tenant_unknown", 403)
    return tenant_id


def provision_tenant(tenant_id: str, name: str) -> None:
    if _UUID.fullmatch(tenant_id) is None:
        raise AuthError("tenant_unknown", 422)
    clean = name.strip()
    if not clean:
        raise AuthError("tenant_unknown", 422)
    _tenants[tenant_id] = clean


def provision_user(*, email: str, password: str, role: str, tenant_id: str) -> str:
    """Store a user. The password is hashed before it is kept."""
    addr = email.strip().lower()
    if "@" not in addr or " " in addr:
        raise AuthError("email_invalid", 422)
    named = _check_role(role)
    tenant = _check_tenant(tenant_id)
    if len(password) < 12:
        raise AuthError("password_too_short", 422)
    found = _users_by_email.get(addr)
    if found is None:
        user = _User(
            user_id=secrets.token_hex(16),
            email=addr,
            password_hash=_hash_password(password),
            memberships={tenant: named},
        )
        _users_by_email[addr] = user
        _users_by_id[user.user_id] = user
        return user.user_id
    found.memberships[tenant] = named
    found.password_hash = _hash_password(password)
    return found.user_id


def provision_hash(*, email: str, password_hash: str, role: str, tenant_id: str) -> str:
    """Same as ``provision_user`` when the operator already hashed the password."""
    if not password_hash.startswith(f"pbkdf2_sha256${PBKDF2_ROUNDS}$"):
        raise AuthError("password_hash_rejected", 422)
    addr = email.strip().lower()
    named = _check_role(role)
    tenant = _check_tenant(tenant_id)
    parts = password_hash.split("$")
    if len(parts) != 4:
        raise AuthError("password_hash_rejected", 422)
    try:
        base64.urlsafe_b64decode(parts[2])
        base64.urlsafe_b64decode(parts[3])
        int(parts[1])
    except (ValueError, TypeError) as exc:
        raise AuthError("password_hash_rejected", 422) from exc
    found = _users_by_email.get(addr)
    if found is None:
        user = _User(
            user_id=secrets.token_hex(16),
            email=addr,
            password_hash=password_hash,
            memberships={tenant: named},
        )
        _users_by_email[addr] = user
        _users_by_id[user.user_id] = user
        return user.user_id
    found.memberships[tenant] = named
    found.password_hash = password_hash
    return found.user_id


def load_bootstrap() -> None:
    """Read ``DMS_SSO_BOOTSTRAP_FILE`` when SSO is on. A missing env is a no-op.

    The file holds hashes, not passwords. A bad line raises with the line
    number only.
    """
    if not enabled():
        return
    path = os.environ.get("DMS_SSO_BOOTSTRAP_FILE", "").strip()
    if not path:
        return
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            try:
                _bootstrap_line(text)
            except AuthError as exc:
                raise AuthError(f"{exc.code}:line {number}", exc.status) from None


def _bootstrap_line(text: str) -> None:
    kind, _, rest = text.partition(" ")
    if kind == "tenant":
        tenant_id, _, name = rest.partition(" ")
        provision_tenant(tenant_id, name)
        return
    if kind == "user":
        email, role, tenant_id, password_hash = rest.split(" ", 3)
        provision_hash(
            email=email,
            password_hash=password_hash,
            role=role,
            tenant_id=tenant_id,
        )
        return
    raise AuthError("bootstrap_rejected", 422)


def _issue(user: _User, tenant_id: str, role: str) -> dict[str, str]:
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    _sessions[digest] = _Session(
        user_id=user.user_id,
        tenant_id=tenant_id,
        role=role,
        expires=_now() + _SESSION_TTL_S,
    )
    return {
        "token": raw,
        "token_type": "bearer",
        "role": role,
        "tenant_id": tenant_id,
        "user_id": user.user_id,
    }


def login(*, email: str, password: str, company: str | None) -> dict[str, str]:
    """Password check, then the company the user already belongs to.

    ``company`` selects a membership. A company they are not in is the same
    refusal as a bad password. The ledger actor is the user id issued here,
    not this field.
    """
    addr = email.strip().lower()
    user = _users_by_email.get(addr)
    if user is None:
        _verify_password(password, _DUMMY_STORED)
        raise AuthError("auth_required")
    if not _verify_password(password, user.password_hash):
        raise AuthError("auth_required")
    if company:
        if company not in user.memberships:
            raise AuthError("auth_required")
        chosen = company
    elif len(user.memberships) == 1:
        chosen = next(iter(user.memberships))
    else:
        raise AuthError("company_required", 409)
    return _issue(user, chosen, user.memberships[chosen])


def principal_for_token(token: str) -> Principal | None:
    if not token:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = _sessions.get(digest)
    if row is None:
        return None
    if row.expires <= _now():
        _sessions.pop(digest, None)
        return None
    user = _users_by_id.get(row.user_id)
    if user is None:
        return None
    return Principal(
        user_id=user.user_id,
        email=user.email,
        tenant_id=row.tenant_id,
        role=row.role,
        kind="user",
    )


def _service_principal(authorization: str | None) -> Principal | None:
    from dms_api.settings import get_settings
    from dms_api.wiring import service_bearer_id

    if service_bearer_id(authorization) is None:
        return None
    settings = get_settings()
    return Principal(
        user_id=settings.dms_actor_user_id,
        email="",
        tenant_id=settings.dms_tenant_id,
        role="steward",
        kind="service",
    )


def _bearer(headers: Mapping[str, str]) -> str | None:
    raw = headers.get("authorization") or headers.get("Authorization")
    if not raw:
        return None
    kind, _, rest = raw.partition(" ")
    if kind.lower() != "bearer" or not rest.strip():
        return None
    return rest.strip()


def resolve_bearer(headers: Mapping[str, str]) -> Principal | None:
    token = _bearer(headers)
    if token is None:
        return None
    found = principal_for_token(token)
    if found is not None:
        return found
    return _service_principal(headers.get("authorization") or headers.get("Authorization"))


def role_allows(principal: Principal, method: str, path: str) -> bool:
    if method in {"GET", "HEAD", "OPTIONS"}:
        return True
    if principal.role in {"admin", "steward"}:
        return True
    if principal.role == "analyst" and method == "POST":
        return path == _ANALYST_POST or path.startswith(_ANALYST_POST + "/")
    return False


def _b64url(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _https_issuer(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return False
    if parts.username or parts.password or parts.query or parts.fragment:
        return False
    return True


def _redirect_ok(url: str) -> bool:
    parts = urlsplit(url)
    if parts.username or parts.password:
        return False
    if parts.scheme == "https" and parts.hostname:
        return True
    return parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost"}


def provider_config(name: str) -> OidcProvider | None:
    key = name.strip().lower()
    if key not in OIDC_PROVIDERS:
        return None
    prefix = f"DMS_OIDC_{key.upper()}"
    client_id = os.environ.get(f"{prefix}_CLIENT_ID", "").strip()
    cred_ref = os.environ.get(f"{prefix}_CRED_REF", "").strip()
    if not client_id or not cred_ref:
        return None
    if key == "google":
        issuer = "https://accounts.google.com"
        return OidcProvider(
            name=key,
            issuer=issuer,
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            jwks_url="https://www.googleapis.com/oauth2/v3/certs",
            client_id=client_id,
            cred_ref=cred_ref,
        )
    if key == "entra":
        tenant = os.environ.get("DMS_OIDC_ENTRA_TENANT", "").strip()
        if _UUID.fullmatch(tenant) is None:
            return None
        base = f"https://login.microsoftonline.com/{tenant}"
        return OidcProvider(
            name=key,
            issuer=f"{base}/v2.0",
            authorize_url=f"{base}/oauth2/v2.0/authorize",
            token_url=f"{base}/oauth2/v2.0/token",
            jwks_url=f"{base}/discovery/v2.0/keys",
            client_id=client_id,
            cred_ref=cred_ref,
        )
    issuer = os.environ.get("DMS_OIDC_OKTA_ISSUER", "").strip().rstrip("/")
    if not _https_issuer(issuer):
        return None
    return OidcProvider(
        name=key,
        issuer=issuer,
        authorize_url=f"{issuer}/v1/authorize",
        token_url=f"{issuer}/v1/token",
        jwks_url=f"{issuer}/v1/keys",
        client_id=client_id,
        cred_ref=cred_ref,
    )


def _trim_states() -> None:
    now = _now()
    stale = [key for key, row in _states.items() if row.expires <= now]
    for key in stale:
        _states.pop(key, None)
    while len(_states) > _MAX_STATES:
        _states.pop(next(iter(_states)))


def begin_oidc(provider: str, redirect_uri: str) -> str:
    cfg = provider_config(provider)
    if cfg is None:
        raise AuthError("oidc_not_configured", 404)
    if not _redirect_ok(redirect_uri):
        raise AuthError("oidc_redirect_rejected", 400)
    _trim_states()
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(18)
    _states[state] = _State(cfg.name, redirect_uri, nonce, _now() + _STATE_TTL_S)
    query = urlencode(
        {
            "client_id": cfg.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "nonce": nonce,
        }
    )
    return f"{cfg.authorize_url}?{query}"


def _exchange_code(cfg: OidcProvider, code: str, redirect_uri: str, secret: str) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                cfg.token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                auth=(cfg.client_id, secret),
            )
    except httpx.HTTPError as exc:
        raise AuthError("oidc_not_configured") from exc
    if response.status_code >= 400:
        raise AuthError("oidc_token_rejected")
    try:
        body = response.json()
    except ValueError as exc:
        raise AuthError("oidc_token_rejected") from exc
    if not isinstance(body, dict):
        raise AuthError("oidc_token_rejected")
    return body


def _fetch_jwks(url: str) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise AuthError("oidc_not_configured") from exc
    if response.status_code >= 400:
        raise AuthError("oidc_token_rejected")
    try:
        body = response.json()
    except ValueError as exc:
        raise AuthError("oidc_token_rejected") from exc
    if not isinstance(body, dict):
        raise AuthError("oidc_token_rejected")
    return body


def _client_secret(cfg: OidcProvider) -> str:
    loader = _secret_loader
    if loader is None:
        raise AuthError("oidc_not_configured")
    try:
        secret = loader(cfg.cred_ref)
    except Exception as exc:  # noqa: BLE001 - the vault error can quote the secret
        raise AuthError("oidc_not_configured") from exc
    if not isinstance(secret, str) or not secret:
        raise AuthError("oidc_not_configured")
    return secret


def verify_rs256(
    token: str,
    *,
    jwks: Mapping[str, Any],
    issuer: str,
    audience: str,
    nonce: str,
    now: int,
) -> dict[str, Any]:
    """RS256 only. ``none`` and HMAC are rejected before any key is used."""
    if not token or len(token) > _TOKEN_MAX or token.count(".") != 2:
        raise AuthError("oidc_token_rejected")
    header_b, payload_b, sig_b = token.split(".")
    try:
        header = json.loads(_b64url(header_b))
        payload = json.loads(_b64url(payload_b))
        signature = _b64url(sig_b)
    except (ValueError, json.JSONDecodeError, TypeError) as exc:
        raise AuthError("oidc_token_rejected") from exc
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise AuthError("oidc_token_rejected")
    if header.get("alg") != "RS256" or header.get("crit"):
        raise AuthError("oidc_token_rejected")
    key = _rsa_key(jwks, header.get("kid"))
    try:
        key.verify(
            signature,
            f"{header_b}.{payload_b}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature as exc:
        raise AuthError("oidc_token_rejected") from exc
    if payload.get("iss") != issuer:
        raise AuthError("oidc_token_rejected")
    aud = payload.get("aud")
    if aud != audience and not (isinstance(aud, list) and audience in aud):
        raise AuthError("oidc_token_rejected")
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, int) or exp < now:
        raise AuthError("oidc_token_rejected")
    claim_nonce = payload.get("nonce")
    if not isinstance(claim_nonce, str) or not hmac.compare_digest(claim_nonce, nonce):
        raise AuthError("oidc_token_rejected")
    return payload


def _rsa_key(jwks: Mapping[str, Any], kid: object) -> Any:
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        raise AuthError("oidc_token_rejected")
    chosen: Mapping[str, Any] | None = None
    for item in keys:
        if not isinstance(item, dict):
            continue
        if item.get("kty") != "RSA":
            continue
        if kid is None or item.get("kid") == kid:
            chosen = item
            if kid is not None:
                break
    if chosen is None or not isinstance(kid, str):
        raise AuthError("oidc_token_rejected")
    try:
        modulus = int.from_bytes(_b64url(str(chosen["n"])), "big")
        exponent = int.from_bytes(_b64url(str(chosen["e"])), "big")
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthError("oidc_token_rejected") from exc
    return RSAPublicNumbers(exponent, modulus).public_key()


def role_from_claims(claims: Mapping[str, Any]) -> str:
    """Exact role token only: admin, steward, analyst, viewer. Else viewer."""
    found = "viewer"
    rank = -1
    for key in ("roles", "groups"):
        raw = claims.get(key)
        items = [raw] if isinstance(raw, str) else raw
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, str):
                continue
            name = item.strip().lower()
            score = _ROLE_RANK.get(name)
            if score is not None and score > rank:
                found = name
                rank = score
    return found


def _email_from_claims(claims: Mapping[str, Any]) -> str:
    for key in ("email", "preferred_username"):
        raw = claims.get(key)
        if isinstance(raw, str):
            addr = raw.strip().lower()
            if "@" in addr and " " not in addr and len(addr) <= 320:
                return addr
    raise AuthError("oidc_token_rejected")


def _tenant_from_claims(claims: Mapping[str, Any]) -> str:
    raw = claims.get("tenant_id")
    if isinstance(raw, str) and raw in _tenants:
        return raw
    fallback = os.environ.get("DMS_OIDC_TENANT_ID", "").strip()
    if fallback and fallback in _tenants:
        return fallback
    raise AuthError("tenant_unknown", 403)


def _upsert_sso(email: str, tenant_id: str, role: str) -> _User:
    found = _users_by_email.get(email)
    if found is None:
        user = _User(
            user_id=secrets.token_hex(16),
            email=email,
            password_hash="",
            memberships={tenant_id: role},
        )
        _users_by_email[email] = user
        _users_by_id[user.user_id] = user
        return user
    found.memberships[tenant_id] = role
    return found


def finish_oidc(provider: str, code: str, state: str) -> dict[str, str]:
    saved = _states.pop(state, None)
    if saved is None or saved.provider != provider or saved.expires <= _now():
        raise AuthError("oidc_state_rejected")
    if not code:
        raise AuthError("oidc_token_rejected")
    cfg = provider_config(provider)
    if cfg is None:
        raise AuthError("oidc_not_configured", 404)
    secret = _client_secret(cfg)
    body = _exchange_code(cfg, code, saved.redirect_uri, secret)
    id_token = body.get("id_token")
    if not isinstance(id_token, str):
        raise AuthError("oidc_token_rejected")
    claims = verify_rs256(
        id_token,
        jwks=_fetch_jwks(cfg.jwks_url),
        issuer=cfg.issuer,
        audience=cfg.client_id,
        nonce=saved.nonce,
        now=int(_now()),
    )
    email = _email_from_claims(claims)
    role = role_from_claims(claims)
    tenant_id = _tenant_from_claims(claims)
    user = _upsert_sso(email, tenant_id, role)
    return _issue(user, tenant_id, role)


def me() -> dict[str, str]:
    principal = _principal.get()
    if principal is None:
        raise AuthError("auth_required")
    return {
        "user_id": principal.user_id,
        "email": principal.email,
        "role": principal.role,
        "tenant_id": principal.tenant_id,
        "kind": principal.kind,
    }


class AuthRequiredMiddleware(BaseHTTPMiddleware):
    """401 unless the request is on the allowlist or carries a verified bearer."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if not enabled():
            return await call_next(request)
        path = request.url.path
        method = request.method.upper()
        if anonymous_allowed(method, path):
            return await call_next(request)
        principal = resolve_bearer(request.headers)
        if principal is None:
            return JSONResponse(
                status_code=401,
                content={"detail": {"code": "auth_required"}},
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not role_allows(principal, method, path):
            return JSONResponse(
                status_code=403,
                content={"detail": {"code": "role_forbidden"}},
            )
        token = bind_principal(principal)
        try:
            return await call_next(request)
        finally:
            reset_principal(token)
