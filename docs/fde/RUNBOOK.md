# Forward-deployed engineer runbook: land DMS at a new company

Audience: the Netie forward-deployed team. Goal: take a company from "here is our database" to measured, governed answers, and hand over a monitored tenant. Every step names the route or script it uses on main and what "done" means. Where main has no route yet, this page says so instead of inventing one.

Companion pieces:
- `scripts/verify/fde_scenarios.py` and `tests/fde_scenarios/` (FDE-SCENARIOS-01): five practice companies on four engines. Rehearse here before touching a customer.
- `scripts/verify/fde_land.py` (FDE-LAND-01, #506): walks connect, ontology and ask with receipts against one warehouse.
- `docs/NEXT_COMPANY_ONTOLOGY_SCALE.md`: what to copy from the demo and what not to.

House rules that apply on every engagement:
- **WRONG = 0.** A confident wrong number is the only unrecoverable failure. Abstain, or reconfirm-by-Yes, is fine.
- **No hand-written word or phrase rules** to make a question pass. Tune with data: verified examples, ontology, and the model choice in OpenVault.
- **Never claim a PASS you did not measure.** Every number in a handover comes from a run artifact.
- **Flags default off.** Turn on per tenant and write down which flags you turned on.
- **Model routing lives only in OpenVault.**

---

## 0. Before the first call (one day ahead)

1. Rehearse on the practice packs. Pick the company closest to the customer's domain (retail, manufacturing, finance_ar, hr or saas) and the engine closest to theirs:
   ```bash
   python scripts/verify/fde_scenarios.py --self-test
   FDE_SOURCE_PASSWORD=... python scripts/verify/fde_scenarios.py --load postgresql --user <loader> --grant-readonly <ro_role> --company retail
   FDE_SOURCE_PASSWORD=... python scripts/verify/fde_scenarios.py --fidelity postgresql --user <ro_role> --expect-keys --company retail
   ```
   Done when fidelity prints `gold 17 ok / 0 mismatch / 0 error` and the PK/FK counts match.
2. Confirm their engine is supported (see the table below). If it is not, stop and raise it now, not on site.
3. Send the access request in the security section (read-only login, network path, PII list).

### Engine support on main

| Engine | `kind` | Notes |
|---|---|---|
| PostgreSQL | `postgresql` | SELECT-only login. Key discovery for a SELECT-only login needs FDE-PG-KEYS-01 (#545); without it, check the ontology for missing links. |
| MySQL / MariaDB | `mysql` | MariaDB uses the same `kind`. Measured on MariaDB 11.8. |
| SQL Server / Azure SQL | `sqlserver` | ODBC Driver 18 on the DMS host. Encrypt is on by default; leave it on for Azure. |
| CSV / Excel | Studio upload | `POST /v1/studio/ingest`, `/ingest-batch`. Keys are proposed by profiling (#521/#533). |
| SQLite, Oracle, Snowflake, BigQuery, Databricks, Redshift | none yet | Export to CSV/Parquet, or wait for the connector. Do not promise these live. |

All engines land in DuckDB bronze and every answer runs there (extract-only, DR-0005). The model writes one dialect, DuckDB, whatever the source.

## 1. Connect (day 1, morning)

1. **Create the tenant Space**: `POST /v1/spaces` with the company name. Record the `space_id` in the tenant sheet.
2. **Credential custody**:
   - Preferred: the customer's DBA puts the read-only password into the tenant's OpenVault. DMS receives an OpenVault credential reference through `POST /v1/connect/sources` (flag `DMS_CONNECT_API`; the connect book is process memory today, so re-connect after a restart). A raw secret in that body is rejected.
   - Fallback: `POST /v1/studio/sources/sql` with `{kind, host, port, database, user, password, tables?, max_rows?, space_id}`. The password is used once and is not stored. Type it through a secure prompt or `FDE_SOURCE_PASSWORD`, never in chat, a ticket or a shell history.
3. **Pull**: name the tables the steward wants (`tables`). Leave `max_rows` at the default (500k) unless you have sized it. Read the receipt:
   - `truncated: true`: the lake is not the source. Raise `max_rows` for that table, or agree a filtered extract, before any accuracy claim.
   - `untyped_columns`: numeric columns that stayed text are refused at ask time. Fix the source type, or accept that the column is not measurable.
   - `skipped`: tables the login cannot see. That is a grant problem on their side.
   - `note`: renames, such as a bronze-name suffix or a duplicate column.
4. **Grant**: `POST /v1/connect/sources/{source_id}/grants` (CONNECT-GRANT-01). Grant only the tables this Space should answer from.

Done when the receipt has 0 truncated tables and 0 skipped tables, and the untyped columns are listed in the tenant sheet with a decision for each.

## 2. Auto-ontology compile (day 1, midday)

1. Derive from the connected data: `POST /v1/spaces/{space_id}/ontology/derive`, then read `GET /v1/spaces/{space_id}/ontology`.
   - Objects come from tables and keys. Links come from declared FKs. Each link starts `unverified` and is measured against the landed rows; only verified links are used to join.
   - Check that the link count matches what the DBA says they declared. If it is zero on PostgreSQL with a read-only login, you are hitting the INFORMATION_SCHEMA blind spot that #545 fixes.
2. AI-assisted compile (flag `DMS_AO_COMPILE`, needs an OpenVault-issued key for DMS): `POST /v1/ontology/data/compile?space_id=...`, then `GET /v1/ontology/data/diff`. This proposes grains, measures and descriptions. See the PII note in the security section before turning it on.

## 3. Steward review (day 1, afternoon, with the customer)

Sit with their data owner, not just IT.

1. Walk the diff item by item. Approve with `POST /v1/ontology/data/items/{item_id}/approve`. Revoke with `.../revoke`. **Only the customer's steward approves.** The FDE never approves on their behalf; the approval reference names the person and the time.
2. For every unverified link the verifier names (for example "parent not unique on these columns"), decide with the steward: is it a real many-to-one, a dirty key, or a bridge table?
3. Capture their business definitions in plain sentences ("revenue = shipped orders, after line discounts, before tax"). These become verified examples in step 4. Main has no separate glossary route yet; put the definitions in the tenant sheet and in verified-example questions.

Done when there is an approved ontology version, and every unverified link has a written decision.

## 4. Tune (day 2)

The levers, in the order to try them. Measure after each one (step 5); keep a change only if the numbers say so.

1. **Verified examples (RAG)**: `POST /v1/studio/verified-queries` with `{space_id, question, sql, synonyms}`, steward-approved. Start with the 10-20 questions the customer asks every week, written with their definitions. With `DMS_VQ_RAG` on, the closest verified examples go into the model's context. They are examples, not answer lookups.
2. **Ontology descriptions**: better column and measure descriptions from the steward session. Fix them in the approved version rather than with prompt hacks.
3. **Model choice per tenant (OpenVault only)**:
   - Set the tenant's route and budget in OpenVault (role routing and hard spend caps: OpenVault ROUTE-ROLE-BUDGET-01, #148, in progress). Never put a model name in DMS config or code.
   - Compare models on the tenant's held-out pack, not on the questions you tuned with.
4. **Flags**: turn on only what you measured helps. Record the exact flag set in the tenant sheet:
   - `DMS_AI_FIRST`
   - `DMS_GRAIN_AI`
   - `DMS_ONTO_FROM_DATA`
   - `DMS_DEF_VALIDATE`
   - `DMS_VQ_RAG`
   - `DMS_RECONFIRM_RUN_HELD`

Not allowed: adding keyword or phrase rules, editing gold answers to match output, or tuning on the held-out questions.

## 5. Evaluate with a company pack (day 2, then every change)

1. **Write the tenant pack** with the steward, in the format of `tests/fde_scenarios/<company>/questions.yaml`:
   - 15-25 real questions in their words, each with gold SQL that the steward signs off.
   - At least 2 traps: one ambiguous ("who are our best customers?", expect clarify) and one about something they do not have (expect refuse).
   - Freeze the gold answers before tuning starts.
   - Split the questions into a tuning half and a **held-out** half. Never tune on the held-out half.
2. **Run**:
   ```bash
   DMS_API_BASE=https://<tenant-dms>/api python scripts/verify/fde_scenarios.py --live --spaces spaces.json --json run.json
   ```
3. **Read the result**:
   - Report per pack: n, answered, correct, WRONG, abstain and reconfirm, on the tuning half and on the held-out half separately.
   - **Any WRONG blocks handover.** Find the cause (ontology, data or model), fix it, and re-run the whole pack.
   - Abstain is acceptable. A confident answer on a trap counts as WRONG.
4. Keep the `run.json` artifact. It, and not a screenshot, is the evidence for the handover.

## 6. Handover and monitor (day 3)

1. **Handover pack**:
   - tenant sheet: Space id, sources, granted tables, flag set, OpenVault route and budget, steward name;
   - approved ontology version id;
   - the last held-out run artifact, with WRONG = 0;
   - known limits: untyped columns, capped tables, engines not covered.
2. **Monitor**:
   - Weekly re-pull. Today every connect is a full pull; there is no incremental SQL-source refresh yet.
   - Re-run the tenant pack after every re-pull and every DMS upgrade. A new WRONG pages the FDE owner.
   - Watch the OpenVault usage ledger for spend per role against the tenant cap.
3. **Drift**: when the source schema changes (new column, renamed table), re-derive the ontology, review the diff with the steward, and re-run the pack.

---

## Automated path (FDE-PIPELINE-01, #549)

Steps 1, 2 (structural draft), 5 and the report run as a single command. Use it first at every tenant, then do the human steps (3, 4, 6) on what it finds.

```
# tenant.yaml: tenant, source {kind, host, port, database, user, password_env}, optional glossary
FDE_RO_PASSWORD=... python scripts/fde/fde_pipeline.py run --tenants tenant.yaml --work run/ --expect-keys
# with the OpenVault-issued Cortex key on the box, ask every generated question on the real ask path:
python scripts/fde/fde_pipeline.py run --tenants tenant.yaml --work run/ --dms armed
```

- `run/<tenant>/profile.json` holds types, nulls, distinct counts, key candidates and FK candidates. A source that declares no keys still gets entities and joins, marked `inferred` for the steward.
- `ontology_draft.json` holds facts, dimensions, line tables, measures, time columns and joins. It is a starting point for step 3 and is never approved by the tool.
- `pack.json` holds generated questions with gold from the landed copy, plus drop reasons and one refuse trap and one clarify trap. Add the customer's own questions to this in step 5.
- `report.json` and `FLEET.md` carry the numbers for the handover. DMS shows SKIPPED until `--dms armed` runs, never a pass.

At fleet scale, `factory` makes seeded tenants (8 domains x 4 naming styles, any row scale) on any engine. CI runs them on every connector change (`fde-fleet.yml`, `fde-engines.yml`).

## Per-tenant onboarding checklist

- [ ] Engine and version confirmed supported (table in section 0)
- [ ] Practice-pack rehearsal on the same engine passed fidelity (80/80 style, keys counted)
- [ ] Read-only login created by the customer's DBA (not by us), SELECT on the agreed tables only
- [ ] Network path agreed (VPN, allow-listed IP or bastion), with no inbound access to DMS internals
- [ ] Credential custody: in the tenant's OpenVault; never pasted into chat, tickets or logs
- [ ] Space created, `space_id` recorded
- [ ] Extract receipt: 0 truncated, 0 skipped, untyped columns decided
- [ ] Grants applied for this Space only
- [ ] Ontology derived; link count matches declared FKs
- [ ] Steward review done; approved version id recorded; unverified links decided
- [ ] PII columns listed and masking confirmed (below)
- [ ] Verified examples registered and steward-approved
- [ ] OpenVault route and budget set for the tenant
- [ ] Flag set recorded
- [ ] Tenant pack written, gold frozen, held-out split recorded
- [ ] Held-out run: WRONG = 0, artifact saved
- [ ] Handover pack delivered; monitoring owner named

## Security notes

- **Read-only credentials only.**
  - Ask for a dedicated login with SELECT on named tables. Never use an owner, DBA or application account.
  - On PostgreSQL, `GRANT USAGE ON SCHEMA` plus `GRANT SELECT ON <tables>`.
  - On MySQL/MariaDB, `GRANT SELECT ON db.*` or per table.
  - On SQL Server, `db_datareader`, or SELECT per table.
  - DMS opens SQL Server connections with `readonly=True`.
- **Extract-only.**
  - DMS reads rows out and never sends model-written SQL to the source (`tests/invariants/test_extract_only.py`).
  - Answers run on the DuckDB copy inside the tenant's DMS.
  - Tell the customer this plainly: their database sees only `SELECT * FROM <granted table>` and catalog reads.
- **Secrets.**
  - Passwords live in the tenant's OpenVault, or are typed once into `/v1/studio/sources/sql` and not stored.
  - They are never printed, never in receipts, and never in run artifacts (`fde_scenarios.py` and `fde_land.py` read them only from `FDE_SOURCE_PASSWORD`).
  - Rotating, minting or changing keys and auth is a founder-approved change with a 1-hour objection window.
- **PII.**
  - Before connecting, ask the steward to list personal-data columns (names, emails, phone numbers, national IDs, salaries).
  - AO-MASK-01 (#505, still open) makes AI compile prompts carry only masked or non-personal sample values. Until it lands, keep PII columns out of the grant for any Space where `DMS_AO_COMPILE` is on.
  - Where a question does not need a PII column, leave it out of the grant.
  - The practice HR pack has `full_name` and `email` on purpose, so the team rehearses this.
- **Tenant isolation.** One Space per company, with grants scoped to that Space. Never reuse a Space or a bronze lake across customers.
- **Data residency.** The bronze copy lives where the tenant's DMS runs. Agree the region before the first pull.
- **Exit.** On contract end, the customer revokes the login. The tenant lake is removed only with the customer's written request and the founder's approval (no silent deletes).
