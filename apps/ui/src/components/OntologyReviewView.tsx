import type { ReactNode } from "react";
import { DependencyNotice, Section } from "@/components/PageShell";
import {
  APPROVED_NOTE,
  MEMORY_NOTICE_HINT,
  MEMORY_NOTICE_TITLE,
  boundSentences,
  canApprove,
  canReject,
  columnList,
  hasNoGrant,
  isKey,
  isStale,
  keyEvidenceSentence,
  linkEvidenceSentence,
  linkUsabilitySentence,
  measureBlocked,
  observationSentence,
  pressBlocked,
  refusalSentence,
  showMemoryNotice,
  skippedSentence,
  statusLabel,
  tableReviews,
  unkeyedSentence,
  type ProfileItem,
  type ReviewState,
} from "@/lib/ontologyReview";

/**
 * ONTO-PROFILE-03 (dms#516). What the steward sees, as a function of the review
 * state and nothing else: no fetch, no router, no state of its own. That is
 * what lets `npm test` render it and assert the rules without a browser.
 *
 * - One item per press: every Approve and Reject carries exactly one item.
 * - A status shown is `item.status`, which only the API's answer changes.
 * - Counts, table names and column names only. No cell value, no measure control.
 */
export function OntologyReviewView({
  state,
  spaceName,
  onMeasure,
  onPress,
}: {
  state: ReviewState;
  spaceName: string | null;
  onMeasure: () => void;
  onPress: (item: ProfileItem, action: "approve" | "reject") => void;
}): ReactNode {
  const { items, measured, refusal, loading, measuring, pending, itemNotice } = state;
  const noGrant = hasNoGrant(state);
  const tables = tableReviews(items, measured);
  const bounds = boundSentences(measured?.bounds);
  const count = (status: string) => items.filter((i) => i.status === status).length;

  function itemRow(item: ProfileItem) {
    const key = isKey(item);
    const what = key
      ? `key ${columnList(item.payload.key)} of ${item.payload.relation}`
      : `link ${item.payload.from_object}.${columnList(item.payload.from_columns)} to ${item.payload.to_object}.${columnList(item.payload.to_columns)}`;
    const busy = pending[item.id];
    const blocked = pressBlocked(state, item);
    const usability = key ? null : linkUsabilitySentence(item, items);
    return (
      <li
        key={item.id}
        data-testid="onto-review-item"
        data-item-id={item.id}
        data-kind={key ? "key" : "link"}
        data-status={item.status}
        className="flex flex-wrap items-start justify-between gap-3 px-3.5 py-3"
      >
        <div className="min-w-0 flex-1 basis-64">
          <p className="break-words text-sm font-medium">
            <span className="text-[var(--color-ink-muted)]">{key ? "Key: " : "Link: "}</span>
            {key ? (
              <span className="font-mono">{columnList(item.payload.key)}</span>
            ) : (
              <span className="font-mono">
                {columnList(item.payload.from_columns)} to {item.payload.to_object}.
                {columnList(item.payload.to_columns)}
              </span>
            )}
            <span
              data-testid="onto-review-status"
              className={`ml-2 inline-flex border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                item.status === "approved"
                  ? "border-[var(--color-ink)] text-[var(--color-ink)]"
                  : item.status === "revoked"
                    ? "border-[var(--color-danger)]/50 text-[var(--color-danger)]"
                    : "border-[var(--color-line)] text-[var(--color-ink-muted)]"
              }`}
            >
              {statusLabel(item.status)}
            </span>
          </p>
          <p data-testid="onto-review-evidence" className="mt-1 text-sm">
            {key
              ? keyEvidenceSentence(item.provenance?.evidence)
              : linkEvidenceSentence(item.provenance?.evidence)}
          </p>
          {usability && (
            <p data-testid="onto-review-link-usability" className="mt-1 text-xs text-[var(--color-warn)]">
              {usability}
            </p>
          )}
          {item.status === "approved" && (
            <p data-testid="onto-review-approved-note" className="mt-1 text-xs text-[var(--color-ink-muted)]">
              {APPROVED_NOTE}
            </p>
          )}
          {isStale(state, item) && (
            <p data-testid="onto-review-stale" className="mt-1 text-xs text-[var(--color-warn)]">
              The last measurement did not find this in the Space any more. These counts are
              from an earlier one, so it cannot be approved. It can be rejected.
            </p>
          )}
          {itemNotice[item.id] && (
            <p
              role="alert"
              data-testid="onto-review-item-notice"
              className="mt-1.5 border border-[var(--color-danger)]/40 px-2 py-1.5 text-xs text-[var(--color-danger)]"
            >
              {itemNotice[item.id]}
            </p>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          {canApprove(state, item) && (
            <button
              type="button"
              data-testid="onto-review-approve"
              aria-label={`Approve ${what}`}
              disabled={blocked}
              onClick={() => onPress(item, "approve")}
              className="min-h-11 min-w-11 border border-[var(--color-accent)] bg-[var(--color-accent)] px-4 text-sm font-medium text-[var(--color-on-accent)] disabled:opacity-50"
            >
              {busy === "approve" ? "Approving…" : "Approve"}
            </button>
          )}
          {canReject(state, item) && (
            <button
              type="button"
              data-testid="onto-review-reject"
              aria-label={`Reject ${what}`}
              disabled={blocked}
              onClick={() => onPress(item, "reject")}
              className="min-h-11 min-w-11 border border-[var(--color-line)] px-4 text-sm hover:border-[var(--color-danger)] disabled:opacity-50"
            >
              {busy === "reject" ? "Rejecting…" : "Reject"}
            </button>
          )}
        </div>
      </li>
    );
  }

  return (
    <>
      <p className="mt-4 break-all text-sm">
        <span className="text-[var(--color-ink-muted)]">Space: </span>
        {spaceName ? <span className="font-medium">{spaceName} </span> : null}
        <span className="font-mono text-xs text-[var(--color-ink-muted)]">{state.spaceId}</span>
      </p>

      {showMemoryNotice(state) && (
        <DependencyNotice
          tone="danger"
          title={MEMORY_NOTICE_TITLE}
          hint={MEMORY_NOTICE_HINT}
          data-testid="onto-review-memory-notice"
        />
      )}

      {noGrant ? (
        <DependencyNotice
          tone="danger"
          title="Steward grant required"
          hint={refusalSentence({ kind: "steward_grant_required" })}
          data-testid="onto-review-steward-required"
        />
      ) : (
        <>
          {refusal && (
            <DependencyNotice
              title={refusal.kind === "no_file_tables" ? "Nothing to measure in this Space" : "Refused"}
              hint={refusalSentence(refusal)}
              data-testid={
                refusal.kind === "no_file_tables" ? "onto-review-no-file-tables" : "onto-review-refusal"
              }
            />
          )}
          {refusal?.kind === "no_file_tables" && refusal.skipped.length > 0 && (
            <ul className="mt-2 text-xs text-[var(--color-ink-muted)]">
              {refusal.skipped.map((row) => (
                <li key={row.table}>{skippedSentence(row)}</li>
              ))}
            </ul>
          )}

          <div className="mt-5 flex flex-wrap items-center gap-3">
            <button
              type="button"
              data-testid="onto-review-measure"
              disabled={measureBlocked(state)}
              onClick={onMeasure}
              className="min-h-11 bg-[var(--color-accent)] px-4 text-sm font-medium text-[var(--color-on-accent)] disabled:opacity-50"
            >
              {measuring ? "Measuring…" : "Measure this Space"}
            </button>
            <p className="text-xs text-[var(--color-ink-muted)]">
              Counts rows, distinct values and empty values. Reads no cell value onto this page.
            </p>
          </div>

          {loading ? (
            <p className="mt-6 text-sm text-[var(--color-ink-muted)]">Loading stored proposals…</p>
          ) : (
            <>
              <p data-testid="onto-review-summary" className="mt-5 text-sm">
                {items.length === 0
                  ? "No proposal is stored for this Space yet."
                  : `${count("proposed")} proposed, ${count("approved")} approved, ${count("revoked")} rejected. Approve or reject one item at a time.`}
              </p>
              {items.length > 0 && state.stale === "all" && (
                <p data-testid="onto-review-all-stale" className="mt-1 text-xs text-[var(--color-warn)]">
                  These stored proposals are for tables this Space no longer has. They are
                  listed as stored, and none of them can be approved.
                </p>
              )}
              {items.length > 0 && !measured && state.stale !== "all" && (
                <p data-testid="onto-review-stored-only" className="mt-1 text-xs text-[var(--color-ink-muted)]">
                  Showing stored proposals with the counts from their last measurement.
                  Observations, keyless tables and search bounds are not stored: measure the
                  Space to see them.
                </p>
              )}

              {tables.map((t) => (
                <section
                  key={t.table}
                  data-testid="onto-review-table"
                  data-table={t.table}
                  className="mt-6 border border-[var(--color-line)] bg-[var(--color-surface)]/60"
                >
                  <header className="border-b border-[var(--color-line)] px-3.5 py-2.5">
                    <h2 className="break-all font-mono text-sm font-semibold">{t.table}</h2>
                    {t.rowCount != null && (
                      <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">
                        {t.rowCount === 1 ? "1 row" : `${t.rowCount} rows`}
                        {t.columnCount != null ? `, ${t.columnCount} columns` : ""}
                      </p>
                    )}
                  </header>
                  {t.unkeyed && (
                    <p data-testid="onto-review-unkeyed" className="px-3.5 py-3 text-sm">
                      {unkeyedSentence(t.unkeyed)}
                    </p>
                  )}
                  {(t.keys.length > 0 || t.links.length > 0) && (
                    <ul className="divide-y divide-[var(--color-line)]/70">
                      {t.keys.map(itemRow)}
                      {t.links.map(itemRow)}
                    </ul>
                  )}
                  {t.observations.length > 0 && (
                    <div className="border-t border-[var(--color-line)]/70 px-3.5 py-3">
                      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
                        Not proposed
                      </p>
                      <ul className="mt-1.5 flex flex-col gap-2">
                        {t.observations.map((obs) => (
                          <li
                            key={`${obs.child_columns.join("+")}>${obs.parent_table}.${obs.parent_columns.join("+")}`}
                            data-testid="onto-review-observation"
                            className="text-sm"
                          >
                            <p>{observationSentence(obs)}</p>
                            <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">
                              {linkEvidenceSentence(obs.evidence)}
                            </p>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </section>
              ))}

              {measured && (measured.skipped?.length ?? 0) > 0 && (
                <Section title="Tables not measured" count={measured.skipped?.length}>
                  <ul className="text-sm">
                    {measured.skipped?.map((row) => (
                      <li key={row.table}>{skippedSentence(row)}</li>
                    ))}
                  </ul>
                </Section>
              )}

              {measured && (
                <Section
                  title="What was and was not searched"
                  description="Every limit the measurement stated. A key or link outside these limits was not looked for."
                >
                  <ul
                    data-testid="onto-review-bounds"
                    className="list-disc space-y-1 pl-5 text-sm text-[var(--color-ink-muted)]"
                  >
                    {bounds.map((line, i) => (
                      <li key={`${i}:${line}`} className="break-words">
                        {line}
                      </li>
                    ))}
                  </ul>
                </Section>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
