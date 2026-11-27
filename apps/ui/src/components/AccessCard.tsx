import { useId, useState } from "react";
import { nextAccessRequestState, postAccessRequest } from "@/lib/api";
import type { AccessNeeded, GrantedSpace } from "@/lib/types";
import { accessRequestVisible } from "@/components/studio/abstainReason";

const BUTTON =
  "min-h-[44px] w-full border px-3 py-2 text-left text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)] disabled:opacity-50 sm:w-auto";

/**
 * Which Space has the table, plus Switch and (only when the route exists)
 * Ask the steward. A missing route hides the request button.
 */
export function AccessCard({
  access,
  busy,
  onSwitch,
  initialRouteMissing = false,
}: {
  access: AccessNeeded;
  busy?: boolean;
  onSwitch: (space: GrantedSpace) => void;
  initialRouteMissing?: boolean;
}) {
  const titleId = useId();
  const [routeMissing, setRouteMissing] = useState(initialRouteMissing);
  const [note, setNote] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const showRequest = accessRequestVisible(access.can_request, routeMissing);

  async function request(space: GrantedSpace) {
    if (!showRequest || pending) return;
    setPending(true);
    setNote(null);
    try {
      const result = await postAccessRequest(space.id, { table: access.table });
      const next = nextAccessRequestState(result);
      setRouteMissing(next.routeMissing);
      setNote(next.note);
    } catch {
      setNote("The access request was refused.");
    } finally {
      setPending(false);
    }
  }

  return (
    <section
      data-testid="access-card"
      role="region"
      aria-labelledby={titleId}
      className="mt-3 max-w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3"
    >
      <h2
        id={titleId}
        className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]"
      >
        Access needed
      </h2>
      <p data-testid="access-table" className="mt-2 break-words text-sm text-[var(--color-ink)]">
        {access.table} is not shared with this Space.
      </p>
      <ul className="mt-3 grid gap-3">
        {access.granted_in_spaces.map((space) => (
          <li key={space.id} data-testid={`access-space-${space.id}`} className="max-w-full">
            <p className="text-sm font-medium text-[var(--color-ink)]">{space.name}</p>
            <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              <button
                type="button"
                data-testid={`access-switch-${space.id}`}
                disabled={busy || pending}
                aria-label={`Switch to that Space, ${space.name}`}
                onClick={() => onSwitch(space)}
                className={`${BUTTON} border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-ink)]`}
              >
                Switch to that Space
              </button>
              {showRequest ? (
                <button
                  type="button"
                  data-testid={`access-request-${space.id}`}
                  disabled={busy || pending}
                  aria-label={`Ask the steward for access, ${space.name}`}
                  onClick={() => {
                    void request(space);
                  }}
                  className={`${BUTTON} border-[var(--color-line)] bg-[var(--color-surface)] text-[var(--color-ink)]`}
                >
                  Ask the steward for access
                </button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      {note ? (
        <p data-testid="access-note" className="mt-2 text-xs text-[var(--color-ink-muted)]" role="status">
          {note}
        </p>
      ) : null}
    </section>
  );
}
