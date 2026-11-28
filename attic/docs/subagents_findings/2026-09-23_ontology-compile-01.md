# ONTOLOGY-COMPILE-01 (#234) -- ranked where-paths + importance

Keywords: ONTOLOGY-COMPILE-01, where-paths, importance, multi-join, supply-chain grains, missing_join, missing_metric, bind_plan, dms-234

Main idea: When an ask spans >=2 of sku/supplier/plant/lane/day, ontology compile ranks where-paths by importance (shortest verified many-to-one = 1) and emits SQL, or ABSTAIN naming the missing join/metric. bind_plan is not the confident path. Not #178 COMPLETE.

## What landed

- `WherePath` + `Ontology.rank_where_paths` / `compile_grains` in `packages/executor/dms_executor/ontology.py`
- Grain aliases: plant -> plant or location; lane -> lane or shipment; day undeclared -> missing_join
- `try_compile_multi_grain` on GEN-01 miss before bind_plan
- Tests: `tests/test_ontology_compile.py`

## What this does not prove

- #232 grain objects themselves (SKU-supplier-plant-lane-day tables)
- Platform live prove / climb counts
- #178 COMPLETE / 1PB LIVE
- #194 ask_path containment / #233 FreeRoute / #235 include-exclude receipt

## Parallel seats to avoid

#232 grains, #235 audit envelope, #238 refuse path, #194 GEN-03 ask_path. This seat stayed on compile.
