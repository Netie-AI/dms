"""Manager governance (DMS-GOV-01).

Deterministic policy: roles, budgets, upload findings, and who may see a
question. It does not answer a question and it does not call a model provider.
OpenVault is read for usage and key quota. Routing stays there.

``DMS_GOV_CONSOLE`` defaults off. Only the exact digit ``1`` turns it on.
``true``, ``yes``, ``on``, and a padded `` 1 `` stay off.
"""

from __future__ import annotations

import os

FLAG = "DMS_GOV_CONSOLE"


def enabled() -> bool:
    """True only when ``DMS_GOV_CONSOLE`` is the digit ``1``."""
    return os.environ.get(FLAG, "0") == "1"
