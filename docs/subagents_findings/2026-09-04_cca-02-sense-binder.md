# 2026-09-04 CCA-02 sense binder

Keywords: CCA-02, sense, lease, buy, housing-rent, bind_sense, ambiguous, dms#134

Main idea: Closed synonym pack certifies lease/buy/housing-rent or abstains. Phrase match uses whole-word boundaries so "residential rental" is not "residential rent". Ambiguous or missing sense on cascade_path demotes L0 numbers. Does not invent a sense. Orchestrator before L0 is still CCA-05.

## Commands

| Command | Result |
|---------|--------|
| `pytest tests/test_sense_binder.py tests/test_constraint_cascade.py -q` | certify + abstain + no L0 on ambiguous |

Does not prove: live_ask cascade (CCA-05), geo/class encodings (CCA-03/04), eval corpus (CCA-06).
