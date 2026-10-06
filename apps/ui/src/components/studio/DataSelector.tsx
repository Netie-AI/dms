import { useState } from "react";
import {
  describeApiError,
  fetchTablePreview,
  postAsk,
  previewForNode,
  type TreeNode,
} from "@/lib/api";
import { askErrorBody, toSelectionPayload } from "@/lib/studioSelection";
import type { AnswerEnvelope } from "@/lib/types";

type Props = {
  spaceId: string | null;
  /** Askable Studio leaves (bronze / warehouse tables). */
  leaves: TreeNode[];
  /** Hand the envelope to a result view. Without one, a one-line outcome shows here. */
  onAnswer?: (env: AnswerEnvelope) => void;
};

/**
 * STUDIO-SELECT-01 (#364). Tick tables and columns, ask. The API packs the
 * ticked schema plus ontology joins into the Cortex ask; nothing here reads or
 * sends rows. An empty or unknown pick comes back as a named refusal.
 */
export function DataSelector({ spaceId, leaves, onAnswer }: Props) {
  const [columnsOf, setColumnsOf] = useState<Map<string, string[] | string>>(new Map());
  const [picks, setPicks] = useState<Map<string, string[]>>(new Map());
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<AnswerEnvelope | null>(null);

  const toggleTable = (node: TreeNode) => {
    const target = previewForNode(node.id);
    if (!target) return;
    const next = new Map(picks);
    if (next.has(target.table)) {
      next.delete(target.table);
      setPicks(next);
      return;
    }
    next.set(target.table, []);
    setPicks(next);
    if (columnsOf.has(target.table)) return;
    // One row is the cheapest read that names the columns; only names are kept.
    void fetchTablePreview(target, 1, 0, spaceId)
      .then((p) => setColumnsOf((m) => new Map(m).set(target.table, p.columns)))
      .catch((e) =>
        setColumnsOf((m) =>
          new Map(m).set(target.table, e instanceof Error ? e.message : "columns unavailable"),
        ),
      );
  };

  const toggleColumn = (table: string, column: string) => {
    const cols = picks.get(table) ?? [];
    setPicks(
      new Map(picks).set(
        table,
        cols.includes(column) ? cols.filter((c) => c !== column) : [...cols, column],
      ),
    );
  };

  const ask = async () => {
    setBusy(true);
    setErr(null);
    setOutcome(null);
    try {
      const env = await postAsk({
        question: question.trim(),
        space_id: spaceId,
        selection: toSelectionPayload(picks),
      });
      if (onAnswer) onAnswer(env);
      else setOutcome(env);
    } catch (e) {
      setErr(describeApiError(askErrorBody(e instanceof Error ? e.message : String(e))));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-testid="studio-data-selector"
      className="mt-6 border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-4 py-4"
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
        Ask over selected tables and columns
      </p>
      <p className="mt-2 max-w-2xl text-sm text-[var(--color-ink-muted)]">
        Only the ticked columns' names and types, and the declared joins between the ticked
        tables, go to the engine with your question. No rows are sent.
      </p>
      {leaves.length === 0 ? (
        <p className="mt-3 text-xs text-[var(--color-ink-muted)]">No askable tables in this Space yet.</p>
      ) : (
        <ul className="mt-3 max-h-80 space-y-1 overflow-y-auto text-sm">
          {leaves.map((leaf) => {
            const table = previewForNode(leaf.id)?.table;
            if (!table) return null;
            const ticked = picks.get(table);
            const cols = columnsOf.get(table);
            return (
              <li key={leaf.id}>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    aria-label={`Select table ${table}`}
                    checked={ticked !== undefined}
                    onChange={() => toggleTable(leaf)}
                    className="h-3.5 w-3.5 accent-[var(--color-accent)]"
                  />
                  <span className="font-mono text-xs text-[var(--color-ink)]">{table}</span>
                </label>
                {ticked !== undefined && (
                  <div className="ml-6 mt-1 flex flex-wrap gap-x-3 gap-y-1">
                    {cols === undefined && (
                      <span className="text-xs text-[var(--color-ink-muted)]">Reading columns…</span>
                    )}
                    {typeof cols === "string" && (
                      <span className="text-xs text-[var(--color-warn)]">{cols}</span>
                    )}
                    {Array.isArray(cols) &&
                      cols.map((c) => (
                        <label key={c} className="flex items-center gap-1 text-xs">
                          <input
                            type="checkbox"
                            aria-label={`Select column ${table}.${c}`}
                            checked={ticked.includes(c)}
                            onChange={() => toggleColumn(table, c)}
                            className="h-3 w-3 accent-[var(--color-accent)]"
                          />
                          <span className="font-mono text-[var(--color-ink-muted)]">{c}</span>
                        </label>
                      ))}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <textarea
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        rows={2}
        aria-label="Question over the selection"
        placeholder="Total quantity by location"
        className="mt-3 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-2 text-sm text-[var(--color-ink)]"
      />
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={busy || !question.trim()}
          onClick={() => void ask()}
          className="border border-[var(--color-accent)] bg-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Asking…" : "Ask over selection"}
        </button>
        {err && <span className="text-xs text-[var(--color-danger)]">{err}</span>}
      </div>
      {outcome && (
        <p className="mt-3 text-sm text-[var(--color-ink)]">
          <span className="font-medium">{outcome.abstained ? "Abstained" : outcome.badge}</span>
          {" · "}
          {outcome.text}
        </p>
      )}
    </div>
  );
}
