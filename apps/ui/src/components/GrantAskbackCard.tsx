import { useState } from "react";
import { Link } from "react-router-dom";
import { postGrantAskback } from "@/lib/api";
import { ceoSafeHref, type ProductMode } from "@/lib/productMode";
import type { AnswerEnvelope, GrantAskback } from "@/lib/types";

/** The card renders only when the envelope carries the structured block. */
export function readGrantAskback(env: { grant_askback?: unknown }): GrantAskback | null {
  const raw = env.grant_askback;
  if (!raw || typeof raw !== "object") return null;
  const block = raw as GrantAskback;
  if (typeof block.text !== "string" || !block.text.trim()) return null;
  if (!Array.isArray(block.actions)) return null;
  return block;
}

const BUTTON =
  "min-h-[44px] border px-3 py-2 text-left text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)] disabled:opacity-50";

/**
 * Ask-back for a measure the Space cannot see yet. Grant is a click.
 * Nothing on this card is a result row.
 */
export function GrantAskbackCard({
  card,
  productMode,
  onResult,
}: {
  card: GrantAskback;
  productMode: ProductMode;
  onResult?: (envelope: AnswerEnvelope) => void;
}) {
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send(action: string, measure?: string) {
    if (!card.token || busy) return;
    setBusy(true);
    setNote(null);
    try {
      const result = await postGrantAskback({
        token: card.token,
        action,
        allow: action === "grant",
        measure,
      });
      if (result.envelope) onResult?.(result.envelope);
      setNote(result.note);
    } catch {
      setNote("The request was refused.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      data-testid="grant-askback-card"
      role="region"
      aria-label="Grant ask-back"
      className="mt-3 max-w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3"
    >
      <p data-testid="grant-askback-text" className="break-words text-sm text-[var(--color-ink)]">
        {card.text}
      </p>
      {card.formula ? (
        <p data-testid="grant-askback-formula" className="mt-2 break-words text-xs text-[var(--color-ink-muted)]">
          {card.formula}
        </p>
      ) : null}
      {card.sql_preview ? (
        <pre
          data-testid="grant-askback-sql"
          className="mt-2 max-w-full overflow-x-auto whitespace-pre-wrap text-xs text-[var(--color-ink)]"
        >
          {card.sql_preview}
        </pre>
      ) : null}
      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        {card.actions.map((action) =>
          action.id === "connect_source" ? (
            <Link
              key={action.id}
              to={ceoSafeHref(productMode, "/studio")}
              data-testid="grant-askback-connect"
              className={`${BUTTON} border-[var(--color-line)] text-[var(--color-ink)]`}
            >
              {action.label}
            </Link>
          ) : (
            <button
              key={action.id}
              type="button"
              data-testid={`grant-askback-${action.id}`}
              disabled={busy || (action.id === "grant" && card.can_grant === false) || !card.token}
              onClick={() => void send(action.id)}
              className={`${BUTTON} border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-ink)]`}
            >
              {action.label}
            </button>
          ),
        )}
        {(card.picks ?? []).map((pick) => (
          <button
            key={pick.name}
            type="button"
            data-testid={`grant-askback-pick-${pick.name}`}
            disabled={busy || !card.token}
            onClick={() => void send("pick_measure", pick.name)}
            className={`${BUTTON} border-[var(--color-line)] text-[var(--color-ink)]`}
          >
            {pick.name}
          </button>
        ))}
      </div>
      {note ? <p className="mt-2 text-sm text-[var(--color-ink-muted)]">{note}</p> : null}
    </section>
  );
}
