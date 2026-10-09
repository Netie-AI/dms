const chip =
  "border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-2.5 py-1.5 text-xs hover:border-[var(--color-accent)]";

/** Reason, one suggested question, and Yes / No. Not an answer. */
export function ConfirmPrompt({
  reason,
  suggestion,
  onChoice,
}: {
  reason: string;
  suggestion: string;
  onChoice: (choice: "yes" | "no") => void;
}) {
  return (
    <div className="space-y-2" data-testid="confirm-prompt">
      <p data-testid="confirm-reason" className="text-sm text-[var(--color-ink)]">
        {reason}
      </p>
      <p data-testid="confirm-suggestion" className="text-sm text-[var(--color-ink)]">
        {suggestion}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          className={chip}
          data-testid="confirm-yes"
          onClick={() => onChoice("yes")}
        >
          Yes
        </button>
        <button
          type="button"
          className={chip}
          data-testid="confirm-no"
          onClick={() => onChoice("no")}
        >
          No
        </button>
      </div>
    </div>
  );
}
