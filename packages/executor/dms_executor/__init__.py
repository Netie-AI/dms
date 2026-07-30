"""Query execution — the only DMS package allowed to call duckdb.execute.

Manifest minting + signing + submit() live here. Path enforcement is Cortex's job.
"""

from __future__ import annotations

from typing import Any

from cortex_client import CortexClient
from cortex_contract.execution import PoolSpec, SubmitRequest
from dms_core.ports import ServingEnginePort

from dms_executor.manifest import (
    ManifestMinter,
    SessionAcl,
    SubmitError,
    classify_submit_error,
    reject_hostile_chat_sql,
    should_rement,
)


class Executor:
    """Serving engine + manifest-aware submit path."""

    def __init__(
        self,
        *,
        cortex: CortexClient | None = None,
        minter: ManifestMinter | None = None,
    ) -> None:
        self._cortex = cortex
        self._minter = minter or ManifestMinter()

    def execute(self, sql: str) -> Any:
        raise NotImplementedError("local duckdb.execute wiring lands with serving pool")

    def submit_sql(
        self,
        sql: str,
        acl: SessionAcl,
        *,
        reminted: bool = False,
    ) -> Any:
        reject_hostile_chat_sql(sql)
        if self._cortex is None:
            raise RuntimeError("CortexClient required for submit")
        manifest = self._minter.mint_manifest(acl)
        req = SubmitRequest(
            pool=PoolSpec(id=acl.pool_id),
            plan={"kind": "sql"},
            body={"sql": sql},
            manifest=manifest,
        )
        try:
            return self._cortex.submit(req)
        except Exception as exc:  # noqa: BLE001 — classify then re-raise
            err = classify_submit_error(exc)
            if should_rement(err.code) and not reminted:
                self._minter.invalidate(acl.session_id)
                return self.submit_sql(sql, acl, reminted=True)
            if err.code in {
                "manifest_signature_invalid",
                "path_not_allowed",
                "statement_not_allowed",
            }:
                raise SubmitError(err.code, err.detail) from exc
            raise SubmitError(err.code, err.detail) from exc


def get_serving_engine() -> ServingEnginePort:
    return Executor()


__all__ = [
    "Executor",
    "ManifestMinter",
    "SessionAcl",
    "get_serving_engine",
]
