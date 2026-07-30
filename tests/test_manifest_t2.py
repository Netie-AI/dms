"""T2: canonical vectors, minting invariant, hostile SQL, key non-leakage."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cortex_contract.execution import Manifest, canonical_manifest_bytes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from dms_executor.manifest import (
    ManifestMinter,
    SecurityEvent,
    SessionAcl,
    assert_no_key_in_exception,
    reject_hostile_chat_sql,
)

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "contract" / "testvectors" / "manifest_canonical.jsonl"


def test_vendored_openapi_sha256() -> None:
    spec = ROOT / "contract" / "openapi-1.1.0.json"
    digest = ROOT / "contract" / "openapi-1.1.0.json.sha256"
    assert spec.is_file() and digest.is_file()
    actual = hashlib.sha256(spec.read_bytes()).hexdigest()
    expected = digest.read_text(encoding="utf-8").split()[0]
    assert actual == expected


def test_dms_reproduces_all_canonical_vectors() -> None:
    assert VECTORS.is_file(), "run python scripts/sync_contract.py"
    failures: list[str] = []
    for line in VECTORS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        manifest = row["manifest"]
        expected = row["canonical_sha256"]
        actual = hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()
        if actual != expected:
            failures.append(row.get("name", actual))
    assert not failures, f"canonical mismatch: {failures}"


def test_minting_invariant_rejects_path_predicate_overlap() -> None:
    minter = ManifestMinter(http=_FakeHttp())  # type: ignore[arg-type]
    # Inject a local key so we never hit OpenVault
    seed = Ed25519PrivateKey.generate()
    from dms_executor.manifest import IntermediateKey

    minter._key = IntermediateKey(
        kid="test-kid",
        private_key=seed,
        not_after=datetime.now(UTC) + timedelta(hours=1),
        _seed_b64="SECRETSEEDVALUE_DO_NOT_LEAK_IN_REPR_OR_TRACE",
    )
    acl = SessionAcl(
        session_id="s1",
        org_id="o1",
        space_id="sp1",
        row_predicates={"silver.orders": "region = 'N'"},
        allowed_paths=["lake/silver/orders/*.parquet"],
        pool_id="pool-read",
    )
    with pytest.raises(SecurityEvent) as ei:
        minter.mint_manifest(acl)
    assert ei.value.code == "minting_invariant"
    assert_no_key_in_exception(ei.value, minter._key._seed_b64)


def test_mint_ok_when_path_or_predicate_not_both() -> None:
    minter = ManifestMinter(http=_FakeHttp())  # type: ignore[arg-type]
    seed = Ed25519PrivateKey.generate()
    from dms_executor.manifest import IntermediateKey

    minter._key = IntermediateKey(
        kid="test-kid",
        private_key=seed,
        not_after=datetime.now(UTC) + timedelta(hours=1),
        _seed_b64="xyz",
    )
    acl = SessionAcl(
        session_id="s2",
        org_id="o1",
        space_id=None,
        row_predicates={"silver.orders": "TRUE"},
        allowed_paths=["lake/bronze/raw/*.parquet"],
        pool_id="pool-read",
    )
    m = minter.mint_manifest(acl)
    assert m.issuer_key_id == "test-kid"
    assert m.signature
    import base64

    pad = "=" * (-len(m.signature) % 4)
    seed.public_key().verify(
        base64.urlsafe_b64decode(m.signature + pad),
        canonical_manifest_bytes(m),
    )


def test_hostile_unnest_shadow_and_file_fns() -> None:
    with pytest.raises(SecurityEvent):
        reject_hostile_chat_sql("SELECT * FROM read_parquet('secrets/*.parquet')")
    with pytest.raises(SecurityEvent):
        reject_hostile_chat_sql(
            "SELECT * FROM UNNEST([{x:1}]) AS orders(x) JOIN secrets USING (x)"
        )


def test_key_not_in_mint_error_repr() -> None:
    secret = "SUPER_SECRET_SEED_B64URL_VALUE_XXXX"
    err = SecurityEvent("path_not_allowed", "nope")
    assert_no_key_in_exception(err, secret)


class _FakeHttp:
    """Unused — tests inject IntermediateKey directly."""

    def close(self) -> None:
        return None
