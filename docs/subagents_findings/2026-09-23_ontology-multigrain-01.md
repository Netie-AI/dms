# ONTOLOGY-MULTIGRAIN-01 (#249) -- try_compile before one-grain GEN-01 plan

Keywords: ONTOLOGY-MULTIGRAIN-01, where-paths, multi-grain, generative_ask, kind=plan short-circuit, missing_join, bind_plan, dms-249, KEEP_HOLD

Main idea: #234 compile existed but live ranking filled a one-grain ontology_plan first, so try_compile_multi_grain (gated on kind != plan) never ran. Move multi-grain compile before GEN-01 plan/SQL; expose where_paths; stamp ontology_compile:where+importance; bind_plan stays non-confident. Rebased onto SC-ONTOLOGY-01 #232 @ `936d810`. Not #178 COMPLETE.

## Wiring (verified in code, not only the labeled hypothesis)

- `maybe_generative_ask` overlayed Insights ranking onto miss, producing kind=plan with group_by product.sku.
- `try_compile_multi_grain` lived inside `if kind != "plan"`.
- Live KEEP_HOLD: sku+plant, sku+day, supplier+sku, sku+lane all L2 sku-only; day did not ABSTAIN.

## What landed

- Multi-grain attempt after compute-unsure, before sql/plan short-circuit
- Envelope `where_paths` when that path wins
- Intent lock then ranked plan measure (shipping cost synonym of shipment cost)

## What this does not prove

- Platform bar (1) PASS / #178 COMPLETE / 1PB LIVE
- #232 grain tables themselves (landed separately @ `936d810`; this seat is ask-path order)
