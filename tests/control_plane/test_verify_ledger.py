"""verify-ledger CLI wiring."""

from __future__ import annotations

import pytest
from cortex_client import CortexClient
from dms_ledger.verify_cli import main


def test_verify_ledger_cli_reports_stub() -> None:
    code = main(["--base-url", "http://127.0.0.1:9"])
    assert code == 2


def test_client_verify_ledger_stub() -> None:
    client = CortexClient("http://127.0.0.1:9")
    with pytest.raises(NotImplementedError, match="sync-contract"):
        client.verify_ledger()
