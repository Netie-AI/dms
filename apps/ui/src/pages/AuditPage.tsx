import { useEffect, useState } from "react";

type Ref = { seq: number; cortex_entry_id: string; created_at?: string | null };

export function AuditPage() {
  const [rows, setRows] = useState<Ref[]>([]);
  const [verify, setVerify] = useState<string | null>(null);

  useEffect(() => {
    void fetch("/api/v1/audit/ledger")
      .then((r) => r.json())
      .then(setRows)
      .catch(() => setRows([]));
  }, []);

  const runVerify = async () => {
    const res = await fetch("/api/v1/audit/ledger/verify", { method: "POST" });
    setVerify(res.ok ? JSON.stringify(await res.json()) : await res.text());
  };

  return (
    <div className="h-full overflow-y-auto px-8 py-8">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
        U4
      </p>
      <h1 className="mt-2 font-[family-name:var(--font-display)] text-3xl font-semibold tracking-tight">
        Audit
      </h1>
      <p className="mt-3 max-w-xl text-[var(--color-ink-muted)]">
        Cortex ledger pointers only — DMS never keeps a second hash chain.
      </p>
      <button
        type="button"
        onClick={() => void runVerify()}
        className="mt-6 border border-[var(--color-line)] px-3 py-2 text-sm hover:border-[var(--color-accent)]"
      >
        Verify chain via Cortex
      </button>
      {verify && (
        <pre className="mt-3 overflow-x-auto border border-[var(--color-line)] bg-[var(--color-paper)] p-3 text-xs">
          {verify}
        </pre>
      )}
      <ul className="mt-8 divide-y divide-[var(--color-line)] border border-[var(--color-line)] bg-[var(--color-surface)]/70">
        {rows.length === 0 && (
          <li className="px-3 py-4 text-sm text-[var(--color-ink-muted)]">No ledger_ref rows yet.</li>
        )}
        {rows.map((r) => (
          <li key={r.seq} className="flex justify-between px-3 py-3 text-sm">
            <span className="font-mono text-xs">{r.cortex_entry_id}</span>
            <span className="text-[var(--color-ink-muted)]">#{r.seq}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
