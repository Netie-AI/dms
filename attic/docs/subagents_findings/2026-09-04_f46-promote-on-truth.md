# 2026-09-04 F46 bakeoff promote-on-truth

Keywords: F46, bakeoff, promote, precision-on-answered, coverage, badge, latency, EPIC-018, dms#35

Main idea: `scripts/bakeoff_l2_models.py` used to pin the fastest `L2_VALIDATED` model (`winners.sort` by `ms`). F46: `select_promote_winner` requires `wrong==0` plus `precision_on_answered` and `coverage`. No oracle fields => no pin, exit 2. Does not enable serve L2 (`DMS_L2_ENABLED` default stays off).

## Commands

| Command | Result |
|---------|--------|
| `pytest tests/test_bakeoff_l2_models.py -q` | fastest badge-only is None; slower 0-wrong oracle wins |

Does not prove: live FreeRoute bake, C7-05 serve L2, epic #35 COMPLETE.
