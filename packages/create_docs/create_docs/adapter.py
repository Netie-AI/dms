"""Call the shared Create adapter. Do not copy it.

bc-bd4acd20 owns the implementation: tool registry, Anthropic skills
(pptx, docx, xlsx, pdf), the versioned prompt library, few-shot attachments,
the tenant brand kit, and OpenVault or Cursor routing. This module is the
import seam ``generate(spec, data_context, style) -> artifact + cost``.
When that module lands, add its name to ``_ADAPTER_MODULES`` and rebase.
Nothing here opens a model socket or draws a slide.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

# Rebase seam. First hit with a callable ``generate`` wins.
_ADAPTER_MODULES = (
    "dms_executor.create_governance",
    "dms_executor.create_provider",
)

Generate = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]
Sign = Callable[[dict[str, Any]], dict[str, Any]]

_GENERATE: Generate | None = None
_SIGN: Sign | None = None


class ProviderStop(Exception):
    """The adapter refused the call. ``budget_exceeded`` is OpenVault HTTP 402."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AdapterMissing(RuntimeError):
    """The shared adapter is not importable yet. No file was invented."""

    def __init__(self, what: str) -> None:
        self.code = "adapter_missing"
        super().__init__(what)


def bind_generate(fn: Generate | None) -> None:
    """Tests inject a stub. Production leaves this unset and imports the adapter."""
    global _GENERATE
    _GENERATE = fn


def bind_sign(fn: Sign | None) -> None:
    global _SIGN
    _SIGN = fn


def resolve_generate() -> Generate:
    if _GENERATE is not None:
        return _GENERATE
    found = _find("generate")
    if found is None:
        raise AdapterMissing("generate")
    return found


def resolve_sign() -> Sign | None:
    """``sign_request`` when the adapter exports it. None means not landed."""
    if _SIGN is not None:
        return _SIGN
    return _find("sign_request")


def _find(name: str) -> Callable[..., Any] | None:
    for module_name in _ADAPTER_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    return None
