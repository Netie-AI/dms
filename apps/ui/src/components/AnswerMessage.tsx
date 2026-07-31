import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/Badge";
import { AnswerRowsTable } from "@/components/AnswerRowsTable";
import { SimpleChart } from "@/components/SimpleChart";
import { useApp } from "@/context/AppContext";
import { COPILOT_PROMPTS, copyText } from "@/lib/copilotPrompts";
import type { AnswerEnvelope, BadgeKind } from "@/lib/types";

const EXCLUSION_YES_RE = /^Yes — exclude\b/i;
const EXCLUSION_NO_RE = /^No — show\b|without excluding/i;

/**
 * Usability rule 4 — four clicks to bedrock, progressive disclosure. The badge is
 * the first click, and it has to say what it actually means in the user's words:
 * who wrote this SQL, and how much of it a model touched.
 */
const LAYER_COPY: Record<BadgeKind, { headline: string; detail: string }> = {
  L0_CERTIFIED: {
    headline: "A human wrote this query, and it was reviewed.",
    detail:
      "The question matched the certified library exactly. No model chose the SQL — it was picked by name.",
  },
  L1_GOVERNED_METRIC: {
    headline: "A governed metric answered this.",
    detail:
      "The question routed to a metric in the semantic layer. A model only chose which metric and filled its slots; the SQL was compiled from the metric definition and re-checked before running.",
  },
  L2_VALIDATED: {
    headline: "This SQL was generated, then checked.",
    detail:
      "No governed metric matched, so the query was generated against the retrieved schema, parsed, EXPLAINed and validated before it ran. Read the sources before acting on it.",
  },
  L2_ANOMALOUS: {
    headline: "Generated, and the result looks unusual.",
    detail:
      "The query passed validation but the result is out of line with history. Verify against the sources before acting on it.",
  },
  ABSTAIN: {
    headline: "No answer, on purpose.",
    detail:
      "Nothing here could be proved, so the engine declined rather than guessing. A wrong number with a confident badge is the failure this avoids.",
  },
};

/** Tokenize answer text so each values[].id becomes a button — no regex over prose. */
function renderWithValues(
  envelope: AnswerEnvelope,
  onValue: (id: string) => void,
): ReactNode[] {
  const parts: ReactNode[] = [];
  let remaining = envelope.text;
  const sorted = [...envelope.values].sort(
    (a, b) => String(b.value).length - String(a.value).length,
  );

  for (const v of sorted) {
    const formatted = new Intl.NumberFormat("en-MY", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(v.value);
    const candidates = [`RM ${formatted}`, formatted, String(v.value)];
    let hit: string | null = null;
    let idx = -1;
    for (const c of candidates) {
      const i = remaining.indexOf(c);
      if (i >= 0) {
        hit = c;
        idx = i;
        break;
      }
    }
    if (hit == null || idx < 0) continue;
    if (idx > 0) parts.push(remaining.slice(0, idx));
    parts.push(
      <button
        key={v.id}
        type="button"
        onClick={() => onValue(v.id)}
        className="mx-0.5 inline border-b-2 border-[var(--color-accent)] font-semibold text-[var(--color-accent)] hover:bg-[var(--color-accent-soft)]"
      >
        {hit}
      </button>,
    );
    remaining = remaining.slice(idx + hit.length);
  }
  if (remaining) parts.push(remaining);
  return parts.length ? parts : [envelope.text];
}

export function AnswerMessage({ envelope }: { envelope: AnswerEnvelope }) {
  const { selectValue, setSourcePanelOpen, ask, suggestions } = useApp();
  const [showSql, setShowSql] = useState(false);
  const [showLayer, setShowLayer] = useState(false);
  const [showCopilot, setShowCopilot] = useState(false);
  const [drillRows, setDrillRows] = useState<Record<string, unknown>[] | null>(null);
  const [drillErr, setDrillErr] = useState<string | null>(null);
  const [drillBusy, setDrillBusy] = useState(false);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const [confirmLeft, setConfirmLeft] = useState<number | null>(null);
  const confirmFired = useRef(false);
  const rows = envelope.rows ?? [];

  const chipSuggestions =
    envelope.suggestions?.length ? envelope.suggestions : suggestions;
  const yesChip = chipSuggestions.find((q) => EXCLUSION_YES_RE.test(q));
  const noChip = chipSuggestions.find((q) => EXCLUSION_NO_RE.test(q));
  const isExclusionConfirm = Boolean(yesChip && noChip && envelope.badge === "ABSTAIN");

  useEffect(() => {
    confirmFired.current = false;
    if (!isExclusionConfirm || !noChip) {
      setConfirmLeft(null);
      return;
    }
    setConfirmLeft(5);
    const tick = window.setInterval(() => {
      setConfirmLeft((n) => {
        if (n == null) return n;
        if (n <= 1) {
          window.clearInterval(tick);
          if (!confirmFired.current) {
            confirmFired.current = true;
            void ask(noChip);
          }
          return 0;
        }
        return n - 1;
      });
    }, 1000);
    return () => window.clearInterval(tick);
  }, [ask, envelope.answer_id, isExclusionConfirm, noChip]);

  function downloadRowsCsv() {
    if (!rows.length) return;
    const cols = Object.keys(rows[0]);
    const escape = (v: unknown) => {
      const s = v == null ? "" : String(v);
      if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
      return s;
    };
    const lines = [
      cols.join(","),
      ...rows.map((row) => cols.map((c) => escape(row[c])).join(",")),
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    a.href = url;
    a.download = `dms_answer_${stamp}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function runDrillthrough() {
    const token = envelope.drillthrough_token;
    if (!token) return;
    setDrillBusy(true);
    setDrillErr(null);
    try {
      const res = await fetch("/api/v1/chat/drillthrough", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      if (!res.ok) {
        setDrillErr(await res.text());
        setDrillRows(null);
        return;
      }
      const body = (await res.json()) as { rows?: Record<string, unknown>[] };
      setDrillRows(body.rows ?? []);
      setSourcePanelOpen(true);
    } catch (e) {
      setDrillErr(String(e));
    } finally {
      setDrillBusy(false);
    }
  }

  return (
    <article className="border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-4 py-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setShowLayer((s) => !s)}
          aria-expanded={showLayer}
          title="What does this badge mean?"
          className="cursor-pointer"
        >
          <Badge kind={envelope.badge} />
        </button>
        {(envelope.demo_fallback_used || envelope.ask_mode === "demo") && (
          <span className="border border-[var(--color-warn)]/50 bg-[var(--color-warn-soft)] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--color-warn)]">
            {envelope.demo_fallback_used ? "fallback → demo" : "demo"}
          </span>
        )}
        <button
          type="button"
          onClick={() => setShowSql((s) => !s)}
          className="text-xs text-[var(--color-ink-muted)] underline-offset-2 hover:underline"
        >
          ⟨sql⟩
        </button>
        {rows.length > 0 && (
          <button
            type="button"
            onClick={downloadRowsCsv}
            className="text-xs text-[var(--color-ink-muted)] underline-offset-2 hover:underline"
          >
            Download CSV
          </button>
        )}
        {rows.length > 0 && (
          <button
            type="button"
            onClick={() => setShowCopilot((s) => !s)}
            className="text-xs text-[var(--color-ink-muted)] underline-offset-2 hover:underline"
          >
            Copilot prompts
          </button>
        )}
        {envelope.drillthrough_token ? (
          <button
            type="button"
            disabled={drillBusy}
            onClick={() => void runDrillthrough()}
            className="text-xs text-[var(--color-accent)] underline-offset-2 hover:underline disabled:opacity-50"
          >
            {drillBusy ? "Drilling…" : "Show me why"}
          </button>
        ) : (
          <button
            type="button"
            onClick={() => setSourcePanelOpen(true)}
            className="text-xs text-[var(--color-ink-muted)] underline-offset-2 hover:underline"
          >
            ⟨sources⟩
          </button>
        )}
        {envelope.audit_id && (
          <a
            href="/audit"
            className="text-xs text-[var(--color-ink-muted)] underline-offset-2 hover:underline"
            title={envelope.audit_id}
          >
            ⟨audit⟩
          </a>
        )}
      </div>
      {showLayer && (
        <div className="mb-3 border border-[var(--color-line)] bg-[var(--color-paper)] px-3 py-2.5">
          <p className="text-sm font-medium text-[var(--color-ink)]">
            {LAYER_COPY[envelope.badge].headline}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-[var(--color-ink-muted)]">
            {LAYER_COPY[envelope.badge].detail}
          </p>
          <p className="mt-2 flex flex-wrap gap-3 text-xs">
            <Link to="/ontology" className="text-[var(--color-accent)] hover:underline">
              See the governed metrics
            </Link>
            <Link to="/trust" className="text-[var(--color-accent)] hover:underline">
              See the evidence behind this badge
            </Link>
            {envelope.audit_id && (
              <Link to="/audit" className="text-[var(--color-accent)] hover:underline">
                Find this in the ledger
              </Link>
            )}
          </p>
        </div>
      )}
      <p className="text-[1.05rem] leading-relaxed text-[var(--color-ink)]">
        {renderWithValues(envelope, selectValue)}
      </p>

      {/* Usability rule 12 — abstain is never a dead end. */}
      {envelope.badge === "ABSTAIN" && (
        <div className="mt-3 border border-[var(--color-line)] bg-[var(--color-paper)] px-3 py-2.5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            {isExclusionConfirm ? "Confirm exclusion" : "Try one of these instead"}
          </p>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {chipSuggestions.slice(0, 4).map((q) => {
              const isYes = EXCLUSION_YES_RE.test(q);
              const isNo = EXCLUSION_NO_RE.test(q);
              return (
                <li key={q}>
                  <button
                    type="button"
                    onClick={() => {
                      confirmFired.current = true;
                      setConfirmLeft(null);
                      void ask(q);
                    }}
                    className={
                      isYes
                        ? "border border-[var(--color-accent)] bg-[var(--color-accent-soft)] px-2.5 py-1.5 text-xs font-semibold text-[var(--color-accent)]"
                        : "border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-2.5 py-1.5 text-xs hover:border-[var(--color-accent)]"
                    }
                  >
                    {isYes && confirmLeft != null && confirmLeft > 0
                      ? `${q} (${confirmLeft}s)`
                      : isNo && confirmLeft === 0
                        ? `${q} (auto)`
                        : q}
                  </button>
                </li>
              );
            })}
          </ul>
          {isExclusionConfirm && confirmLeft != null && confirmLeft > 0 && (
            <p className="mt-2 text-xs text-[var(--color-ink-muted)]">
              Yes cancels automatically in {confirmLeft}s — then the unfiltered top ranking runs.
            </p>
          )}
          <p className="mt-2 flex flex-wrap gap-3 text-xs">
            <Link to="/ontology" className="text-[var(--color-accent)] hover:underline">
              Browse what can be asked
            </Link>
            <Link to="/studio" className="text-[var(--color-accent)] hover:underline">
              Attach the missing source
            </Link>
          </p>
        </div>
      )}
      {rows.length > 0 && <AnswerRowsTable rows={rows} />}
      {envelope.chart && rows.length > 0 && (
        <SimpleChart chart={envelope.chart} rows={rows} />
      )}
      {showCopilot && rows.length > 0 && (
        <div className="mt-4 border border-[var(--color-line)] bg-[var(--color-paper)] p-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            After Download CSV → Excel → Copilot
          </p>
          <ol className="mt-2 space-y-2">
            {COPILOT_PROMPTS.map((p, i) => (
              <li key={i} className="flex gap-2 text-xs text-[var(--color-ink)]">
                <span className="shrink-0 text-[var(--color-ink-muted)]">{i + 1}.</span>
                <span className="flex-1">{p}</span>
                <button
                  type="button"
                  className="shrink-0 underline-offset-2 hover:underline"
                  onClick={() => {
                    void copyText(p).then((ok) => {
                      if (ok) {
                        setCopiedIdx(i);
                        window.setTimeout(() => setCopiedIdx(null), 1200);
                      }
                    });
                  }}
                >
                  {copiedIdx === i ? "Copied" : "Copy"}
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}
      {showSql && envelope.sql_used && (
        <pre className="mt-3 overflow-x-auto border border-[var(--color-line)] bg-[var(--color-paper)] p-3 text-xs text-[var(--color-ink-muted)]">
          {envelope.sql_used}
        </pre>
      )}
      {drillErr && (
        <p className="mt-3 text-xs text-[var(--color-danger)]">Drill-through: {drillErr}</p>
      )}
      {drillRows && (
        <div className="mt-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            Drill-through rows
          </p>
          {drillRows.length ? (
            <AnswerRowsTable rows={drillRows} />
          ) : (
            <p className="mt-2 text-xs text-[var(--color-ink-muted)]">No drill rows returned.</p>
          )}
        </div>
      )}
      {envelope.assumptions.length > 0 && (
        <div className="mt-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            Assumptions
          </p>
          <ul className="mt-1 list-inside list-disc text-sm text-[var(--color-ink-muted)]">
            {envelope.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}
