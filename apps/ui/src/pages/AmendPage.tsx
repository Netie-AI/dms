import { useEffect, useState } from "react";

type Proposal = {
  id: string;
  space_id?: string | null;
  version_num?: number;
  status?: string;
  idempotency_token?: string;
  diff?: { summary?: string };
};

export function AmendPage() {
  const [list, setList] = useState<Proposal[]>([]);
  const [summary, setSummary] = useState("Correct quantity on demo row");
  const [msg, setMsg] = useState<string | null>(null);

  const reload = () => {
    void fetch("/api/v1/amend/proposals")
      .then((r) => r.json())
      .then(setList)
      .catch(() => setList([]));
  };

  useEffect(() => {
    reload();
  }, []);

  const propose = async () => {
    setMsg(null);
    const res = await fetch("/api/v1/amend/proposals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        summary,
        diff: { summary, plain: `Propose: ${summary}` },
      }),
    });
    if (!res.ok) {
      setMsg(await res.text());
      return;
    }
    const body = await res.json();
    setMsg(`Proposed v${body.version_num} — token ${body.idempotency_token?.slice(0, 12)}…`);
    reload();
  };

  const confirm = async (p: Proposal) => {
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
            {p.status === "pending_confirm" && (
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
    </div>
  );
}
