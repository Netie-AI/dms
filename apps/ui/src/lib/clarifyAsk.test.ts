import { afterEach, describe, expect, it, vi } from "vitest";
import { clarifyPickBody } from "@/components/studio/ClarifyCard";
import { accessRequestVisible } from "@/components/studio/abstainReason";
import {
  askRequestBody,
  nextAccessRequestState,
  postAccessRequest,
  postClarifyPick,
} from "@/lib/api";
import type { ClarifyBlock } from "@/lib/types";

const ANSWER = {
  answer_id: "ans_pick",
  text: "Reading served",
  values: [],
  badge: "ABSTAIN",
  abstained: true,
  assumptions: [],
  as_of: "2026-11-27T00:00:00Z",
  contributing_sources: [],
};

const BLOCK: ClarifyBlock = {
  clarify_id: "c1",
  question: "Which reading?",
  readings: [
    { reading_id: "r_month", label: "Revenue by month" },
    { reading_id: "r_country", label: "Revenue by country" },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ask body flag off", () => {
  it("omits clarify ids so the ask bytes match a normal ask", () => {
    const off = JSON.stringify(
      askRequestBody({
        question: "q",
        space_id: "sp",
        session_id: null,
        grounded_tables: [],
        clarify_id: "",
        reading_id: "",
      }),
    );
    expect(off).toBe('{"question":"q","space_id":"sp"}');
    expect(JSON.stringify(askRequestBody({ question: "q", clarify_id: "c1" }))).toBe(
      '{"question":"q"}',
    );
  });
});

describe("clarify pick", () => {
  it("calls the ask route with clarify_id and reading_id", async () => {
    const calls: { url: string; body: string }[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: String(init?.body ?? "") });
      return new Response(JSON.stringify(ANSWER), { status: 200 });
    });
    const reading = BLOCK.readings[0];
    const env = await postClarifyPick(
      clarifyPickBody(BLOCK, reading, {
        question: "How are sales",
        space_id: "sp",
        session_id: "ses_1",
      }),
    );
    expect(env.text).toBe("Reading served");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain("/api/v1/chat/ask");
    expect(JSON.parse(calls[0].body)).toEqual({
      question: "How are sales",
      space_id: "sp",
      session_id: "ses_1",
      clarify_id: "c1",
      reading_id: "r_month",
    });
  });
});

describe("access request", () => {
  it("hides the button when the route is missing and does not keep the body", async () => {
    vi.stubGlobal(
      "fetch",
      async () => new Response("secret-row", { status: 404 }),
    );
    const result = await postAccessRequest("space-1", { table: "orders" });
    expect(result).toEqual({ ok: false, missing: true });
    expect(JSON.stringify(result)).not.toContain("secret-row");
    expect(nextAccessRequestState(result)).toEqual({ routeMissing: true, note: null });
    expect(accessRequestVisible(true, true)).toBe(false);
    expect(accessRequestVisible(false, false)).toBe(false);
    expect(accessRequestVisible(true, false)).toBe(true);
  });

  it("posts the table to the space that has it", async () => {
    let url = "";
    let body = "";
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      url = String(input);
      body = String(init?.body ?? "");
      return new Response("{}", { status: 201 });
    });
    const result = await postAccessRequest("dddddddd-dddd-dddd-dddd-dddddddddddd", {
      table: "orders",
    });
    expect(result).toEqual({ ok: true });
    expect(url).toBe(
      "/api/v1/spaces/dddddddd-dddd-dddd-dddd-dddddddddddd/access-requests",
    );
    expect(JSON.parse(body)).toEqual({ table: "orders" });
    expect(nextAccessRequestState(result).note).toBe("Request sent.");
  });
});
