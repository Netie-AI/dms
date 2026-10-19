import { useEffect, useState } from "react";
import { AnswerRowsTable } from "@/components/AnswerRowsTable";
import { SimpleChart } from "@/components/SimpleChart";
import { copyText } from "@/lib/copilotPrompts";
import { postAskConfirm } from "@/lib/api";
import { splitInsights } from "@/lib/splitInsights";
import { isAbstain, namedAbstainReason, type AbstainFields } from "./abstainReason";
import { chartFromRows } from "./chartShape";
import { StampsPanel } from "./StampsPanel";

/** Fields this view reads. Stamp keys stay optional and are not defaulted. */
export type StudioAskEnvelope = AbstainFields & {
  sql_used?: string | null;
  rows?: Record<string, unknown>[] | null;
  served_provider?: string | null;
  served_model?: string | null;
  served_local?: boolean | null;
  served_attribution?: string | null;
  learn_enabled?: boolean | null;
  learn_source?: string | null;
  route_store_id?: string | null;
  model_calls?: number | null;
  generate_legs?: unknown;
  plan_source?: string | null;
  plan_origin?: string | null;
  lane?: string | null;
  engine_as_of?: string | null;
  engine_as_of_after?: string | null;
  engine_timezone?: string | null;
  engine_timezone_after?: string | null;
  contract?: string | null;
  cortex_sha?: string | null;
  reconfirm?: {
    reason_code?: string;
    reason_text?: string;
    suggested_question?: string;
    token?: string;
  } | null;
};

const PAGE_SIZE = 10;

type Props = {
  envelope: StudioAskEnvelope | null;
};

/**
 * Studio result for one DMS ask envelope.
 * Chart is SimpleChart (apps/ui/src/components/SimpleChart.tsx), the SVG
 * chart already in the UI. No chart package is added.
 */
export function ResultView({ envelope }: Props) {
  const [page, setPage] = useState(0);
  const [copied, setCopied] = useState(false);
  const [follow, setFollow] = useState<StudioAskEnvelope | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  useEffect(() => {
    setFollow(null);
    setConfirmError(null);
  }, [envelope]);
  if (!envelope) return null;
  const shown = follow ?? envelope;

  const offer = shown.reconfirm;
  const suggestion =
    offer != null && typeof offer.suggested_question === "string"
      ? offer.suggested_question.trim()
      : "";
  const reconfirm =
    offer != null && typeof offer.token === "string" && offer.token.length > 0 && suggestion.length > 0;
  const abstained = isAbstain(shown);

  async function choose(yes: boolean) {
    if (!offer || typeof offer.token !== "string") return;
    setConfirming(true);
    setConfirmError(null);
    try {
      const next = (await postAskConfirm({ token: offer.token, yes })) as StudioAskEnvelope;
      setFollow(next);
      setPage(0);
    } catch (err) {
      setConfirmError(err instanceof Error ? err.message : "confirm failed");
    } finally {
      setConfirming(false);
    }
  }
  const rows = abstained || reconfirm ? [] : listRows(shown.rows);
  const spec = abstained || reconfirm ? null : chartFromRows(rows);
  const { prose, insights } = splitInsights(typeof shown.text === "string" ? shown.text : "");
  const sql = typeof shown.sql_used === "string" ? shown.sql_used : "";
  const offset = page * PAGE_SIZE;
  const pageRows = rows.slice(offset, offset + PAGE_SIZE);

  return (
    <section
      data-testid="studio-result"
      className="mt-6 border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-4 py-4"
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
        Ask result
      </p>

      {reconfirm && offer ? (
        <div
          data-testid="studio-reconfirm"
          className="mt-3 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3"
        >
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            Reconfirm
          </p>
          <p data-testid="studio-reconfirm-reason" className="mt-2 text-sm text-[var(--color-ink)]">
            {offer.reason_text || offer.reason_code}
          </p>
          <p data-testid="studio-reconfirm-suggestion" className="mt-2 text-sm font-medium text-[var(--color-ink)]">
            {offer.suggested_question}
          </p>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              data-testid="studio-reconfirm-yes"
              disabled={confirming}
              onClick={() => {
                void choose(true);
              }}
              className="border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-1 text-sm text-[var(--color-ink)] hover:border-[var(--color-accent)]"
            >
              Yes
            </button>
            <button
              type="button"
              data-testid="studio-reconfirm-no"
              disabled={confirming}
              onClick={() => {
                void choose(false);
              }}
              className="border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-1 text-sm text-[var(--color-ink)] hover:border-[var(--color-accent)]"
            >
              No
            </button>
          </div>
          {confirmError ? (
            <p data-testid="studio-reconfirm-error" className="mt-2 text-xs text-[var(--color-danger)]">
              {confirmError}
            </p>
          ) : null}
        </div>
      ) : null}

      {!reconfirm && abstained ? (
        <div
          data-testid="studio-abstain"
          className="mt-3 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3"
        >
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            Abstained
          </p>
          <p data-testid="studio-abstain-reason" className="mt-2 text-sm font-medium text-[var(--color-ink)]">
            {namedAbstainReason(shown)}
          </p>
        </div>
      ) : null}
      {!reconfirm && !abstained ? (
        <div data-testid="studio-insight" className="mt-3 text-sm text-[var(--color-ink)]">
          {prose ? <p className="whitespace-pre-wrap">{prose}</p> : null}
          {insights.length > 0 ? (
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {insights.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <div className="mt-4">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            SQL
          </p>
          {sql ? (
            <button
              type="button"
              data-testid="studio-sql-copy"
              onClick={() => {
                void copyText(sql).then((ok) => {
                  if (!ok) return;
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1200);
                });
              }}
              className="border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1 text-xs text-[var(--color-ink)] hover:border-[var(--color-accent)]"
            >
              {copied ? "Copied" : "Copy"}
            </button>
          ) : null}
        </div>
        {sql ? (
          <pre
            data-testid="studio-sql"
            aria-readonly="true"
            className="mt-2 overflow-x-auto border border-[var(--color-line)] bg-[var(--color-paper)] p-3 font-mono text-xs text-[var(--color-ink)]"
          >
            {sql}
          </pre>
        ) : (
          <p data-testid="studio-sql-missing" className="mt-2 text-xs text-[var(--color-ink-muted)]">
            no SQL
          </p>
        )}
      </div>

      {!abstained && spec ? (
        <div data-testid="studio-chart" data-chart-kind={spec.kind}>
          <SimpleChart chart={spec} rows={rows} />
        </div>
      ) : null}
      {!abstained && !spec ? (
        <p data-testid="studio-no-chart" className="mt-4 text-xs text-[var(--color-ink-muted)]">
          no chartable shape
        </p>
      ) : null}

      {!abstained && rows.length > 0 ? (
        <div data-testid="studio-rows">
          <AnswerRowsTable
            rows={pageRows}
            totalRows={rows.length}
            pageOffset={offset}
            pageSize={PAGE_SIZE}
            onPageChange={(next) => setPage(Math.floor(next / PAGE_SIZE))}
          />
        </div>
      ) : null}

      <StampsPanel envelope={shown} />
    </section>
  );
}

function listRows(rows: StudioAskEnvelope["rows"]): Record<string, unknown>[] {
  if (!Array.isArray(rows)) return [];
  return rows.filter((row) => row && typeof row === "object");
}
