"""Query execution — the only DMS package allowed to call duckdb.execute.

Manifest minting + signing + submit() live here. Path enforcement is Cortex's job.
"""

from __future__ import annotations

import logging
from typing import Any

from cortex_client import CortexClient
from cortex_contract.execution import PoolSpec, SubmitRequest
from dms_core.ports import ServingEnginePort

from dms_executor.acl import (
    SessionContext,
    SourceGrant,
    intersect_space_grants,
    mint_manifest_for_session,
    resolve_session_acl,
)
from dms_executor.manifest import (
    ManifestMinter,
    SessionAcl,
    SubmitError,
    classify_submit_error,
    reject_hostile_chat_sql,
    should_rement,
)

logger = logging.getLogger(__name__)


class Executor:
    """Serving engine + manifest-aware submit path."""

    def __init__(
        self,
        *,
        cortex: CortexClient | None = None,
        minter: ManifestMinter | None = None,
        fetch_key_on_start: bool = False,
    ) -> None:
        self._cortex = cortex
        self._minter = minter or ManifestMinter()
        if fetch_key_on_start:
            self.startup()

    def startup(self) -> None:
        """Fetch OpenVault intermediate signing key into memory only."""
        self._minter.fetch_intermediate()
        logger.info("executor startup: intermediate signing key loaded (kid only logged)")

    def execute(self, sql: str) -> Any:
        raise NotImplementedError("local duckdb.execute wiring lands with serving pool")

    def mint_manifest(self, session: SessionContext | SessionAcl) -> Any:
        if isinstance(session, SessionAcl):
            return self._minter.mint_manifest(session)
        return mint_manifest_for_session(self._minter, session)

    def submit_sql(
        self,
        sql: str,
        session: SessionContext | SessionAcl,
        *,
        reminted: bool = False,
    ) -> Any:
        reject_hostile_chat_sql(sql)
        if self._cortex is None:
            raise RuntimeError("CortexClient required for submit")
        acl = session if isinstance(session, SessionAcl) else resolve_session_acl(session)
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
                "manifest_rejected",
            }:
                logger.error("security_event submit code=%s", err.code)
            raise SubmitError(err.code, err.detail) from exc


def get_serving_engine() -> ServingEnginePort:
    return Executor()


__all__ = [
    "Executor",
    "ManifestMinter",
    "SessionAcl",
    "SessionContext",
    "SourceGrant",
    "intersect_space_grants",
    "get_serving_engine",
    "resolve_session_acl",
]
