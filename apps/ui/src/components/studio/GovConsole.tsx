import { useEffect, useState } from "react";

type Session = {
  enabled: boolean;
  role: "manager" | "member";
  user_id: string;
  actor_kind: string;
  upload: string;
  redact_sensitive: boolean;
};

type Burn = {
  user_id: string;
  role: string;
  daily_usd: number | null;
  spent_usd: number | null;
  daily_tokens: number | null;
  spent_tokens: number;
  usd_priced: boolean;
};

type Dashboard = {
  upload: string;
  redact_sensitive: boolean;
  ov: { ok: boolean; priced: boolean };
  spend_by_user_day: {
    user_id: string;
    day: string;
    tokens: number;
    cost_usd: number | null;
    model: string;
  }[];
  top_questions: {
    asked_at: string;
    actor: string;
    actor_kind: string;
    question: string;
    pii: string[];
  }[];
  blocked: { at: string; kind: string; summary: string; user_id: string }[];
  pii_hits: { at: string; finding_type: string; user_id: string; file_sha256: string }[];
  budget_burn: Burn[];
};

function money(value: number | null): string {
  if (value == null) return "Not priced";
  return `USD ${value.toFixed(2)}`;
}

function ratio(spent: number, cap: number | null): number {
  if (cap == null || cap <= 0) return 0;
  return Math.max(0, Math.min(1, spent / cap));
}

/** Studio governance. Renders nothing until the flag-on route answers. */
export function GovConsole() {
  const [session, setSession] = useState<Session | null>(null);
  const [dash, setDash] = useState<Dashboard | null>(null);
  const [form, setForm] = useState({
    user_id: "",
    role: "member",
    daily_usd: "",
    monthly_usd: "",
    daily_tokens: "",
    monthly_tokens: "",
    ask: true,
    create: true,
    connectors: true,
  });
  const [paste, setPaste] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetch("/api/v1/gov/session")
      .then(async (res) => (res.ok ? ((await res.json()) as Session) : null))
      .then((body) => {
        if (!cancelled && body && body.enabled) setSession(body);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!session || session.role !== "manager") return;
    let cancelled = false;
    void fetch("/api/v1/gov/dashboard")
      .then(async (res) => (res.ok ? ((await res.json()) as Dashboard) : null))
      .then((body) => {
        if (!cancelled && body) setDash(body);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [session]);

  if (!session || session.role !== "manager" || !dash) return null;

  const blocked = dash.blocked.length;
  const pii = dash.pii_hits.length;
  const todayTokens = dash.spend_by_user_day.reduce((sum, row) => sum + row.tokens, 0);

  const save = async () => {
    setSaved(null);
    const num = (raw: string) => (raw.trim() === "" ? null : Number(raw));
    const res = await fetch("/api/v1/gov/assignments", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        subject_id: form.user_id.trim(),
        assignment: form.role,
        daily_usd: num(form.daily_usd),
        monthly_usd: num(form.monthly_usd),
        daily_tokens: num(form.daily_tokens),
        monthly_tokens: num(form.monthly_tokens),
        ask: form.ask,
        create: form.create,
        connectors: form.connectors,
      }),
    });
    setSaved(res.ok ? "Budget saved." : "Budget was not saved.");
  };

  const scanPaste = async () => {
    setNote(null);
    const res = await fetch("/api/v1/gov/scan", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: paste }),
    });
    const body = (await res.json().catch(() => null)) as {
      detail?: { message?: string };
      findings?: string[];
      action?: string;
    } | null;
    if (!res.ok) {
      setNote(body?.detail?.message || "The paste was blocked.");
      setPaste("");
      return;
    }
    const found = body?.findings?.length ? body.findings.join(", ") : "nothing";
    setNote(`Scan ${body?.action || "allow"}: ${found}. Values are not kept in this note.`);
    if (body?.action === "redact") setPaste("");
  };

  return (
    <section data-testid="gov-console" className="gov-rise mt-8">
      <style>{`
        @keyframes gov-rise {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: none; }
        }
        .gov-rise { animation: gov-rise 0.45s ease; }
        .gov-bar { transition: width 0.6s ease; }
      `}</style>
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
        Governance
      </p>
      <h2 className="mt-2 font-[family-name:var(--font-display)] text-2xl font-semibold tracking-tight">
        Spend, questions, and blocks
      </h2>
      <p className="mt-2 max-w-2xl text-sm text-[var(--color-ink-muted)]">
        Budgets are checked before a request. OpenVault reports usage. Provider keys stay there.
        {session.actor_kind === "deployment"
          ? " The signed-in actor is the deployment identity until a person signs in."
          : ""}
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {[
          ["Tokens recorded", String(todayTokens)],
          ["Blocked events", String(blocked)],
          ["PII hits", String(pii)],
        ].map(([label, value]) => (
          <div
            key={label}
            className="border border-[var(--color-line)] bg-[var(--color-surface)]/80 px-4 py-3"
          >
            <p className="text-[11px] uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
              {label}
            </p>
            <p className="mt-1 text-2xl font-semibold text-[var(--color-ink)]">{value}</p>
          </div>
        ))}
      </div>
      {!dash.ov.priced && (
        <p className="mt-3 text-sm text-[var(--color-warn)]">
          OpenVault did not price every row. A USD-only budget stops the request instead of
          assuming the spend is zero. Token budgets still use the reported tokens.
        </p>
      )}
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div>
          <p className="text-sm font-medium">Budget burn today</p>
          <ul className="mt-3 space-y-3">
            {dash.budget_burn.length === 0 && (
              <li className="text-sm text-[var(--color-ink-muted)]">No budgets assigned yet.</li>
            )}
            {dash.budget_burn.map((row) => {
              const tokenRatio = ratio(row.spent_tokens, row.daily_tokens);
              return (
                <li key={row.user_id} className="text-sm">
                  <div className="flex justify-between gap-3">
                    <span className="truncate font-mono text-xs">{row.user_id}</span>
                    <span className="text-[var(--color-ink-muted)]">
                      {row.spent_tokens} tokens · {money(row.spent_usd)}
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 bg-[var(--color-paper-2)]">
                    <div
                      className="gov-bar h-1.5 bg-[var(--color-accent)]"
                      style={{ width: `${Math.round(tokenRatio * 100)}%` }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
        <div>
          <p className="text-sm font-medium">Top questions</p>
          <ul className="mt-3 space-y-2">
            {dash.top_questions.length === 0 && (
              <li className="text-sm text-[var(--color-ink-muted)]">No asks recorded yet.</li>
            )}
            {dash.top_questions.map((row) => (
              <li key={`${row.asked_at}-${row.actor}`} className="text-sm">
                <p>{row.question}</p>
                <p className="text-xs text-[var(--color-ink-muted)]">
                  {row.actor_kind === "deployment" ? "Deployment identity" : row.actor}
                  {row.pii.length ? ` · PII ${row.pii.join(", ")}` : ""}
                </p>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <div>
          <p className="text-sm font-medium">Blocked and alerts</p>
          <ul className="mt-3 space-y-2 text-sm">
            {dash.blocked.length === 0 && (
              <li className="text-[var(--color-ink-muted)]">Nothing blocked.</li>
            )}
            {dash.blocked.map((row) => (
              <li key={`${row.at}-${row.summary}`} className="border border-[var(--color-line)] px-3 py-2">
                {row.summary}
              </li>
            ))}
          </ul>
          <p className="mt-4 text-sm font-medium">PII findings</p>
          <ul className="mt-2 space-y-1 text-sm text-[var(--color-ink-muted)]">
            {dash.pii_hits.length === 0 && <li>No PII findings.</li>}
            {dash.pii_hits.map((row) => (
              <li key={`${row.at}-${row.file_sha256}`}>
                {row.finding_type} · {row.file_sha256.slice(0, 12)}
              </li>
            ))}
          </ul>
        </div>
        <form
          className="space-y-2 border border-[var(--color-line)] bg-[var(--color-surface)]/70 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <p className="text-sm font-medium">Assign a budget</p>
          <input
            aria-label="User id"
            value={form.user_id}
            onChange={(event) => setForm({ ...form, user_id: event.target.value })}
            placeholder="User id"
            className="w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm"
          />
          <select
            aria-label="Role"
            value={form.role}
            onChange={(event) => setForm({ ...form, role: event.target.value })}
            className="w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm"
          >
            <option value="member">Member</option>
            <option value="manager">Manager</option>
          </select>
          <div className="grid grid-cols-2 gap-2">
            {(
              [
                ["daily_usd", "Daily USD"],
                ["monthly_usd", "Monthly USD"],
                ["daily_tokens", "Daily tokens"],
                ["monthly_tokens", "Monthly tokens"],
              ] as const
            ).map(([key, label]) => (
              <input
                key={key}
                aria-label={label}
                value={form[key]}
                placeholder={label}
                onChange={(event) => setForm({ ...form, [key]: event.target.value })}
                className="border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm"
              />
            ))}
          </div>
          <div className="flex gap-3 text-sm">
            {(["ask", "create", "connectors"] as const).map((feature) => (
              <label key={feature} className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={form[feature]}
                  onChange={(event) => setForm({ ...form, [feature]: event.target.checked })}
                />
                {feature}
              </label>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <button
              type="submit"
              className="border border-[var(--color-line)] px-3 py-1.5 text-sm hover:border-[var(--color-accent)]"
            >
              Save
            </button>
            <a className="text-sm text-[var(--color-accent)]" href="/api/v1/gov/spend.csv">
              Download spend CSV
            </a>
          </div>
          {saved && <p className="text-sm">{saved}</p>}
        </form>
      </div>
      <div className="mt-6">
        <p className="text-sm font-medium">Scan pasted text</p>
        <textarea
          aria-label="Paste to scan"
          value={paste}
          onChange={(event) => setPaste(event.target.value)}
          className="mt-2 h-24 w-full border border-[var(--color-line)] bg-[var(--color-panel)] p-2 text-sm"
        />
        <button
          type="button"
          onClick={() => void scanPaste()}
          className="mt-2 border border-[var(--color-line)] px-3 py-1.5 text-sm hover:border-[var(--color-accent)]"
        >
          Scan
        </button>
        {note && <p className="mt-2 text-sm text-[var(--color-ink-muted)]">{note}</p>}
      </div>
    </section>
  );
}
