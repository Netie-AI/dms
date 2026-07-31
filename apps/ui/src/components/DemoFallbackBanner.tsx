import { useApp } from "@/context/AppContext";

/** Permanent, unmissable — DMS_DEMO_FALLBACK must never look like live success. */
export function DemoFallbackBanner() {
  const { demoFallbackEnabled, askMode } = useApp();
  if (!demoFallbackEnabled && askMode !== "demo") return null;

  return (
    <div
      role="status"
      className="shrink-0 border-b-2 border-[var(--color-warn)] bg-[var(--color-warn-soft)] px-4 py-2 text-center text-xs font-semibold tracking-wide text-[var(--color-warn)]"
    >
      {askMode === "demo"
        ? "DEMO ASK MODE — numbers come from the local DuckDB warehouse, not a certified Cortex path."
        : "DEMO FALLBACK ENABLED — if live Cortex fails, answers silently switch to demo numbers. Set DMS_DEMO_FALLBACK=0 for demo-ready / customer runs."}
    </div>
  );
}
