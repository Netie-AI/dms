# Subagents findings -- DMS

PREFLIGHT for this goal: PARTIAL (cca-01..06 PRs; C7-05 blocked).

| Date | Topic | Keywords | Main idea | Path |
|------|-------|----------|-----------|------|
| 2026-09-04 | cca-06-eval-corpus | CCA-06, precision-on-answered, WRONG, R-0003, dms#138 | Stack-free corpus. Fail on planted L0 on abstain. Certified golden is trace-only. | `2026-09-04_cca-06-eval-corpus.md` |
| 2026-09-04 | cca-05-orchestrator | CCA-05, live_ask, cascade_path, before L0, dms#137 | Cascade after VQ, before Cortex. Ordinary SKU asks skip. Mid-stage abstain does not call Cortex. | `2026-09-04_cca-05-orchestrator.md` |
| 2026-09-04 | cca-04-geo-sea | CCA-04, SEA, geo_region_members, landed dim, dms#136 | Empty SEA pack by default. CERTIFY pack ∩ dim only. Extra proposed countries abstain. | `2026-09-04_cca-04-geo-sea.md` |
| 2026-09-04 | cca-03-asset-class | CCA-03, commercial, residential, encodings, dms#135 | Empty class encodings by default. CERTIFY only on landed dim. Missing encoding abstains before L0. | `2026-09-04_cca-03-asset-class.md` |
| 2026-09-04 | cca-02-sense-binder | CCA-02, lease, buy, housing-rent, bind_sense, ambiguous, dms#134 | Closed synonym pack. CERTIFY one sense or ABSTAIN. cascade_path will not ship L0 on uncertified sense. | `2026-09-04_cca-02-sense-binder.md` |
| 2026-09-04 | cca-01-constraint-schema | CCA-01, cascade, constraint_trace, CERTIFIED, ABSTAIN, dms#133 | Typed constraints + envelope stage trace. Later CERTIFIED after ABSTAIN is illegal. Missing schema refuses before L0. Rename require_certified_priors: def gate_* is banned outside cortex_client. | `2026-09-04_cca-01-constraint-schema.md` |
| 2026-09-04 | vq-01-oracle-ranks | VQ-01, categoty, ELECTRONICS, DISTINCT, fan-out, dms#39 | Envelope pins conservation ranks; 133M JOIN and Wide_Fill fail. Cortex DISTINCT sku SQL is on main. | `2026-09-04_vq-01-oracle-ranks.md` |
| 2026-09-04 | vq-02-studio-register | VQ-02, Studio, verified-queries, L0, space isolation | Space-scoped DuckDB `_verified_queries`; Studio POST gated; ask in-space L0, foreign miss. | `2026-09-04_vq-02-studio-register.md` |
| 2026-09-04 | f46-promote-on-truth | F46, bakeoff, precision-on-answered, coverage, badge, latency, EPIC-018 | Bakeoff will not pin on L2_VALIDATED or ms. Oracle wrong==0 + precision then coverage, or no pin. | `2026-09-04_f46-promote-on-truth.md` |
| 2026-09-04 | vq-01-ask-envelope | VQ-01, categoty, L0_CERTIFIED, chat/ask, Wide_Fill | HTTP mock: certified warehouse ranks map to L0_CERTIFIED; not Wide_Fill. Cortex match is PR #125. | `2026-09-04_vq-01-ask-envelope.md` |
| 2026-09-03 | lineage-05-company-space-race | lineage-05, playwright, company-default, space-switcher, fetchSpaces | selectOption empty is a no-op; fetchSpaces snaps null company-default back to Finance. 12/12 and 31/31 on ticket ports. | `2026-09-03_lineage-05-company-space-race.md` |
| 2026-09-03 | library-tree-duckdb-config | library, duckdb, read_only, lineage-05, 500 | /tree 500s if DuckDB mixes RO+RW on one file. List/preview/receipt stay write-mode. | `2026-09-03_library-tree-duckdb-config.md` |
| 2026-09-03 | playwright-cream-chrome | playwright, cream, graphite, ingest, duckdb-lock, lineage-05 | Cream locators fixed 10/10. Ingest 0: Cortex :8010 pid 32816 holds D:/DMS/data/dms_demo.duckdb. Do not kill :8010/:8090. | `2026-09-03_playwright-cream-chrome.md` |
| 2026-09-03 | lineage-03-library-node | lineage-03, library, promote-receipt, silver, gold, epic-024 | Silver/Gold folders on company-default Library only. Named Spaces hide promote nodes. No silver/gold row preview. Receipt numbers as received. | `2026-09-03_lineage-03-library-node.md` |
| 2026-09-03 | epic020-f0019-worktree | F-0019, worktree, epic-020, extract-only, db_connector, Netie-git | EPIC-020 lives in D:/DMS-epic020. Writing it on D:/DMS loses the connector. Netie .git empty-init: fetch + mixed reset, never add -A. | `2026-09-03_epic020-f0019-worktree.md` |
| 2026-09-03 | hostile-coverage | hostile, bronze-sheet, MULTI_TABLE, precision, coverage | Live pack 100% precision-on-answered (10/10), coverage 71.43% (10/14), 0 WRONG. BETA/KL/F32/RAG still abstain. | `2026-09-03_hostile-coverage.md` |
| 2026-09-03 | bronze-sync-hostile | accuracy, bronze, constructor, genie | Serving sync unblocked an earlier 6/11 L0 0 WRONG. Constructor --ask 5/5. Curated 10/14. | `2026-08-28_three-subagent-scale.md` |
| 2026-08-28 | delivery-excel-bakeoff | excel-mcp, copilot-prompt, bakeoff, constructor, epic-022 | Governed Excel prompt+playbook; bar chart SUM/axis0 match; Constructor bronze-only until sync; EPIC-022 blocked on coverage. | `2026-08-28_delivery-excel-bakeoff.md` |
| 2026-08-28 | three-subagent-scale | accuracy, surface, delivery, genie, constructor, excel-mcp | Curated pack 9/13 L0 0 WRONG. Constructor --ask 4/6. Hostile was 0/11. Cream hides Studio. | `2026-08-28_three-subagent-scale.md` |
