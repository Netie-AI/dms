"""HTTP facade over openapi-python-client output in ``cortex_client.generated``.

Regenerate with ``just sync-contract``. Do not hand-edit ``generated/``.
"""

from __future__ import annotations

from typing import Any

import httpx
from cortex_contract.execution import Manifest as ContractManifest
from cortex_contract.execution import QueryResult as ContractQueryResult
from cortex_contract.execution import SubmitRequest as ContractSubmitRequest

from cortex_client.generated import Client as GeneratedClient
from cortex_client.generated.api.contract import (
    ask as ask_api,
    ledger_append as ledger_append_api,
    ledger_verify as ledger_verify_api,
    submit as submit_api,
    tool_registry as tool_registry_api,
)
from cortex_client.generated.models.ask_request import AskRequest as GenAskRequest
from cortex_client.generated.models.ledger_append_request import (
    LedgerAppendRequest as GenLedgerAppendRequest,
)
from cortex_client.generated.models.ledger_verify_request import LedgerVerifyRequest
from cortex_client.generated.models.manifest import Manifest as GenManifest
from cortex_client.generated.models.pool_spec import PoolSpec as GenPoolSpec
from cortex_client.generated.models.submit_request import SubmitRequest as GenSubmitRequest
from cortex_client.generated.models.submit_request_body import SubmitRequestBody
from cortex_client.generated.models.submit_request_plan import SubmitRequestPlan
from cortex_client.models import (
    AskRequest,
    AskResponse,
    LedgerAppendRequest,
    LedgerAppendResponse,
    LedgerVerifyResponse,
    ToolRegistryResponse,
)

CONTRACT_MAJOR = 1


def _to_gen_manifest(m: ContractManifest) -> GenManifest:
    return GenManifest.from_dict(m.model_dump(mode="json"))


def _to_gen_submit(req: ContractSubmitRequest) -> GenSubmitRequest:
    body = GenSubmitRequest(
        pool=GenPoolSpec.from_dict(req.pool.model_dump(mode="json")),
        plan=SubmitRequestPlan.from_dict(req.plan),
        body=SubmitRequestBody.from_dict(req.body),
        manifest=_to_gen_manifest(req.manifest),
    )
    return body


class CortexClient:
    """Pin base_url to a Cortex image/tag via compose; contract major must be 1."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = GeneratedClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            raise_on_unexpected_status=True,
        )

    def close(self) -> None:
        self._client.get_httpx_client().close()

    def __enter__(self) -> CortexClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def ask(self, req: AskRequest) -> AskResponse:
        body = GenAskRequest.from_dict(req.model_dump(mode="json"))
        parsed = ask_api.sync(client=self._client, body=body)
        if parsed is None:
            raise RuntimeError("ask: empty response")
        if hasattr(parsed, "to_dict"):
            return AskResponse.model_validate(parsed.to_dict())
        raise RuntimeError(f"ask failed: {parsed!r}")

    def submit(self, req: ContractSubmitRequest) -> ContractQueryResult:
        body = _to_gen_submit(req)
        parsed = submit_api.sync(client=self._client, body=body)
        if parsed is None:
            raise RuntimeError("submit: empty response")
        if hasattr(parsed, "to_dict"):
            return ContractQueryResult.model_validate(parsed.to_dict())
        raise RuntimeError(f"submit failed: {parsed!r}")

    def ledger_append(self, req: LedgerAppendRequest) -> LedgerAppendResponse:
        body = GenLedgerAppendRequest.from_dict(req.model_dump(mode="json"))
        parsed = ledger_append_api.sync(client=self._client, body=body)
        if parsed is None:
            raise RuntimeError("ledger_append: empty response")
        data = parsed.to_dict() if hasattr(parsed, "to_dict") else {}
        return LedgerAppendResponse.model_validate(
            {
                "entry_id": data.get("id") or data.get("entry_id") or "",
                "hash": data.get("hash") or data.get("entry_hash") or "",
            }
        )

    def verify_ledger(self) -> LedgerVerifyResponse:
        body = LedgerVerifyRequest()
        parsed = ledger_verify_api.sync(client=self._client, body=body)
        if parsed is None:
            raise RuntimeError("verify_ledger: empty response")
        data = parsed.to_dict() if hasattr(parsed, "to_dict") else {}
        return LedgerVerifyResponse.model_validate(
            {
                "ok": bool(data.get("ok", data.get("valid", False))),
                "first_break": data.get("first_break") or data.get("break_at"),
                "checked": data.get("checked") or data.get("entries_checked"),
            }
        )

    def tool_registry(self) -> ToolRegistryResponse:
        parsed = tool_registry_api.sync(client=self._client)
        if parsed is None:
            raise RuntimeError("tool_registry: empty response")
        data = parsed.to_dict() if hasattr(parsed, "to_dict") else {"tools": []}
        return ToolRegistryResponse.model_validate(data)
