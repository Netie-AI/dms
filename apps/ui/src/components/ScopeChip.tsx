import { useApp } from "@/context/AppContext";
import { scopeChipLabel } from "@/lib/scopeChipLabel";

export function ScopeChip() {
  const { activeSpace, scopedSourceCount, setSourcePanelOpen } = useApp();
  const label = scopeChipLabel(activeSpace?.name, scopedSourceCount);

  return (
    <button
      type="button"
      onClick={() => setSourcePanelOpen(true)}
      className="inline-flex max-w-full items-center border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-1.5 text-left text-sm text-[var(--color-ink-muted)] transition hover:border-[var(--color-accent)] hover:text-[var(--color-ink)]"
      title="Open sources in this scope"
      aria-label="Current scope"
      data-testid="scope-chip"
    >
      <span className="truncate">{label}</span>
    </button>
  );
}
