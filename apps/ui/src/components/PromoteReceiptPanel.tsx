import type { PromoteReceipt, PromoteReceiptState } from "@/lib/api";
import { describeReceiptDefect } from "@/lib/promoteReceipt";

function fmtCount(n: number | null): string {
  return n == null ? "null" : String(n);
}

function ConservationLine({ r }: { r: PromoteReceipt }) {
  return (
    <p data-testid="promote-conservation">
      <span data-testid="promote-source-rows">{fmtCount(r.source_rows)}</span>
      {" -> "}
      <span data-testid="promote-passed">{r.passed}</span>
      {" + "}
      <span data-testid="promote-quarantined">{r.quarantined}</span>
    </p>
  );
}

function ReceiptMeta({
  r,
  recordedAt,
  runs,
}: {
  r: PromoteReceipt;
  recordedAt: string;
  runs: number;
}) {
  const reasons = Object.entries(r.counts_by_reason).sort((a, b) => b[1] - a[1]);
  return (
    <>
      <dl className="mt-4 space-y-1 text-xs">
        <div>
          <dt className="inline font-semibold">unmatched </dt>
          <dd className="inline" data-testid="promote-unmatched">
            {r.unmatched}
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold">reconciled </dt>
          <dd className="inline" data-testid="promote-reconciled">
            {String(r.reconciled)}
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold">recorded_at </dt>
          <dd className="inline" data-testid="promote-recorded-at">
            {recordedAt}
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold">runs </dt>
          <dd className="inline" data-testid="promote-runs">
            {runs}
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold">sources </dt>
          <dd className="inline" data-testid="promote-sources">
            {r.sources.join(", ") || "-"}
          </dd>
        </div>
        {r.quarantine_table ? (
          <div>
            <dt className="inline font-semibold">quarantine_table </dt>
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
    </>
  );
}

export function PromoteReceiptPanel({
  state,
  error,
}: {
  state: PromoteReceiptState | null;
  error?: string | null;
}) {
  if (error) {
    return (
      <p
        data-testid="receipt-error"
        className="mt-2 text-sm text-[var(--color-danger)]"
      >
        {error}
      </p>
    );
  }
  if (!state) return null;
  if (state.state === "no_receipt_yet") {
    return (
      <p
        data-testid="receipt-none"
        className="mt-2 text-sm text-[var(--color-ink-muted)]"
      >
        No promote has run for this table yet.
      </p>
    );
  }
  const r = state.receipt;
  const defect = describeReceiptDefect(r);
  if (!defect) {
    return (
      <div data-testid="receipt-healthy" className="mt-2 text-sm text-[var(--color-ink)]">
        <ConservationLine r={r} />
        <ReceiptMeta r={r} recordedAt={state.recorded_at} runs={state.runs} />
      </div>
    );
  }
  return (
    <div
      data-testid="receipt-defect"
      className="mt-2 border border-[var(--color-danger)]/40 bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-danger)]"
    >
      <p data-testid="receipt-defect-reason">{defect.sentence}</p>
      {defect.kind !== "unmeasured" ? <ConservationLine r={r} /> : null}
      <ReceiptMeta r={r} recordedAt={state.recorded_at} runs={state.runs} />
    </div>
  );
}
