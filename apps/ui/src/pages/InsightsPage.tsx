import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate } from "react-router-dom";
import { InsightProofCard } from "@/components/InsightProofCard";
import { PageHeader } from "@/components/PageShell";
import { useApp } from "@/context/AppContext";
import {
  createApprovalFlow,
  confirmPin,
  fetchInsightProofStatus,
  listPins,
  previewPin,
  replayPin,
  runInsightProof,
  type ApprovalState,
  type InsightItem,
  type PinSummary,
} from "@/lib/insightProof";

/**
 * INS-PROOF-03 - /insights, reachable by URL only (no nav link).
 *
 * It asks GET /v1/insight-proof/status first. With the flag off that is a 404
 * and this page renders the same redirect to "/" as the catch-all route, so the
 * URL behaves as it does on main.
 */

export type InsightsView =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "error"; message: string }
  | {
      kind: "ready";
      items: InsightItem[];
      pins: PinSummary[];
      replays: Record<string, InsightItem>;
      approvals: Record<string, ApprovalState>;
    };

/** A status 404 means the routes are not mounted: the page does not exist. */
export function statusToView(status: number): "missing" | "ready" | "error" {
  if (status === 404) return "missing";
  return status >= 200 && status < 300 ? "ready" : "error";
}

export function InsightsPageView({
  view,
  onApprove,
  onConfirm,
  onCancel,
  onReplay,
}: {
  view: InsightsView;
  onApprove?: (insightId: string) => void;
  onConfirm?: (insightId: string) => void;
  onCancel?: (insightId: string) => void;
  onReplay?: (assetId: string) => void;
}) {
  if (view.kind === "missing") return <Navigate to="/" replace />;
  return (
    <div className="mx-auto max-w-4xl p-6">
      <PageHeader
        phase="Insights"
        title="Proven insights"
        blurb="Every figure is a cell of a query that ran twice through Cortex and added back to its total. A steward approves a pin; nothing is pinned or replayed on its own."
      />
      {view.kind === "loading" ? <p className="mt-6 text-sm">Loading...</p> : null}
      {view.kind === "error" ? (
        <p className="mt-6 text-sm" data-testid="insights-error">
          {view.message}
        </p>
      ) : null}
      {view.kind === "ready" ? (
        <>
          <section className="mt-6 space-y-4" data-testid="insights-proven">
            {view.items.map((item) => {
              const id = item.proof.insight_id ?? item.envelope.text;
              return (
                <InsightProofCard
                  key={id}
                  item={item}
                  approval={view.approvals[id]}
                  onApprove={onApprove ? () => onApprove(id) : undefined}
                  onConfirm={onConfirm ? () => onConfirm(id) : undefined}
                  onCancel={onCancel ? () => onCancel(id) : undefined}
                />
              );
            })}
          </section>
          <section className="mt-8" data-testid="insights-pins">
            <h2 className="text-sm font-semibold">Approved pins</h2>
            {view.pins.length === 0 ? <p className="mt-2 text-xs">No pins yet.</p> : null}
            <ul className="mt-2 space-y-3">
              {view.pins.map((pin) => {
                const replay = view.replays[pin.asset_id];
                return (
                  <li key={pin.asset_id} className="text-xs">
                    <p>
                      {pin.headline} (approved {pin.approved_at} by {pin.approved_by})
                    </p>
                    <button
                      type="button"
                      data-testid="pin-replay"
                      className="mt-1 border border-[var(--color-line)] px-2 py-1"
                      onClick={onReplay ? () => onReplay(pin.asset_id) : undefined}
                    >
                      Replay now
                    </button>
                    {replay ? (
                      <div className="mt-2">
                        <InsightProofCard item={replay} />
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </section>
        </>
      ) : null}
    </div>
  );
}

export function InsightsPage() {
  const { activeSpaceId } = useApp();
  const [view, setView] = useState<InsightsView>({ kind: "loading" });
  const flows = useRef<Record<string, ReturnType<typeof createApprovalFlow>>>({});

  const load = useCallback(async () => {
    const status = await fetchInsightProofStatus();
    const next = statusToView(status.status);
    if (next === "missing") return setView({ kind: "missing" });
    if (next === "error") return setView({ kind: "error", message: `status ${status.status}` });
    const [run, pins] = await Promise.all([runInsightProof(activeSpaceId), listPins(activeSpaceId)]);
    if (!run.ok) return setView({ kind: "error", message: run.refusal.code });
    setView({
      kind: "ready",
      items: run.body.insights,
      pins: pins.ok ? pins.body.pins : [],
      replays: {},
      approvals: {},
    });
  }, [activeSpaceId]);

  useEffect(() => {
    flows.current = {};
    void load();
  }, [load]);

  const setApproval = useCallback((id: string, s: ApprovalState) => {
    setView((v) => (v.kind === "ready" ? { ...v, approvals: { ...v.approvals, [id]: s } } : v));
    if (s.phase === "pinned") void load();
  }, [load]);

  const flowFor = useCallback(
    (id: string) => {
      flows.current[id] ??= createApprovalFlow(
        { preview: previewPin, confirm: confirmPin },
        activeSpaceId,
        id,
        (s) => setApproval(id, s),
      );
      return flows.current[id];
    },
    [activeSpaceId, setApproval],
  );

  const handlers = useMemo(
    () => ({
      onApprove: (id: string) => void flowFor(id).approve(),
      onConfirm: (id: string) => void flowFor(id).confirm(),
      onCancel: (id: string) => flowFor(id).cancel(),
      onReplay: async (assetId: string) => {
        const got = await replayPin(assetId);
        if (!got.ok) return;
        setView((v) =>
          v.kind === "ready" ? { ...v, replays: { ...v.replays, [assetId]: got.body } } : v,
        );
      },
    }),
    [flowFor],
  );

  return <InsightsPageView view={view} {...handlers} />;
}
