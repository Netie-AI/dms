# 2026-09-13 VQ-04 harden planted refuse after #175 WRONG2

Keywords: VQ-04, trap_delayed_count, trap_how_full_synonym, L1, certified_queries, route_to_metric, dms-176, EPIC-019 incomplete
Main idea: VQ-04 #176. Live WRONG2 was Cortex L1 on two planted refuse phrases, not pack exact-match. DMS exact-phrase refuse before cortex.ask. Do not green traps. Not COMPLETE.

## Cortex leak (certify boundary)

`cq_capacity_utilisation` has no certified synonym. Cortex `vocabulary.py` rewrites `how full` to `capacity utilisation`, then `route_to_metric` matches L1.

`delayed_incoming_per_wh` is TARGET, not certified. Cortex regex `delayed` + `per` + `warehouse` compiles `count_by_destination`.

## DMS

Exact `_norm` of the two curated_ceo phrases. Not product regex. VQ-03 LAYER lifts untouched. Tests use an L1 FakeCortex (the live failure).

Does not prove: Platform `--live` re-score. Does not close tickets.
