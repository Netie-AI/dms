# ONTOLOGY-MULTIGRAIN-02 (#254) -- mg_sku_plant granted path or named missing_join/plant

Keywords: ONTOLOGY-MULTIGRAIN-02, mg_sku_plant, missing_join, plant, ungranted shipments, grantable, dms-254, KEEP_HOLD

Main idea: After #249, live bar (1) was 3/4. `mg_sku_plant` (shipping cost by SKU and plant) compiled through `shipments` then ABSTAINED `validate:ungranted:shipments` on the Finance grant (no shipments). Compile is now grant-aware: emit a join the Space may read, or ABSTAIN `missing_join` naming `plant`. Rebased onto GEN-03 #194 @ `9b29c565`. Not #178 COMPLETE. Bar (1) stays KEEP_HOLD.

## Wiring

- Finance grant: locations, inventory, transactions, suppliers -- no shipments.
- Ops grant: locations, inventory, shipments.
- `shipping_cost_myr` grain is shipment. SKU+plant SQL cited `shipments`.
- Validate mapped that to a generic ungranted reason; customer text did not name `missing_join` / plant.

## What landed

- `Ontology.compile_grains(..., grantable=)` refuses `missing_join` when every path to a grain cites an ungranted table
- `try_compile_multi_grain` + `_try_multi_grain_envelope` pass the Space grant
- Fallback: `validate:ungranted:...` on a multi-grain ask is rewritten to `missing_join` naming the grain
- Tests: Finance shipping-cost named ABSTAIN; Ops shipping-cost granted path; Finance stock-value granted plant path through inventory+locations

## What this does not prove

- Platform bar (1) PASS / #178 COMPLETE / 1PB LIVE
- Live Studio re-prove (Platform after Formal GREEN + Studio >= oid)
