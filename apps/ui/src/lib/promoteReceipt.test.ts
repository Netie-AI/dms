import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchPromoteReceipt } from "./api";
import type { PromoteReceipt, PromoteReceiptState } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function mockFetch(status: number, body: unknown): ReturnType<typeof vi.fn> {
  const ok = status >= 200 && status < 300;
  const fn = vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("fetchPromoteReceipt", () => {
  it("recorded: receipt is non-null and source_rows may be null", async () => {
    const receipt: PromoteReceipt = {
      run_id: "run_gold_1",
      target: "gold.sales_total",
      sources: ["silver.sales"],
      source_rows: null,
      passed: 5,
      quarantined: 0,
      unmatched: -2,
      reconciled: false,
      counts_by_reason: { join_cardinality_change: 2 },
      dedup_key: [],
      lineage: "aggregate",
      table: "gold.sales_total",
      quarantine_table: null,
    };
    const body: PromoteReceiptState = {
      target: "gold.sales_total",
      state: "recorded",
      recorded_at: "2026-09-03T06:00:00Z",
      runs: 1,
      receipt,
    };
    const fetchMock = mockFetch(200, body);

    const got = await fetchPromoteReceipt("gold.sales_total", "space_ops");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/pipelines/receipts?target=gold.sales_total&space_id=space_ops",
      expect.objectContaining({ signal: undefined }),
    );
    expect(got.state).toBe("recorded");
    if (got.state !== "recorded") throw new Error("discriminant");
    const rows: number | null = got.receipt.source_rows;
    expect(rows).toBeNull();
    expect(got.receipt).not.toBeNull();
    const unmatched: number = got.receipt.unmatched;
    expect(unmatched).toBe(-2);
  });

  it("no_receipt_yet: receipt is null", async () => {
    const body: PromoteReceiptState = {
      target: "silver.sales",
      state: "no_receipt_yet",
      runs: 0,
      receipt: null,
    };
    mockFetch(200, body);

    const got = await fetchPromoteReceipt("silver.sales");
    expect(got.state).toBe("no_receipt_yet");
    expect(got.receipt).toBeNull();
    expect(got.runs).toBe(0);
  });

  it("403 gate_unavailable rejects with the steward sentence, never null", async () => {
    mockFetch(403, { detail: "gate_unavailable" });

    await expect(fetchPromoteReceipt("silver.sales")).rejects.toThrow(
      /Cortex gate is unreachable/,
    );
  });
});
