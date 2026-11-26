import { describe, expect, it } from "vitest";
import {
  ABSTAIN_CARD_TEXT,
  assertNoNewFigures,
  createApprovalFlow,
  confirmPin,
  listPins,
  previewPin,
  proofLines,
  replayPin,
  revokePin,
  toProofCard,
  type InsightItem,
  type InsightProof,
} from "./insightProof";
import { fixtureItem } from "./insightProofFixtures";

function withProof(patch: Partial<InsightProof>): InsightItem {
  return { envelope: fixtureItem.envelope, proof: { ...fixtureItem.proof, ...patch } };
}

describe("proofLines", () => {
  it("renders the conservation, the two runs, the ledger entry and the definition", () => {
    const lines = proofLines(fixtureItem.proof);
    expect(lines).toContain(
      "Grouped total 1,100.00 = raw total with no join 1,100.00 (tolerance 0.03)",
    );
    expect(lines.some((l) => l.startsWith("Ran twice through Cortex: same rows (result "))).toBe(true);
    expect(lines).toContain("Ran twice through Cortex: same rows (result f031665dc7fb)");
    expect(lines).toContain("Ledger entry led_000123");
    expect(lines).toContain(
      "Definition: order_amount = ROUND(SUM(f.amount), 2) (item item_measure_order_amount vver_sales_b1)",
    );
    expect(lines).toContain("Source freshness: 2026-10-01T00:00:00Z");
    expect(lines.some((l) => l.startsWith("Changed since approval"))).toBe(false);
  });

  it("says unknown when a watermark is missing", () => {
    const lines = proofLines({ ...fixtureItem.proof, watermarks: { orders: null } });
    expect(lines).toContain("Source freshness: unknown");
  });

  it("names the changed labels of a drift", () => {
    const lines = proofLines({
      ...fixtureItem.proof,
      drift: {
        approved_result_hash: "a".repeat(64),
        current_result_hash: "b".repeat(64),
        labels_added: [],
        labels_removed: [],
        labels_changed: ["North"],
        reason: "drift_without_source_change",
      },
    });
    expect(lines).toContain("Changed since approval: North");
  });
});

describe("toProofCard", () => {
  it("shows a proven insight with its rows", () => {
    const card = toProofCard(fixtureItem);
    expect(card.kind).toBe("proven");
    if (card.kind !== "proven") return;
    expect(card.headline).toBe(fixtureItem.envelope.text);
    expect(card.rows).toHaveLength(5);
  });

  it("refuses when the proof does not conserve", () => {
    const card = toProofCard(withProof({ conserves: false }));
    expect(card).toEqual({ kind: "abstain", text: ABSTAIN_CARD_TEXT, reason: "not_conserved" });
  });

  it("refuses when the two execution hashes differ", () => {
    const card = toProofCard(
      withProof({
        executions: [
          { engine: "submit", result_hash: "a".repeat(64), row_count: 5 },
          { engine: "submit", result_hash: "b".repeat(64), row_count: 5 },
        ],
      }),
    );
    expect(card.kind).toBe("abstain");
  });

  it("refuses a headline with a figure that is not a cell", () => {
    const item: InsightItem = {
      envelope: { ...fixtureItem.envelope, text: fixtureItem.envelope.text.replace("86.4", "99.9") },
      proof: fixtureItem.proof,
    };
    expect(toProofCard(item).kind).toBe("abstain");
  });
});

describe("assertNoNewFigures", () => {
  it("throws on a headline figure no row carries", () => {
    expect(() =>
      assertNoNewFigures(
        "2 of 4 Territory.name values (North, South) carry 99.9 pct of order_amount",
        fixtureItem.envelope.rows,
      ),
    ).toThrow(/99\.9/);
  });

  it("accepts the cells, the k-of-n counts and the replay prefix date", () => {
    expect(() =>
      assertNoNewFigures(fixtureItem.envelope.text, fixtureItem.envelope.rows),
    ).not.toThrow();
    expect(() =>
      assertNoNewFigures(
        `Unchanged since steward approval on 2026-10-30: ${fixtureItem.envelope.text}`,
        fixtureItem.envelope.rows,
      ),
    ).not.toThrow();
  });

  it("counts a bare integer above the row count as a figure", () => {
    expect(() => assertNoNewFigures("9 of 4 values", fixtureItem.envelope.rows)).toThrow();
  });
});

type Call = { url: string; method: string; body: unknown };

function fakeFetch(responses: Record<string, { status: number; body: unknown }>) {
  const calls: Call[] = [];
  const impl = async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const key = Object.keys(responses).find((k) => url.endsWith(k)) ?? "";
    const r = responses[key] ?? { status: 404, body: { detail: "Not Found" } };
    return new Response(JSON.stringify(r.body), { status: r.status });
  };
  return { calls, impl };
}

describe("fetchers", () => {
  it("hit only the DMS insight-proof paths and never send SQL or rows", async () => {
    const f = fakeFetch({
      "/pins/preview": { status: 200, body: { ...fixtureItem, token: "ipcabc", expires_at: "x" } },
      "/pins/confirm": { status: 409, body: { ok: false, code: "confirm_token_used" } },
      "/replay": { status: 200, body: { ...fixtureItem, verdict: "unchanged", asset_id: "pin_1" } },
      "/revoke": { status: 200, body: { asset_id: "pin_1" } },
      "/pins?space_id=sp1": { status: 200, body: { pins: [] } },
    });
    const pre = await previewPin(null, "ins_156d4c0dd99d3b10", f.impl);
    expect(pre.ok).toBe(true);
    const con = await confirmPin("ipcabc", f.impl);
    expect(con).toEqual({
      ok: false,
      refusal: { status: 409, code: "confirm_token_used", body: { ok: false, code: "confirm_token_used" } },
    });
    expect((await replayPin("pin_1", f.impl)).ok).toBe(true);
    expect((await revokePin("pin_1", f.impl)).ok).toBe(true);
    expect((await listPins("sp1", f.impl)).ok).toBe(true);
    expect(f.calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      "POST /api/v1/insight-proof/pins/preview",
      "POST /api/v1/insight-proof/pins/confirm",
      "POST /api/v1/insight-proof/pins/pin_1/replay",
      "POST /api/v1/insight-proof/pins/pin_1/revoke",
      "GET /api/v1/insight-proof/pins?space_id=sp1",
    ]);
    expect(f.calls[0].body).toEqual({ space_id: null, insight_id: "ins_156d4c0dd99d3b10" });
    expect(f.calls[1].body).toEqual({ token: "ipcabc" });
  });
});

describe("createApprovalFlow", () => {
  it("previews on approve and confirms only on confirm", async () => {
    const f = fakeFetch({
      "/pins/preview": { status: 200, body: { ...fixtureItem, token: "ipctok", expires_at: "x" } },
      "/pins/confirm": { status: 200, body: { asset_id: "pin_0123456789abcdef" } },
    });
    const states: string[] = [];
    const flow = createApprovalFlow(
      {
        preview: (s, i) => previewPin(s, i, f.impl),
        confirm: (t) => confirmPin(t, f.impl),
      },
      null,
      "ins_156d4c0dd99d3b10",
      (s) => states.push(s.phase),
    );
    await flow.confirm(); // nothing to confirm yet
    expect(f.calls).toEqual([]);
    await flow.approve();
    expect(flow.state.phase).toBe("confirming");
    expect(f.calls.map((c) => c.url)).toEqual(["/api/v1/insight-proof/pins/preview"]);
    await flow.confirm();
    expect(f.calls.map((c) => c.url)).toEqual([
      "/api/v1/insight-proof/pins/preview",
      "/api/v1/insight-proof/pins/confirm",
    ]);
    expect(f.calls[1].body).toEqual({ token: "ipctok" });
    expect(flow.state).toEqual({ phase: "pinned", assetId: "pin_0123456789abcdef" });
    expect(states).toEqual(["previewing", "confirming", "pinning", "pinned"]);
  });
});
