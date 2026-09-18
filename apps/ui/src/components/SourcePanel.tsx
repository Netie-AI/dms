import { useState } from "react";
import { PreviewGrid } from "@/components/PreviewGrid";
import { useApp } from "@/context/AppContext";
import { postReveal } from "@/lib/api";
import { isFilesystemOriginUri } from "@/lib/filesystemOrigin";
import { sheetForSource } from "@/lib/previewFixtures";
import { sourcesHeadline } from "@/lib/sourcePanel";

function formatMoney(n: number) {
  return new Intl.NumberFormat("en-MY", {
    style: "currency",
    currency: "MYR",
    maximumFractionDigits: 2,
  }).format(n);
}

export function SourcePanel() {
  const {
    sourcePanelOpen,
    setSourcePanelOpen,
    contributingSources,
    focusedSourceId,
    setFocusedSourceId,
    selectedValueId,
    latestAnswer: answer,
  } = useApp();
  const [revealNote, setRevealNote] = useState<string | null>(null);

  if (!sourcePanelOpen) {
    return (
      <button
        type="button"
        data-testid="source-panel-open"
        aria-label="Open sources"
        onClick={() => setSourcePanelOpen(true)}
        className="flex items-center justify-center bg-[var(--color-panel)] text-[10px] font-semibold uppercase tracking-wider text-[var(--color-ink-muted)] max-lg:fixed max-lg:right-3 max-lg:top-[3.25rem] max-lg:z-20 max-lg:min-h-11 max-lg:min-w-[2.75rem] max-lg:border max-lg:border-[var(--color-line)] max-lg:px-3 lg:relative lg:w-9 lg:shrink-0 lg:items-start lg:border-l lg:border-[var(--color-line)] lg:pt-4 lg:[writing-mode:vertical-rl]"
      >
        Sources
      </button>
    );
  }

  const totalRows = contributingSources.reduce((s, c) => s + c.row_count, 0);
  const totalContribution = contributingSources.reduce(
    (s, c) => s + c.contribution,
    0,
  );
  const sorted = [...contributingSources].sort(
    (a, b) => b.contribution - a.contribution,
  );

  async function onReveal(uri: string) {
    setRevealNote(null);
    try {
      const result = await postReveal(uri);
      if (!result.ok) {
        setRevealNote(result.error || "Could not open path");
      }
    } catch (err) {
      setRevealNote(err instanceof Error ? err.message : "Reveal failed");
    }
  }

  return (
    <>
      <button
        type="button"
        data-testid="source-panel-backdrop"
        aria-label="Dismiss sources"
        className="fixed inset-0 top-12 z-30 bg-black/40 lg:hidden"
        onClick={() => setSourcePanelOpen(false)}
      />
      <aside
        data-testid="source-panel"
        aria-label="Sources"
        className="flex w-[22rem] shrink-0 flex-col border-l border-[var(--color-line)] bg-[var(--color-panel)] max-lg:fixed max-lg:top-12 max-lg:right-0 max-lg:bottom-0 max-lg:z-40 max-lg:w-[min(22rem,calc(100%-2.75rem))]"
      >
      <div className="flex items-center justify-between border-b border-[var(--color-line)] px-3 py-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            Sources
          </p>
          <p className="mt-0.5 text-sm text-[var(--color-ink)]">
            {sourcesHeadline(answer, sorted, totalRows)}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setSourcePanelOpen(false)}
          className="flex min-h-11 min-w-11 items-center justify-center text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
          aria-label="Collapse sources"
        >
          ⟩
        </button>
      </div>

      {selectedValueId && answer && (
        <p className="border-b border-[var(--color-line)] bg-[var(--color-accent-soft)] px-3 py-2 text-xs text-[var(--color-accent)]">
          Tracing{" "}
          <strong>
            {answer.values.find((v) => v.id === selectedValueId)?.label}
          </strong>
        </p>
      )}

      {revealNote && (
        <p className="border-b border-[var(--color-line)] px-3 py-2 text-xs text-[var(--color-ink-muted)]">
          {revealNote}
        </p>
      )}

      <div className="flex-1 overflow-y-auto p-2">
        {sorted.length === 0 ? (
          <p className="px-2 py-6 text-sm text-[var(--color-ink-muted)]">
            {answer
              ? "Cortex did not attach a file card. Use SQL on the answer, or Library, to see the table."
              : "When an answer arrives, contributing sources appear here."}
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {sorted.map((src) => {
              const pct =
                totalContribution > 0
                  ? ((src.contribution / totalContribution) * 100).toFixed(1)
                  : "0";
              const open = focusedSourceId === src.ref_id;
              const isDoc = src.kind !== "sql";
              const preview =
                answer?.ask_mode === "demo" && !isDoc ? sheetForSource(src) : null;
              const canReveal = isFilesystemOriginUri(src.origin_uri);
              return (
                <li key={src.ref_id}>
                  <button
                    type="button"
                    onClick={() =>
                      setFocusedSourceId(open ? null : src.ref_id)
                    }
                    className={`w-full border px-3 py-2.5 text-left transition ${
                      open
                        ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)]/40"
                        : "border-[var(--color-line)] bg-[var(--color-surface)]/50 hover:border-[var(--color-ink-muted)]"
                    }`}
                  >
                    <p className="text-sm font-medium text-[var(--color-ink)]">
                      ▸ {src.container}
                      {src.member ? ` › ${src.member}` : ""}
                    </p>
                    <p className="mt-1 text-xs text-[var(--color-ink-muted)]">
                      {isDoc
                        ? `chunk${src.chunk_index != null ? ` #${src.chunk_index + 1}` : ""}`
                        : `${src.row_count.toLocaleString()} rows`}
                      {" · "}
                      {formatMoney(src.contribution)} · {pct}%
                    </p>
                    {src.source_kind === "sql" && src.extracted_at ? (
                      <p
                        data-testid="source-extracted-at"
                        className="mt-1 text-[10px] text-[var(--color-ink-muted)]"
                      >
                        extracted {src.extracted_at}
                      </p>
                    ) : null}
                  </button>
                  {open && (
                    <div className="mt-1 space-y-2 px-1 pb-2">
                      {src.origin_uri && (
                        <p className="break-all font-mono text-[10px] text-[var(--color-ink-muted)]">
                          {src.origin_uri}
                        </p>
                      )}
                      {preview ? (
                        <PreviewGrid sheet={preview} />
                      ) : src.snippet ? (
                        <p className="border border-[var(--color-line)] bg-[var(--color-paper)] px-3 py-3 text-xs leading-relaxed text-[var(--color-ink)]">
                          {src.snippet}
                        </p>
                      ) : (
                        <p className="border border-[var(--color-line)] bg-[var(--color-paper)] px-3 py-4 text-xs text-[var(--color-ink-muted)]">
                          {isDoc
                            ? "Document excerpt unavailable for this source."
                            : "Cell preview unavailable for this source."}
                        </p>
                      )}
                      <div className="flex flex-wrap gap-2 text-xs">
                        {canReveal ? (
                          <button
                            type="button"
                            className="border border-[var(--color-line)] px-2 py-1 hover:border-[var(--color-ink-muted)]"
                            onClick={() => void onReveal(src.origin_uri!)}
                          >
                            Open original
                          </button>
                        ) : (
                          <span className="border border-[var(--color-line)] px-2 py-1 text-[var(--color-ink-muted)]">
                            Open original
                          </span>
                        )}
                        {src.origin_uri ? (
                          <button
                            type="button"
                            className="border border-[var(--color-line)] px-2 py-1 hover:border-[var(--color-ink-muted)]"
                            onClick={() => {
                              void navigator.clipboard?.writeText(src.origin_uri!);
                            }}
                          >
                            Copy path
                          </button>
                        ) : (
                          <span className="border border-[var(--color-line)] px-2 py-1 text-[var(--color-ink-muted)]">
                            Copy path
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </aside>
    </>
  );
}
