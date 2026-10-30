import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ActionRefusal,
  applyAction,
  declineAction,
  describeRefusal,
  isOutcomeUnknown,
  listActionProposals,
  proposeAction,
} from "./actions";

// #489 verify round: a 5xx or a dropped connection on a decision rendered
// "Refused: http_504" although the write may have committed.

function raw(status: number, body: string, type = "text/plain"): Response {
  return new Response(body, { status, headers: { "Content-Type": type } });
}

async function refusalOf(p: Promise<unknown>): Promise<ActionRefusal> {
  const e = await p.catch((err: unknown) => err);
  expect(e).toBeInstanceOf(ActionRefusal);
  return e as ActionRefusal;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("decision outcome unknown", () => {
  for (const status of [500, 502, 503, 504]) {
    it(`apply answered ${status} with a non-JSON body is outcome_unknown, never 'Refused'`, async () => {
      vi.stubGlobal("fetch", vi.fn(async () => raw(status, "<html>Gateway Timeout</html>", "text/html")));
      const r = await refusalOf(applyAction("p1", "t1", "CHG-1"));
      expect(r.code).toBe("outcome_unknown");
      expect(isOutcomeUnknown(r.code)).toBe(true);
      expect(r.sentence).toBe(
        `The server answered HTTP ${status} without a result, so it is not known whether this decision was recorded. Check Recent decisions before doing anything else.`,
      );
      expect(r.sentence).not.toMatch(/Refused/);
      expect(r.sentence).not.toContain("nothing changed");
      expect(r.sentence).not.toContain("Nothing is known to have changed");
      expect(r.sentence).not.toContain("Applied");
    });
  }

  it("apply whose connection drops is outcome_unknown, not network_error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const r = await refusalOf(applyAction("p1", "t1", "CHG-1"));
    expect(r.code).toBe("outcome_unknown");
    expect(r.sentence).toBe(
      "The connection dropped before the server answered, so it is not known whether this decision was recorded. Check Recent decisions before doing anything else.",
    );
  });

  it("decline answered 504 is outcome_unknown too", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => raw(504, "upstream timed out")));
    const r = await refusalOf(declineAction("p1", "t1", "CHG-1"));
    expect(r.code).toBe("outcome_unknown");
    expect(r.sentence).toContain("HTTP 504");
    expect(r.sentence).toContain("Check Recent decisions");
  });

  it("FastAPI's default 500 (JSON string detail) on apply is outcome_unknown", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => raw(500, JSON.stringify({ detail: "Internal Server Error" }), "application/json")),
    );
    const r = await refusalOf(applyAction("p1", "t1", "CHG-1"));
    expect(r.code).toBe("outcome_unknown");
    expect(r.sentence).toContain("HTTP 500");
  });

  it("a 2xx apply whose body cannot be read is outcome_unknown", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => raw(200, "not json")));
    const r = await refusalOf(applyAction("p1", "t1", "CHG-1"));
    expect(r.code).toBe("outcome_unknown");
  });

  it("a coded 503 from the route stays a definite refusal (nothing written)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        raw(503, JSON.stringify({ detail: { code: "ledger_unavailable" } }), "application/json"),
      ),
    );
    const r = await refusalOf(applyAction("p1", "t1", "CHG-1"));
    expect(r.code).toBe("ledger_unavailable");
    expect(isOutcomeUnknown(r.code)).toBe(false);
    expect(r.sentence).toBe(
      "The Cortex ledger is unreachable. Nothing was written, because there would be no receipt.",
    );
  });

  it("commit_outcome_unknown from the route is also an unknown outcome", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        raw(
          503,
          JSON.stringify({ detail: { code: "commit_outcome_unknown", cortex_entry_id: "e9" } }),
          "application/json",
        ),
      ),
    );
    const r = await refusalOf(applyAction("p1", "t1", "CHG-1"));
    expect(isOutcomeUnknown(r.code)).toBe(true);
    expect(r.sentence).toContain("Ledger entry e9 was written");
  });
});

describe("non-decision 5xx and network", () => {
  it("propose answered 504 is server_error with the status, not 'Refused: http_504'", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => raw(504, "Gateway Timeout")));
    const r = await refusalOf(
      proposeAction({ space_id: "S", object_key: { sku: "A", location_id: "B" }, value: "1" }),
    );
    expect(r.code).toBe("server_error");
    expect(r.sentence).toBe(
      "The server answered HTTP 504 without a reason, so the result is not known. Reload before trying again.",
    );
  });

  it("a read whose connection drops stays network_error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const r = await refusalOf(listActionProposals("S"));
    expect(r.code).toBe("network_error");
  });

  it("a non-JSON 4xx names its status in a sentence", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => raw(418, "teapot")));
    const r = await refusalOf(applyAction("p1", "t1", "CHG-1"));
    expect(r.code).toBe("http_418");
    expect(r.sentence).toBe("The server answered HTTP 418 without a refusal code, so this was not accepted.");
  });
});

describe("F5 gate statuses outside the fixture", () => {
  for (const status of ["blocked", "needs_approval", "rejected", "escalate"]) {
    it(`a 403 '${status}' from enforce reads as a gate refusal, not 'Refused: ${status}'`, async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => raw(403, JSON.stringify({ detail: status }), "application/json")),
      );
      const r = await refusalOf(applyAction("p1", "t1", "CHG-1"));
      expect(r.code).toBe("gate_refused");
      expect(r.sentence).toBe(
        `The compliance gate refused this action (Cortex F5 status: ${status}), so nothing was written.`,
      );
    });
  }

  it("a 403 reason that is not a plain identifier is not echoed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => raw(403, JSON.stringify({ detail: "<b>no</b> way" }), "application/json")),
    );
    const r = await refusalOf(applyAction("p1", "t1", "CHG-1"));
    expect(r.sentence).toBe("The compliance gate refused this action, so nothing was written.");
  });

  it("a known 403 code keeps its own sentence", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => raw(403, JSON.stringify({ detail: "steward_identity_unset" }), "application/json")),
    );
    const r = await refusalOf(applyAction("p1", "t1", "CHG-1"));
    expect(r.code).toBe("steward_identity_unset");
  });

  it("an Object.prototype name is not a refusal code", () => {
    expect(describeRefusal("constructor")).toBe("Refused: constructor");
    expect(describeRefusal("toString")).toBe("Refused: toString");
  });
});
