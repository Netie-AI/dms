import { describe, expect, it } from "vitest";
import {
  consumeObjectAskHandoff,
  objectAskNavigateState,
  takeAskTool,
} from "./objectAskHandoff";

describe("consumeObjectAskHandoff", () => {
  it("carries the authored tool so Chat does not free-text abstain", () => {
    expect(
      consumeObjectAskHandoff({
        groundedTables: ["objects.sales_sku"],
        groundedLabels: ["sales_sku"],
        askTool: "sales_sku_metric",
        askNonce: "n1",
      }),
    ).toEqual({
      groundedTables: ["objects.sales_sku"],
      groundedLabels: ["sales_sku"],
      askTool: "sales_sku_metric",
      askNonce: "n1",
    });
  });

  it("ignores Library grounding that has no tool", () => {
    const h = consumeObjectAskHandoff({
      groundedTables: ["bronze.sales_sku"],
      groundedLabels: ["sales_sku"],
    });
    expect(h?.askTool).toBeUndefined();
    expect(h?.groundedTables).toEqual(["bronze.sales_sku"]);
  });

  it("returns null when nothing was handed over", () => {
    expect(consumeObjectAskHandoff(null)).toBeNull();
    expect(consumeObjectAskHandoff({})).toBeNull();
  });

  it("builds Studio navigate state Chat can consume", () => {
    const state = objectAskNavigateState("sales_sku", "sales_sku_metric");
    expect(consumeObjectAskHandoff(state)).toMatchObject({
      groundedTables: ["objects.sales_sku"],
      groundedLabels: ["sales_sku"],
      askTool: "sales_sku_metric",
    });
    expect(state.askNonce).toMatch(/^\d+$/);
  });

  it("asks a tool once per Studio click nonce", () => {
    const first = consumeObjectAskHandoff({
      groundedTables: ["objects.sales_sku"],
      askTool: "sales_sku_metric",
      askNonce: "click-a",
    });
    expect(takeAskTool(first)).toBe("sales_sku_metric");
    expect(takeAskTool(first)).toBeUndefined();
    const second = consumeObjectAskHandoff({
      groundedTables: ["objects.sales_sku"],
      askTool: "sales_sku_metric",
      askNonce: "click-b",
    });
    expect(takeAskTool(second)).toBe("sales_sku_metric");
  });
});