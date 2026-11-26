import { Badge } from "@/components/Badge";
import type { ApprovalState, InsightItem, ProofRow } from "@/lib/insightProof";
import { toProofCard } from "@/lib/insightProof";

/**
 * INS-PROOF-03 - one proven insight with its proof, or the abstain card.
 *
 * Presentational: the page owns the fetches. "Approve as steward" only asks
 * for a preview; the pin is POSTed from the confirm dialog's own button.
 */
export function InsightProofCard({
  item,
  approval = { phase: "idle" },
  onApprove,
  onConfirm,
  onCancel,
  onReplay,
}: {
  item: InsightItem;
  approval?: ApprovalState;
  onApprove?: () => void;
  onConfirm?: () => void;
  onCancel?: () => void;
  onReplay?: () => void;
}) {
  const card = toProofCard(item);
  if (card.kind === "abstain") {
    return (
      <article
        data-testid="insight-abstain-card"
        className="border border-[var(--color-line)] bg-[var(--color-paper-2)] p-4"
      >
        <Badge kind="ABSTAIN" />
        <p className="mt-2 text-sm" data-testid="insight-abstain-text">
          {card.text}
        </p>
      </article>
    );
  }
  return (
    <article data-testid="insight-proof-card" className="border border-[var(--color-line)] p-4">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="text-base font-semibold" data-testid="insight-headline">
          {card.headline}
        </h3>
        <Badge kind={card.badge} />
      </header>
      <table className="mt-3 w-full text-xs" data-testid="insight-rows">
        <thead>
          <tr>
            <th className="text-left">label</th>
            <th className="text-right">value</th>
            <th className="text-right">share %</th>
            <th className="text-right">cumulative %</th>
          </tr>
        </thead>
        <tbody>
          {card.rows.map((r: ProofRow, i: number) => (
            <tr key={`${r.label ?? "null"}-${i}`}>
              <td>{r.label ?? "(no value)"}</td>
              <td className="text-right">{String(r.value)}</td>
              <td className="text-right">{String(r.share_pct)}</td>
              <td className="text-right">{String(r.cum_share_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details className="mt-3 text-xs" data-testid="insight-sql">
        <summary className="cursor-pointer">SQL</summary>
        <pre className="mt-1 whitespace-pre-wrap">{card.sql}</pre>
      </details>
      <section className="mt-3 text-xs" data-testid="insight-proof-lines">
        <h4 className="font-semibold">How this is proven</h4>
        <ul className="mt-1 list-disc pl-4">
          {card.lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>
      <footer className="mt-3 flex flex-wrap gap-2 text-xs">
        {onApprove && !card.assetId ? (
          <button
            type="button"
            data-testid="approve-steward"
            className="border border-[var(--color-line)] px-2 py-1"
            disabled={approval.phase !== "idle" && approval.phase !== "refused"}
            onClick={onApprove}
          >
            Approve as steward
          </button>
        ) : null}
        {onReplay && card.assetId ? (
          <button
            type="button"
            data-testid="replay-now"
            className="border border-[var(--color-line)] px-2 py-1"
            onClick={onReplay}
          >
            Replay now
          </button>
        ) : null}
        {approval.phase === "pinned" ? (
          <span data-testid="approve-pinned">Pinned as {approval.assetId}</span>
        ) : null}
        {approval.phase === "refused" ? (
          <span data-testid="approve-refused">Not pinned: {approval.code}</span>
        ) : null}
      </footer>
      {approval.phase === "confirming" ? (
        <div
          role="dialog"
          aria-modal="true"
          data-testid="approve-dialog"
          className="mt-3 border border-[var(--color-line)] bg-[var(--color-paper-2)] p-3 text-xs"
        >
          <p>Pin this insight as approved? It will be re-run before it is shown again.</p>
          <p className="mt-1 font-semibold" data-testid="approve-dialog-headline">
            {approval.preview.envelope.text}
          </p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              data-testid="approve-confirm"
              className="border border-[var(--color-line)] px-2 py-1"
              onClick={onConfirm}
            >
              Confirm approval
            </button>
            <button
              type="button"
              data-testid="approve-cancel"
              className="px-2 py-1"
              onClick={onCancel}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </article>
  );
}
