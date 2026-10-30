import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ActionRefusal,
  REFUSAL_CODES,
  REFUSAL_TEXT,
  applyAction,
  describeRefusal,
  probeActions,
  proposeAction,
} from "./actions";

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(join(here, "../../../../tests/fixtures/actions/act02_refusal_codes.json"), "utf8"),
) as { route_codes: string[]; gate_reasons: string[]; client_codes: string[] };

function respond(status: number, body: unknown): Response {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("probeActions", () => {
  it("is 'off' for a 404 (flag off: the routes do not match)", async () => {
    const fetchMock = vi.fn(async () => respond(404, { detail: "Not Found" }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(probeActions()).resolves.toBe("off");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/amend/actions/types", expect.anything());
  });

  it("is 'off' for a 500", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond(500, "boom")));
    await expect(probeActions()).resolves.toBe("off");
  });

  it("is 'off' for a 403 gate refusal", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond(403, { detail: "gate_unavailable" })));
    await expect(probeActions()).resolves.toBe("off");
  });

  it("is 'off' when the network rejects", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    await expect(probeActions()).resolves.toBe("off");
  });

  it("is 'on' only for a 200", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond(200, [{ name: "inventory.set_reorder_level" }])));
    await expect(probeActions()).resolves.toBe("on");
  });
});

describe("refusals", () => {
  it("REFUSAL_CODES equals the committed ACT-02 error-map fixture", () => {
    const expected = new Set([
      ...fixture.route_codes,
      ...fixture.gate_reasons,
      ...fixture.client_codes,
    ]);
    expect([...REFUSAL_CODES].sort()).toEqual([...expected].sort());
    expect(Object.keys(REFUSAL_TEXT).sort()).toEqual([...expected].sort());
  });

  it("every code gives one non-empty honest sentence", () => {
    for (const code of REFUSAL_CODES) {
      const s = describeRefusal(code);
      expect(s.trim().length, code).toBeGreaterThan(10);
      expect(s, code).not.toMatch(/^Refused: /);
      expect(s, code).not.toContain("Applied");
    }
  });

  it("an unknown code is named, never blank", () => {
    expect(describeRefusal("zzz")).toBe("Refused: zzz");
    expect(describeRefusal("")).toBe("Refused: unknown");
  });

  it("object_key_not_found names the key and the candidate (rule 12)", () => {
    const s = describeRefusal("object_key_not_found", {
      code: "object_key_not_found",
      object_key: { sku: "BETA", location_id: "WH-B" },
      candidates: [{ sku: "SKU-BETA", location_id: "WH-B" }],
    });
    expect(s).toBe('No inventory item SKU "BETA" at WH-B. Did you mean SKU-BETA?');
  });

  it("rule-12 candidates at another location name that location", () => {
    const s = describeRefusal("object_key_not_found", {
      code: "object_key_not_found",
      object_key: { sku: "SKU-BETA", location_id: "WH-Z" },
      candidates: [{ sku: "SKU-BETA", location_id: "WH-B" }],
    });
    expect(s).toBe('No inventory item SKU "SKU-BETA" at WH-Z. Did you mean SKU-BETA at WH-B?');
  });

  it("commit_outcome_unknown names the entry and never claims either outcome", () => {
    const s = describeRefusal("commit_outcome_unknown", {
      code: "commit_outcome_unknown",
      cortex_entry_id: "e1",
    });
    expect(s).toBe(
      "Ledger entry e1 was written but the database could not confirm whether the change committed. Check Recent decisions before retrying.",
    );
    expect(s).not.toContain("nothing changed");
    expect(s).not.toContain("Applied");
  });

  it("record_failed_after_append names the voided entry and says nothing changed", () => {
    const s = describeRefusal("record_failed_after_append", {
      code: "record_failed_after_append",
      cortex_entry_id: "e1",
    });
    expect(s).toBe(
      "Ledger entry e1 was written but the database failed before the decision was recorded; a void entry was requested and nothing changed.",
    );
    expect(s).not.toContain("Applied");
  });

  it("approval_ref_invalid says what a ref must look like", () => {
    const s = describeRefusal("approval_ref_invalid", { code: "approval_ref_invalid", max: 200 });
    expect(s).toContain("one ticket or change id");
    expect(s).toContain("Nothing was written.");
  });

  it("object_changed names the current value", () => {
    const s = describeRefusal("object_changed", {
      code: "object_changed",
      current_value: "450",
      current_row_version: 1,
    });
    expect(s).toBe("Value changed to 450 since this was proposed.");
  });
});

describe("calls", () => {
  it("propose posts the typed body and returns the diff", async () => {
    const fetchMock = vi.fn(async () =>
      respond(200, {
        proposal_id: "p1",
        version_num: 1,
        idempotency_token: "t1",
        status: "pending_confirm",
        diff: { kind: "action" },
        mutation: { executed: false, detail: "Proposed only; nothing changes until a steward applies." },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const out = await proposeAction({
      space_id: "S",
      object_key: { sku: "SKU-BETA", location_id: "WH-B" },
      value: "450",
      reason: "r",
    });
    expect(out.mutation.executed).toBe(false);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/amend/actions/proposals");
    expect(JSON.parse(String(init.body))).toEqual({
      space_id: "S",
      action_type: "inventory.set_reorder_level",
      object_key: { sku: "SKU-BETA", location_id: "WH-B" },
      value: "450",
      reason: "r",
    });
  });

  it("a refused apply throws ActionRefusal with the server code and detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        respond(409, {
          detail: { code: "object_changed", current_value: "450", current_row_version: 1 },
        }),
      ),
    );
    const err = await applyAction("p1", "t1", "CAB-1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ActionRefusal);
    const r = err as ActionRefusal;
    expect(r.status).toBe(409);
    expect(r.code).toBe("object_changed");
    expect(r.sentence).toBe("Value changed to 450 since this was proposed.");
  });

  it("a gate refusal (string detail) keeps the gate reason as the code", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => respond(403, { detail: "gate_task_unknown" })));
    const err = (await applyAction("p1", "t1", "CAB-1").catch((e: unknown) => e)) as ActionRefusal;
    expect(err.code).toBe("gate_task_unknown");
  });
});
