"""Exactly five abstracted ports — each needs a stated swap scenario before use.

1. catalog        — schema / table registry (swap: Postgres → Glue / Unity)
2. object_store   — blob/parquet home (swap: local FS → MinIO → S3)
3. model_provider — LLM calls (swap: OpenVault → Azure OpenAI)
4. serving_engine — SQL/lake execute (swap: DuckDB → warehouse)
5. secrets        — key material (swap: env → OpenVault)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CatalogPort(Protocol):
    def list_tables(self, schema: str | None = None) -> list[str]: ...


@runtime_checkable
class ObjectStorePort(Protocol):
    def put(self, key: str, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...


@runtime_checkable
class ModelProviderPort(Protocol):
    def complete(self, prompt: str, **kwargs: Any) -> str: ...


@runtime_checkable
class ServingEnginePort(Protocol):
    def execute(self, sql: str) -> Any: ...


@runtime_checkable
class SecretsPort(Protocol):
    def get(self, name: str) -> str | None: ...
