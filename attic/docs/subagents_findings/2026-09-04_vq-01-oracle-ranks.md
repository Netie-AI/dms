# 2026-09-04 VQ-01 oracle ranks on envelope

Keywords: VQ-01, categoty, ELECTRONICS, DISTINCT, fan-out, L0_CERTIFIED, dms#39, dms#130

Main idea: Customer envelope for `show top 3 categoty sales` must pin conservation oracles ELECTRONICS 8,953,922.60; CHEMICALS 8,799,446.70; FOOD_COLD 8,754,427.11. Naive JOIN inventory (133M / FOOD_DRY) and Wide_Fill class fail the helper. Cortex main SQL is DISTINCT sku,category. Replaces invented 125000.50 fixture from #128. Conflicts on #130 resolved by branching from current origin/main.

## Commands

| Command | Result |
|---------|--------|
| `pytest tests/test_vq01_ask_envelope.py tests/test_e9_02_ask_envelope.py -q` | oracle helper + E9-02 still demote Wide_Fill |
