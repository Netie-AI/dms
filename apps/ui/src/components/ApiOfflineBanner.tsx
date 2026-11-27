import { useApp } from "@/context/AppContext";

/**
 * Flag-off sentence. DMS_RUNTIME_SELFCHECK off must keep these bytes.
 * The plain sentence is the flag-on path only.
 */
export const OFFLINE_BANNER_FLAG_OFF =
  "DMS API unreachable at /api/health — chrome stays interactive on fixtures until the API is up.";

export function runtimeSelfCheckOn(
  env: { DMS_RUNTIME_SELFCHECK?: string; VITE_DMS_RUNTIME_SELFCHECK?: string } = import.meta
    .env as { DMS_RUNTIME_SELFCHECK?: string; VITE_DMS_RUNTIME_SELFCHECK?: string },
): boolean {
  const raw = String(env.DMS_RUNTIME_SELFCHECK ?? env.VITE_DMS_RUNTIME_SELFCHECK ?? "")
    .trim()
    .toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

export function runtimeProblem(input: {
  apiOnline: boolean | null;
  cortexContractOk: boolean | null;
  cortexTrustOk: boolean | null;
  databaseConfigured: boolean | null;
}): string | null {
  if (input.apiOnline === false) {
    return "The server is not responding. You can keep looking around. Questions and saves resume when the connection returns.";
  }
  if (input.apiOnline !== true) return null;
  const parts: string[] = [];
  if (input.cortexContractOk === false) {
    parts.push("The answer engine is not responding, so questions are paused.");
  }
  if (input.cortexTrustOk === false) {
    parts.push("Sign-in trust is not ready, so questions are paused.");
  }
  if (input.databaseConfigured === false) {
    parts.push("Saved work is not turned on, so new spaces disappear when the server restarts.");
  }
  return parts.length ? parts.join(" ") : null;
}

export function ApiOfflineBannerView({
  apiOnline,
  selfCheck,
  problem,
}: {
  apiOnline: boolean | null;
  selfCheck: boolean;
  problem: string | null;
}) {
  if (!selfCheck) {
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
  if (!problem) return null;
  return (
    <div
      role="status"
      data-testid="runtime-degraded"
      className="border-b border-[var(--color-line)] bg-[var(--color-warn-soft)] px-4 py-2 text-sm text-[var(--color-warn)]"
    >
      {problem}
    </div>
  );
}

export function ApiOfflineBanner() {
  const { apiOnline, cortexContractOk, cortexTrustOk, databaseConfigured } = useApp();
  const selfCheck = runtimeSelfCheckOn();
  const problem = selfCheck
    ? runtimeProblem({
        apiOnline,
        cortexContractOk,
        cortexTrustOk,
        databaseConfigured,
      })
    : null;
  return (
    <ApiOfflineBannerView apiOnline={apiOnline} selfCheck={selfCheck} problem={problem} />
  );
}
