# 2026-09-04 VQ-01 DMS ask envelope

Keywords: VQ-01, categoty, L0_CERTIFIED, POST /v1/chat/ask, certified, Wide_Fill, EPIC-019, dms#39, 8953922.60, fan-out

Main idea: DMS maps engine `badge=certified` to customer `L0_CERTIFIED` on `POST /v1/chat/ask`. Envelope tests pin corrected warehouse ranks (ELECTRONICS 8,953,922.60 / CHEMICALS 8,799,446.70 / FOOD_COLD 8,754,427.11) for `show top 3 categoty sales` and pack paraphrases. Synonyms live on Cortex pack `cq_top3_category_sales` (exact + synonyms, Cortex#125). Naive `JOIN inventory` inflates ~14.8x and ranks FOOD_DRY second -- the helper goes red on those magnitudes even if Cortex certifies them. Wide_Fill-class totals must not pass. DMS does not upgrade L2 `query_skill` to L0. E9-02 still demotes Wide_Fill SQL (`test_e9_02_ask_envelope.py`). Live HTTP skips when DMS is down. This file does not import CortexOS.

## Commands

| Command | Result |
|---------|--------|
| `pytest tests/test_vq01_ask_envelope.py tests/test_e9_02_ask_envelope.py -q` | HTTP envelope: L0 + oracle ranks; L2 not upgraded; fan-out/Wide_Fill rejected |
| live `DMS_VQ01_LIVE=1 pytest tests/test_vq01_ask_envelope.py::test_live_chat_ask_categoty_hits_oracle_envelope` | skip unless stack up; pins the same oracle |

Env: `DMS_SKIP_CONTROL_PLANE_TESTS=1`, `DMS_DEMO_FALLBACK=0`.

Does not prove: Cortex pack SQL uses DISTINCT (Cortex#125 YAML still fan-out JOIN), Excel Sales-sheet oracles, VQ-02 Studio register, C7-05 serve L2.
