import { useApp } from "@/context/AppContext";

/** Bottom-right activity chip — ask / ingest / drill progress without blocking the page. */
export function ActivityToast() {
  const { asking, activity } = useApp();
  const label = activity?.label ?? (asking ? "Thinking…" : null);
  const progress = activity?.progress;
  if (!label) return null;

  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-50 min-w-[12rem] max-w-xs border border-[var(--color-line)] bg-[var(--color-panel)]/95 px-3 py-2 shadow-sm backdrop-blur-sm"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center gap-2">
        <span
          className="inline-block h-2 w-2 shrink-0 animate-pulse rounded-full bg-[var(--color-accent)]"
          aria-hidden
        />
        <p className="text-xs font-medium text-[var(--color-ink)]">{label}</p>
      </div>
      <div className="mt-2 h-1 overflow-hidden bg-[var(--color-paper-2)]">
        <div
          className={`h-full bg-[var(--color-accent)] transition-[width] duration-300 ${
            progress == null ? "w-1/3 animate-pulse" : ""
          }`}
          style={progress != null ? { width: `${Math.max(4, Math.min(100, progress))}%` } : undefined}
        />
      </div>
    </div>
  );
}
