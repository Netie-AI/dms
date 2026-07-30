"""Smoke: mint → session_bind → ask against Cortex (C4-min live flip).

Requires Cortex :8010 + OpenVault :5000. Exit 0 on success.
Does not import CortexOS.
"""

from __future__ import annotations

import os
import sys
from uuid import uuid4

from cortex_client import CortexClient
from cortex_client.models import AskRequest
from dms_executor import Executor


def main() -> int:
    cortex_url = os.environ.get("CORTEX_URL", "http://127.0.0.1:8010")
    ov = os.environ.get("OPENVAULT_URL", "http://127.0.0.1:5000")
    print(f"CORTEX_URL={cortex_url} OPENVAULT_URL={ov}")
    cortex = CortexClient(cortex_url)
    exe = Executor(cortex=cortex)
    try:
        exe.startup()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN startup key fetch: {exc}")
    session_id = f"ses_smoke_{uuid4().hex[:12]}"
    try:
        acl = exe.demo_acl(session_id=session_id)
        # Align allowlist with Cortex warehouse tables when present
        acl.row_predicates = {
            "transactions": "TRUE",
            "inventory": "TRUE",
            "locations": "TRUE",
            "suppliers": "TRUE",
            "shipments": "TRUE",
            "alerts": "TRUE",
        }
        bind = exe.bind_session(acl)
        print(f"bind ok={getattr(bind, 'ok', None)} status={getattr(bind, 'status', None)}")
        ans = cortex.ask(
            AskRequest(
                question="Top 5 selling SKUs by revenue",
                session_id=session_id,
            )
        )
        print(f"ask abstained={ans.abstained} badge={ans.badge}")
        print((ans.answer or "")[:240])
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}")
        return 1
    finally:
        exe.close()
        cortex.close()


if __name__ == "__main__":
    raise SystemExit(main())
