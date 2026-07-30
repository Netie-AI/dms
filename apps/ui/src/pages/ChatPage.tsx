import { useState } from "react";
import { AnswerMessage } from "@/components/AnswerMessage";
import { ScopeChip } from "@/components/ScopeChip";
import { useApp } from "@/context/AppContext";
import { SUGGESTED_QUESTIONS } from "@/lib/fixtures";

export function ChatPage() {
  const { fixtureAnswer, showFixtureAnswer, clearAnswer, activeSpace } = useApp();
  const [draft, setDraft] = useState("");

  const ask = (q: string) => {
    setDraft(q);
    // U0: fixture answer only — live SSE waits on DMS chat API + Cortex
    showFixtureAnswer();
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {!fixtureAnswer ? (
          <div className="mx-auto max-w-2xl">
            <h1 className="font-[family-name:var(--font-display)] text-3xl font-semibold tracking-tight text-[var(--color-ink)]">
              Ask about your data
            </h1>
            <p className="mt-2 text-[var(--color-ink-muted)]">
              {activeSpace
                ? `Scoped to ${activeSpace.name}. Every number is a button back to its cell.`
                : "Company default ACL. Pick a Space in the top bar to sandbox sources."}
            </p>
            <p className="mt-8 text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
              Suggested
            </p>
            <ul className="mt-3 grid gap-2 sm:grid-cols-2">
              {SUGGESTED_QUESTIONS.map((q) => (
                <li key={q}>
                  <button
                    type="button"
                    onClick={() => ask(q)}
                    className="h-full w-full border border-[var(--color-line)] bg-white/60 px-3 py-3 text-left text-sm leading-snug text-[var(--color-ink)] transition hover:border-[var(--color-accent)] hover:bg-[var(--color-accent-soft)]/30"
                  >
                    {q}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div className="mx-auto max-w-2xl space-y-4">
            <div className="flex items-center justify-between">
              <p className="text-sm text-[var(--color-ink-muted)]">You asked</p>
              <button
                type="button"
                onClick={clearAnswer}
                className="text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
              >
                Clear
              </button>
            </div>
            <p className="border border-[var(--color-line)] bg-[var(--color-paper-2)]/50 px-3 py-2 text-sm">
              {draft || SUGGESTED_QUESTIONS[0]}
            </p>
            <AnswerMessage envelope={fixtureAnswer} />
            <p className="text-xs text-[var(--color-ink-muted)]">
              Click the number to open Sources — no modal over the answer.
            </p>
          </div>
        )}
      </div>

      <div className="border-t border-[var(--color-line)] bg-[var(--color-panel)]/80 px-4 py-3">
        <div className="mx-auto flex max-w-2xl flex-col gap-2">
          <ScopeChip />
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (draft.trim()) ask(draft.trim());
            }}
          >
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Ask about your data…"
              className="h-11 flex-1 border border-[var(--color-line)] bg-white px-3 text-sm"
            />
            <button
              type="submit"
              className="h-11 bg-[var(--color-accent)] px-4 text-sm font-medium text-white"
            >
              Ask
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
