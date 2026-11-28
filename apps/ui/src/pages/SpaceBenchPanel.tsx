import { useEffect, useState } from "react";
import { useApp } from "@/context/AppContext";
import {
  fetchSpaceBench,
  postSpaceBench,
  spaceBenchPath,
  type SpaceBenchBody,
  type SpaceBenchResult,
} from "@/lib/api";
import { useAsync } from "@/lib/useAsync";

function coverageText(value: number | null | undefined): string {
  if (value == null) return "none";
  return `${(value * 100).toFixed(1)}%`;
}

export function SpaceBenchView({
  body,
  busy,
  note,
  onNote,
  onRun,
  onCancel,
  onReview,
  onCreate,
}: {
  body: SpaceBenchBody;
  busy?: boolean;
  note?: string;
  onNote?: (value: string) => void;
  onRun?: () => void;
  onCancel?: () => void;
  onReview?: (resultId: string, review: "good" | "bad", note: string) => void;
  onCreate?: (question: string, goldSql: string) => void;
}) {
  const run = body.latest_run;
  const tally = run?.tally;
  const wrong = Number(tally?.WRONG ?? 0);
  const running = run?.status === "running" || run?.status === "queued";
  const [question, setQuestion] = useState("");
  const [goldSql, setGoldSql] = useState("");

  return (
    <section className="mt-4" data-testid="trust-bench-panel">
      <div className="border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-4 py-4">
        <p
          data-testid="trust-bench-wrong"
          className={`font-[family-name:var(--font-display)] text-3xl font-semibold ${
            wrong > 0 ? "text-[var(--color-danger)]" : ""
          }`}
        >
          WRONG {wrong}
        </p>
        <p className="mt-1 text-sm text-[var(--color-ink-muted)]" data-testid="trust-bench-coverage">
          coverage {coverageText(tally?.coverage)} ({tally?.correct ?? 0}/{tally?.graded ?? 0})
        </p>
        <p className="mt-1 text-xs text-[var(--color-ink-muted)]">
          {tally?.clarify ?? 0} clarify, {tally?.reconfirm ?? 0} reconfirm, {tally?.abstain ?? 0}{" "}
          abstain
          {run ? `, ${run.progress_done}/${run.progress_total} ${run.status}` : ""}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            data-testid="trust-bench-run"
            disabled={busy}
            onClick={onRun}
            className="border border-[var(--color-line)] px-2.5 py-1 text-xs hover:border-[var(--color-accent)]"
          >
            Run benchmarks
          </button>
          {running && (
            <button
              type="button"
              data-testid="trust-bench-cancel"
              onClick={onCancel}
              className="border border-[var(--color-danger)]/50 px-2.5 py-1 text-xs text-[var(--color-danger)]"
            >
              Cancel
            </button>
          )}
        </div>
      </div>

      <ul className="mt-3 divide-y divide-[var(--color-line)] border border-[var(--color-line)]">
        {(run?.results ?? []).map((row) => (
          <BenchRow key={row.id} row={row} note={note ?? ""} onNote={onNote} onReview={onReview} />
        ))}
      </ul>

      <form
        className="mt-4 grid gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (!question.trim() || !goldSql.trim()) return;
          onCreate?.(question.trim(), goldSql.trim());
          setQuestion("");
          setGoldSql("");
        }}
      >
        <label className="text-xs text-[var(--color-ink-muted)]">
          Question
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            className="mt-1 w-full border border-[var(--color-line)] bg-transparent px-2 py-1 text-sm"
          />
        </label>
        <label className="text-xs text-[var(--color-ink-muted)]">
          Ground-truth SQL
          <textarea
            value={goldSql}
            onChange={(event) => setGoldSql(event.target.value)}
            className="mt-1 w-full border border-[var(--color-line)] bg-transparent px-2 py-1 font-mono text-xs"
            rows={3}
          />
        </label>
        <button
          type="submit"
          className="w-fit border border-[var(--color-line)] px-2.5 py-1 text-xs hover:border-[var(--color-accent)]"
        >
          Save benchmark
        </button>
      </form>

      {body.items.some((item) => item.status === "invalid") && (
        <p className="mt-2 text-xs text-[var(--color-ink-muted)]">
          {body.items.filter((item) => item.status === "invalid").length} saved as invalid
        </p>
      )}
    </section>
  );
}

function BenchRow({
  row,
  note,
  onNote,
  onReview,
}: {
  row: SpaceBenchResult;
  note: string;
  onNote?: (value: string) => void;
  onReview?: (resultId: string, review: "good" | "bad", note: string) => void;
}) {
  const bad = row.outcome === "WRONG";
  return (
    <li className="px-3.5 py-2.5" data-testid="trust-bench-row">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm">{row.question}</p>
        <span className={`text-xs font-semibold ${bad ? "text-[var(--color-danger)]" : ""}`}>
          {row.outcome}
        </span>
      </div>
      <p className="mt-1 text-xs text-[var(--color-ink-muted)]">
        rows {row.served_n} compared with {row.gold_n}
        {row.reason ? ` (${row.reason})` : ""}
        {row.review ? `, marked ${row.review}` : ""}
      </p>
      {row.served_sql && (
        <pre className="mt-1 overflow-x-auto border border-[var(--color-line)] bg-[var(--color-paper)] p-2 text-[11px]">
          {row.served_sql}
        </pre>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => onReview?.(row.id, "good", "")}
          className="border border-[var(--color-line)] px-2 py-0.5 text-xs"
        >
          Good
        </button>
        <button
          type="button"
          onClick={() => onReview?.(row.id, "bad", note)}
          className="border border-[var(--color-line)] px-2 py-0.5 text-xs"
        >
          Bad
        </button>
        <input
          value={note}
          onChange={(event) => onNote?.(event.target.value)}
          placeholder="Why this is bad"
          aria-label="Why this is bad"
          className="min-w-40 flex-1 border border-[var(--color-line)] bg-transparent px-2 py-0.5 text-xs"
        />
      </div>
    </li>
  );
}

export function SpaceBenchPanel() {
  const { activeSpaceId: spaceId } = useApp();
  const state = useAsync(
    (signal) =>
      spaceId
        ? fetchSpaceBench(spaceId, signal)
        : Promise.resolve(null as SpaceBenchBody | null),
    [spaceId],
  );
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const runStatus = state.data?.latest_run?.status;

  useEffect(() => {
    if (runStatus !== "running" && runStatus !== "queued") return;
    const timer = setInterval(() => state.reload(), 1000);
    return () => clearInterval(timer);
  }, [runStatus, state.reload]);

  if (!spaceId) {
    return <p className="mt-4 text-sm text-[var(--color-ink-muted)]">Choose a Space first.</p>;
  }
  if (state.loading && !state.data) {
    return <p className="mt-4 text-sm text-[var(--color-ink-muted)]">Loading benchmarks.</p>;
  }
  if (state.error && state.error.includes("404")) {
    return (
      <p className="mt-4 text-sm" data-testid="trust-bench-off">
        Benchmarks are off on this server.
      </p>
    );
  }
  if (state.error || !state.data) {
    return (
      <p className="mt-4 text-sm text-[var(--color-danger)]">
        Benchmarks could not be loaded.
        <button type="button" className="ml-2 underline" onClick={state.reload}>
          Retry
        </button>
      </p>
    );
  }

  const data = state.data;
  const run = data.latest_run;

  async function act(path: string, body?: unknown) {
    setBusy(true);
    setActionError(null);
    try {
      await postSpaceBench(path, body);
      state.reload();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {actionError && (
        <p className="mt-3 text-sm text-[var(--color-danger)]" data-testid="trust-bench-error">
          {actionError}
        </p>
      )}
      <SpaceBenchView
        body={data}
        busy={busy}
        note={note}
        onNote={setNote}
        onRun={() => void act(`${spaceBenchPath(spaceId)}/runs`)}
        onCancel={() => {
          if (run) void act(`${spaceBenchPath(spaceId)}/runs/${run.id}/cancel`);
        }}
        onReview={(resultId, review, text) => {
          if (!run) return;
          void act(`${spaceBenchPath(spaceId)}/runs/${run.id}/results/${resultId}/review`, {
            review,
            note: text,
          });
        }}
        onCreate={(question, goldSql) =>
          void act(spaceBenchPath(spaceId), { question, gold_sql: goldSql })
        }
      />
    </>
  );
}
