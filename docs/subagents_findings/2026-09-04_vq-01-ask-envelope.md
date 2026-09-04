# 2026-09-04 VQ-01 DMS ask envelope

Keywords: VQ-01, categoty, L0_CERTIFIED, POST /v1/chat/ask, certified, Wide_Fill, EPIC-019, dms#39

Main idea: DMS maps engine `badge=certified` to customer `L0_CERTIFIED` on `POST /v1/chat/ask`. The HTTP test mocks Cortex returning warehouse `transactions` JOIN `inventory` ranks for `show top 3 categoty sales`. That is the asset-declared scope (Cortex pack), not Excel Sales-sheet oracles. Wide_Fill-class totals must not appear. E9-02 still demotes when executed SQL cites Wide_Fill (sibling `test_e9_02_ask_envelope.py`). Cortex match-before-unknown-noun is PR #125; this file does not import CortexOS.

## Commands

| Command | Result |
|---------|--------|
| `pytest tests/test_vq01_ask_envelope.py -q` | HTTP envelope: badge L0_CERTIFIED, 3 ranked rows, not Wide_Fill |

Env: `DMS_SKIP_CONTROL_PLANE_TESTS=1`, `DMS_DEMO_FALLBACK=0`.

Does not prove: live Cortex warehouse numbers, Excel Sales oracles, VQ-02 Studio register, C7-05 serve L2.
