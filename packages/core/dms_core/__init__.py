"""DMS shared core — ports and domain types. No FastAPI, no CortexOS, no policy."""

from dms_core.ports import (
    CatalogPort,
    ModelProviderPort,
    ObjectStorePort,
    SecretsPort,
    ServingEnginePort,
)

__all__ = [
    "CatalogPort",
    "ObjectStorePort",
    "ModelProviderPort",
    "ServingEnginePort",
    "SecretsPort",
]

__version__ = "0.1.0"
