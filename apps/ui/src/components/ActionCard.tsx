import { useRef, useState } from "react";
import {
  applyAction,
  declineAction,
  describeRefusal,
  isOutcomeUnknown,
  refusalInfo,
  type ActionProposal,
  type ActionReceipt,
  type DeclineResult,
  type RefusalInfo,
} from "@/lib/actions";

/** Matches dms_core.control_plane.actions.APPROVAL_REF_MIN. */
export const APPROVAL_REF_MIN = 3;

const SOURCE_LABEL: Record<string, string> = {
  demo_warehouse: "demo warehouse",
};

export type ActionCardOutcome =
  | { kind: "applied"; receipt: ActionReceipt }
  | { kind: "declined"; result: DeclineResult };

/**
 * One decision attempt. ``rearm`` is false when the outcome is unknown (a 5xx
 * with no route code, a dropped connection, commit_outcome_unknown): the write
 * may exist, so the card stays locked and the steward checks Recent decisions.
 */
export type DecideResult =
  | { ok: true; outcome: ActionCardOutcome }
  | { ok: false; refusal: RefusalInfo; rearm: boolean };

export async function decide(
  kind: "apply" | "decline",
  proposal: ActionProposal,
  approvalRef: string,
): Promise<DecideResult> {
  try {
    if (kind === "apply") {
      const receipt = await applyAction(proposal.proposal_id, proposal.idempotency_token, approvalRef);
      return { ok: true, outcome: { kind: "applied", receipt } };
    }
    const result = await declineAction(proposal.proposal_id, proposal.idempotency_token, approvalRef);
    return { ok: true, outcome: { kind: "declined", result } };
  } catch (e) {
    const refusal = refusalInfo(e);
    return { ok: false, refusal, rearm: !isOutcomeUnknown(refusal.code) };
  }
}

type ViewProps = {
  proposal: ActionProposal;
  approvalRef: string;
  onApprovalRefChange: (v: string) => void;
  busy: boolean;
  /** The last attempt's outcome is unknown: Apply and Decline stay off. */
  locked?: boolean;
  refusal: RefusalInfo | null;
  onApply: () => void;
  onDecline: () => void;
};

function proposedText(p: ActionProposal): string {
  const d = p.diff;
  if (d.op === "clear") return `clear (back to source ${d.before?.source_value ?? "none"})`;
  return d.value ?? "—";
}

/** Presentational card: the field diff, the approval reference, Apply and Decline. */
export function ActionCardView({
  proposal,
  approvalRef,
  onApprovalRefChange,
  busy,
  locked = false,
  refusal,
  onApply,
  onDecline,
}: ViewProps) {
  const d = proposal.diff;
  const refOk = approvalRef.trim().length >= APPROVAL_REF_MIN;
  const off = !refOk || busy || locked;
  const keyText = Object.values(d.object_key ?? {}).join(" @ ");
  return (
    <div
      data-testid="action-card"
      className="border border-[var(--color-line)] bg-[var(--color-surface)] px-4 py-3 text-sm"
    >
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="text-[var(--color-ink-muted)]">
            <th className="py-1 pr-3 font-semibold">Object</th>
            <th className="py-1 pr-3 font-semibold">Field</th>
            <th className="py-1 pr-3 font-semibold">Source</th>
            <th className="py-1 pr-3 font-semibold">Current override</th>
            <th className="py-1 font-semibold">Proposed</th>
          </tr>
        </thead>
        <tbody>
          <tr data-testid="action-diff-row">
            <td className="py-1 pr-3">{keyText}</td>
            <td className="py-1 pr-3 font-mono">{d.field}</td>
            <td className="py-1 pr-3" data-testid="diff-source">
              {d.before?.source_value ?? "—"}
            </td>
            <td className="py-1 pr-3" data-testid="diff-current">
              {d.before?.value ?? "—"}
            </td>
            <td className="py-1 font-semibold" data-testid="diff-proposed">
              {proposedText(proposal)}
            </td>
          </tr>
        </tbody>
      </table>
      <p className="mt-2 text-xs">{d.reason || "No reason given."}</p>
      <p className="mt-1 text-xs text-[var(--color-ink-muted)]">
        Source: {SOURCE_LABEL[d.source_kind] ?? d.source_kind}
        {d.evidence?.question ? ` · cites: ${d.evidence.question}` : ""}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          data-testid="action-approval-ref"
          aria-label="Approval reference"
          placeholder="Change ticket id, e.g. CHG-1234 (required)"
          required
          disabled={locked}
          value={approvalRef}
          onChange={(e) => onApprovalRefChange(e.target.value)}
          className="h-8 w-56 border border-[var(--color-line)] bg-[var(--color-surface)] px-2 text-xs"
        />
        <button
          type="button"
          data-testid="action-apply"
          disabled={off}
          onClick={onApply}
          className="h-8 bg-[var(--color-accent)] px-3 text-xs text-[var(--color-on-accent)]"
        >
          Apply
        </button>
        <button
          type="button"
          data-testid="action-decline"
          disabled={off}
          onClick={onDecline}
          className="h-8 border border-[var(--color-line)] px-3 text-xs"
        >
          Decline
        </button>
      </div>
      {refusal && locked && (
        <div data-testid="action-outcome-unknown" className="mt-2 text-xs">
          <p className="font-semibold">Outcome unknown - this was not confirmed either way.</p>
          <p data-testid="action-outcome-unknown-text">{describeRefusal(refusal.code, refusal.detail)}</p>
          <p className="text-[var(--color-ink-muted)]">
            Recent decisions below has been refreshed. If a decision for this object is listed
            there, this proposal was decided. If none is listed and the proposal is still pending
            after you reload the page, it was not decided and can be decided then. Apply stays off
            on this card until the page is reloaded.
          </p>
        </div>
      )}
      {refusal && !locked && (
        <p data-testid="action-refusal" className="mt-2 text-xs text-[var(--color-danger)]">
          {describeRefusal(refusal.code, refusal.detail)}
        </p>
      )}
    </div>
  );
}

/**
 * A pending governed action. Apply and Decline both need an approval
 * reference; a firedRef guard (as AnswerMessage's confirmFired) stops a
 * double click from sending the token twice. A definite refusal re-arms the
 * guard so the steward can retry the same token after, say, a ledger outage.
 * An unknown outcome does not: the card locks and asks the parent to refresh
 * Recent decisions (onOutcomeUnknown).
 */
export function ActionCard({
  proposal,
  onDone,
  onOutcomeUnknown,
}: {
  proposal: ActionProposal;
  onDone: (outcome: ActionCardOutcome) => void;
  onOutcomeUnknown?: () => void;
}) {
  const [approvalRef, setApprovalRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [locked, setLocked] = useState(false);
  const [refusal, setRefusal] = useState<RefusalInfo | null>(null);
  const firedRef = useRef(false);

  const run = async (kind: "apply" | "decline") => {
    if (firedRef.current) return;
    if (approvalRef.trim().length < APPROVAL_REF_MIN) return;
    firedRef.current = true;
    setBusy(true);
    setRefusal(null);
    try {
      const out = await decide(kind, proposal, approvalRef.trim());
      if (out.ok) {
        onDone(out.outcome);
      } else if (out.rearm) {
        firedRef.current = false;
        setRefusal(out.refusal);
      } else {
        setLocked(true);
        setRefusal(out.refusal);
        onOutcomeUnknown?.();
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <ActionCardView
      proposal={proposal}
      approvalRef={approvalRef}
      onApprovalRefChange={setApprovalRef}
      busy={busy}
      locked={locked}
      refusal={refusal}
      onApply={() => void run("apply")}
      onDecline={() => void run("decline")}
    />
  );
}
