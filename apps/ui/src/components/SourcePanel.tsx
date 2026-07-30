import { PreviewGrid } from "@/components/PreviewGrid";
import { useApp } from "@/context/AppContext";
import { sheetForSource } from "@/lib/previewFixtures";

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
    fixtureAnswer,
  } = useApp();

  if (!sourcePanelOpen) {
    return (
      <button
        type="button"
        onClick={() => setSourcePanelOpen(true)}
        className="flex w-9 shrink-0 items-start justify-center border-l border-[var(--color-line)] bg-[var(--color-panel)] pt-4 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-ink-muted)] [writing-mode:vertical-rl]"
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

  return (
    <aside className="flex w-[22rem] shrink-0 flex-col border-l border-[var(--color-line)] bg-[var(--color-panel)]">
      <div className="flex items-center justify-between border-b border-[var(--color-line)] px-3 py-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            Sources
          </p>
          <p className="mt-0.5 text-sm text-[var(--color-ink)]">
            {sorted.length
              ? `${sorted.length} files · ${totalRows.toLocaleString()} rows`
              : "No answer yet — ask from Chat"}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setSourcePanelOpen(false)}
          className="text-sm text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
          aria-label="Collapse sources"
        >
          ⟩
        </button>
      </div>

      {selectedValueId && fixtureAnswer && (
        <p className="border-b border-[var(--color-line)] bg-[var(--color-accent-soft)] px-3 py-2 text-xs text-[var(--color-accent)]">
          Tracing{" "}
          <strong>
            {fixtureAnswer.values.find((v) => v.id === selectedValueId)?.label}
          </strong>
        </p>
      )}

      <div className="flex-1 overflow-y-auto p-2">
        {sorted.length === 0 ? (
          <p className="px-2 py-6 text-sm text-[var(--color-ink-muted)]">
            When an answer arrives, contributing sources appear here — docked,
            never a modal.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {sorted.map((src) => {
              const pct =
                totalContribution > 0
                  ? ((src.contribution / totalContribution) * 100).toFixed(1)
                  : "0";
              const open = focusedSourceId === src.ref_id;
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
                        : "border-[var(--color-line)] bg-white/50 hover:border-[var(--color-ink-muted)]"
                    }`}
                  >
                    <p className="text-sm font-medium text-[var(--color-ink)]">
                      ▸ {src.container}
                      {src.member ? ` › ${src.member}` : ""}
                    </p>
                    <p className="mt-1 text-xs text-[var(--color-ink-muted)]">
                      {src.row_count.toLocaleString()} rows ·{" "}
                      {formatMoney(src.contribution)} · {pct}%
                    </p>
                  </button>
                  {open && (
                    <div className="mt-1 space-y-2 px-1 pb-2">
                      {src.origin_uri && (
                        <p className="break-all font-mono text-[10px] text-[var(--color-ink-muted)]">
                          {src.origin_uri}
                        </p>
                      )}
                      <PreviewGrid sheet={sheetForSource(src)} />
                      <div className="flex flex-wrap gap-2 text-xs">
                        <span className="border border-[var(--color-line)] px-2 py-1">
                          Open original
                        </span>
                        <span className="border border-[var(--color-line)] px-2 py-1">
                          Copy path
                        </span>
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
  );
}
