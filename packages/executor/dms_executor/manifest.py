"""Session manifest minting — DMS signs; Cortex enforces.

Signing key material is held in memory only. Never log or put keys in exceptions.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from cortex_contract.execution import Manifest, canonical_manifest_bytes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
        service_id: str = "dms",
        http: httpx.Client | None = None,
    ) -> None:
        self.openvault_url = (
            openvault_url or os.environ.get("OPENVAULT_URL", "http://127.0.0.1:5000")
        ).rstrip("/")
        self.service_id = service_id
        self._http = http or httpx.Client(timeout=15.0)
        self._owns_http = http is None
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

    def fetch_intermediate(self, *, ttl_s: int = 900) -> IntermediateKey:
        """POST /keys/services then /keys/intermediate. Private key stays in RAM."""
        reveal = self._http.post(
            f"{self.openvault_url}/keys/services",
            json={"service_id": self.service_id},
            headers={"X-OpenVault-Reveal": "intentional"},
        )
        reveal.raise_for_status()
        token = reveal.json()["token"]
        inter = self._http.post(
            f"{self.openvault_url}/keys/intermediate",
            json={
                "service_id": self.service_id,
                "subject": "dms-manifest-signer",
                "ttl_s": ttl_s,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        inter.raise_for_status()
        body = inter.json()
        seed_b64 = body["private_key"]
        pad = "=" * (-len(seed_b64) % 4)
        seed = base64.urlsafe_b64decode(seed_b64 + pad)
        key = Ed25519PrivateKey.from_private_bytes(seed)
        not_after_raw = body["not_after"]
        if isinstance(not_after_raw, (int, float)):
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
        now = datetime.now(UTC)
        if self._key is None or self._key.not_after <= now + timedelta(seconds=30):
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
