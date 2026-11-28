"""Hostile HTTP stress against DMS API — expect safe failures, never crash.

Uses TestClient (in-process) so no live server required.
Does not import CortexOS. Does not write Excel outbound.
"""

from __future__ import annotations

import io
import sys

from fastapi.testclient import TestClient

from dms_api.app import create_app
from dms_api.settings import get_settings


def main() -> int:
    import os

    os.environ.setdefault("DMS_ASK_MODE", "demo")
    os.environ.setdefault("DMS_DEMO_FALLBACK", "1")
    get_settings.cache_clear()
    client = TestClient(create_app())
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            failures.append(f"{name}: {detail}")
            print(f"FAIL {name} {detail}")
        else:
            print(f"ok   {name}")

    # Health
    r = client.get("/health")
    check("health", r.status_code == 200, str(r.status_code))

    # Flood demo ask
    for i in range(12):
        r = client.post("/v1/chat/ask", json={"question": f"stress {i} top skus"})
        check(f"ask_{i}", r.status_code in {200, 403, 429, 503}, f"status={r.status_code}")

    # Empty / hostile ask bodies
    r = client.post("/v1/chat/ask", json={"question": ""})
    check("ask_empty", r.status_code in {422, 400}, str(r.status_code))
    r = client.post("/v1/chat/ask", json={"question": "x" * 20000})
    check("ask_huge", r.status_code in {200, 403, 413, 422, 503}, str(r.status_code))

    # Bad drillthrough
    r = client.post("/v1/chat/drillthrough", json={"token": "not-a-token"})
    check("drill_bad", r.status_code in {400, 502, 503}, str(r.status_code))
    r = client.post("/v1/chat/drillthrough", json={})
    check("drill_empty", r.status_code in {422, 400}, str(r.status_code))

    # Library injection paths
    for table in ("transactions;drop", "../etc/passwd", "nope", "transactions"):
        r = client.get(f"/v1/library/warehouse/{table}/preview?limit=5")
        check(
            f"wh_{table[:12]}",
            r.status_code in {200, 404, 422},
            str(r.status_code),
        )

    r = client.get("/v1/library/data-map")
    check("data_map", r.status_code == 200, str(r.status_code))

    # Studio malformed
    r = client.post("/v1/studio/ingest", files={"file": ("x.bin", b"\x00\x01notcsv", "application/octet-stream")})
    check("ingest_junk", r.status_code in {200, 400, 403, 422}, str(r.status_code))
    r = client.post(
        "/v1/studio/ingest",
        files={"file": ("ok.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
    )
    check("ingest_csv", r.status_code in {200, 403}, str(r.status_code))

    # Spaces unknown
    r = client.post("/v1/chat/ask", json={"question": "hi", "space_id": "sp_does_not_exist"})
    check("ask_bad_space", r.status_code in {404, 200, 403}, str(r.status_code))

    print("---")
    if failures:
        print(f"{len(failures)} failures")
        return 1
    print("stress ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
