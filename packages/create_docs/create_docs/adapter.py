"""Import seam for the shared Create adapter. Do not copy it.

``_ADAPTER_MODULES`` is the rebase point. The implementation (tool registry,
prompt library, brand fields, judge, and ``generate``) lives in that module.
"""

from __future__ import annotations

import importlib
from types import ModuleType

_ADAPTER_MODULES = ("dms_core.create_generator",)


def governance() -> ModuleType:
    """The shared module. Callers use its ``generate`` and judge, not a local one."""
    return importlib.import_module(_ADAPTER_MODULES[0])
