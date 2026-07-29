"""DMS shared core — ports, compliance gate, types. No FastAPI, no CortexOS."""

from dms_core.compliance import compliance_gate
from dms_core.ports import (
    CatalogPort,
    ModelProviderPort,
    ObjectStorePort,
    SecretsPort,
    ServingEnginePort,
)

__all__ = [
    "compliance_gate",
    "CatalogPort",
    "ObjectStorePort",
    "ModelProviderPort",
    "ServingEnginePort",
    "SecretsPort",
]

__version__ = "0.1.0"
