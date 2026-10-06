import { describe, expect, it } from "vitest";
import { askErrorBody, toSelectionPayload } from "./studioSelection";

describe("toSelectionPayload", () => {
  it("keeps tick order and only the ticked columns", () => {
    const picks = new Map([
      ["transactions", ["sku", "quantity_kg"]],
      ["locations", ["name"]],
    ]);
    expect(toSelectionPayload(picks)).toEqual([
      { table: "transactions", columns: ["sku", "quantity_kg"] },
      { table: "locations", columns: ["name"] },
    ]);
  });

  it("sends a table with no columns so the API names it, and [] when nothing is ticked", () => {
    expect(toSelectionPayload(new Map([["transactions", []]]))).toEqual([
      { table: "transactions", columns: [] },
    ]);
    expect(toSelectionPayload(new Map())).toEqual([]);
  });
});

describe("askErrorBody", () => {
  it("strips the postAsk prefix so the API detail can be read", () => {
    expect(askErrorBody('ask 400: {"detail":{"code":"selection_empty"}}')).toBe(
      '{"detail":{"code":"selection_empty"}}',
    );
  });
});
