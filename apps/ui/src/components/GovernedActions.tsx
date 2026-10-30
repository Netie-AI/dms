import { useEffect, useRef, useState } from "react";
import { ActionCard, type ActionCardOutcome } from "@/components/ActionCard";
import { ActionReceiptPanel } from "@/components/ActionReceiptPanel";
import {
  describeRefusal,
  fetchDecisions,
  fetchOverrides,
  listActionProposals,
  proposeAction,
  refusalInfo,
  revertDecision,
  type ActionDecision,
  type ActionOverride,
  type ActionProposal,
  type ActionReceipt,
  type RefusalInfo,
} from "@/lib/actions";
import { cellText, decisionText } from "@/lib/actionReceipt";

export type GovernedActionsSeed = {
  proposals?: ActionProposal[];
  decisions?: ActionDecision[];
  overrides?: ActionOverride[];
  receipts?: ActionReceipt[];
};

/**
 * Last-request-wins guard for reloads. ``begin()`` starts a load and returns
 * ``isCurrent``; only the most recent load may write state. A Space switch
 * starts a new load, so a slower answer for the previous Space is dropped
 * instead of showing that Space's cards under the new one.
 */
export function createLatestGate(): { begin: () => () => boolean } {
  let seq = 0;
  return {
    begin() {
      const mine = ++seq;
      return () => mine === seq;
    },
  };
}

function keyText(key: Record<string, string> | null | undefined): string {
  return Object.values(key ?? {}).join(" @ ");
}

const inputClass =
  "h-9 border border-[var(--color-line)] bg-[var(--color-surface)] px-2 text-sm";

/**
 * The "Governed actions" section of AmendPage. Rendered only when the server
 * answers the ACT-02 registry probe (DMS_ACTIONS=1); AmendPage renders nothing
 * of it otherwise.
 */
export function GovernedActions({
  spaceId,
  seed,
  onChanged,
}: {
  spaceId: string | null;
  seed?: GovernedActionsSeed;
  onChanged?: () => void;
}) {
  const [proposals, setProposals] = useState<ActionProposal[]>(seed?.proposals ?? []);
  const [decisions, setDecisions] = useState<ActionDecision[]>(seed?.decisions ?? []);
  const [overrides, setOverrides] = useState<ActionOverride[]>(seed?.overrides ?? []);
  const [receipts, setReceipts] = useState<ActionReceipt[]>(seed?.receipts ?? []);
  const [loadError, setLoadError] = useState<RefusalInfo | null>(null);

  const [sku, setSku] = useState("");
  const [loc, setLoc] = useState("");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [auditId, setAuditId] = useState("");
  const [proposing, setProposing] = useState(false);
  const [proposeMsg, setProposeMsg] = useState<string | null>(null);
  const [proposeRefusal, setProposeRefusal] = useState<RefusalInfo | null>(null);
  const [revertMsg, setRevertMsg] = useState<string | null>(null);

  const gate = useRef(createLatestGate());

  const reload = () => {
    const isCurrent = gate.current.begin();
    if (!spaceId) return;
    setLoadError(null);
    Promise.all([listActionProposals(spaceId), fetchDecisions(spaceId), fetchOverrides(spaceId)])
      .then(([p, d, o]) => {
        if (!isCurrent()) return;
        setProposals(p);
        setDecisions(d);
        setOverrides(o);
      })
      .catch((e: unknown) => {
        if (isCurrent()) setLoadError(refusalInfo(e));
      });
  };

  useEffect(() => {
    // A Space switch must not leave another Space's proposals on screen, and a
    // slower answer for the previous Space must not land after it (the gate).
    setProposals(seed?.proposals ?? []);
    setDecisions(seed?.decisions ?? []);
    setOverrides(seed?.overrides ?? []);
    setReceipts(seed?.receipts ?? []);
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spaceId]);

  const changed = () => {
    reload();
    onChanged?.();
  };

  const propose = async () => {
    if (!spaceId || proposing) return;
    setProposing(true);
    setProposeMsg(null);
    setProposeRefusal(null);
    try {
      const out = await proposeAction({
        space_id: spaceId,
        object_key: { sku: sku.trim(), location_id: loc.trim() },
        value: value.trim(),
        reason: reason.trim(),
        evidence_audit_id: auditId.trim() ? auditId.trim() : undefined,
      });
      setProposeMsg(`${out.diff.summary}. ${out.mutation.detail ?? ""}`.trim());
      changed();
    } catch (e) {
      setProposeRefusal(refusalInfo(e));
    } finally {
      setProposing(false);
    }
  };

  const done = (outcome: ActionCardOutcome) => {
    if (outcome.kind === "applied") setReceipts((r) => [outcome.receipt, ...r]);
    changed();
  };

  const revert = async (decisionId: string) => {
    setRevertMsg(null);
    try {
      const out = await revertDecision(decisionId);
      setRevertMsg(`Revert proposed: ${out.diff.summary}. It still needs its own apply.`);
      changed();
    } catch (e) {
      const info = refusalInfo(e);
      setRevertMsg(describeRefusal(info.code, info.detail));
    }
  };

  const pending = proposals.filter(
    (p) => p.status === "pending_confirm" && p.diff?.kind === "action",
  );

  return (
    <section data-testid="governed-actions" className="mt-10 max-w-3xl">
      <h2 className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-tight">
        Governed actions
      </h2>
      <p className="mt-2 text-sm text-[var(--color-ink-muted)]">
        One typed change to one object. Apply writes a single row of a DMS-owned writeback
        table, re-reads it and records a Cortex ledger receipt in the same transaction. The
        source system is never edited. One configured identity proposes and applies.
      </p>

      {!spaceId && (
        <p className="mt-3 text-sm text-[var(--color-ink-muted)]">
          Pick a Space to propose governed actions.
        </p>
      )}

      <form
        data-testid="action-propose-form"
        className="mt-4 grid max-w-xl grid-cols-2 gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void propose();
        }}
      >
        <input aria-label="SKU" placeholder="SKU (e.g. SKU-BETA)" value={sku} onChange={(e) => setSku(e.target.value)} className={inputClass} />
        <input aria-label="Location" placeholder="Location (e.g. WH-B)" value={loc} onChange={(e) => setLoc(e.target.value)} className={inputClass} />
        <input aria-label="New reorder level (kg)" placeholder="New reorder level (kg)" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} className={inputClass} />
        <input aria-label="Cited answer audit id" placeholder="Cited answer audit id (optional)" value={auditId} onChange={(e) => setAuditId(e.target.value)} className={inputClass} />
        <input aria-label="Reason" placeholder="Reason" value={reason} onChange={(e) => setReason(e.target.value)} className={`${inputClass} col-span-2`} />
        <button
          type="submit"
          disabled={!spaceId || proposing || !sku.trim() || !loc.trim() || !value.trim()}
          className="col-span-2 h-9 bg-[var(--color-accent)] px-4 text-sm text-[var(--color-on-accent)]"
        >
          Propose action
        </button>
      </form>
      {proposeMsg && (
        <p data-testid="action-proposed" className="mt-2 text-sm text-[var(--color-ink-muted)]">
          {proposeMsg}
        </p>
      )}
      {proposeRefusal && (
        <p data-testid="action-propose-refusal" className="mt-2 text-sm text-[var(--color-danger)]">
          {describeRefusal(proposeRefusal.code, proposeRefusal.detail)}
        </p>
      )}
      {loadError && (
        <p className="mt-2 text-sm text-[var(--color-danger)]">
          {describeRefusal(loadError.code, loadError.detail)}
        </p>
      )}

      <h3 className="mt-6 text-sm font-semibold">Pending</h3>
      <div className="mt-2 space-y-3">
        {pending.length === 0 && (
          <p className="text-sm text-[var(--color-ink-muted)]">No pending governed actions.</p>
        )}
        {pending.map((p) => (
          <ActionCard
            key={`${p.proposal_id}:${p.version_num}`}
            proposal={p}
            onDone={done}
            onOutcomeUnknown={changed}
          />
        ))}
      </div>

      {receipts.length > 0 && (
        <>
          <h3 className="mt-6 text-sm font-semibold">Receipts</h3>
          <div className="mt-2 space-y-3">
            {receipts.map((r) => (
              <ActionReceiptPanel
                key={r.decision_id}
                receipt={r}
                onRevert={() => void revert(r.decision_id)}
              />
            ))}
          </div>
        </>
      )}
      {revertMsg && (
        <p data-testid="action-revert-msg" className="mt-2 text-sm text-[var(--color-ink-muted)]">
          {revertMsg}
        </p>
      )}

      <h3 className="mt-6 text-sm font-semibold">Recent decisions</h3>
      <p data-testid="action-decisions-note" className="mt-1 text-xs text-[var(--color-ink-muted)]">
        Each row is what the DMS database recorded. The ledger chain is read back only on an
        apply receipt, so a row is not by itself a ledger proof.
      </p>
      <table data-testid="action-decisions" className="mt-2 w-full text-left text-xs">
        <thead>
          <tr className="text-[var(--color-ink-muted)]">
            <th className="py-1 pr-3">Decided</th>
            <th className="py-1 pr-3">Object</th>
            <th className="py-1 pr-3">Change</th>
            <th className="py-1 pr-3">Decision</th>
            <th className="py-1 pr-3">Ledger entry</th>
            <th className="py-1" />
          </tr>
        </thead>
        <tbody>
          {decisions.length === 0 && (
            <tr>
              <td colSpan={6} className="py-2 text-[var(--color-ink-muted)]">
                No decisions yet.
              </td>
            </tr>
          )}
          {decisions.map((d) => (
            <tr key={d.id}>
              <td className="py-1 pr-3">{d.decided_at ?? ""}</td>
              <td className="py-1 pr-3">{keyText(d.object_key)}</td>
              <td className="py-1 pr-3">
                {d.decision === "applied"
                  ? `${cellText(d.before)} -> ${cellText(d.after)}`
                  : "nothing written"}
              </td>
              <td className="py-1 pr-3" data-testid="action-decision-status">
                {decisionText(d, receipts)}
              </td>
              <td className="py-1 pr-3 font-mono">{d.cortex_entry_id ?? "none"}</td>
              <td className="py-1">
                {d.decision === "applied" && (
                  <button
                    type="button"
                    onClick={() => void revert(d.id)}
                    className="border border-[var(--color-line)] px-2 py-0.5"
                  >
                    Propose revert
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="mt-6 text-sm font-semibold">Current overrides</h3>
      <table data-testid="action-overrides" className="mt-2 w-full text-left text-xs">
        <thead>
          <tr className="text-[var(--color-ink-muted)]">
            <th className="py-1 pr-3">Object</th>
            <th className="py-1 pr-3">Field</th>
            <th className="py-1 pr-3">Override</th>
            <th className="py-1 pr-3">Row version</th>
            <th className="py-1">Updated</th>
          </tr>
        </thead>
        <tbody>
          {overrides.length === 0 && (
            <tr>
              <td colSpan={5} className="py-2 text-[var(--color-ink-muted)]">
                No overrides: every value is the source value.
              </td>
            </tr>
          )}
          {overrides.map((o) => (
            <tr key={`${keyText(o.object_key)}:${o.field}`}>
              <td className="py-1 pr-3">{keyText(o.object_key)}</td>
              <td className="py-1 pr-3 font-mono">{o.field}</td>
              <td className="py-1 pr-3">{o.value ?? "cleared (source value)"}</td>
              <td className="py-1 pr-3">{o.row_version}</td>
              <td className="py-1">{o.updated_at ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
