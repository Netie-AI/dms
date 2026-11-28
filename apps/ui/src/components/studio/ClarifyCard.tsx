import { useId, useState } from "react";
import type { ClarifyBlock, ClarifyReading } from "@/lib/types";

/** The ask body for one tap. Ids come from the envelope, not from the question text. */
export function clarifyPickBody(
  block: ClarifyBlock,
  reading: ClarifyReading,
  ctx: { question?: string | null; space_id?: string | null; session_id?: string | null },
): {
  question: string;
  space_id?: string | null;
  session_id?: string | null;
  clarify_id: string;
  reading_id: string;
} {
  return {
    question: (ctx.question ?? "").trim() || reading.label,
    space_id: ctx.space_id,
    session_id: ctx.session_id,
    clarify_id: block.clarify_id,
    reading_id: reading.reading_id,
  };
}

export function clarifyRefineBody(
  block: ClarifyBlock,
  text: string,
  ctx: { question?: string | null; space_id?: string | null; session_id?: string | null },
): {
  question: string;
  space_id?: string | null;
  session_id?: string | null;
  clarify_id: string;
  text: string;
} {
  const typed = text.trim();
  return {
    question: (ctx.question ?? "").trim() || typed,
    space_id: ctx.space_id,
    session_id: ctx.session_id,
    clarify_id: block.clarify_id,
    text: typed,
  };
}

const BUTTON =
  "min-h-[44px] w-full border border-[var(--color-accent)] bg-[var(--color-accent-soft)] px-3 py-2 text-left text-sm font-medium text-[var(--color-ink)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)] disabled:opacity-50 sm:w-auto";

/** Question, 1 to 4 reading buttons, and one free-text refine. */
export function ClarifyCard({
  block,
  busy,
  error,
  onPick,
  onRefine,
}: {
  block: ClarifyBlock;
  busy?: boolean;
  error?: string | null;
  onPick: (reading: ClarifyReading) => void | Promise<void>;
  onRefine?: (text: string) => void | Promise<void>;
}) {
  const titleId = useId();
  const questionId = useId();
  const refineId = useId();
  const [draft, setDraft] = useState("");
  return (
    <section
      data-testid="clarify-card"
      role="region"
      aria-labelledby={titleId}
      className="mt-3 max-w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3"
    >
      <h2
        id={titleId}
        className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]"
      >
        Clarify
      </h2>
      <p id={questionId} data-testid="clarify-question" className="mt-2 break-words text-sm text-[var(--color-ink)]">
        {block.question}
      </p>
      <div role="group" aria-labelledby={questionId} className="mt-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        {block.readings.map((reading) => (
          <button
            key={reading.reading_id}
            type="button"
            data-testid={`clarify-reading-${reading.reading_id}`}
            disabled={busy}
            onClick={() => onPick(reading)}
            className={BUTTON}
          >
            {reading.label}
          </button>
        ))}
      </div>
      <form
        className="mt-3"
        onSubmit={(event) => {
          event.preventDefault();
          const typed = draft.trim();
          if (!typed || busy || !onRefine) return;
          void onRefine(typed);
        }}
      >
        <label className="block text-sm text-[var(--color-ink)]" htmlFor={refineId}>
          Or describe it
          <input
            id={refineId}
            data-testid="clarify-refine"
            value={draft}
            disabled={busy}
            onChange={(event) => setDraft(event.target.value)}
            className="mt-1 min-h-[44px] w-full border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-ink)]"
          />
        </label>
        <button
          type="submit"
          data-testid="clarify-refine-submit"
          disabled={busy || !draft.trim()}
          className={`${BUTTON} mt-2`}
        >
          Use this wording
        </button>
      </form>
      {error ? (
        <p data-testid="clarify-error" className="mt-2 text-xs text-[var(--color-danger)]" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
