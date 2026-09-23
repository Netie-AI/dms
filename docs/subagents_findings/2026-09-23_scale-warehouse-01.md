# SCALE-WAREHOUSE-01 (TB->PB design pack, not LIVE 1PB)

Keywords: SCALE-WAREHOUSE-01, warehouse, partition, Space ACL, grain cardinality, SKU, supplier, plant, lane, day, TB, PB, NOT LIVE 1PB, dms-237, EPIC-INSIGHTS-UX
Main idea: #237 is a docs/schemas pack. Partition facts by day+plant_id; Space ACL stays DR-0002 predicates (never space_id Hive forks). Map #232 grains; plant is not locations. Mark ROADMAP_NOT_LIVE. No founder lake remount without Platform GO. Not COMPLETE.

Ceiling: P-DMS-34 still blocks TB load (91-row writer lock). PB envelopes are roadmap only. Upgrade: Platform GO + measured TB lake + serving implementation. Epic stays INCOMPLETE.
