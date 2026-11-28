"""One-shot semantic model import (FEAT-SEMANTIC-IMPORT-01).

The flag defaults off. Callers that run with it off get
``SemanticImportDisabled`` and touch neither the warehouse nor the proposal
store.
"""

from dms_executor.semantic_import.flags import FLAG, SemanticImportDisabled, enabled

__all__ = [
    "FLAG",
    "SemanticImportDisabled",
    "approve_import_examples",
    "audit_records",
    "enabled",
    "export_space",
    "import_file",
    "parse_bytes",
    "parse_document",
    "reset_audit",
]

_SERVICE = {
    "approve_import_examples",
    "audit_records",
    "export_space",
    "import_file",
    "parse_bytes",
    "parse_document",
    "reset_audit",
}


def __getattr__(name: str) -> object:
    """Service imports stay off the ask path. The store module is the hot one."""
    if name not in _SERVICE:
        raise AttributeError(name)
    from dms_executor.semantic_import import service

    return getattr(service, name)
