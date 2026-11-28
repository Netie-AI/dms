# UX-GRANT-DATA-01 grant ask-back

PREFLIGHT: HIT. `2026-11-26_onto-gaps-01.md` files a proposed measure and does not serve it. `2026-11-27_scale-miss-01.md` asks back without a phrase table. This card follows both.

## What was learned

`unknown_measure` and the other scope-miss heads (`no_declared_measure`, `ungranted`, `ungranted_table`, `sql_relation_not_granted`, `unknown_column`, `unknown_object`, `missing_metric`) mean the granted scope cannot answer. The try-instead chips do not grant a table.

`DMS_GRANT_ASKBACK` defaults off. Unset, `0`, and `true` are off. Only `1` enables it. Off, `live_ask` does not import the card module, so the envelope stays byte-identical.

On, the card ranks schema text and ontology measure text with `rank_texts` (embeddings when the OpenVault embedder returns one vector per text, otherwise BM25). The query is the question plus the measure token from the abstain reason. There is no keyword table and no regex on the question text.

A connected ungranted column wins as a grant card. A granted column with no declared measure is a define card. Nothing ranked is a missing card that names the gap and offers connect-a-source. The envelope stays an abstain: no rows, no values, no `sql_used`, no drillthrough. The preview SQL is not executed.

`consider` does not grant. `POST /v1/chat/grant-askback` with `allow: true` grants only when the actor holds a steward grant, through `grant_tables` (the same book as `POST /sources/{id}/grants`), which writes the audit. Then the original question runs through `live_ask` again. `allow: false` or a denied steward is 403 and writes no grant. A token grants once.

`define_measure` stores a proposed metric (`served` false, source `grant-askback`) and a verified-example row with provenance `grant_askback_proposed`. Lookup and few-shot skip that provenance. Steward-adjusted examples stay few-shot and still do not replay.

## Evidence

- `tests/test_grant_askback_01.py`: 10 passed. Flag off is the same object. Flag on fake stack shows the card and no digits. Approval re-runs after the audit row. No auto-grant. A denied steward cannot grant. A proposal is not a lookup hit.
- `tests/test_studio_adjust_01.py`: 10 passed after few-shot kept `steward_adjusted`.
- `apps/ui` vitest `AnswerMessage.test.ts`: 7 passed. The card renders the sentence and no figure.
- grade52 `--main` at `6d11dc9`, flag off and flag on, same line: correct=43 wrong=0 reconfirm=5 clarify=0 abstain=0 refusal_ok=4, paths ai=0 rule=43. Rule-served did not grow.
- Armed grade52 was not run. Not PASS.

## Reuse

Hook the end of `Executor.live_ask` only when the env digit is `1`. Rank with `rank_texts`. Grant with `grant_tables`. File a proposal with `store_proposal` and `save_proposed_example`. Do not add the card key to the PII safe list; scan it with `mask_unknown_keys`.
