"""verify-ledger CLI wiring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from cortex_client.models import LedgerVerifyResponse
from dms_ledger.verify_cli import main


def test_verify_ledger_cli_ok() -> None:
    mock = MagicMock()
    mock.verify_ledger.return_value = LedgerVerifyResponse(ok=True, checked=3)
    with patch("dms_ledger.verify_cli.CortexClient", return_value=mock):
        assert main(["--base-url", "http://127.0.0.1:9"]) == 0


def test_verify_ledger_cli_break() -> None:
    mock = MagicMock()
    mock.verify_ledger.return_value = LedgerVerifyResponse(
        ok=False, first_break="entry-2"
    )
    with patch("dms_ledger.verify_cli.CortexClient", return_value=mock):
        assert main(["--base-url", "http://127.0.0.1:9"]) == 1
