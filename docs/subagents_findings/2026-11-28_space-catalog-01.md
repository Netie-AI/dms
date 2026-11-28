# FDE-CORTEX-SPACE-CATALOG-01

Keywords: space_catalog, DMS_SPACE_CATALOG, space_catalog_handoff, metric chooser, session manifest

Main idea: The session manifest carries grants but not the Space ontology, so the chooser stays on the demo catalog. `space_catalog` (objects, links, measures, definitions, granted_tables) is attached beside the signed bytes. It is not in `canonical_manifest_bytes`. Flag `DMS_SPACE_CATALOG` defaults off. The demo Space never gets the key. An empty measure list is a generative handoff, not a chooser reconfirm. Stub 52 flag off and on: correct=43 wrong=0 reconfirm=5 ai=0 rule=43. Not PASS.
