# WORD-RULES-OUT-01

Keywords: WORD-RULES-OUT-01, DMS_NO_WORD_RULES, rank_texts, measure_tie, measure_unranked, keyword_routes, ontology synonyms

Main idea: Flag default 0. Off, the keyword locker in keyword_routes.py still runs, so the prompt, envelope, and route stay the same. On, measure linking calls rank_texts (embedder, else BM25) on the measure name, description, and synonyms stored on the ontology. Floor 0.5, top 3. One top score links that measure. A tie or nothing above the floor reconfirms and is not served. The AST guard fails if semantic_retrieve.py or generative_ask.py compares the question with `in` or runs a routing regex on it.

Offline count on the 52 questions, BM25, no embedder, no SQL: keyword lock 45, rank unique 46, same measure id 33, different id 7, reconfirm 6 (tie 2, under floor 4). Not a grade52 score. The armed on-vs-off run was not run. Not PASS.
