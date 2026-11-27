import { describe, expect, it } from "vitest";
import { chatVisibilityLine, isRefused } from "./receiptLines";

describe("chatVisibilityLine", () => {
  it("says it plainly when nothing was ingested", () => {
    expect(chatVisibilityLine({ ingested: 0, serving_sync_detail: "ignored" })).toBe(
      "Nothing was ingested, so Chat cannot see this upload.",
    );
  });
  it("ends with exactly one full stop", () => {
    expect(chatVisibilityLine({ ingested: 1 })).toBe("Not confirmed that Chat can see this upload.");
    expect(chatVisibilityLine({ ingested: 1, serving_sync_detail: "sync did not run" })).toBe(
      "Not confirmed that Chat can see this upload: sync did not run.",
    );
    expect(chatVisibilityLine({ ingested: 1, serving_sync_detail: "sync did not run.  " })).toBe(
      "Not confirmed that Chat can see this upload: sync did not run.",
    );
    expect(chatVisibilityLine({ ingested: 1, serving_sync_detail: "..." })).toBe("Not confirmed that Chat can see this upload.");
  });
  it("keeps a closing mark the detail already has", () => {
    expect(chatVisibilityLine({ ingested: 1, serving_sync_detail: "sync failed!" })).toBe(
      "Not confirmed that Chat can see this upload: sync failed!",
    );
    expect(chatVisibilityLine({ ingested: 1, serving_sync_detail: "pending…" })).toBe(
      "Not confirmed that Chat can see this upload: pending…",
    );
  });
  it("drops characters that reorder the text", () => {
    expect(chatVisibilityLine({ ingested: 1, serving_sync_detail: "a‮b⁦c" })).toBe(
      "Not confirmed that Chat can see this upload: abc.",
    );
  });
});

describe("isRefused", () => {
  it("trusts the ingested flag", () => {
    expect(isRefused({ ingested: false, table: "t" })).toBe(true);
    expect(isRefused({ ingested: true, table: null, reason: "header_and_types_ok" })).toBe(false);
  });
  it("without a flag, no table means refused", () => {
    expect(isRefused({ table: null })).toBe(true);
    expect(isRefused({ table: "t" })).toBe(false);
  });
});
