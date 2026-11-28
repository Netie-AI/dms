"""SCIM 2.0 directory provisioning (SCIM-01).

``DMS_SCIM`` defaults off. Only the exact digit ``1`` turns it on.
``true``, ``yes``, ``on``, and a padded `` 1 `` stay off. Off, ``/scim/v2``
is not mounted.

Mapping is configuration a manager saves. This package has no per-company
role table.
"""

from __future__ import annotations

import os

FLAG = "DMS_SCIM"


def enabled() -> bool:
    """True only when ``DMS_SCIM`` is the digit ``1``."""
    return os.environ.get(FLAG, "0") == "1"
