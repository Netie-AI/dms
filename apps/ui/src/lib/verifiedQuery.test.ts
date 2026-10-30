import { afterEach, describe, expect, it, vi } from "vitest";
import { registerVerifiedQuery } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("registerVerifiedQuery", () => {
  it("POSTs synonyms on the Studio register body", async () => {
    const fn = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        asset_id: "vq_x",
        space_id: "sp_finance",
        question: "q",
        sql: "SELECT 1",
        synonyms: ["alt"],
        created_at: "t",
      }),
      text: async () => "",
    });
    vi.stubGlobal("fetch", fn);
    await registerVerifiedQuery({
      spaceId: "sp_finance",
      question: "q",
      sql: "SELECT 1",
      synonyms: ["alt"],
    });
    const init = fn.mock.calls[0][1] as { body: string };
    expect(JSON.parse(init.body).synonyms).toEqual(["alt"]);
  });
});
