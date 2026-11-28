import { useState } from "react";
import { SimpleChart } from "@/components/SimpleChart";
import { AnswerRowsTable } from "@/components/AnswerRowsTable";
import { ClarifyCard } from "./ClarifyCard";
import { chartFromRows } from "./chartShape";
import { validateModelSpec } from "./chartSpec";
import { readClarify } from "./abstainReason";
import type { AnswerEnvelope, ClarifyReading } from "@/lib/types";

const FALLBACK_CAP = 20;

type Props = {
  envelope: AnswerEnvelope;
  busy?: boolean;
  error?: string | null;
  onPick?: (reading: ClarifyReading) => void;
};

function rowsOf(envelope: AnswerEnvelope): Record<string, unknown>[] {
  if (!Array.isArray(envelope.rows)) return [];
  return envelope.rows.filter((row) => row && typeof row === "object");
}

/** Answer first, then the chart, then the capped table behind See rows. */
export function SellAnswerCard({ envelope, busy, error, onPick }: Props) {
  const [sqlOpen, setSqlOpen] = useState(false);
  const [rowsOpen, setRowsOpen] = useState(false);
  const [checksOpen, setChecksOpen] = useState(false);
  const all = rowsOf(envelope);
  const cap =
    typeof envelope.sell_row_cap === "number" && envelope.sell_row_cap > 0
      ? envelope.sell_row_cap
      : FALLBACK_CAP;
  const shown = all.slice(0, cap);
  const clarify = readClarify(envelope);
  const spec =
    validateModelSpec(envelope.chart_spec, shown) ??
    (envelope.chart && envelope.chart.kind ? envelope.chart : null) ??
    chartFromRows(shown);
  const sql = typeof envelope.sql_used === "string" ? envelope.sql_used : "";
  const checks = Array.isArray(envelope.sell_checks) ? envelope.sell_checks : [];
  const askback =
    envelope.reconfirm && typeof envelope.reconfirm.suggested_question === "string"
      ? envelope.reconfirm.suggested_question.trim()
      : "";

  return (
    <div data-testid="sell-answer" className="answer-landed mt-3">
      <p data-testid="sell-answer-text" className="whitespace-pre-wrap text-sm text-[var(--color-ink)]">
        {envelope.text}
      </p>
      {spec ? (
        <div data-testid="sell-chart" data-chart-kind={spec.kind} className="mt-3">
          <SimpleChart chart={spec} rows={shown} />
        </div>
      ) : null}
      {clarify ? (
        <ClarifyCard block={clarify} busy={busy} error={error} onPick={(reading) => onPick?.(reading)} />
      ) : null}
      {!clarify && askback ? (
        <p data-testid="sell-askback" className="mt-3 text-sm text-[var(--color-ink)]">
          {askback}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          data-testid="sell-see-sql"
          aria-expanded={sqlOpen}
          onClick={() => setSqlOpen((open) => !open)}
          className="border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-1 text-sm text-[var(--color-ink)]"
        >
          See SQL
        </button>
        <button
          type="button"
          data-testid="sell-see-rows"
          aria-expanded={rowsOpen}
          onClick={() => setRowsOpen((open) => !open)}
          className="border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-1 text-sm text-[var(--color-ink)]"
        >
          See rows
        </button>
        <button
          type="button"
          data-testid="sell-see-checks"
          aria-expanded={checksOpen}
          onClick={() => setChecksOpen((open) => !open)}
          className="border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-1 text-sm text-[var(--color-ink)]"
        >
          How I checked
        </button>
      </div>
      {sqlOpen ? (
        <pre
          data-testid="sell-sql"
          className="mt-3 overflow-x-auto border border-[var(--color-line)] bg-[var(--color-paper)] p-3 font-mono text-xs text-[var(--color-ink)]"
        >
          {sql || "No SQL was run."}
        </pre>
      ) : null}
      {rowsOpen ? (
        <div data-testid="sell-rows" className="mt-3">
          {shown.length > 0 ? (
            <AnswerRowsTable rows={shown} totalRows={all.length} pageOffset={0} pageSize={cap} />
          ) : (
            <p className="text-sm text-[var(--color-ink-muted)]">No rows.</p>
          )}
          {all.length > shown.length ? (
            <p className="mt-1 text-xs text-[var(--color-ink-muted)]">
              Showing {shown.length} of {all.length}
            </p>
          ) : null}
        </div>
      ) : null}
      {checksOpen ? (
        <ul data-testid="sell-checks" className="mt-3 list-disc pl-5 text-sm text-[var(--color-ink)]">
          {checks.length > 0 ? (
            checks.map((item) => <li key={item}>{item}</li>)
          ) : (
            <li>No check was recorded.</li>
          )}
        </ul>
      ) : null}
    </div>
  );
}
