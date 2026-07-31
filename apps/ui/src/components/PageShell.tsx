import type { ReactNode } from "react";

/**
 * The chrome every non-chat surface shares: a phase tag, a title, a one-line
 * statement of what the page is for, and a right-hand slot for the actions that
 * belong to it. Usability rule 8 — the label on the button here is the label in
 * the toast and in the ledger, so page actions live in one place per surface.
 */
export function PageHeader({
  phase,
  title,
  blurb,
  actions,
}: {
  phase: string;
  title: string;
  blurb: string;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--color-line)] pb-5">
      <div className="min-w-0">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
          {phase}
        </p>
        <h1 className="mt-1.5 font-[family-name:var(--font-display)] text-3xl font-semibold tracking-tight">
          {title}
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-ink-muted)]">
          {blurb}
        </p>
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap gap-2">{actions}</div> : null}
    </header>
  );
}

export function Page({ children }: { children: ReactNode }) {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-8 py-8">{children}</div>
    </div>
  );
}

export function StatTile({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "neutral" | "ok" | "warn" | "danger";
}) {
  const accent =
    tone === "ok"
      ? "text-[var(--color-badge-ok)]"
      : tone === "warn"
        ? "text-[var(--color-badge-warn)]"
        : tone === "danger"
          ? "text-[var(--color-danger)]"
          : "text-[var(--color-ink)]";
  return (
    <div className="border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-3.5 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
        {label}
      </p>
      <p className={`mt-1 font-[family-name:var(--font-display)] text-2xl leading-none ${accent}`}>
        {value}
      </p>
      {hint ? <p className="mt-1.5 text-[11px] text-[var(--color-ink-muted)]">{hint}</p> : null}
    </div>
  );
}

export function Section({
  title,
  count,
  description,
  actions,
  children,
}: {
  title: string;
  count?: number;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mt-8">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-tight">
          {title}
          {count != null && (
            <span className="ml-2 text-sm font-normal text-[var(--color-ink-muted)]">{count}</span>
          )}
        </h2>
        {actions}
      </div>
      {description ? (
        <p className="mt-1 max-w-2xl text-sm text-[var(--color-ink-muted)]">{description}</p>
      ) : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}

/** Loading / error / empty in one place so no page invents its own vocabulary. */
export function AsyncBoundary({
  loading,
  error,
  empty,
  emptyMessage,
  onRetry,
  children,
}: {
  loading: boolean;
  error: string | null;
  empty?: boolean;
  emptyMessage?: string;
  onRetry?: () => void;
  children: ReactNode;
}) {
  if (loading) {
    return (
      <p className="border border-[var(--color-line)] bg-[var(--color-surface)]/50 px-3 py-6 text-sm text-[var(--color-ink-muted)]">
        Loading…
      </p>
    );
  }
  if (error) {
    return (
      <div className="border border-[var(--color-danger)]/40 bg-[var(--color-surface)] px-3 py-4">
        <p className="text-sm text-[var(--color-danger)]">{error}</p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="mt-2 border border-[var(--color-line)] px-2.5 py-1 text-xs hover:border-[var(--color-accent)]"
          >
            Try again
          </button>
        )}
      </div>
    );
  }
  if (empty) {
    return (
      <p className="border border-[var(--color-line)] bg-[var(--color-surface)]/50 px-3 py-6 text-sm text-[var(--color-ink-muted)]">
        {emptyMessage ?? "Nothing here yet."}
      </p>
    );
  }
  return <>{children}</>;
}

/**
 * A degraded dependency, stated plainly with the fix. Never a toast — the user
 * needs to keep reading it while they act on it.
 */
export function DependencyNotice({
  title,
  hint,
  tone = "warn",
}: {
  title: string;
  hint?: string;
  tone?: "warn" | "danger";
}) {
  const cls =
    tone === "danger"
      ? "border-[var(--color-danger)]/40 bg-[var(--color-surface)] text-[var(--color-danger)]"
      : "border-[var(--color-warn)]/40 bg-[var(--color-warn-soft)] text-[var(--color-warn)]";
  return (
    <div className={`mt-4 border px-3 py-2.5 text-xs ${cls}`}>
      <p className="font-semibold">{title}</p>
      {hint ? <p className="mt-1 opacity-90">{hint}</p> : null}
    </div>
  );
}

export function Pill({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: "neutral" | "ok" | "warn" | "danger" | "accent";
  title?: string;
}) {
  const cls =
    tone === "ok"
      ? "border-[var(--color-badge-ok)]/40 text-[var(--color-badge-ok)]"
      : tone === "warn"
        ? "border-[var(--color-warn)]/50 bg-[var(--color-warn-soft)] text-[var(--color-warn)]"
        : tone === "danger"
          ? "border-[var(--color-danger)]/40 text-[var(--color-danger)]"
          : tone === "accent"
            ? "border-[var(--color-accent)]/50 bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
            : "border-[var(--color-line)] text-[var(--color-ink-muted)]";
  return (
    <span
      title={title}
      className={`inline-flex items-center border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${cls}`}
    >
      {children}
    </span>
  );
}
