# Demo company seed

Keywords: harborline, seed_company, buyer walk, gold.json, schema catalog, late delivery, stockout

Main idea: Harborline is a synthetic distributor, 50 tables, seed 20261128, full scale above 1M rows. Gold answers are the SQL result on that scale. CI rebuilds `--scale small` and only checks that the same SQL still runs. Inventory snapshots are stored counts, not a receipt ledger. Schema retrieval ranks the late-delivery and stockout tables when max_tokens is 4000. At the 480 default the ranker appends table descriptions after the fit check and the prompt becomes an empty SCHEMA. No ask-path change.

Not PASS. Not a scored pack.
