# DMS-ACC-HELDOUT-01

**Status:** Not PASS. Live OpenVault keys are absent on this VM. No live run. Not a live pass.

## What

The held-out tally sits on `scripts/bench/bird`. It does not start a second runner and it does not change `POST /v1/chat/ask`.

Official EX still counts a matching row set as CORRECT. Held-out correct requires path `ai`. A rule-served match is `rule_fail`: a failure, and not a WRONG number. A row set that disagrees with gold stays WRONG on every path. The report line starts with WRONG.

`heldout-check` is four synthetic databases and a scripted stub. It does not download BIRD or Spider and it does not call OpenVault. CI runs that command. `--provider stub` abstains and does not invent SQL. `--provider live` calls OpenVault with role `sql`, then `escalate`, and does not send a provider or a model id. The schema text is names and types only. A missing URL or `ov_` key exits 2 and writes no score.

Spider 2.0-lite is MIT in `xlang-ai/Spider2`. The loader reads a local unpack. It does not download the repo or the SQLite zip. Cloud databases are excluded. A question with no gold SQL is unscored, stays in the denominator, and is not WRONG. That is not a Spider leaderboard score.

## Stub numbers

Command: `python scripts/bench/bird/__main__.py heldout-check`

Data: four synthetic SQLite databases (shop, yard, dock, mill), eight questions, scripted replies. Not Mini-Dev. Not Spider.

BEFORE, official EX, rule-served match counted as CORRECT: WRONG=2 CORRECT=3 ABSTAIN=3 n=8 databases=4

AFTER, held-out, that match is rule_fail: WRONG=2 heldout_correct=2 rule_fail=1 ABSTAIN=3 n=8 databases=4 heldout_ex=0.2500

Stages on the failures: schema_retrieval=1 ontology_mapping=1 sql_generation=1 self_correction=1 rule_served=1 unknown=1

WRONG did not move. The tally change is the rule-served match leaving the correct count. No ask-path edit was landed: there is no live failure class to fix, and a guessed prompt change would be an unmeasured claim.

## Ceiling

No live Claude route was called. `OPENVAULT_URL`, `OPENVAULT_API_KEY`, and `CORTEX_API_KEY` were unset. A live Mini-Dev or Spider number is not this run.
