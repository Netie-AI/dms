"""PDF render, PAdES sign, verify, watermark.

Cryptography is pyHanko and the ``cryptography`` library. This module does not
implement a signature scheme. Private keys are not logged. A missing library
raises ``sign_runtime_missing`` instead of a partial signature.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Any

from dms_core.signing.errors import SignError
from dms_core.signing.material import SigningMaterial

INTENT = "I agree to sign electronically"
_MAX_PDF = 15 * 1024 * 1024


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _libs() -> Any:
    try:
        from PIL import Image
        from pyhanko.keys import (
            load_certs_from_pemder_data,
            load_private_key_from_pemder_data,
        )
        from pyhanko.pdf_utils.images import PdfImage
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.fields import SigFieldSpec, SigSeedSubFilter
        from pyhanko.sign.signers import PdfSignatureMetadata, PdfSigner
        from pyhanko.sign.signers.pdf_cms import SimpleSigner
        from pyhanko.sign.timestamps import HTTPTimeStamper
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko.stamp import TextStampStyle
        from pyhanko_certvalidator import ValidationContext
        from pyhanko_certvalidator.registry import SimpleCertificateStore
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas
    except ImportError:
        raise SignError("sign_runtime_missing", 503) from None
    return {
        "Image": Image,
        "load_certs_from_pemder_data": load_certs_from_pemder_data,
        "load_private_key_from_pemder_data": load_private_key_from_pemder_data,
        "PdfImage": PdfImage,
        "IncrementalPdfFileWriter": IncrementalPdfFileWriter,
        "PdfFileReader": PdfFileReader,
        "SigFieldSpec": SigFieldSpec,
        "SigSeedSubFilter": SigSeedSubFilter,
        "PdfSignatureMetadata": PdfSignatureMetadata,
        "PdfSigner": PdfSigner,
        "SimpleSigner": SimpleSigner,
        "HTTPTimeStamper": HTTPTimeStamper,
        "validate_pdf_signature": validate_pdf_signature,
        "TextStampStyle": TextStampStyle,
        "ValidationContext": ValidationContext,
        "SimpleCertificateStore": SimpleCertificateStore,
        "PdfReader": PdfReader,
        "PdfWriter": PdfWriter,
        "canvas": canvas,
    }


def assert_pdf(data: bytes) -> None:
    if not data.startswith(b"%PDF") or len(data) > _MAX_PDF:
        raise SignError("bad_pdf")


def render_pdf(title: str, body: str, fields: dict[str, str]) -> bytes:
    """One-page PDF. Field values are drawn as text. No signature yet."""
    lib = _libs()
    canvas = lib["canvas"]
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(612, 792))
    pdf.setFont("Times-Bold", 16)
    pdf.drawString(72, 740, (title or "Document")[:120])
    pdf.setFont("Times-Roman", 11)
    y = 710
    lines = (body or "").splitlines() or [""]
    for line in lines:
        pdf.drawString(72, y, line[:110])
        y -= 16
        if y < 160:
            break
    for key, value in fields.items():
        pdf.drawString(72, y, f"{str(key)[:40]}: {str(value)[:60]}"[:110])
        y -= 16
        if y < 80:
            break
    pdf.save()
    data = buf.getvalue()
    assert_pdf(data)
    return data


def timestamper_for(url: str) -> Any | None:
    """RFC 3161 client when ``url`` is set. Unset means no timestamp."""
    text = url.strip()
    if not text:
        return None
    lib = _libs()
    https = text.startswith("https:")
    return lib["HTTPTimeStamper"](text, https=https, timeout=5)


def sign_pdf(
    pdf: bytes,
    *,
    material: SigningMaterial,
    field_name: str,
    signer_name: str,
    reason: str,
    png: bytes | None,
    box: tuple[float, float, float, float],
    timestamper: Any | None = None,
) -> bytes:
    """Append one visible PAdES signature. ``material`` is not retained."""
    assert_pdf(pdf)
    lib = _libs()
    try:
        signing_key = lib["load_private_key_from_pemder_data"](material.key_pem, None)
        certs = list(lib["load_certs_from_pemder_data"](material.cert_pem))
    except Exception:
        raise SignError("signing_key_rejected", 503) from None
    if len(certs) != 1:
        raise SignError("signing_key_rejected", 503)
    registry = lib["SimpleCertificateStore"]()
    cms = lib["SimpleSigner"](
        signing_cert=certs[0],
        signing_key=signing_key,
        cert_registry=registry,
    )
    writer = lib["IncrementalPdfFileWriter"](BytesIO(pdf))
    stamp_text = f"Signed by {signer_name}\n{reason}"
    background = None
    if png:
        try:
            image = lib["Image"].open(BytesIO(png))
            image.load()
            background = lib["PdfImage"](image, writer=writer)
        except Exception:
            raise SignError("capture_invalid") from None
    style = lib["TextStampStyle"](
        stamp_text=stamp_text,
        background=background,
        border_width=1,
    )
    meta = lib["PdfSignatureMetadata"](
        field_name=field_name,
        name=signer_name[:120],
        reason=reason[:200],
        subfilter=lib["SigSeedSubFilter"].PADES,
    )
    pdf_signer = lib["PdfSigner"](
        meta,
        signer=cms,
        timestamper=timestamper,
        stamp_style=style,
        new_field_spec=lib["SigFieldSpec"](sig_field_name=field_name, box=box),
    )
    try:
        signed = pdf_signer.sign_pdf(writer)
    except Exception:
        raise SignError("sign_failed", 500) from None
    data = signed.getvalue()
    assert_pdf(data)
    return data


def verify_pdf(pdf: bytes, *, trust_pems: list[bytes]) -> list[dict[str, Any]]:
    """Check each embedded signature. Trust roots are the public certs we stored."""
    assert_pdf(pdf)
    lib = _libs()
    roots = []
    for pem in trust_pems:
        try:
            roots.extend(lib["load_certs_from_pemder_data"](pem))
        except Exception:
            continue
    context = lib["ValidationContext"](trust_roots=roots, allow_fetching=False)
    try:
        reader = lib["PdfFileReader"](BytesIO(pdf))
        embedded = list(reader.embedded_signatures)
    except Exception:
        raise SignError("bad_pdf") from None
    out: list[dict[str, Any]] = []
    for sig in embedded:
        try:
            status = lib["validate_pdf_signature"](sig, context)
        except Exception:
            out.append(
                {
                    "field": str(getattr(sig, "field_name", "") or ""),
                    "intact": False,
                    "valid": False,
                    "trusted": False,
                    "tsa": False,
                    "signer": "",
                }
            )
            continue
        signer = ""
        try:
            signer = str(sig.signer_cert.subject.native.get("common_name", ""))
        except Exception:
            signer = ""
        out.append(
            {
                "field": str(getattr(sig, "field_name", "") or ""),
                "intact": bool(status.intact),
                "valid": bool(status.valid),
                "trusted": bool(status.trusted),
                "tsa": status.timestamp_validity is not None,
                "signer": signer,
            }
        )
    return out


def watermark_pdf(pdf: bytes, text: str) -> bytes:
    """Stamp a disclosure copy. The signed original is a different byte string."""
    assert_pdf(pdf)
    lib = _libs()
    reader = lib["PdfReader"](BytesIO(pdf))
    if not reader.pages:
        raise SignError("bad_pdf")
    box = reader.pages[0].mediabox
    width = float(box.width)
    height = float(box.height)
    packet = BytesIO()
    canvas = lib["canvas"].Canvas(packet, pagesize=(width, height))
    canvas.setFillColorRGB(0.45, 0.45, 0.45, alpha=0.35)
    canvas.saveState()
    canvas.translate(width / 2, height / 2)
    canvas.rotate(32)
    canvas.setFont("Helvetica", 26)
    canvas.drawCentredString(0, 0, (text or "DISCLOSURE")[:80])
    canvas.restoreState()
    canvas.save()
    packet.seek(0)
    stamp = lib["PdfReader"](packet)
    writer = lib["PdfWriter"]()
    for page in reader.pages:
        writer.add_page(page)
    for page in writer.pages:
        page.merge_page(stamp.pages[0])
    out = BytesIO()
    writer.write(out)
    data = out.getvalue()
    assert_pdf(data)
    return data
