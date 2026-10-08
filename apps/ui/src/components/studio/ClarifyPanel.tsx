import type { ClarifyOption, ClarifyPlan, InsightTiers } from "@/lib/types";

export type { ClarifyOption, ClarifyPlan, InsightTiers };

/**
 * ASK-GUIDE-01. Buttons for server readings. Nothing runs until Run.
 * Tier 3 buttons are suggestions: they do not call ask or Run.
 */

const TRUST: Record<string, string> = {
  L0_CERTIFIED: "L0",
  L1_GOVERNED_METRIC: "L1",
  L2_VALIDATED: "L2",
  L2_ANOMALOUS: "L2",
};

/** Body sent on Run. SQL preview stays on the screen; it is not the request. */
export function clarifyRunBody(option: ClarifyOption): {
  option_id: string;
  plan: ClarifyPlan;
} {
  return { option_id: option.id, plan: option.plan };
}

export function confirmCardText(option: ClarifyOption): string {
  const sentence = option.text.trim().replace(/\s+$/, "");
  return sentence.endsWith("?") ? sentence : `${sentence} Run?`;
}

type PanelProps = {
  options: ClarifyOption[];
  pickedId: string | null;
  onPick: (id: string) => void;
  onRephrase: () => void;
  onRun?: (option: ClarifyOption) => void;
};

export function ClarifyPanel({ options, pickedId, onPick, onRephrase, onRun }: PanelProps) {
  const picked = options.find((opt) => opt.id === pickedId) ?? null;
  return (
    <div data-testid="clarify-panel" className="mt-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
        Possible readings
      </p>
      <div className="mt-2 flex flex-col gap-2">
        {options.map((opt) => (
          <button
            key={opt.id}
            type="button"
            data-testid="clarify-option"
            data-option-id={opt.id}
            onClick={() => onPick(opt.id)}
            className="border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-2 text-left text-sm text-[var(--color-ink)] hover:border-[var(--color-accent)]"
          >
            <span>{opt.text}</span>
            {opt.sql_preview ? (
              <pre className="mt-1 overflow-x-auto font-mono text-[11px] text-[var(--color-ink-muted)]">
                {opt.sql_preview}
              </pre>
            ) : null}
          </button>
        ))}
        <button
          type="button"
          data-testid="clarify-rephrase"
          onClick={onRephrase}
          className="border border-dashed border-[var(--color-line)] px-3 py-2 text-left text-sm text-[var(--color-ink-muted)]"
        >
          None of these, let me rephrase
        </button>
      </div>
      {picked ? (
        <div
          data-testid="clarify-confirm"
          className="mt-3 border border-[var(--color-line)] bg-[var(--color-paper)] px-3 py-3"
        >
          <p data-testid="clarify-confirm-text" className="text-sm text-[var(--color-ink)]">
            {confirmCardText(picked)}
          </p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              data-testid="clarify-run"
              onClick={() => onRun?.(picked)}
              className="border border-[var(--color-accent)] bg-[var(--color-accent)] px-3 py-1 text-sm text-white"
            >
              Run
            </button>
            <button
              type="button"
              data-testid="clarify-cancel"
              onClick={onRephrase}
              className="border border-[var(--color-line)] px-3 py-1 text-sm"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function TrustBadge({ badge }: { badge?: string | null }) {
  const short = badge ? TRUST[badge] : undefined;
  if (!short) return null;
  return (
    <p data-testid="trust-badge" data-trust={short} className="text-[11px] font-semibold uppercase">
      {short}
    </p>
  );
}

export function InsightTiersView({ insights }: { insights?: InsightTiers | null }) {
  if (!insights) return null;
  const tier3 = Array.isArray(insights.tier3) ? insights.tier3.slice(0, 3) : [];
  return (
    <div data-testid="insight-tiers" className="mt-3">
      {insights.tier1 ? (
        <p data-testid="insight-tier1" className="text-sm font-medium text-[var(--color-ink)]">
          {insights.tier1.label}: {String(insights.tier1.value)}
        </p>
      ) : null}
      {insights.tier2 ? (
        <p data-testid="insight-tier2" className="mt-2 text-sm text-[var(--color-ink)]">
          {insights.tier2.text}
        </p>
      ) : null}
      {tier3.length > 0 ? (
        <div className="mt-2 flex flex-col gap-2">
          {tier3.map((q) => (
            <button
              key={q}
              type="button"
              data-testid="insight-tier3"
              data-executes="false"
              className="border border-[var(--color-line)] px-3 py-2 text-left text-sm"
            >
              {q}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
