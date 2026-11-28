/**
 * One map from a server token to a sentence a buyer can read.
 *
 * Chip words stay in badgeCopy.ts. Every reason_code the server can emit,
 * and every badge token that would otherwise be printed, has a sentence
 * here. Render sites call the helpers. They do not keep a second table.
 *
 * This is not a rule about the question. It looks up a token the server
 * already chose.
 */

export const FALLBACK_REASON = "No figure was given.";

export const REASON_COPY: Record<string, string> = {
  abstain: "No figure was given, on purpose.",
  ai_first_no_model_sql: "No model query was returned, so no figure was given.",
  ambiguous_column: "More than one column fits that name, so no figure was given.",
  ambiguous_measure: "More than one measure fits, so no figure was given until you choose.",
  ambiguous_path: "More than one way to join the tables fits, so no figure was given.",
  arbiter_disagree: "The checked results disagree, so no figure was given.",
  ask_failed: "The answer service did not return a result, so no figure was given.",
  as_of: "The as-of date could not be applied, so no figure was given.",
  budget: "This question used the time allowed for one answer, so it stopped.",
  budget_askback: "There is a choice to continue or to narrow the question before a figure is given.",
  checker: "A result check did not pass, so no figure was given.",
  clarify_ambiguous: "More than one reading fits, so no figure was given until you choose.",
  column_class_unsure: "A column's sensitivity could not be decided, so no figure was given.",
  compile_without_ai: "There is no checked model query to agree with, so no figure was given.",
  compute_unsure: "The calculation could not be checked, so no figure was given.",
  coverage_invalid: "The figure had no include, exclude, and unsure coverage, so it was not given.",
  currency_mismatch: "The question and the data name different currencies, so no figure was given.",
  db_error: "The database returned an error, so no figure was given.",
  definition_mismatch:
    "The query does not match the approved definition, so no figure was given until you confirm it.",
  demo_scope: "That question is outside the demo data, so no figure was given.",
  empty_result_unverified:
    "The query returned nothing, and that empty result is not verified, so no figure was given.",
  envelope_demoted: "The result could not be certified, so no figure was given.",
  execute_failed: "That reading could not be run, so no figure was given.",
  explain: "The database could not describe how this query would run, so no figure was given.",
  fanout_refused: "That join would count the same row more than once, so no figure was given.",
  filter_dropped: "A filter in the question was dropped, so no figure was given.",
  filter_parse_failed: "A filter in the question could not be read, so no figure was given.",
  function_missing: "The query uses a function this database does not have, so no figure was given.",
  generate: "The query was not written, so no figure was given.",
  grant_unreadable: "This Space's table list could not be read, so no figure was given.",
  hostile_sql: "That is not a read of the data, so it was not run.",
  insights_ai_error: "The analysis service returned an error, so no figure was given.",
  insights_bearer_insecure_transport:
    "The analysis service refused an insecure connection, so no figure was given.",
  insights_bearer_missing:
    "No credentials were available for the analysis service, so no figure was given.",
  insights_call_cap: "The analysis used its allowed calls, so no figure was given.",
  insights_no_sql_no_ranking:
    "The analysis returned neither a query nor a ranking, so no figure was given.",
  insights_refused: "The analysis service refused the question, so no figure was given.",
  insights_timeout: "The analysis took longer than the time limit, so no figure was given.",
  insights_unarmed: "The analysis service was not turned on, so no figure was given.",
  insights_unauthorized: "The analysis service rejected the credentials, so no figure was given.",
  intent_spec_mismatch: "The query does not match the intended meaning, so no figure was given.",
  intent_spec_unverified: "The intended meaning could not be verified, so no figure was given.",
  join_unconfirmed:
    "That join is not confirmed for this Space, so no figure was given until you say yes.",
  ladder_exhausted: "The attempts to answer are used up, so no figure was given.",
  loop_exhausted: "The attempts to repair the query are used up, so no figure was given.",
  measure_drift:
    "A measure does not match its approved definition, so no figure was given until you confirm it.",
  measure_link_mismatch: "The measure does not match the linked columns, so no figure was given.",
  measure_proposed: "That measure is only a proposal, so no figure was given.",
  measure_tie: "Two measures fit equally well, so no figure was given until you choose.",
  measure_unranked: "No measure stood out as the one to use, so no figure was given until you choose.",
  missing_column: "The query names a column that is not in the data, so no figure was given.",
  missing_join: "A required join is missing, so no figure was given.",
  missing_metric: "No metric is available for that question, so no figure was given.",
  missing_ontology: "No approved model is loaded for this Space, so no figure was given.",
  missing_table: "The query names a table that is not in the data, so no figure was given.",
  model_unreachable: "I could not reach the model just now; ask again in a minute",
  multi_statement: "More than one statement was produced, so no figure was given.",
  no_candidate: "Nothing checked was available to answer, so no figure was given.",
  no_declared_measure: "That measure is not declared for this Space, so no figure was given.",
  no_path: "No join connects that question to the data, so no figure was given.",
  no_plan: "No plan was produced for that question, so no figure was given.",
  no_space: "No Space was selected, so no figure was given.",
  no_sql: "No query was produced for that question, so no figure was given.",
  not_in_data: "The data does not contain what the question asks for, so no figure was given.",
  not_in_ontology:
    "That item is not in the approved model for this Space, so no figure was given until you confirm it.",
  ontology: "The model for this Space could not be used, so no figure was given.",
  ontology_store_unavailable: "The model for this Space could not be loaded, so no figure was given.",
  ontology_unverified:
    "The model for this Space has not been checked against the data, so no figure was given.",
  openvault_mint: "A credential could not be issued, so no figure was given.",
  other: "The check did not finish in a named way, so no figure was given.",
  path_not_allowed: "That path is not allowed, so no figure was given.",
  pii: "That asks for personal data, so no figure was given.",
  predictive: "History is not a forecast, so no figure was given.",
  query_sql_empty: "No query was produced, so no figure was given.",
  rank_window_entity_mismatch:
    "The ranking does not match the thing being counted, so no figure was given.",
  rank_window_open_ended: "The ranking has no end, so no figure was given.",
  rank_window_unhandled_terms:
    "The ranking uses a term this Space cannot apply, so no figure was given.",
  reading_missing: "That reading had no query to run, so no figure was given.",
  reading_rejected: "That reading did not pass the check, so no figure was given.",
  reconfirm_no: "You declined the suggested question, so no figure was given.",
  reserved_param: "That question uses a reserved date setting, so no figure was given.",
  retry: "A later try can answer this. Nothing was served just now.",
  revoked_measure: "That measure was revoked, so no figure was given.",
  rules_off: "The checked rules for this answer are turned off, so no figure was given from them.",
  schema_repair_exhausted: "The attempts to repair the table names are used up, so no figure was given.",
  serving_deadline_exceeded: "The answer did not finish in the time allowed, so no figure was given.",
  serving_deadline_reserve:
    "This question waited so a running answer could finish. Ask again in a moment.",
  serving_lease_cap: "Every answer slot is in use. Ask again in a moment.",
  serving_lease_queue_full: "The queue for answers is full. Ask again in a moment.",
  shape_misfit: "The query does not match the shape of the question, so no figure was given.",
  sql_dialect_unknown: "The database dialect is unknown, so no figure was given.",
  sql_not_analyzable: "The query could not be read, so no figure was given.",
  sql_relation_not_granted: "This Space is not allowed to read that table, so no figure was given.",
  sql_relation_unresolved: "A table in the query could not be resolved, so no figure was given.",
  statement_not_allowed: "That statement is not a read, so it was not run.",
  submit_failed: "The query could not be submitted, so no figure was given.",
  syntax: "The query was not valid, so no figure was given.",
  ticket_missing: "The failure record was missing, so no figure was given.",
  type_mismatch: "A value had the wrong kind for its column, so no figure was given.",
  uncertified_paraphrase: "That wording is not the certified question, so no figure was given.",
  undeclared_join:
    "That join is not declared for this Space, so no figure was given until you confirm it.",
  ungranted: "This Space is not allowed to use that table, so no figure was given.",
  ungranted_table: "That table is not shared with this Space, so no figure was given.",
  ungrounded_measure: "That measure is not tied to this Space's data, so no figure was given.",
  ungrounded_qualifier: "A qualifier in the question is not tied to the data, so no figure was given.",
  unhonored_qualifier: "A qualifier in the question was dropped from the query, so no figure was given.",
  unknown_column: "The question names a column that is not defined here, so no figure was given.",
  unknown_link: "That link is not in the approved model, so no figure was given.",
  unknown_measure: "This question asks for a measure that is not defined here, so no figure was given.",
  unknown_object: "The question names something this Space does not have, so no figure was given.",
  unspecified: "The reason was not named, so no figure was given.",
  unrequested_offset: "The query skipped rows that were not asked for, so no figure was given.",
  untyped_numeric: "A number in this result has no unit, so no figure was given.",
  unverified_join: "That join is not an approved link in this Space, so no figure was given.",
  vague_question:
    "The question can be read in more than one way, so no figure was given until you choose.",
  value_exists_pending: "Whether that value exists is still unchecked, so no figure was given.",
  warehouse_missing: "The data file for this Space is missing, so no figure was given.",
  year_not_certified: "That year is not in the certified data, so no figure was given.",
};

/** Sentence used when a badge token would otherwise be shown. The chip stays short. */
export const BADGE_SENTENCE: Record<string, string> = {
  L0_CERTIFIED: "A person reviewed the query.",
  L1_GOVERNED_METRIC: "It comes from a governed metric.",
  L2_VALIDATED: "The query was generated, then checked.",
  L2_ANOMALOUS: "The query was generated, and the result looks unusual.",
  ABSTAIN: "No answer was given, on purpose.",
};

const BADGE_PHRASE: Record<string, string> = {
  L0_CERTIFIED: "a reviewed query",
  L1_GOVERNED_METRIC: "a governed metric",
  L2_VALIDATED: "a generated query that was checked",
  L2_ANOMALOUS: "a generated query with an unusual result",
  ABSTAIN: "no answer, on purpose",
};

/** Fixed include / exclude lines from build_audit_receipt. Not question text. */
export const RECEIPT_WHY: Record<string, string> = {
  "N/A: abstained; no include set": "Nothing was included, because no answer was given.",
  "executed result rows that produced the certified figures (query grain / LIMIT); not a claim of full-table completeness":
    "These are the rows behind the figure. They are the query result, not every row in the table.",
  "result rows returned with the answer; not a SQL grain claim":
    "These are the rows returned with the answer.",
  "N/A: document retrieval; include set is cited snippets, not SQL rows":
    "This answer cites documents, not table rows.",
  "N/A: executed SQL returned no include rows": "The query ran and returned no rows.",
  "N/A: abstained; no exclude receipt": "Nothing was excluded, because no answer was given.",
  "caller-supplied exclude reasons; not invented":
    "Rows were excluded for the reasons supplied with this answer.",
  "extracted from executed SQL WHERE/HAVING/FILTER; grain-level exclusions not claimed":
    "The query filtered rows. This is not a claim about every exclusion in the source.",
  "N/A: no WHERE/HAVING/FILTER in executed SQL; none excluded at result grain":
    "The query did not filter rows.",
  "N/A: document retrieval; no SQL filter grain": "This answer did not filter table rows.",
};

const IDENT = /^[a-z][a-z0-9_]*$/;

function stripGen(raw: string): string {
  const text = raw.trim();
  if (text.startsWith("GEN-01:")) return text.slice("GEN-01:".length).trim();
  if (text.startsWith("GEN-01 ")) return text.slice("GEN-01 ".length).trim();
  return text;
}

function identParts(raw: string): string[] {
  return stripGen(raw)
    .split(":")
    .map((part) => part.trim())
    .filter((part) => IDENT.test(part));
}

/** A server token, or a GEN-01 note. A sentence with spaces is not a token. */
export function isBareCode(raw: string): boolean {
  const text = raw.trim();
  if (!text) return false;
  if (text.startsWith("GEN-01:") || text.startsWith("GEN-01 ")) return true;
  return text.split(":").every((part) => IDENT.test(part.trim()));
}

/** First token in the chain that has a sentence. */
export function reasonHead(raw: string): string {
  const parts = identParts(raw);
  for (const part of parts) {
    if (REASON_COPY[part]) return part;
  }
  return parts[0] ?? "";
}

export function plainShownReason(raw: string): string {
  const text = raw.trim();
  if (!text) return FALLBACK_REASON;
  if (!isBareCode(text)) return replaceBadgeTokens(text);
  const head = reasonHead(text);
  return (head && REASON_COPY[head]) || FALLBACK_REASON;
}

/** Prose stays. A bare reason_code becomes its sentence. */
export function visibleReason(text: string | undefined, code: string | undefined): string {
  const prose = (text ?? "").trim();
  if (prose && !isBareCode(prose)) return replaceBadgeTokens(prose);
  return plainShownReason(prose || (code ?? ""));
}

export function badgeSentence(kind: string): string {
  return BADGE_SENTENCE[kind] ?? "The result is marked so you can check it.";
}

export function replaceBadgeTokens(text: string): string {
  let out = text;
  for (const [kind, phrase] of Object.entries(BADGE_PHRASE)) {
    out = out.split(`badge=${kind}`).join(phrase);
  }
  return out;
}

export function plainReceiptWhy(why: string): string {
  const text = why.trim();
  const exact = RECEIPT_WHY[text];
  if (exact) return exact;
  const given = "not abstained; badge=";
  if (text.startsWith(given)) {
    return `This answer was given. ${badgeSentence(text.slice(given.length).trim())}`;
  }
  if (text.startsWith("abstained; badge=")) return "No answer was given, on purpose.";
  if (isBareCode(text)) return plainShownReason(text);
  return replaceBadgeTokens(text);
}

export function recordedModel(env: { served_model?: unknown }): string | null {
  const raw = env.served_model;
  if (typeof raw !== "string") return null;
  const name = raw.trim();
  return name ? name : null;
}
