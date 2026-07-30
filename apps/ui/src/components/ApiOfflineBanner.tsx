import { useApp } from "@/context/AppContext";

export function ApiOfflineBanner() {
  const { apiOnline } = useApp();
  if (apiOnline !== false) return null;
  return (
    <div
      role="status"
      className="border-b border-[var(--color-line)] bg-[var(--color-warn-soft)] px-4 py-2 text-sm text-[var(--color-warn)]"
    >
      DMS API unreachable at{" "}
      <code className="font-mono text-xs">/api/health</code> — chrome stays
      interactive on fixtures until the API is up.
    </div>
  );
}
