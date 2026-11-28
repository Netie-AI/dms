/**
 * Friendly 401 and 403. Replaces the bare nginx page a buyer sees if they cancel
 * the browser password popup.
 */
export function StatusPage({
  status,
  onBack,
}: {
  status: 401 | 403;
  onBack?: () => void;
}) {
  const denied = status === 403;
  return (
    <main
      data-testid={denied ? "page-403" : "page-401"}
      className="grid min-h-full place-items-center bg-[var(--color-paper)] px-6 py-16 text-[var(--color-ink)]"
    >
      <div className="w-full max-w-md border border-[var(--color-line)] bg-[var(--color-panel)] px-8 py-10">
        <p className="font-[family-name:var(--font-display)] text-2xl font-semibold tracking-tight">
          netie
        </p>
        <h1 className="mt-6 text-xl font-semibold">
          {denied ? "You do not have access" : "Sign in to continue"}
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-[var(--color-ink-muted)]">
          {denied
            ? "A steward can grant access. Nothing is emailed from this form."
            : "This install uses a Netie sign-in page. Closing it does not show a browser password error."}
        </p>
        {onBack ? (
          <button
            type="button"
            onClick={onBack}
            className="mt-6 h-10 border border-[var(--color-accent)] px-4 text-sm font-medium text-[var(--color-accent)]"
          >
            Back to sign in
          </button>
        ) : null}
      </div>
    </main>
  );
}
