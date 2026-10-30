---
keywords: [pytest, skip-control-plane, 831, suite-subagents, Docker, dms-116]
main_idea: DMS skip-control-plane pytest 831 passed, 0 failed, 1 skipped in 32 min. Docker stayed off. Live Chat still unproven (:8090 down). #116 stays open.
---

# DMS skip-CP suite green -- 2026-09-11

PREFLIGHT: HIT `suite-subagents`, `2026-09-11_suite-support-probe`.

[DMS pack](41ba9bbd-e632-4164-bd0e-18b1e23e14be)

```
D:\DMS\.venv\Scripts\python.exe -m pytest tests -q --tb=line -rfE --ignore=tests/control_plane -p no:cacheprovider
```

Env: `DMS_SKIP_CONTROL_PLANE_TESTS=1` `DMS_DEMO_FALLBACK=0`

**831 passed, 1 skipped, 0 failed** in 1941.82s (0:32:21). Log `D:\DMS\.tmp\pytest_skip_cp.txt`.

Does not prove control_plane (Docker off, R-0015). Does not prove live `POST /v1/chat/ask` (`:8090` down). Close nothing.

Warnings only: Starlette TestClient httpx deprecation; `test_extract_lab_bootstrap.py` maxsplit positional (path printed as E:\DMS in the warning).
