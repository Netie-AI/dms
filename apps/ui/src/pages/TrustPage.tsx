import { useState } from "react";
import {
  AsyncBoundary,
  DependencyNotice,
  Page,
  PageHeader,
  Pill,
  Section,
  StatTile,
} from "@/components/PageShell";
import { useApp } from "@/context/AppContext";
import { fetchTrustRun, fetchTrustSummary } from "@/lib/api";
import {
  claimHeadline,
  measuredText,
  publicTrustHint,
  recordedCount,
  trustCorpusRun,
  trustIsEmpty,
  wrongCountLabel,
} from "@/lib/trustEmpty";
import { useAsync } from "@/lib/useAsync";
import type { TrustRun, TrustRunDetailItem, TrustSummary } from "@/lib/types";
import { SpaceBenchPanel } from "@/pages/SpaceBenchPanel";

function pct(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "Not measured yet";
  return `${(value * 100).toFixed(1)}%`;
}

/**
 * Two bars, not one. The filled bar is what a human verified — the only number
 * the claim may rest on. The outline behind it is everything scored, which is
 * larger and reads as progress but proves nothing on its own. Drawing a single
 * bar at the scored figure is the visual version of the lie this page exists to
 * prevent.
 */
function VerificationBar({
  verified,
  scored,
  target,
}: {
  verified: number;
  scored: number;
  target: number;
}) {
  const pctOf = (n: number) => `${Math.min(100, (n / Math.max(1, target)) * 100).toFixed(1)}%`;
  return (
    <div className="mt-3">
      <div
        className="relative h-3 w-full border border-[var(--color-line)] bg-[var(--color-surface)]"
        role="img"
        aria-label={`${verified} of ${target} human-verified; ${scored} scored`}
      >
        <div
          className="absolute inset-y-0 left-0 bg-[var(--color-ink-muted)]/25"
          style={{ width: pctOf(scored) }}
        />
        <div
          className="absolute inset-y-0 left-0 bg-[var(--color-accent)]"
          style={{ width: pctOf(verified) }}
        />
      </div>
      <p className="mt-1.5 text-xs text-[var(--color-ink-muted)]">
        <strong className="text-[var(--color-ink)]">{verified.toLocaleString()}</strong> human-verified
        of {target.toLocaleString()} needed
        {scored > verified && (
          <>
            {" · "}
            {scored.toLocaleString()} scored ({(scored - verified).toLocaleString()} awaiting review —
            they count against wrong, not toward the claim)
          </>
        )}
      </p>
    </div>
  );
}

function RunCard({
  run,
  onInspect,
}: {
  run: TrustRun;
  onInspect: (id: string, outcome?: string) => void;
}) {
  const totals = run.totals ?? {};
  const wrong = recordedCount(totals.wrong);
  const failed = recordedCount(totals.fail);
  const tone = wrong !== null && wrong > 0 ? "danger" : wrong === null ? "warn" : "ok";

  return (
    <li className="border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-3.5 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-medium">{run.label}</p>
        {!run.present ? (
          <Pill tone="warn">Not measured yet</Pill>
        ) : (
          <span className="flex flex-wrap gap-1.5">
            {run.mode && <Pill>{run.mode}</Pill>}
            <Pill tone={tone === "ok" ? "ok" : tone}>{wrongCountLabel(wrong)}</Pill>
            {failed !== null && failed > 0 && <Pill tone="danger">{failed} failed</Pill>}
            {run.passed != null && (
              <Pill tone={run.passed ? "ok" : "danger"}>{run.passed ? "pass" : "fail"}</Pill>
            )}
          </span>
        )}
      </div>
      <p className="mt-1 text-sm text-[var(--color-ink-muted)]">{run.proves}</p>

      {run.present ? (
        <>
          <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-[var(--color-ink-muted)]">
            <span>
              n{" "}
              <strong className="text-[var(--color-ink)]">
                {measuredText(recordedCount(totals.total) ?? recordedCount(totals.pass))}
              </strong>
            </span>
            {totals.correct != null && (
              <span>
                correct <strong className="text-[var(--color-ink)]">{totals.correct}</strong> ·{" "}
                {pct(run.rates?.correct)}
              </span>
            )}
            {totals.abstain != null && (
              <span>
                abstain <strong className="text-[var(--color-ink)]">{totals.abstain}</strong> ·{" "}
                {pct(run.rates?.abstain)}
              </span>
            )}
            {totals.error != null && (
              <span>
                error <strong className="text-[var(--color-ink)]">{totals.error}</strong>
              </span>
            )}
            {totals.robustness != null && (
              <span>
                robustness <strong className="text-[var(--color-ink)]">{pct(totals.robustness)}</strong>
              </span>
            )}
            {totals.envelope_errors != null && (
              <span>
                envelope errors{" "}
                <strong className="text-[var(--color-ink)]">{totals.envelope_errors}</strong>
              </span>
            )}
          </div>
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => onInspect(run.id)}
              className="border border-[var(--color-line)] px-2.5 py-1 text-xs hover:border-[var(--color-accent)]"
            >
              Inspect cases
            </button>
            {wrong !== null && wrong > 0 && (
              <button
                type="button"
                onClick={() => onInspect(run.id, "wrong")}
                className="border border-[var(--color-danger)]/50 px-2.5 py-1 text-xs text-[var(--color-danger)]"
              >
                Show the {wrong} wrong
              </button>
            )}
            <span className="font-mono text-[10px] text-[var(--color-ink-muted)]">{run.file}</span>
          </div>
        </>
      ) : (
        <p className="mt-2 font-mono text-[11px] text-[var(--color-ink-muted)]">
          no artifact at {run.file}
        </p>
      )}
    </li>
  );
}

function CategoryTable({ run }: { run: TrustRun }) {
  const rows = Object.entries(run.by_category ?? run.tiers ?? {});
  if (!rows.length) return null;
  return (
    <div className="overflow-x-auto border border-[var(--color-line)] bg-[var(--color-surface)]/60">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            <th className="px-3 py-2 font-semibold">Category</th>
            <th className="px-3 py-2 font-semibold">n</th>
            <th className="px-3 py-2 font-semibold">Correct</th>
            <th className="px-3 py-2 font-semibold">Wrong</th>
            <th className="px-3 py-2 font-semibold">Abstain</th>
            <th className="px-3 py-2 font-semibold">Error</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, t]) => {
            const wrong = recordedCount(t.wrong);
            return (
            <tr key={name} className="border-t border-[var(--color-line)]/60">
              <td className="px-3 py-1.5 font-mono text-xs">{name}</td>
              <td className="px-3 py-1.5 text-xs">{measuredText(t.total)}</td>
              <td className="px-3 py-1.5 text-xs">{measuredText(t.correct)}</td>
              <td
                className={`px-3 py-1.5 text-xs ${
                  wrong !== null && wrong > 0 ? "font-semibold text-[var(--color-danger)]" : ""
                }`}
              >
                {measuredText(wrong)}
              </td>
              <td className="px-3 py-1.5 text-xs">{measuredText(t.abstain)}</td>
              <td className="px-3 py-1.5 text-xs">{measuredText(t.error)}</td>
            </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CaseList({ items }: { items: TrustRunDetailItem[] }) {
  if (!items.length) {
    return (
      <p className="border border-[var(--color-line)] px-3 py-4 text-sm text-[var(--color-ink-muted)]">
        No cases returned.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-[var(--color-line)] border border-[var(--color-line)] bg-[var(--color-surface)]/60">
      {items.map((item, i) => (
        <li key={item.id ?? i} className="px-3.5 py-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-xs">{item.id ?? `case ${i + 1}`}</span>
            <span className="flex flex-wrap gap-1.5">
              {item.category && <Pill>{item.category}</Pill>}
              {item.persona && <Pill>{item.persona}</Pill>}
              {item.outcome && (
                <Pill
                  tone={
                    item.outcome === "correct"
                      ? "ok"
                      : item.outcome === "wrong"
                        ? "danger"
                        : "warn"
                  }
                >
                  {item.outcome}
                </Pill>
              )}
            </span>
          </div>
          {(item.raw_question || item.question) && (
            <p className="mt-1 text-sm">{item.raw_question ?? item.question}</p>
          )}
          {item.detail && (
            <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">{item.detail}</p>
          )}
          {item.sql_used && (
            <pre className="mt-1.5 overflow-x-auto border border-[var(--color-line)] bg-[var(--color-paper)] p-2 text-[11px]">
              {item.sql_used}
            </pre>
          )}
        </li>
      ))}
    </ul>
  );
}

export function TrustPage() {
  const { activeSpaceId } = useApp();
  const { data, error, loading, reload } = useAsync<TrustSummary>((signal) =>
    fetchTrustSummary(signal),
  );
  const [tab, setTab] = useState<"evidence" | "benchmarks">("evidence");
  const [inspect, setInspect] = useState<{ id: string; outcome?: string } | null>(null);
  const detail = useAsync(
    (signal) =>
      inspect
        ? fetchTrustRun(inspect.id, inspect.outcome, signal)
        : Promise.resolve({ ok: true, id: "", items: [] as TrustRunDetailItem[] }),
    [inspect?.id, inspect?.outcome],
  );

  const claim = data?.claim;
  const empty = trustIsEmpty(data);
  const runs = Object.values(data?.runs ?? {});
  const corpusRun = trustCorpusRun(data?.runs);
  const wrongN = recordedCount(claim?.confidently_wrong);
  const corpusN = recordedCount(claim?.corpus_n);
  const expandedN = recordedCount(claim?.corpus_expanded_n);
  const unverifiedN = recordedCount(claim?.corpus_unverified_n);
  const thresholds = (data?.thresholds ?? {}) as Record<string, unknown>;

  return (
    <Page>
      <PageHeader
        phase="Answer checks"
        title="Trust"
        blurb="See how many questions were answered and whether any answered number was wrong."
        actions={
          <button
            type="button"
            onClick={reload}
            className="h-9 border border-[var(--color-line)] px-3 text-sm hover:border-[var(--color-accent)]"
          >
            Refresh
          </button>
        }
      />

      <div className="mt-4 flex gap-2" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "evidence"}
          onClick={() => setTab("evidence")}
          className="border border-[var(--color-line)] px-2.5 py-1 text-xs"
        >
          Evidence
        </button>
        <button
          type="button"
          role="tab"
          data-testid="trust-bench-tab"
          aria-selected={tab === "benchmarks"}
          onClick={() => setTab("benchmarks")}
          className="border border-[var(--color-line)] px-2.5 py-1 text-xs"
        >
          Benchmarks
        </button>
      </div>

      {tab === "benchmarks" ? (
        <SpaceBenchPanel spaceId={activeSpaceId} />
      ) : (
        <>
      {empty && (
        <div
          data-testid="trust-empty"
          className="mt-6 border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-4 py-4"
        >
          <p className="font-medium" data-testid="trust-unmeasured">
            Not measured yet
          </p>
          <p className="mt-1 font-medium">{data?.empty_title || "No benchmark run yet"}</p>
          <p className="mt-2 text-sm text-[var(--color-ink-muted)]">{data?.how_to_run}</p>
        </div>
      )}

      {data?.ok === false && !empty && (
        <DependencyNotice
          tone="danger"
          title="Evidence is unavailable"
          hint={publicTrustHint(data.hint)}
        />
      )}

      {!!data?.ask_path?.length && (
        <Section title="Questions answered in this app">
          <p className="mb-3 text-sm text-[var(--color-ink-muted)]">
            These counts come from questions this app answered. A wrong number is never traded for more coverage.
          </p>
          <ul className="space-y-2">
            {data.ask_path.map((row) => (
              <li
                key={row.pack}
                className="border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-3.5 py-3 text-sm"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-medium">{row.pack}</p>
                  <Pill
                    tone={
                      recordedCount(row.wrong) === null
                        ? "warn"
                        : recordedCount(row.wrong) === 0 && row.passed
                          ? "ok"
                          : "danger"
                    }
                  >
                    {wrongCountLabel(row.wrong)}
                  </Pill>
                  <Pill>
                    precision {row.precision_on_answered.toFixed(2)}% ({row.correct}/
                    {row.answered})
                  </Pill>
                  <Pill>
                    coverage {row.coverage_pct.toFixed(1)}% ({row.answered}/{row.total})
                  </Pill>
                </div>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        {!empty && claim && (
          <div
            className={`mt-6 border px-4 py-4 ${
              claim.supported
                ? "border-[var(--color-badge-ok)]/40 bg-[var(--color-accent-soft)]/40"
                : "border-[var(--color-warn)]/50 bg-[var(--color-warn-soft)]"
            }`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-[family-name:var(--font-display)] text-xl font-semibold">
                “{claimHeadline(claim)}”
              </p>
              <Pill tone={claim.supported ? "ok" : "warn"}>
                {claim.supported ? "supported by evidence" : "not yet claimable"}
              </Pill>
              {claim.phase && <Pill>phase {claim.phase}</Pill>}
            </div>
            {claim.supported ? (
              <p className="mt-2 text-sm">
                Every recorded run is green and the corpus has reached its target size.
              </p>
            ) : claim.blockers?.length ? (
              <ul className="mt-2 list-inside list-disc text-sm text-[var(--color-ink)]">
                {claim.blockers.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-[var(--color-ink-muted)]">
                The files on disk do not mark this claim supported.
              </p>
            )}
            {claim.corpus_target != null && corpusN !== null ? (
              <VerificationBar
                verified={corpusN}
                scored={expandedN ?? corpusN}
                target={claim.corpus_target}
              />
            ) : null}
          </div>
        )}

        {!empty && (
        <>
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <StatTile
            label="Confidently wrong"
            value={measuredText(wrongN)}
            tone={wrongN === null ? "neutral" : wrongN > 0 ? "danger" : "ok"}
            hint="A confident wrong number is not allowed."
          />
          <StatTile
            label="Verified corpus"
            value={
              claim?.corpus_target != null && corpusN !== null
                ? `${corpusN} / ${claim.corpus_target}`
                : measuredText(corpusN)
            }
            tone={
              corpusN === null
                ? "neutral"
                : claim?.corpus_target != null && corpusN < claim.corpus_target
                  ? "warn"
                  : "ok"
            }
            hint={
              expandedN !== null &&
              corpusN !== null &&
              unverifiedN !== null &&
              expandedN > corpusN
                ? `${expandedN} scored, ${unverifiedN} unreviewed`
                : "human-reviewed cases behind the claim"
            }
          />
          <StatTile
            label="Correct rate"
            value={pct(corpusRun?.rates?.correct)}
            hint={`floor ${measuredText(thresholds.correct_rate_floor)}`}
          />
          <StatTile
            label="Abstain rate"
            value={pct(corpusRun?.rates?.abstain)}
            hint={`ceiling ${measuredText(thresholds.abstain_rate_ceiling)}`}
          />
        </div>

        <Section
          title="Benchmark runs"
          count={runs.length}
          description="Each artifact is a file on the engine's disk that can be regenerated offline. Nothing here is computed by this page."
        >
          <ul className="flex flex-col gap-2">
            {runs.map((run) => (
              <RunCard
                key={run.id}
                run={run}
                onInspect={(id, outcome) => setInspect({ id, outcome })}
              />
            ))}
          </ul>
        </Section>

        {corpusRun?.present && (
          <Section
            title="Corpus by failure category"
            description="The 12 categories exist because these are the ways a warehouse question goes wrong quietly — fan-out, NULL semantics, silent dedup, code-switching, value normalisation."
          >
            <CategoryTable run={corpusRun} />
          </Section>
        )}
        </>
        )}

        {!empty && inspect && (
          <Section
            title={`Cases · ${inspect.id}${inspect.outcome ? ` · ${inspect.outcome}` : ""}`}
            actions={
              <button
                type="button"
                onClick={() => setInspect(null)}
                className="text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
              >
                Close
              </button>
            }
          >
            <AsyncBoundary loading={detail.loading} error={detail.error} onRetry={detail.reload}>
              <CaseList items={detail.data?.items ?? []} />
            </AsyncBoundary>
          </Section>
        )}
      </AsyncBoundary>
        </>
      )}
    </Page>
  );
}
