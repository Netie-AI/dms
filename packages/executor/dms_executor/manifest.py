"""Session manifest minting — DMS signs; Cortex enforces.

Signing key material is held in memory only. Never log or put keys in exceptions.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from cortex_contract.execution import Manifest, canonical_manifest_bytes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dms_core.control_plane.connect_secrets import ConnectCredentialError, key_id_for_ref

logger = logging.getLogger(__name__)

# Corpus case: never grant both a raw parquet path and a row predicate over the
# same underlying data (minting_invariant).
_SECURITY = "security_event"


class ManifestMintError(Exception):
    """Minting failed. Never includes key material in args or message."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)

    def __repr__(self) -> str:
        return f"ManifestMintError(code={self.code!r})"


class SecurityEvent(ManifestMintError):
    """ACL / signature refusal — never re-mint."""


#: OpenVault service_id. OV compares it case-sensitively: ``DMS`` is another service.
OV_SERVICE_ID = "dms"
#: Current ``dms`` service Bearer for OpenVault. Wins over the file.
SERVICE_TOKEN_ENV = "DMS_OV_SERVICE_TOKEN"
#: Path of the secret file holding that Bearer. A first mint writes it 0600.
SERVICE_TOKEN_FILE_ENV = "DMS_OV_SERVICE_TOKEN_FILE"

OV_TOKEN_MISSING = "ov_service_token_missing"
OV_TOKEN_UNAUTHORIZED = "ov_service_token_unauthorized"
OV_MINT_FAILED = "ov_mint_failed"

#: Seconds DMS asks OV for, and the key lifetime when OV omits ``not_after``.
INTERMEDIATE_TTL_ENV = "DMS_OV_INTERMEDIATE_TTL_S"
DEFAULT_INTERMEDIATE_TTL_S = 900
#: Refetch this long before ``not_after`` so no manifest is signed by a dying key.
KEY_EXPIRY_SKEW = timedelta(seconds=30)
#: One pooled keep-alive client per minter, shared by every OV call.
OV_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
OV_LIMITS = httpx.Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=60.0)


def _now() -> datetime:
    return datetime.now(UTC)


def _intermediate_ttl_s() -> int:
    raw = os.environ.get(INTERMEDIATE_TTL_ENV, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_INTERMEDIATE_TTL_S


def _unauthorized(resp: httpx.Response) -> bool:
    """OV b4d68021: 401 = no Bearer, 403 = wrong/stale Bearer. A sealed vault is not this."""
    return resp.status_code == 401 or (resp.status_code == 403 and "sealed" not in resp.text)


class OpenVaultTokenError(ManifestMintError):
    """No intermediate key from OpenVault. Never carries the service token."""


def _token_file() -> Path | None:
    raw = os.environ.get(SERVICE_TOKEN_FILE_ENV, "").strip()
    return Path(raw) if raw else None


def _load_service_token() -> str:
    token = os.environ.get(SERVICE_TOKEN_ENV, "").strip()
    if token:
        return token
    path = _token_file()
    if path is not None and path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def match_service_bearer(presented: str) -> str | None:
    """Short id of the configured service bearer when ``presented`` matches.

    Same token ``_load_service_token`` already sends to OpenVault. No new mint.
    The return value is a hash prefix. The token is not returned or logged.
    """
    expected = _load_service_token()
    if not expected or not presented:
        return None
    if not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
        return None
    return hashlib.sha256(expected.encode("utf-8")).hexdigest()[:12]


@dataclass
class IntermediateKey:
    kid: str
    private_key: Ed25519PrivateKey = field(repr=False)
    not_after: datetime
    # raw seed kept only to detect accidental leakage in tests — not logged
    _seed_b64: str = field(repr=False, default="")


@dataclass
class SessionAcl:
    """Resolved ACL for one session (Space ∩ source grants)."""

    session_id: str
    org_id: str  # tenant
    space_id: str | None
    # Table name -> SQL boolean (TRUE if no filter). Keys = table allowlist.
    row_predicates: dict[str, str]
    # Parquet/blob globs for file reads — must NOT overlap row_predicates data.
    allowed_paths: list[str]
    pool_id: str
    ttl_seconds: int = 900


@dataclass
class _CacheEntry:
    manifest: Manifest
    expires_at: datetime


class ManifestMinter:
    """Fetch OpenVault intermediate key; mint + cache signed manifests."""

    def __init__(
        self,
        *,
        openvault_url: str | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.openvault_url = (
            openvault_url or os.environ.get("OPENVAULT_URL", "http://127.0.0.1:5000")
        ).rstrip("/")
        self._http = http or httpx.Client(timeout=OV_TIMEOUT, limits=OV_LIMITS)
        self._owns_http = http is None
        self._token = _load_service_token()
        self._key: IntermediateKey | None = None
        self._cache: dict[str, _CacheEntry] = {}

    def close(self) -> None:
        if self._owns_http:
            self._http.close()
        self._key = None

    def invalidate(self, session_id: str | None = None) -> None:
        if session_id is None:
            self._cache.clear()
        else:
            self._cache.pop(session_id, None)

    def fetch_intermediate(self, *, ttl_s: int | None = None) -> IntermediateKey:
        """POST /keys/intermediate with the cached ``dms`` Bearer. Key stays in RAM.

        An unauthorized answer re-reads env / file once (a rotation may have
        landed there) and re-fetches with that Bearer. It never re-registers:
        OV refuses a reveal-only rotation (OV#128).
        """
        ttl = ttl_s or _intermediate_ttl_s()
        if not self._token:
            self._token = _load_service_token() or self._first_mint()
        inter = self._post_intermediate(self._token, ttl)
        if _unauthorized(inter):
            self._token = _load_service_token() or self._token
            inter = self._post_intermediate(self._token, ttl)
        if _unauthorized(inter):
            raise OpenVaultTokenError(
                OV_TOKEN_UNAUTHORIZED, f"intermediate: HTTP {inter.status_code} after one re-read"
            )
        if inter.status_code == 403:
            raise OpenVaultTokenError(OV_MINT_FAILED, "intermediate: vault sealed (HTTP 403)")
        if inter.status_code >= 400:
            raise OpenVaultTokenError(OV_MINT_FAILED, f"intermediate: HTTP {inter.status_code}")
        body = inter.json()
        seed_b64 = body["private_key"]
        pad = "=" * (-len(seed_b64) % 4)
        seed = base64.urlsafe_b64decode(seed_b64 + pad)
        key = Ed25519PrivateKey.from_private_bytes(seed)
        not_after_raw = body.get("not_after")
        if not_after_raw is None:
            not_after = _now() + timedelta(seconds=ttl)
        elif isinstance(not_after_raw, (int, float)):
            not_after = datetime.fromtimestamp(not_after_raw, tz=UTC)
        else:
            not_after = datetime.fromisoformat(str(not_after_raw).replace("Z", "+00:00"))
        self._key = IntermediateKey(
            kid=body["kid"],
            private_key=key,
            not_after=not_after,
            _seed_b64=seed_b64,
        )
        self._notify_cortex_jwks_refresh()
        return self._key

    def resolve_credential(self, ref: str) -> str:
        """Reveal one vault secret for a connect. Plaintext stays with the caller.

        Uses the same HTTP client and the reveal header ``_first_mint`` already
        sends. A bare id or ``service/kid`` both hit the existing
        ``GET /api/keys/{key_id}/secret`` (the kid, when the ref has a slash).
        Failures raise ``ConnectCredentialError`` and do not include the secret
        or the vault body.
        """
        key_id = key_id_for_ref(ref)
        if key_id is None:
            raise ConnectCredentialError()
        token = self._token or _load_service_token()
        headers = {"X-OpenVault-Reveal": "intentional"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            res = self._http.get(
                f"{self.openvault_url}/api/keys/{key_id}/secret",
                headers=headers,
            )
        except httpx.HTTPError:
            raise ConnectCredentialError() from None
        if res.status_code >= 400:
            raise ConnectCredentialError() from None
        try:
            body = res.json()
        except ValueError:
            raise ConnectCredentialError() from None
        secret = body.get("secret") if isinstance(body, dict) else None
        if not isinstance(secret, str) or secret == "":
            raise ConnectCredentialError() from None
        return secret

    def _post_intermediate(self, token: str, ttl_s: int) -> httpx.Response:
        try:
            return self._http.post(
                f"{self.openvault_url}/keys/intermediate",
                json={
                    "service_id": OV_SERVICE_ID,
                    "subject": "dms-manifest-signer",
                    "ttl_s": ttl_s,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise OpenVaultTokenError(
                OV_MINT_FAILED, f"intermediate: {type(exc).__name__}"
            ) from exc

    def _first_mint(self) -> str:
        """POST /keys/services once, when no token exists, and persist the result."""
        path = _token_file()
        if path is None:
            raise OpenVaultTokenError(
                OV_TOKEN_MISSING, f"set {SERVICE_TOKEN_ENV} or {SERVICE_TOKEN_FILE_ENV}"
            )
        # Open the file before OV issues the token: an unwritable path must not
        # cost the only copy of a token that cannot be re-fetched.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.chmod(path, 0o600)
        except OSError as exc:
            raise OpenVaultTokenError(
                OV_TOKEN_MISSING, f"token file not writable: {type(exc).__name__}"
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            try:
                reg = self._http.post(
                    f"{self.openvault_url}/keys/services",
                    json={"service_id": OV_SERVICE_ID},
                    headers={"X-OpenVault-Reveal": "intentional"},
                )
            except httpx.HTTPError as exc:
                raise OpenVaultTokenError(
                    OV_MINT_FAILED, f"register: {type(exc).__name__}"
                ) from exc
            if reg.status_code == 401:
                # Already registered on OV. Rotation needs the current Bearer
                # or admin, and DMS holds neither.
                raise OpenVaultTokenError(
                    OV_TOKEN_MISSING, "first mint refused: service already registered (HTTP 401)"
                )
            if reg.status_code >= 400:
                raise OpenVaultTokenError(OV_MINT_FAILED, f"register: HTTP {reg.status_code}")
            body = reg.json()
            if body.get("service_id") != OV_SERVICE_ID:
                raise OpenVaultTokenError(OV_MINT_FAILED, "register: service_id mismatch")
            token = str(body.get("token") or "").strip()
            if not token:
                raise OpenVaultTokenError(OV_MINT_FAILED, "register: no token in response")
            fh.write(token)
        return token

    def _notify_cortex_jwks_refresh(self) -> None:
        """Ask Cortex to cold-refresh JWKS so the new intermediate verifies."""
        cortex_url = (
            os.environ.get("CORTEX_URL") or os.environ.get("CORTEX_API_URL") or "http://127.0.0.1:8010"
        ).rstrip("/")
        try:
            r = self._http.post(f"{cortex_url}/v1/contract/jwks/refresh", timeout=3.0)
            if r.status_code >= 400:
                logger.warning(
                    "Cortex JWKS refresh returned %s — restart Cortex or POST "
                    "/v1/contract/jwks/refresh",
                    r.status_code,
                )
            else:
                kids = (r.json() or {}).get("kids") or []
                logger.info("Cortex JWKS refreshed (%d kids)", len(kids))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cortex JWKS refresh failed: %s", exc)

    def _ensure_key(self) -> IntermediateKey:
        if self._key is None or self._key.not_after <= _now() + KEY_EXPIRY_SKEW:
            return self.fetch_intermediate()
        return self._key

    def mint_manifest(self, acl: SessionAcl) -> Manifest:
        """Build, sign, and cache a session Manifest.

        Enforces the minting invariant: no overlapping path+predicate grants.
        """
        _assert_minting_invariant(acl)
        cached = self._cache.get(acl.session_id)
        now = datetime.now(UTC)
        if cached and cached.expires_at > now + timedelta(seconds=15):
            return cached.manifest

        key = self._ensure_key()
        issued = now
        expires = now + timedelta(seconds=acl.ttl_seconds)
        unsigned = Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            allowed_paths=list(acl.allowed_paths),
            expires_at=expires.isoformat(),
            signature="",  # placeholder removed by canonicalisation
            pool_id=acl.pool_id,
            issued_at=issued.isoformat(),
            issuer_key_id=key.kid,
            row_predicates=dict(acl.row_predicates),
        )
        payload = canonical_manifest_bytes(unsigned)
        sig = key.private_key.sign(payload)
        signature = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
        manifest = unsigned.model_copy(update={"signature": signature})
        self._cache[acl.session_id] = _CacheEntry(manifest=manifest, expires_at=expires)
        return manifest


def _assert_minting_invariant(acl: SessionAcl) -> None:
    """Never grant both a raw path and a row predicate over the same data."""
    # Heuristic: if a path stem matches a table name that has a non-TRUE predicate,
    # refuse. Exact lake layout is known at mint time via source_ref (later);
    # for T2 we reject obvious overlaps: path containing `/{table}/` or `/{table}.`.
    for table, pred in acl.row_predicates.items():
        if pred.strip().upper() == "TRUE":
            continue
        needle = table.split(".")[-1].lower()
        for path in acl.allowed_paths:
            pl = path.lower().replace("\\", "/")
            if f"/{needle}/" in pl or f"/{needle}." in pl or pl.endswith(f"/{needle}"):
                logger.error("%s minting_invariant table=%s path=%s", _SECURITY, table, path)
                raise SecurityEvent(
                    "minting_invariant",
                    "raw path and row predicate overlap",
                )


def sign_manifest_bytes(private_key: Ed25519PrivateKey, manifest: Manifest) -> str:
    sig = private_key.sign(canonical_manifest_bytes(manifest))
    return base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")


# ── submit error branching ──────────────────────────────────────────────────


@dataclass
class SubmitError(Exception):
    code: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code


def classify_submit_error(exc: BaseException) -> SubmitError:
    """Map Cortex refusals to stable codes for DMS branching."""
    text = str(exc)
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        return SubmitError(code=code, detail=text)
    for known in (
        "manifest_expired",
        "manifest_not_yet_valid",
        "manifest_malformed",
        "manifest_unknown_issuer",
        "manifest_signature_invalid",
        "path_not_allowed",
        "statement_not_allowed",
        "sql_not_analyzable",
        "pool_queue_timeout",
        "statement_timeout",
        "pool_mismatch",
        "pool_required",
        "pool_saturated",
        "session_unbound",
        "session_expired",
        "sql_required",
    ):
        if known in text:
            return SubmitError(code=known, detail=text)
    return SubmitError(code="submit_failed", detail=text)


def should_rement(code: str) -> bool:
    """Only manifest_expired may re-mint once."""
    return code == "manifest_expired"


_HOSTILE_PATTERNS = (
    re.compile(r"\bread_parquet\s*\(", re.I),
    re.compile(r"\bread_csv(?:_auto)?\s*\(", re.I),
    re.compile(r"\bread_json(?:_auto)?\s*\(", re.I),
    re.compile(r"\bparquet_scan\s*\(", re.I),
    re.compile(r"\bATTACH\b", re.I),
    re.compile(r"\bCOPY\b", re.I),
    re.compile(r"\bINSTALL\b", re.I),
    re.compile(r"\bLOAD\b", re.I),
    re.compile(r"\bPRAGMA\b", re.I),
    re.compile(r"\bCREATE\b", re.I),
    re.compile(r"\bDROP\b", re.I),
    re.compile(r"\bDELETE\b", re.I),
    re.compile(r"\bUPDATE\b", re.I),
    re.compile(r"\bINSERT\b", re.I),
    re.compile(r"\bchr\s*\(", re.I),
)


def reject_hostile_chat_sql(sql: str) -> None:
    """DMS-side hostile check before submit — security event, never LLM/UI.

    Full enforcement is Cortex; this catches known escape attempts early and logs.
    Does **not** inject predicates (Cortex does).
    """
    for pat in _HOSTILE_PATTERNS:
        if pat.search(sql):
            code = (
                "statement_not_allowed"
                if pat.pattern
                in {
                    r"\bCOPY\b",
                    r"\bINSTALL\b",
                    r"\bLOAD\b",
                    r"\bPRAGMA\b",
                    r"\bCREATE\b",
                    r"\bDROP\b",
                    r"\bDELETE\b",
                    r"\bUPDATE\b",
                    r"\bINSERT\b",
                }
                else "path_not_allowed"
            )
            logger.error("%s hostile_sql pattern=%s", _SECURITY, pat.pattern)
            raise SecurityEvent(code, "hostile statement or file/attach function")
    # UNNEST name-shadowing: UNNEST(...) AS orders(
    if re.search(r"\bUNNEST\s*\(.*\)\s+AS\s+\w+\s*\(", sql, re.I | re.S):
        logger.error("%s hostile_sql unnest_shadow", _SECURITY)
        raise SecurityEvent("sql_not_analyzable", "unnest name-shadowing")
    # Only allow SELECT / WITH for chat SQL
    head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
    if head and head not in {"SELECT", "WITH", "("}:
        logger.error("%s hostile_sql non_select head=%s", _SECURITY, head)
        raise SecurityEvent("statement_not_allowed", f"non-select head {head}")

def key_fingerprint(seed_b64: str) -> str:
    """Safe identifier for tests — never the key itself."""
    return hashlib.sha256(seed_b64.encode()).hexdigest()[:16]


def assert_no_key_in_exception(exc: BaseException, seed_b64: str) -> None:
    blob = f"{exc!r}\n{exc}"
    if seed_b64 and seed_b64 in blob:
        raise AssertionError("signing key leaked into exception")
