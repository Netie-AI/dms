"""Signing material. The private key is never logged and never repr'd.

Production loads PEM from OpenVault through ``material_from_resolver``.
``ephemeral_dev_material`` builds a self-signed cert in memory for tests.
Nothing writes that cert to the repo.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from dms_core.signing.errors import SignError

_KEY_MARK = "PRIVATE KEY"
_CERT_MARK = "CERTIFICATE"


@dataclass(repr=False)
class SigningMaterial:
    key_pem: bytes
    cert_pem: bytes

    def __repr__(self) -> str:
        return "SigningMaterial(redacted)"


def cert_fingerprint(cert_pem: bytes) -> str:
    """SHA-256 of the certificate DER. The private key is not an input."""
    cert = x509.load_pem_x509_certificate(cert_pem)
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def _pem_ok(key: str, cert: str) -> bool:
    return _KEY_MARK in key and _CERT_MARK in cert and "BEGIN" in key and "BEGIN" in cert


def material_from_resolver(tenant_id: str, resolve: Callable[[str], str]) -> SigningMaterial:
    """Load ``signkey/{tenant}`` and ``signcert/{tenant}``. Failures drop the body."""
    try:
        key = resolve(f"signkey/{tenant_id}")
        cert = resolve(f"signcert/{tenant_id}")
    except Exception:
        raise SignError("signing_key_unavailable", 503) from None
    if not isinstance(key, str) or not isinstance(cert, str) or not _pem_ok(key, cert):
        raise SignError("signing_key_rejected", 503)
    return SigningMaterial(key.encode("utf-8"), cert.encode("utf-8"))


def ephemeral_dev_material(common_name: str = "dms-dev") -> SigningMaterial:
    """Self-signed RSA cert. Tests only. Not a licensed CA and not stored."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return SigningMaterial(key_pem, cert_pem)
