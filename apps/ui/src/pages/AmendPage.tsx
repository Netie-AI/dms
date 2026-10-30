import { useEffect, useState } from "react";
import { GovernedActions } from "@/components/GovernedActions";
import { useApp } from "@/context/AppContext";
import { probeActions, type ActionProposal, type ActionsAvailability } from "@/lib/actions";
import {
  fetchAmendProposals,
  postAmendProposal,
  type AmendProposal,
} from "@/lib/api";

export type AmendPageProps = {
  /** Seeds for a server render or a test; the browser starts from null / []. */
  initialActionsAvailable?: ActionsAvailability | null;
  initialProposals?: AmendProposal[];
  initialActionProposals?: ActionProposal[];
};

function isActionRow(p: AmendProposal): boolean {
  return (p.diff as { kind?: unknown } | undefined)?.kind === "action";
}

export function AmendPage({
  initialActionsAvailable = null,
  initialProposals,
  initialActionProposals,
}: AmendPageProps = {}) {
  const { activeSpaceId } = useApp();
  const [list, setList] = useState<AmendProposal[]>(initialProposals ?? []);
  // Governed actions (ACT-03) render only when the server answers the ACT-02
  // registry probe with a 200, i.e. it runs with DMS_ACTIONS=1. null (probing)
  // and 'off' render exactly the page main renders.
  const [actionsAvailable, setActionsAvailable] = useState<ActionsAvailability | null>(
    initialActionsAvailable,
  );
  const [summary, setSummary] = useState("Correct quantity on demo row");
  const [msg, setMsg] = useState<string | null>(null);

  const reload = () => {
    // Clear first so a Space switch cannot leave another Space's diffs on screen.
    setList([]);
    void fetchAmendProposals(activeSpaceId)
      .then(setList)
      .catch(() => setList([]));
  };

  useEffect(() => {
    reload();
    // reload closes over activeSpaceId; re-run when the Space switcher moves.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSpaceId]);

  useEffect(() => {
    let live = true;
    void probeActions().then((a) => {
      if (live) setActionsAvailable(a);
    });
    return () => {
      live = false;
    };
  }, []);

  const propose = async () => {
    setMsg(null);
    try {
      const body = await postAmendProposal(summary, activeSpaceId);
      setMsg(`Proposed v${body.version_num} — token ${body.idempotency_token?.slice(0, 12)}…`);
      reload();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const confirm = async (p: AmendProposal) => {
    if (!p.idempotency_token) return;
    const res = await fetch(`/api/v1/amend/proposals/${p.id}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idempotency_token: p.idempotency_token }),
    });
    if (!res.ok) {
      setMsg(await res.text());
      reload();
      return;
    }
    // The response says whether anything was actually mutated. Confirm records
    // the proposal version and writes a ledger receipt; it does not yet change
    // warehouse data. Reporting a bare "Confirmed" over an unchanged warehouse
    // is the difference the customer cannot see for themselves.
    const body = (await res.json()) as {
      ledger_entry_id?: string | null;
      mutation?: { executed?: boolean; detail?: string };
    };
    const receipt = body.ledger_entry_id
      ? `receipt ${body.ledger_entry_id.slice(0, 8)}…`
      : "no receipt";
    setMsg(
      body.mutation?.executed === false
        ? `Recorded ${p.id.slice(0, 8)}… (${receipt}) — proposal accepted, no warehouse data changed yet.`
        : `Applied ${p.id.slice(0, 8)}… (${receipt})`,
    );
    reload();
  };

  return (
    <div className="h-full overflow-y-auto px-8 py-8">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
        U4 · T4
      </p>
      <h1 className="mt-2 font-[family-name:var(--font-display)] text-3xl font-semibold tracking-tight">
        Amend
      </h1>
      <p className="mt-3 max-w-xl text-[var(--color-ink-muted)]">
        Plain-language first. Confirm passes compliance_gate, takes an advisory lock, records the
        proposal version once, and writes a ledger receipt — the receipt is part of the write, so a
        confirm that cannot be recorded is refused rather than applied silently. It does not change
        warehouse data yet.
      </p>
      <div className="mt-6 flex max-w-xl gap-2">
        <input
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          className="h-10 flex-1 border border-[var(--color-line)] bg-[var(--color-surface)] px-3 text-sm"
        />
        <button
          type="button"
          onClick={() => void propose()}
          className="h-10 bg-[var(--color-accent)] px-4 text-sm text-[var(--color-on-accent)]"
        >
          Propose
        </button>
      </div>
      {msg && <p className="mt-3 text-sm text-[var(--color-ink-muted)]">{msg}</p>}
      <ul className="mt-8 divide-y divide-[var(--color-line)] border border-[var(--color-line)] bg-[var(--color-surface)]/70">
        {list.length === 0 && (
          <li className="px-3 py-4 text-sm text-[var(--color-ink-muted)]">
            No proposals — needs DATABASE_URL + seed.
          </li>
        )}
        {list.map((p) => (
          <li key={p.id} className="flex items-center justify-between gap-3 px-3 py-3 text-sm">
            <div>
              <p className="font-medium">{p.diff?.summary || p.id}</p>
              <p className="text-[var(--color-ink-muted)]">
                v{p.version_num} · {p.status}
              </p>
            </div>
            {p.status === "pending_confirm" && actionsAvailable === "on" && isActionRow(p) && (
              <span className="text-xs text-[var(--color-ink-muted)]">
                Apply in Governed actions
              </span>
            )}
            {p.status === "pending_confirm" && !(actionsAvailable === "on" && isActionRow(p)) && (
              <button
                type="button"
                onClick={() => void confirm(p)}
                className="border border-[var(--color-accent)] px-3 py-1 text-[var(--color-accent)]"
              >
                Confirm
              </button>
            )}
          </li>
        ))}
      </ul>
      {actionsAvailable === "on" && (
        <GovernedActions
          spaceId={activeSpaceId ?? null}
          seed={initialActionProposals ? { proposals: initialActionProposals } : undefined}
          onChanged={reload}
        />
      )}
    </div>
  );
}
