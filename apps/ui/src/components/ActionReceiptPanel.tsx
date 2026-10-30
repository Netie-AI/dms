import type { ActionReceipt } from "@/lib/actions";
import { cellText, describeActionReceipt, fieldLabel, objectText } from "@/lib/actionReceipt";

const DEFECT_TITLE: Record<string, string> = {
  unverified: "Not verified",
  not_executed: "No write executed",
  no_ledger_entry: "No ledger receipt",
  hash_unchanged: "Nothing provably changed",
  chain_unverified: "Ledger chain did not verify",
  chain_unproven: "Ledger chain not read back",
};

function short(hash: string | null | undefined): string {
  return hash ? hash.slice(0, 12) : "none";
}

function evidenceText(r: ActionReceipt): string {
  const ev = r.evidence ?? {};
  if (ev.question) {
    const n = ev.row_count ?? 0;
    return `Cited answer: ${ev.question} - ${ev.badge ?? "unknown"}, ${n} ${n === 1 ? "row" : "rows"}`;
  }
  if (ev.reverts_decision_id) return `Reverts decision ${ev.reverts_decision_id.slice(0, 8)}`;
  return "No cited answer";
}

/**
 * The receipt of one governed action apply. Says "Applied" only when
 * describeActionReceipt finds no defect; otherwise it names the defect.
 * Revert proposes the inverse change; it never applies it.
 */
export function ActionReceiptPanel({
  receipt,
  onRevert,
  reverting = false,
}: {
  receipt: ActionReceipt;
  onRevert?: () => void;
  reverting?: boolean;
}) {
  const { sentence, defect } = describeActionReceipt(receipt);
  const healthy = defect === null;
  const sod = receipt.separation_of_duties;
  return (
    <section
      data-testid="action-receipt"
      className={`border px-4 py-3 text-sm ${
        healthy ? "border-[var(--color-line)]" : "border-[var(--color-danger)]"
      } bg-[var(--color-surface)]`}
    >
      {healthy ? (
        <p data-testid="action-healthy" className="font-semibold">
          Applied - 1 row in DMS writeback changed and re-read
        </p>
      ) : (
        <p data-testid="action-defect" className="font-semibold text-[var(--color-danger)]">
          {DEFECT_TITLE[defect] ?? defect}
        </p>
      )}
      <p data-testid="action-sentence" className="mt-1 text-[var(--color-ink-muted)]">
        {sentence}
      </p>
      <dl className="mt-3 space-y-1 text-xs">
        <div>
          <dt className="inline font-semibold">{fieldLabel(receipt.field)} </dt>
          <dd className="inline">{objectText(receipt.object?.object_key)}</dd>
        </div>
        <div>
          <dt className="inline font-semibold">before </dt>
          <dd className="inline" data-testid="action-before">
            {cellText(receipt.before)}
          </dd>
          <dt className="inline font-semibold"> after </dt>
          <dd className="inline" data-testid="action-after">
            {cellText(receipt.after)}
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold">hashes </dt>
          <dd className="inline font-mono" data-testid="action-hashes">
            {`${short(receipt.before_hash)} -> ${short(receipt.after_hash)}`}
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold">ledger </dt>
          <dd className="inline font-mono" data-testid="action-ledger">
            {receipt.ledger_entry_id
              ? `${receipt.ledger_entry_id}${receipt.ledger_seq != null ? ` (seq ${receipt.ledger_seq})` : ""}`
              : "no entry"}
          </dd>
        </div>
        <div>
          <dt className="inline font-semibold">approval </dt>
          <dd className="inline">{receipt.approval_ref || "none"}</dd>
        </div>
      </dl>
      <p data-testid="action-evidence" className="mt-2 text-xs">
        {evidenceText(receipt)}
      </p>
      <p data-testid="action-sod" className="mt-1 text-xs text-[var(--color-ink-muted)]">
        {sod?.proven
          ? `Separation of duties: ${sod.reason ?? "recorded"}`
          : "Proposed and applied by the same configured identity (no second reviewer)."}
      </p>
      <p data-testid="action-source-unchanged" className="mt-1 text-xs text-[var(--color-ink-muted)]">
        {receipt.mutation?.source_system_changed === false
          ? "Source system unchanged: DMS writes only its own writeback table. No customer source or Excel file is edited."
          : "The server did not confirm the source system is unchanged."}
      </p>
      {onRevert && (
        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            data-testid="action-revert"
            disabled={reverting}
            onClick={onRevert}
            className="border border-[var(--color-line)] px-3 py-1 text-xs"
          >
            Propose revert
          </button>
          <span className="text-xs text-[var(--color-ink-muted)]">
            Revert creates a new proposal; nothing changes until it is applied.
          </span>
        </div>
      )}
    </section>
  );
}
