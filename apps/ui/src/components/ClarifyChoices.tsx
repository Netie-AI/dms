import { useState } from "react";

export type ClarifyChoice = {
  id: string;
  label: string;
};

const chip =
  "border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-2.5 py-1.5 text-xs hover:border-[var(--color-accent)]";

/** One clarifying question and its option buttons. Not an answer. */
export function ClarifyChoices({
  question,
  options,
  onPick,
}: {
  question: string;
  options: ClarifyChoice[];
  onPick: (optionId: string | null, freeText?: string) => void;
}) {
  const [text, setText] = useState("");
  return (
    <div className="space-y-2" data-testid="clarify-choices">
      <p data-testid="clarify-question" className="text-sm text-[var(--color-ink)]">
        {question}
      </p>
      <div className="flex flex-wrap gap-2">
        {options.map((opt) => (
          <button
            key={opt.id}
            type="button"
            className={chip}
            data-testid={`clarify-option-${opt.id}`}
            onClick={() => onPick(opt.id)}
          >
            {opt.label}
          </button>
        ))}
      </div>
      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          const trimmed = text.trim();
          if (trimmed) onPick(null, trimmed);
        }}
      >
        <input
          data-testid="clarify-freetext"
          value={text}
          onChange={(event) => setText(event.target.value)}
          className="min-w-0 flex-1 border border-[var(--color-line)] bg-transparent px-2 py-1 text-xs"
          placeholder="Or type a short answer"
        />
      </form>
    </div>
  );
}
