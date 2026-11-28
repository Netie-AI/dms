import { useEffect, useState } from "react";

type Row = {
  ask_id: string;
  asked_at: string;
  actor: string;
  actor_kind: string;
  question: string;
  sql: string;
  row_count: number;
  cost_usd: number | null;
  model: string;
  pii: string[];
};

/** Manager ask history. Hidden until the governance flag is on. */
export function GovAudit() {
  const [role, setRole] = useState<string | null>(null);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [redact, setRedact] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const sessionRes = await fetch("/api/v1/gov/session");
      if (!sessionRes.ok) return;
      const session = (await sessionRes.json()) as { role?: string; redact_sensitive?: boolean };
      if (cancelled || !session.role) return;
      setRole(session.role);
      setRedact(Boolean(session.redact_sensitive));
      if (session.role !== "manager") {
        setMessage("Question text is shown to managers.");
        return;
      }
      const auditRes = await fetch("/api/v1/gov/audit");
      if (!auditRes.ok) {
        setMessage("Question text is shown to managers.");
        return;
      }
      const body = (await auditRes.json()) as { rows?: Row[]; redact_sensitive?: boolean };
      if (!cancelled) {
        setRows(body.rows || []);
        setRedact(Boolean(body.redact_sensitive));
      }
    })().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  if (!role) return null;

  return (
    <section data-testid="gov-audit" className="mt-8">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
        Asks
      </p>
      <h2 className="mt-2 text-xl font-semibold">Who asked what</h2>
      {redact && (
        <p className="mt-2 text-sm text-[var(--color-ink-muted)]">
          Sensitive values are redacted in this view.
        </p>
      )}
      {message && <p className="mt-3 text-sm">{message}</p>}
      {rows && (
        <div className="mt-4 overflow-x-auto border border-[var(--color-line)]">
          <table className="w-full text-left text-sm">
            <thead className="bg-[var(--color-paper-2)] text-xs uppercase tracking-wide text-[var(--color-ink-muted)]">
              <tr>
                {["Who", "When", "Question", "SQL", "Rows", "Cost", "Model", "PII"].map((head) => (
                  <th key={head} className="px-3 py-2 font-medium">
                    {head}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td className="px-3 py-3 text-[var(--color-ink-muted)]" colSpan={8}>
                    No asks recorded.
                  </td>
                </tr>
              )}
              {rows.map((row) => (
                <tr key={row.ask_id} className="border-t border-[var(--color-line)] align-top">
                  <td className="px-3 py-2">
                    {row.actor_kind === "deployment" ? "Deployment identity" : row.actor}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">{row.asked_at}</td>
                  <td className="px-3 py-2">{row.question}</td>
                  <td className="px-3 py-2 font-mono text-xs">{row.sql}</td>
                  <td className="px-3 py-2">{row.row_count}</td>
                  <td className="px-3 py-2">
                    {row.cost_usd == null ? "Not priced" : `USD ${row.cost_usd.toFixed(2)}`}
                  </td>
                  <td className="px-3 py-2">{row.model || ""}</td>
                  <td className="px-3 py-2">{row.pii.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
