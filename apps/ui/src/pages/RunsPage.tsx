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
import { fetchRuns } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import type { RunRecord, RunsBody } from "@/lib/types";

const FILTERS: { id: string; label: string }[] = [
  { id: "", label: "All" },
  { id: "ingest", label: "Ingest" },
  { id: "query", label: "Query" },
];

function tone(status: string): "ok" | "warn" | "danger" | "neutral" {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "danger";
  if (status === "partial") return "warn";
  return "neutral";
}

function RunRow({ run }: { run: RunRecord }) {
  const [open, setOpen] = useState(false);
  const hasReasons = run.reasons.length > 0;
  return (
    <li className="border border-[var(--color-line)] bg-[var(--color-surface)]/60">
      <div className="flex flex-wrap items-start justify-between gap-2 px-3.5 py-2.5">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-sm">
            <Pill>{run.kind}</Pill>
            <Pill tone={tone(run.status)}>{run.status}</Pill>
            {run.space_name && <span className="text-[var(--color-ink-muted)]">{run.space_name}</span>}
          </p>
          <p className="mt-1 break-words text-sm text-[var(--color-ink)]">{run.detail || "—"}</p>
          <p className="mt-0.5 font-mono text-[10px] text-[var(--color-ink-muted)]">
            {run.created_at ?? "no timestamp"} · {run.id}
            {run.ledger_seq != null ? ` · ledger #${run.ledger_seq}` : ""}
          </p>
        </div>
        {hasReasons && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="shrink-0 border border-[var(--color-warn)]/50 px-2.5 py-1 text-xs text-[var(--color-warn)]"
          >
            {run.reasons.length} need attention
          </button>
        )}
      </div>
      {open && hasReasons && (
        <ul className="divide-y divide-[var(--color-line)]/60 border-t border-[var(--color-line)] text-sm">
          {run.reasons.map((r, i) => (
            <li key={`${r.file}-${i}`} className="px-3.5 py-2">
              <p className="font-mono text-xs">{r.file}</p>
              <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">{r.reason}</p>
              {r.fix && (
                <p className="mt-0.5 text-xs text-[var(--color-accent)]">Fix: {r.fix}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function RunsPage() {
  const [kind, setKind] = useState("");
  const { data, error, loading, reload } = useAsync<RunsBody>(
    (signal) => fetchRuns(kind || undefined, signal),
    [kind],
  );

  const runs = data?.runs ?? [];
  const counts = data?.counts ?? {};

  return (
    <Page>
      <PageHeader
        phase="U3 · durable state"
        title="Runs"
        blurb="What the system actually did: ingest receipts and query executions, with the file and the fix named on anything that needs attention. Usability rule 7 — never a stack trace, never an apology."
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

      {data?.configured === false && (
        <DependencyNotice title="No durable run history" hint={data.hint} />
      )}

      <div className="mt-6 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatTile label="Runs shown" value={runs.length} />
        <StatTile label="Succeeded" value={counts.succeeded ?? 0} tone="ok" />
        <StatTile
          label="Partial"
          value={counts.partial ?? 0}
          tone={counts.partial ? "warn" : "neutral"}
        />
        <StatTile
          label="Failed"
          value={counts.failed ?? 0}
          tone={counts.failed ? "danger" : "neutral"}
        />
      </div>

      <div className="mt-6 flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.id || "all"}
            type="button"
            onClick={() => setKind(f.id)}
            className={`border px-3 py-1.5 text-sm ${
              kind === f.id
                ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                : "border-[var(--color-line)] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <Section title="Recent activity" count={runs.length}>
        <AsyncBoundary
          loading={loading}
          error={error}
          onRetry={reload}
          empty={runs.length === 0}
          emptyMessage={
            data?.configured === false
              ? "Run history needs Postgres — nothing is recorded in this deployment."
              : "No runs recorded yet. Ingest a file in Studio and it will appear here with its receipt."
          }
        >
          <ul className="flex flex-col gap-1.5">
            {runs.map((r) => (
              <RunRow key={`${r.kind}-${r.id}`} run={r} />
            ))}
          </ul>
        </AsyncBoundary>
      </Section>
    </Page>
  );
}
