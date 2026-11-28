"""Upload and paste scan. Secrets from gitleaks rules, PII from ``pii.py``."""

from __future__ import annotations

import re

from dms_core.governance.secrets import redact_spans, secret_hits
from dms_core.pii import mask_payload

_PII_KIND = re.compile(r"DMSMASK_([a-z]+)_")


def _pii_kinds(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    masked = str(mask_payload(text=text).get("text") or "")
    if masked == text:
        return ()
    return tuple(sorted(set(_PII_KIND.findall(masked))))


def pii_kinds(text: str) -> tuple[str, ...]:
    """Kind names only (``email``, ``phone``). The value is not returned."""
    return _pii_kinds(text)


def scan_text(text: str) -> tuple[tuple[str, ...], str]:
    """Return finding types and a redacted copy.

    Finding types look like ``secret:github-pat`` or ``pii:email``.
    The redacted copy is for the redact policy. Callers that block must
    not send either the original or this copy to a model.
    """
    hits = secret_hits(text or "")
    secret_types = tuple(f"secret:{hit.rule_id}" for hit in hits if hit.rule_id != "unscanned_tail")
    if any(hit.rule_id == "unscanned_tail" for hit in hits):
        secret_types = (*secret_types, "secret:unscanned_tail")
    redacted = redact_spans(text or "", hits)
    kinds = _pii_kinds(redacted)
    pii_types = tuple(f"pii:{kind}" for kind in kinds)
    if kinds:
        redacted = str(mask_payload(text=redacted).get("text") or redacted)
    return secret_types + pii_types, redacted
