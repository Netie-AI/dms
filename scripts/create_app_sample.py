"""Generate one inventory reorder app when a provider key is present.

Prints the missing key names and exits 2 when neither Cursor nor OpenVault
is configured. A run that returns a bundle is still not a PASS: the judge
result is printed and the caller decides.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_NEED_CURSOR = "CURSOR_API_KEY"
_NEED_OV = ("OPENVAULT_URL", "OPENVAULT_API_KEY", "OV_API_KEY", "DMS_OV_KEY")


def missing() -> list[str]:
    absent: list[str] = []
    if not os.environ.get(_NEED_CURSOR, "").strip():
        absent.append(_NEED_CURSOR)
    if not os.environ.get("OPENVAULT_URL", "").strip():
        absent.append("OPENVAULT_URL")
    if not any(os.environ.get(name, "").strip() for name in _NEED_OV[1:]):
        absent.append("OPENVAULT_API_KEY or OV_API_KEY or DMS_OV_KEY")
    return absent


def main() -> int:
    absent = missing()
    has_cursor = bool(os.environ.get(_NEED_CURSOR, "").strip())
    has_ov = bool(os.environ.get("OPENVAULT_URL", "").strip()) and any(
        os.environ.get(name, "").strip() for name in _NEED_OV[1:]
    )
    if not has_cursor and not has_ov:
        print("LIVE CHECK NOT RUN")
        print("missing: " + ", ".join(absent))
        return 2
    root = Path(__file__).resolve().parents[1]
    for part in (
        "apps/api",
        "packages/core",
        "packages/cortex_client",
        "packages/executor",
        "packages/ledger",
    ):
        path = str(root / part)
        if path not in sys.path:
            sys.path.insert(0, path)
    os.environ.setdefault("DMS_CREATE_APP", "1")
    os.environ.setdefault("DMS_CREATE_APP_SALESFORCE", "1")
    from dms_executor.create_app_run import generate_app

    catalog = [
        {
            "name": "inventory",
            "columns": [
                {"name": "sku", "type": "VARCHAR"},
                {"name": "on_hand", "type": "INTEGER"},
                {"name": "location_id", "type": "VARCHAR"},
            ],
        }
    ]
    key = (
        os.environ.get("OPENVAULT_API_KEY")
        or os.environ.get("OV_API_KEY")
        or os.environ.get("DMS_OV_KEY")
        or "ov_live"
    )
    if not key.startswith("ov_"):
        key = "ov_" + key
    result = generate_app(
        space_id=os.environ.get("DMS_CREATE_APP_SPACE", "demo"),
        goal="Inventory reorder app with a create purchase order action as a dry run",
        actor="sample",
        api_key=key,
        grants=["inventory"],
        catalog=catalog,
    )
    print("code", result.get("code"))
    print("ok", result.get("ok"))
    print("spend", result.get("spend"))
    print("judge", result.get("judge"))
    print("app_id", result.get("app_id"))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
