import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { DependencyNotice, Page, PageHeader, Section } from "@/components/PageShell";
import { useApp } from "@/context/AppContext";
import {
  APPROVED_NOTE,
  MEMORY_NOTICE_HINT,
  MEMORY_NOTICE_TITLE,
  approveItem,
  boundSentences,
  columnList,
  fetchSpaceItems,
  isKey,
  keyConflictSentence,
  keyEvidenceSentence,
  linkEvidenceSentence,
  linkUsabilitySentence,
  measureSpace,
  observationSentence,
  refusalSentence,
  rejectItem,
  skippedSentence,
  statusLabel,
  tableReviews,
  unkeyedSentence,
  withItem,
  type ProfileBody,
  type ProfileItem,
  type Refusal,
} from "@/lib/ontologyReview";

/**
 * ONTO-PROFILE-03 (dms#516). The steward review of what the profiler proposed
 * for a file Space: keys and links, each with its counts in view.
 *
 * Three rules this page keeps:
 * - One item per press. Nothing here approves a table, a version or a list.
 * - The page is not a control. A status on screen is the status the API last
 *   returned for that item. A press shows "working" until the API answers.
 * - Counts, table names and column names only. No cell value is rendered, and
 *   there is no control for a measure.
 */
export function OntologyReviewPage() {
  const { spaceId = "" } = useParams();
  const { spaces } = useApp();
  const spaceName = spaces.find((s) => s.id === spaceId)?.name ?? null;

  const [items, setItems] = useState<ProfileItem[]>([]);
  const [measured, setMeasured] = useState<ProfileBody | null>(null);
  const [persisted, setPersisted] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [measuring, setMeasuring] = useState(false);
  /** A refusal for the whole page (load or measure). */
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  /** Item id -> the press that is waiting for the API. */
  const [pending, setPending] = useState<Record<string, "approve" | "reject">>({});
  /** Item id -> what the API said when it refused the last press. */
  const [itemNotice, setItemNotice] = useState<Record<string, string>>({});

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    setRefusal(null);
    setMeasured(null);
    setItems([]);
    fetchSpaceItems(spaceId, ctrl.signal)
      .then((result) => {
        if (result.ok) {
          setItems(result.body.items ?? []);
          setPersisted(typeof result.body.persisted === "boolean" ? result.body.persisted : null);
        } else {
          setRefusal(result.refusal);
        }
        setLoading(false);
      })
      .catch(() => {
        /* aborted: the next effect owns the state */
      });
    return () => ctrl.abort();
  }, [spaceId]);

  async function measure() {
    setMeasuring(true);
    setRefusal(null);
    const result = await measureSpace(spaceId);
    setMeasuring(false);
    if (!result.ok) {
      setRefusal(result.refusal);
      return;
    }
    const body = result.body;
    // Items the Space holds that this run did not measure again stay listed.
    const kept = new Set(body.not_remeasured ?? []);
    const fresh = new Set(body.items.map((i) => i.id));
    setItems((prev) => [...body.items, ...prev.filter((i) => kept.has(i.id) && !fresh.has(i.id))]);
    setMeasured(body);
    setPersisted(typeof body.persisted === "boolean" ? body.persisted : null);
    setItemNotice({});
  }

  /** One item, one request. The status changes only from the returned item. */
  async function press(item: ProfileItem, action: "approve" | "reject") {
    if (pending[item.id]) return;
    setPending((p) => ({ ...p, [item.id]: action }));
    setItemNotice((n) => {
      const { [item.id]: _gone, ...rest } = n;
      return rest;
    });
    const result = await (action === "approve" ? approveItem(item.id) : rejectItem(item.id));
    setPending((p) => {
      const { [item.id]: _gone, ...rest } = p;
      return rest;
    });
    if (result.ok) {
      if (result.body && result.body.id === item.id && typeof result.body.status === "string") {
        setItems((prev) => withItem(prev, result.body));
      } else {
        setItemNotice((n) => ({
          ...n,
          [item.id]: "The API answer did not name this item. The status shown is unchanged.",
        }));
      }
      return;
    }
    if (result.refusal.kind === "steward_grant_required") {
      setRefusal(result.refusal);
      return;
    }
    const sentence =
      result.refusal.kind === "key_already_approved"
        ? keyConflictSentence(result.refusal, items)
        : refusalSentence(result.refusal);
    setItemNotice((n) => ({ ...n, [item.id]: sentence }));
  }

  const noGrant = refusal?.kind === "steward_grant_required";
  const tables = tableReviews(items, measured);
  const bounds = boundSentences(measured?.bounds);
  const notRemeasured = new Set(measured?.not_remeasured ?? []);
  const count = (status: string) => items.filter((i) => i.status === status).length;

  function itemRow(item: ProfileItem) {
    const key = isKey(item);
    const what = key
      ? `key ${columnList(item.payload.key)} of ${item.payload.relation}`
      : `link ${item.payload.from_object}.${columnList(item.payload.from_columns)} to ${item.payload.to_object}.${columnList(item.payload.to_columns)}`;
    const busy = pending[item.id];
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
          {notRemeasured.has(item.id) && (
            <p className="mt-1 text-xs text-[var(--color-warn)]">
              Not measured again in the last run: these counts are from an earlier one.
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
        {!noGrant && (
          <div className="flex shrink-0 gap-2">
            {item.status !== "approved" && (
              <button
                type="button"
                data-testid="onto-review-approve"
                aria-label={`Approve ${what}`}
                disabled={!!busy}
                onClick={() => void press(item, "approve")}
                className="min-h-11 min-w-11 border border-[var(--color-accent)] bg-[var(--color-accent)] px-4 text-sm font-medium text-[var(--color-on-accent)] disabled:opacity-50"
              >
                {busy === "approve" ? "Approving…" : "Approve"}
              </button>
            )}
            {item.status !== "revoked" && (
              <button
                type="button"
                data-testid="onto-review-reject"
                aria-label={`Reject ${what}`}
                disabled={!!busy}
                onClick={() => void press(item, "reject")}
                className="min-h-11 min-w-11 border border-[var(--color-line)] px-4 text-sm hover:border-[var(--color-danger)] disabled:opacity-50"
              >
                {busy === "reject" ? "Rejecting…" : "Reject"}
              </button>
            )}
          </div>
        )}
      </li>
    );
  }

  return (
    <Page>
      <div data-testid="onto-review-page">
        <PageHeader
          phase="Steward · review"
          title="Review keys and links"
          blurb="The system counted this Space's uploaded files and proposes which columns identify a row and which columns point at another table. Nothing proposed here is used in an answer until a steward approves it, one item at a time, with the counts in view."
          actions={
            <Link
              to="/spaces"
              className="inline-flex min-h-11 items-center border border-[var(--color-line)] px-3 text-sm hover:border-[var(--color-accent)]"
            >
              Back to Spaces
            </Link>
          }
        />

        <p className="mt-4 break-all text-sm">
          <span className="text-[var(--color-ink-muted)]">Space: </span>
          {spaceName ? <span className="font-medium">{spaceName} </span> : null}
          <span className="font-mono text-xs text-[var(--color-ink-muted)]">{spaceId}</span>
        </p>

        {persisted === false && (
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
                title={
                  refusal.kind === "no_file_tables" ? "Nothing to measure in this Space" : "Refused"
                }
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
                disabled={measuring || loading}
                onClick={() => void measure()}
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
                {items.length > 0 && !measured && (
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
                      {bounds.map((line) => (
                        <li key={line} className="break-words">
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
      </div>
    </Page>
  );
}
