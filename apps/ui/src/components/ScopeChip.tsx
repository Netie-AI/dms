import { useApp } from "@/context/AppContext";

export function ScopeChip() {
  const { activeSpace, setSourcePanelOpen } = useApp();
  const label = activeSpace
    ? `Asking: ${activeSpace.name} · ${activeSpace.source_count} sources`
    : "Asking: Company (default ACL)";

  return (
    <button
      type="button"
      onClick={() => setSourcePanelOpen(true)}
      className="inline-flex max-w-full items-center border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-1.5 text-left text-sm text-[var(--color-ink-muted)] transition hover:border-[var(--color-accent)] hover:text-[var(--color-ink)]"
      title="Open sources in this scope"
    >
      <span className="truncate">{label}</span>
    </button>
  );
}
