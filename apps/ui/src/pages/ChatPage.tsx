import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { AnswerMessage } from "@/components/AnswerMessage";
import { ScopeChip } from "@/components/ScopeChip";
import { useApp } from "@/context/AppContext";

export function ChatPage() {
  const {
    messages,
    ask,
    askError,
    asking,
    clearThread,
    activeSpace,
    askMode,
    spacesFromApi,
    suggestions,
    sessionId,
    cortexContractOk,
    cortexContractRoutesOk,
    cortexTrustOk,
    cortexTrustHint,
    askQueueDepth,
    composerPaused,
    composerPauseReason,
    groundedTables,
    groundedLabels,
    setGrounded,
  } = useApp();
  const location = useLocation();
  const navigate = useNavigate();
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, askError, asking, askQueueDepth]);

  // Studio hands the selection over through router state. Consume it once and
  // clear it, so a later back-navigation does not silently re-apply a scope the
  // user has since cleared.
  useEffect(() => {
    const s = location.state as
      | { groundedTables?: string[]; groundedLabels?: string[] }
      | null;
    if (s?.groundedTables?.length) {
      setGrounded(s.groundedTables, s.groundedLabels ?? []);
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location, navigate, setGrounded]);

  const submit = (q: string) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    setDraft("");
    void ask(trimmed);
  };

  const empty = messages.length === 0 && !askError;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {groundedTables.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-accent)]/40 bg-[var(--color-accent)]/5 px-6 py-2 text-xs">
          <span className="font-medium text-[var(--color-ink)]">
            Grounded in {groundedTables.length}{" "}
            {groundedTables.length === 1 ? "file" : "files"}
          </span>
          <span className="text-[var(--color-ink-muted)]">
            {(groundedLabels.length ? groundedLabels : groundedTables).join(", ")}
          </span>
          <span className="text-[var(--color-ink-muted)]">
            — anything outside this is refused, not just ignored.
          </span>
          <button
            type="button"
            onClick={() => setGrounded([], [])}
            className="ml-auto text-[var(--color-accent)] hover:underline"
          >
            Use the whole Space
          </button>
        </div>
      )}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {empty ? (
          <div className="mx-auto max-w-2xl">
            <h1 className="font-[family-name:var(--font-display)] text-3xl font-semibold tracking-tight text-[var(--color-ink)]">
              Ask about your data
            </h1>
            <p className="mt-2 text-[var(--color-ink-muted)]">
              {activeSpace
                ? `Scoped to ${activeSpace.name}. Every number is a button back to its cell.`
                : "Company default ACL. Pick a Space in the top bar to sandbox sources."}
            </p>
            {askMode === "demo" && (
              <p className="mt-3 border border-[var(--color-warn)]/40 bg-[var(--color-warn-soft)] px-3 py-2 text-xs text-[var(--color-warn)]">
                Demo ask mode — answers compute from the local DuckDB warehouse. Not certified.
                {spacesFromApi ? " Spaces loaded from DMS API." : " Using offline Space fixtures."}
              </p>
            )}
            {askMode === "live" && cortexContractRoutesOk === false && (
              <p className="mt-3 border border-[var(--color-danger)]/40 bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--color-danger)]">
                Cortex is up but contract routes are missing (submit would 404). Restart Cortex from
                the current tree:{" "}
                <code className="font-mono">D:\Cortex\scripts\start_cortex_engine.ps1 -Port 8010 -Force</code>
              </p>
            )}
            {askMode === "live" &&
              cortexContractRoutesOk !== false &&
              cortexContractOk === false &&
              cortexTrustOk === false && (
              <p className="mt-3 border border-[var(--color-danger)]/40 bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--color-danger)]">
                Cortex or OpenVault trust is degraded (JWKS refresh). Ask may fail until siblings
                restart.{" "}
                {cortexTrustHint ??
                  "Pin OPENVAULT_HOME=D:\\OpenVault\\.openvault, then restart Cortex + DMS API."}
              </p>
            )}
            {askMode === "live" &&
              cortexContractRoutesOk !== false &&
              cortexContractOk !== false &&
              cortexTrustOk === false && (
              <p className="mt-3 border border-[var(--color-danger)]/40 bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--color-danger)]">
                Live trust gap — OpenVault JWKS / Cortex refresh.{" "}
                {cortexTrustHint ??
                  "Pin OPENVAULT_HOME=D:\\OpenVault\\.openvault, mint via smoke, POST /v1/contract/jwks/refresh."}
              </p>
            )}
            <p className="mt-8 text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
              Suggested
            </p>
            <ul className="mt-3 grid gap-2 sm:grid-cols-2">
              {suggestions.map((q) => (
                <li key={q}>
                  <button
                    type="button"
                    onClick={() => submit(q)}
                    className="h-full w-full border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-3 py-3 text-left text-sm leading-snug text-[var(--color-ink)] transition hover:border-[var(--color-accent)] hover:bg-[var(--color-accent-soft)]/30"
                  >
                    {q}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div className="mx-auto max-w-2xl space-y-4">
            <div className="flex items-center justify-between gap-3">
              <p className="truncate text-xs text-[var(--color-ink-muted)]">
                Session {sessionId}
                {askMode === "demo" ? " · demo warehouse" : ""}
                {askQueueDepth > 0 ? ` · queue ${askQueueDepth}` : ""}
              </p>
              <button
                type="button"
                onClick={clearThread}
                className="shrink-0 text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
              >
                New chat
              </button>
            </div>
            {askMode === "demo" && (
              <p className="border border-[var(--color-warn)]/40 bg-[var(--color-warn-soft)] px-3 py-2 text-xs text-[var(--color-warn)]">
                Demo mode — L2 badges only. Numbers come from DuckDB, not a certified Cortex path.
              </p>
            )}
            {messages.map((m) =>
              m.role === "user" ? (
                <div
                  key={m.id}
                  className="ml-8 border border-[var(--color-line)] bg-[var(--color-paper-2)]/50 px-3 py-2 text-sm"
                >
                  {m.text}
                </div>
              ) : (
                <AnswerMessage key={m.id} envelope={m.envelope} />
              ),
            )}
            {askError && (
              <p className="border border-[var(--color-danger)]/30 bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-danger)]">
                {askError}
              </p>
            )}
            {asking && <p className="text-sm text-[var(--color-ink-muted)]">Thinking…</p>}
            {messages.some((m) => m.role === "assistant") && (
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
                  Continue
                </p>
                <ul className="mt-2 flex flex-wrap gap-2">
                  {suggestions.slice(0, 4).map((q) => (
                    <li key={q}>
                      <button
                        type="button"
                        onClick={() => submit(q)}
                        className="border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-2.5 py-1.5 text-xs text-[var(--color-ink)] hover:border-[var(--color-accent)]"
                      >
                        {q}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <div className="border-t border-[var(--color-line)] bg-[var(--color-panel)]/80 px-4 py-3">
        <div className="mx-auto flex max-w-2xl flex-col gap-2">
          <ScopeChip />
          {composerPaused && composerPauseReason && (
            <p className="border border-[var(--color-warn)]/40 bg-[var(--color-warn-soft)] px-3 py-2 text-xs text-[var(--color-warn)]">
              {composerPauseReason}
            </p>
          )}
          {askQueueDepth > 0 && (
            <p className="text-[11px] text-[var(--color-ink-muted)]">
              {askQueueDepth} question{askQueueDepth === 1 ? "" : "s"} queued — will send after the
              current answer (paused ≠ dead).
            </p>
          )}
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (draft.trim()) submit(draft.trim());
            }}
          >
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (draft.trim()) submit(draft.trim());
                }
              }}
              placeholder={
                asking
                  ? "Type next question — it will queue…"
                  : "Ask about your data… (Shift+Enter for newline)"
              }
              rows={2}
              className="min-h-[2.75rem] flex-1 resize-y border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-sm"
            />
            <button
              type="submit"
              disabled={!draft.trim()}
              className="h-11 self-end bg-[var(--color-accent)] px-4 text-sm font-medium text-[var(--color-on-accent)] disabled:opacity-50"
            >
              {asking ? "Queue" : "Ask"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
