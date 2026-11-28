import { useEffect, useState } from "react";
import { AnswerRowsTable } from "@/components/AnswerRowsTable";
import { SimpleChart } from "@/components/SimpleChart";
import { copyText } from "@/lib/copilotPrompts";
import { postAskConfirm } from "@/lib/api";
import { splitInsights } from "@/lib/splitInsights";
import { plainShownReason, visibleReason } from "@/lib/reasonCopy";
import { isAbstain, namedAbstainReason, type AbstainFields } from "./abstainReason";
import { AskStages, type AskStageEvent } from "./AskStages";
import { chartFromRows } from "./chartShape";
import {
  chooseChart,
  kindLabel,
  readAdjust,
  validateModelSpec,
  type ModelChartSpec,
} from "./chartSpec";
import { AdjustSqlPanel } from "./AdjustSqlPanel";
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
    definition?: string;
    token?: string;
  } | null;
  /** CHART-SPEC-01. Absent when DMS_CHART_SPEC is off. */
  chart_spec?: unknown;
  chart_adjust?: unknown;
};

const PAGE_SIZE = 10;

type Props = {
  envelope: StudioAskEnvelope | null;
  stages?: AskStageEvent[];
  onCancel?: () => void;
  /** The ask this answer belongs to. Save uses it. Absent hides nothing by itself. */
  question?: string;
  spaceId?: string | null;
  /** Only a steward mounts Adjust SQL. Anyone else gets the same markup as before. */
  role?: string | null;
};

/**
 * Studio result for one DMS ask envelope.
 * Chart is SimpleChart (apps/ui/src/components/SimpleChart.tsx), the SVG
 * chart already in the UI. No chart package is added.
 */
export function ResultView({ envelope, stages, onCancel, question, spaceId, role }: Props) {
  const [page, setPage] = useState(0);
  const [copied, setCopied] = useState(false);
  const [follow, setFollow] = useState<StudioAskEnvelope | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [pick, setPick] = useState<ModelChartSpec | null>(null);
  useEffect(() => {
    setFollow(null);
    setConfirmError(null);
    setPick(null);
  }, [envelope]);
  const stageList = stages ?? [];
  if (!envelope) {
    if (stageList.length === 0) return null;
    return (
      <section data-testid="studio-result" className="mt-6 border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-4 py-4">
        <AskStages stages={stageList} onCancel={onCancel} />
      </section>
    );
  }
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
  const adjust = abstained || reconfirm ? null : readAdjust(shown.chart_adjust, rows);
  const proposal = abstained || reconfirm ? null : validateModelSpec(shown.chart_spec, rows);
  const spec = abstained || reconfirm ? null : pick ?? proposal ?? chartFromRows(rows);
  const currentY = Array.isArray(spec?.y) ? spec.y : spec?.y ? [spec.y] : [];
  const currentSeries = spec && "series" in spec ? (spec.series ?? null) : null;
  const activeKind =
    spec && adjust?.kinds.some((kind) => kind === spec.kind) ? spec.kind : (adjust?.kinds[0] ?? "");

  function apply(choice: {
    kind: string;
    x?: string;
    y?: string[];
    series?: string | null;
    repairX?: boolean;
    repairY?: boolean;
  }) {
    if (!adjust) return;
    const next = chooseChart(rows, adjust, choice);
    if (next) setPick(next);
  }
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
      <AskStages stages={stageList} onCancel={onCancel} />

      {reconfirm && offer ? (
        <div
          data-testid="studio-reconfirm"
          className="mt-3 border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3"
        >
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            Reconfirm
          </p>
          <p data-testid="studio-reconfirm-reason" className="mt-2 text-sm text-[var(--color-ink)]">
            {visibleReason(offer.reason_text, offer.reason_code)}
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
            No answer
          </p>
          <p data-testid="studio-abstain-reason" className="mt-2 text-sm font-medium text-[var(--color-ink)]">
            {plainShownReason(namedAbstainReason(shown))}
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
        {role === "steward" && sql ? (
          <AdjustSqlPanel sql={sql} question={question ?? ""} spaceId={spaceId ?? null} />
        ) : null}
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

      {adjust && activeKind !== "" ? (
        <div data-testid="studio-chart-adjust" className="mt-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            Adjust
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {adjust.kinds.map((kind) => (
              <button
                key={kind}
                type="button"
                data-testid={`studio-chart-kind-${kind}`}
                aria-pressed={spec?.kind === kind}
                onClick={() =>
                  apply({
                    kind,
                    x: spec?.x,
                    y: currentY,
                    series: currentSeries,
                    repairX: true,
                    repairY: true,
                  })
                }
                className={chip(spec?.kind === kind)}
              >
                {kindLabel(kind)}
              </button>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {adjust.x.map((name) => (
              <button
                key={`x-${name}`}
                type="button"
                data-testid={`studio-chart-x-${name}`}
                aria-pressed={spec?.x === name}
                onClick={() =>
                  apply({
                    kind: activeKind,
                    x: name,
                    y: currentY,
                    series: currentSeries,
                    repairX: false,
                    repairY: true,
                  })
                }
                className={chip(spec?.x === name)}
              >
                {name}
              </button>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {adjust.y.map((name) => {
              const on = currentY.includes(name);
              const multi = activeKind === "stacked" || activeKind === "multi_line";
              return (
                <button
                  key={`y-${name}`}
                  type="button"
                  data-testid={`studio-chart-y-${name}`}
                  aria-pressed={on}
                  onClick={() => {
                    const nextY = !multi ? [name] : on ? currentY.filter((item) => item !== name) : [...currentY, name];
                    apply({
                      kind: activeKind,
                      x: spec?.x,
                      y: nextY,
                      series: currentSeries,
                      repairX: true,
                      repairY: false,
                    });
                  }}
                  className={chip(on)}
                >
                  {name}
                </button>
              );
            })}
          </div>
          {adjust.series.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                data-testid="studio-chart-series-none"
                aria-pressed={currentSeries == null}
                onClick={() =>
                  apply({
                    kind: activeKind,
                    x: spec?.x,
                    y: currentY,
                    series: null,
                    repairX: true,
                    repairY: true,
                  })
                }
                className={chip(currentSeries == null)}
              >
                none
              </button>
              {adjust.series.map((name) => (
                <button
                  key={`s-${name}`}
                  type="button"
                  data-testid={`studio-chart-series-${name}`}
                  aria-pressed={currentSeries === name}
                  onClick={() =>
                    apply({
                      kind: activeKind,
                      x: spec?.x,
                      y: currentY,
                      series: name,
                      repairX: true,
                      repairY: true,
                    })
                  }
                  className={chip(currentSeries === name)}
                >
                  {name}
                </button>
              ))}
            </div>
          ) : null}
        </div>
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

function chip(active: boolean): string {
  return active
    ? "border border-[var(--color-accent)] bg-[var(--color-panel)] px-2 py-1 text-xs text-[var(--color-ink)]"
    : "border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1 text-xs text-[var(--color-ink)] hover:border-[var(--color-accent)]";
}

function listRows(rows: StudioAskEnvelope["rows"]): Record<string, unknown>[] {
  if (!Array.isArray(rows)) return [];
  return rows.filter((row) => row && typeof row === "object");
}
