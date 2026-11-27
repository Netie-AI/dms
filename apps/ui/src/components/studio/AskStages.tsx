import { useEffect, useState } from "react";

/** One ladder event. `stage` is a public token. `status` is started, done, failed, cancelled, or rungN. */
export type AskStageEvent = {
  stage: string;
  t_ms: number;
  status: string;
};

const LABELS: Record<string, string> = {
  retrieve: "Retrieve",
  plan: "Plan",
  generate: "Generate",
  validate: "Validate",
  retry: "Retry",
  vote: "Vote",
  reconfirm: "Reconfirm",
};

function rungNumber(status: string): string | null {
  if (status.length !== 5 || !status.startsWith("rung")) return null;
  const digit = status[4];
  if (digit < "1" || digit > "9") return null;
  return digit;
}

export function stageLabel(event: AskStageEvent): string {
  const rung = event.stage === "retry" ? rungNumber(event.status) : null;
  if (rung) return `Retry rung ${rung}`;
  return LABELS[event.stage] ?? event.stage;
}

type Props = {
  stages: AskStageEvent[];
  onCancel?: () => void;
};

/**
 * Stage list for one in-flight ask. Elapsed seconds come from `t_ms`.
 * The open stage ticks once a second on the client clock.
 * Cancel calls the caller's abort, which is the existing ask disconnect.
 */
export function AskStages({ stages, onCancel }: Props) {
  const [tick, setTick] = useState(0);
  const last = stages.length > 0 ? stages[stages.length - 1] : null;
  const open = last != null && last.status === "started";
  useEffect(() => {
    if (!open) return;
    setTick(0);
    const id = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [open, stages]);
  if (stages.length === 0) return null;

  return (
    <div data-testid="ask-stages" className="mt-3 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
          Ask progress
        </p>
        {onCancel ? (
          <button
            type="button"
            data-testid="ask-stages-cancel"
            onClick={onCancel}
            className="border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1 text-xs text-[var(--color-ink)] hover:border-[var(--color-accent)]"
          >
            Cancel
          </button>
        ) : null}
      </div>
      <ol className="mt-2 grid gap-1">
        {stages.map((event, index) => {
          const base = Math.max(0, Math.floor(event.t_ms / 1000));
          const seconds = index === stages.length - 1 && event.status === "started" ? base + tick : base;
          return (
            <li
              key={`${event.stage}-${event.status}-${event.t_ms}-${index}`}
              data-testid="ask-stage"
              data-stage={event.stage}
              data-status={event.status}
              className="flex items-baseline justify-between gap-3 text-sm text-[var(--color-ink)]"
            >
              <span>{stageLabel(event)}</span>
              <span className="text-xs text-[var(--color-ink-muted)]">{seconds}s</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

let streamOff = false;

/** Subscribe to stage events for one ask id. A 404 (flag off) stops further connects. */
export function useAskStages(askId: string | null): AskStageEvent[] {
  const [stages, setStages] = useState<AskStageEvent[]>([]);
  useEffect(() => {
    if (!askId || streamOff) {
      setStages([]);
      return;
    }
    setStages([]);
    let closed = false;
    const source = new EventSource(`/api/v1/studio/ask/${encodeURIComponent(askId)}/events`);
    source.onmessage = (msg) => {
      if (closed) return;
      let body: AskStageEvent;
      try {
        body = JSON.parse(msg.data) as AskStageEvent;
      } catch {
        return;
      }
      if (typeof body.stage !== "string" || typeof body.t_ms !== "number") return;
      const event = { stage: body.stage, t_ms: body.t_ms, status: String(body.status ?? "") };
      setStages((prev) => [...prev, event]);
    };
    source.onerror = () => {
      source.close();
      if (!closed) {
        setStages((prev) => {
          if (prev.length === 0) streamOff = true;
          return prev;
        });
      }
    };
    return () => {
      closed = true;
      source.close();
    };
  }, [askId]);
  return stages;
}
