import type { ReactNode } from "react";
import { isAbstain, namedAbstainReason, type AbstainFields } from "@/components/studio/abstainReason";

/**
 * Plain sentences for the abstain codes in
 * packages/executor/dms_executor/gen_path_refuse.py GAP_REASONS, plus
 * currency_mismatch from customer_abstain_text in that file.
 * Codes absent from that set stay raw. Do not add a code that file does not name.
 */
export const ABSTAIN_SENTENCES: Record<string, string> = {
  unknown_measure:
    "The question names a measure this ontology does not have, so no answer was given.",
  no_path: "No join path connects that question to the data, so no answer was given.",
  unknown_object:
    "The question names an object this ontology does not have, so no answer was given.",
  unknown_column:
    "The question names a column this ontology does not have, so no answer was given.",
  ontology_unverified:
    "The ontology has not been verified against this data, so no answer was given.",
  missing_metric: "No metric is available for that question, so no answer was given.",
  missing_ontology: "No ontology is loaded for that question, so no answer was given.",
  missing_join: "A required join is missing, so no answer was given.",
  fanout_refused:
    "That join would count the same row more than once, so no answer was given.",
  ambiguous_path: "More than one join path fits, so no answer was given.",
  unknown_link: "The named link is not in the ontology, so no answer was given.",
  coverage_invalid:
    "The numeric answer had no include, exclude, and unsure coverage, so no answer was given.",
  insights_unarmed: "The analysis service was not armed, so no answer was given.",
  insights_refused: "The analysis service refused the question, so no answer was given.",
  insights_unauthorized:
    "The analysis service rejected the credentials, so no answer was given.",
  insights_timeout:
    "The analysis took longer than the time limit, so no answer was given.",
  insights_no_sql_no_ranking:
    "The analysis returned neither SQL nor a ranking, so no answer was given.",
  insights_bearer_missing:
    "No credentials were available for the analysis service, so no answer was given.",
  insights_bearer_insecure_transport:
    "The analysis service refused an insecure connection, so no answer was given.",
  unhonored_qualifier:
    "The question included a qualifier the query dropped, so no answer was given.",
  currency_mismatch:
    "The question and the query name different currencies, so no answer was given.",
};

export const MODEL_NOT_RECORDED = "Model not recorded";

export const GENERIC_ABSTAIN_SENTENCE =
  "No plain-language note is stored for this code.";

export type StampEnvelope = AbstainFields & {
  served_model?: unknown;
};

/** Top-level served_model only. A missing or blank value is not a model name. */
export function servedModelLine(env: StampEnvelope): string {
  const raw = env.served_model;
  if (typeof raw !== "string") return MODEL_NOT_RECORDED;
  const name = raw.trim();
  if (!name) return MODEL_NOT_RECORDED;
  return `Answered by ${name}`;
}

export type AbstainView = {
  known: boolean;
  /** Plain sentence for a known code. The raw code itself when the code is unknown. */
  label: string;
  sentence: string;
  code: string;
};

export function abstainView(env: AbstainFields): AbstainView {
  const code = namedAbstainReason(env);
  const head = code.split(":")[0]?.trim() ?? "";
  const sentence = ABSTAIN_SENTENCES[head];
  if (sentence) return { known: true, label: sentence, sentence, code };
  return { known: false, label: code, sentence: GENERIC_ABSTAIN_SENTENCE, code };
}

export function assumptionNotes(assumptions: unknown): string[] {
  if (!Array.isArray(assumptions)) return [];
  return assumptions.map((item) => String(item).trim()).filter(Boolean);
}

export function ServedModelLine({ envelope }: { envelope: StampEnvelope }) {
  return (
    <p data-testid="served-model" className="text-xs text-[var(--color-ink-muted)]">
      {servedModelLine(envelope)}
    </p>
  );
}

export function AbstainNote({ envelope }: { envelope: StampEnvelope }) {
  if (!isAbstain(envelope)) return null;
  const view = abstainView(envelope);
  return (
    <div
      data-testid="abstain-reason"
      className="mb-3 border border-[var(--color-line)] bg-[var(--color-paper)] px-3 py-2.5"
    >
      <p data-testid="abstain-label" className="text-sm text-[var(--color-ink)]">
        {view.label}
      </p>
      {!view.known && (
        <p
          data-testid="abstain-sentence"
          className="mt-1 text-sm text-[var(--color-ink-muted)]"
        >
          {view.sentence}
        </p>
      )}
      <p
        data-testid="abstain-code"
        className="mt-1 text-[11px] text-[var(--color-ink-muted)]"
      >
        {view.code}
      </p>
    </div>
  );
}

export function AnswerDetails({
  envelope,
  children,
}: {
  envelope: StampEnvelope;
  children?: ReactNode;
}) {
  const notes = assumptionNotes(envelope.assumptions);
  if (notes.length === 0 && children == null) return null;
  return (
    <details data-testid="answer-details" className="mt-4">
      <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
        Details
      </summary>
      {notes.length > 0 && (
        <ul
          data-testid="answer-assumptions"
          className="mt-1 list-inside list-disc text-sm text-[var(--color-ink-muted)]"
        >
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
      {children}
    </details>
  );
}
