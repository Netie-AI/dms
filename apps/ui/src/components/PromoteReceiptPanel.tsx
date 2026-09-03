import type { PromoteReceiptState } from "@/lib/api";

function fmtCount(n: number | null): string {
  return n == null ? "null" : String(n);
}

export function PromoteReceiptPanel({ state }: { state: PromoteReceiptState }) {
  if (state.state === "no_receipt_yet") {
    return (
      <p data-testid="promote-no-receipt" className="text-sm text-[var(--color-ink-muted)]">
        no_receipt_yet TODO(EPIC-024 T4)
      </p>
    );
  }
  const r = state.receipt;
  const healthy = r.reconciled;
  const reasons = Object.entries(r.counts_by_reason).sort((a, b) => b[1] - a[1]);
  return (
    <div
      className={
        healthy
          ? "text-sm text-[var(--color-ink)]"
          : "text-sm text-[var(--color-ink)]"
      }
    >
      {!healthy ? (
        <p data-testid="promote-unhealthy" className="text-xs text-[var(--color-ink-muted)]">
          reconciled false TODO(EPIC-024 T4)
        </p>
      ) : null}
      <p data-testid="promote-conservation">
        <span data-testid="promote-source-rows">{fmtCount(r.source_rows)}</span>
        {" -> "}
        <span data-testid="promote-passed">{r.passed}</span>
        {" + "}
        <span data-testid="promote-quarantined">{r.quarantined}</span>
      </p>
      <dl className="mt-4 space-y-1 text-xs text-[var(--color-ink-muted)]">
        <div>
          <dt className="inline font-semibold text-[var(--color-ink)]">unmatched </dt>
          <dd className="inline" data-testid="promote-unmatched">
            {r.unmatched}
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold text-[var(--color-ink)]">reconciled </dt>
          <dd className="inline" data-testid="promote-reconciled">
            {String(r.reconciled)}
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold text-[var(--color-ink)]">recorded_at </dt>
          <dd className="inline" data-testid="promote-recorded-at">
            {state.recorded_at}
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold text-[var(--color-ink)]">runs </dt>
          <dd className="inline" data-testid="promote-runs">
            {state.runs}
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold text-[var(--color-ink)]">sources </dt>
          <dd className="inline" data-testid="promote-sources">
            {r.sources.join(", ") || "—"}
          </dd>
        </div>
        {r.quarantine_table ? (
          <div>
            <dt className="inline font-semibold text-[var(--color-ink)]">quarantine_table </dt>
            <dd className="inline" data-testid="promote-quarantine-table">
              {r.quarantine_table}
            </dd>
          </div>
        ) : null}
      </dl>
      <ul data-testid="promote-reasons" className="mt-3 list-disc pl-5 text-xs">
        {reasons.map(([reason, count]) => (
          <li key={reason}>
            {reason}: {count}
          </li>
        ))}
      </ul>
    </div>
  );
}
