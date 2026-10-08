import type { AnswerEnvelope } from "./types";

export type ClarifyPickPayload = {
  question: string;
  clarify_id?: string;
  option_id?: string;
  clarify_text?: string;
};

/** Original question plus the pick. Bindings stay on the server. */
export function clarifyPickPayload(
  envelope: Pick<AnswerEnvelope, "original_question" | "clarify_id">,
  optionId: string | null,
  freeText?: string,
): ClarifyPickPayload {
  const text = (freeText || "").trim();
  return {
    question: (envelope.original_question || "").trim(),
    clarify_id: envelope.clarify_id,
    option_id: optionId || undefined,
    clarify_text: optionId ? undefined : text || undefined,
  };
}
