import { describe, expect, it } from "vitest";
import {
  createReview,
  type KeyItem,
  type LinkItem,
  type ProfileBody,
  type ProfileItem,
  type Result,
  type ReviewApi,
} from "./ontologyReview";

/**
 * The page's rules, run against a fake API whose every answer the test holds and
 * releases by hand. Nothing here is a browser: these run in `npm test`.
 */

function key(id: string, status = "proposed"): KeyItem {
  return {
    id,
    kind: "object",
    status,
    payload: { relation: `t_${id}`, key: ["id"] },
    provenance: { evidence: { row_count: 10, distinct_count: 10, null_count: 0 } },
  };
}
function link(id: string, status = "proposed"): LinkItem {
  return {
    id,
    kind: "link",
    status,
    payload: { from_object: "a", from_columns: ["x"], to_object: "b", to_columns: ["id"] },
  };
}

type Held<T> = { promise: Promise<Result<T>>; resolve: (r: Result<T>) => void };
function hold<T>(): Held<T> {
  let resolve!: (r: Result<T>) => void;
  const promise = new Promise<Result<T>>((res) => (resolve = res));
  return { promise, resolve };
}
const ok = <T>(body: T): Result<T> => ({ ok: true, body });
const body = (items: ProfileItem[], extra: Partial<ProfileBody> = {}): ProfileBody => ({
  status: "ok",
  served: true,
  space_id: "s1",
  items,
  ...extra,
});

function fake(stored: ProfileItem[]) {
  const calls: string[] = [];
  const measure: Held<ProfileBody>[] = [];
  const press: Record<string, Held<ProfileItem>> = {};
  const api: ReviewApi = {
    fetchSpaceItems: async () => ok({ space_id: "s1", served: true, persisted: true, items: stored }),
    measureSpace: () => {
      calls.push("measure");
      const h = hold<ProfileBody>();
      measure.push(h);
      return h.promise;
    },
    approveItem: (id) => {
      calls.push(`approve ${id}`);
      return (press[`approve ${id}`] = hold<ProfileItem>()).promise;
    },
    rejectItem: (id) => {
      calls.push(`reject ${id}`);
      return (press[`reject ${id}`] = hold<ProfileItem>()).promise;
    },
  };
  return { api, calls, measure, press };
}

async function loaded(stored: ProfileItem[]) {
  const f = fake(stored);
  const review = createReview("s1", f.api);
  await review.load();
  return { ...f, review };
}
const statusOf = (r: { getState(): { items: ProfileItem[] } }, id: string) =>
  r.getState().items.find((i) => i.id === id)?.status;

describe("one press, one item, one call", () => {
  it("sends one call for two presses in the same tick", async () => {
    const { review, calls, press } = await loaded([key("k1")]);
    const item = review.getState().items[0];
    void review.press(item, "approve");
    void review.press(item, "approve");
    void review.press(item, "reject");
    expect(calls).toEqual(["approve k1"]);
    press["approve k1"].resolve(ok({ ...key("k1"), status: "approved" }));
    await Promise.resolve();
  });

  it("shows no new status until the API has answered, then the status the API returned", async () => {
    const { review, press } = await loaded([key("k1")]);
    const p = review.press(review.getState().items[0], "approve");
    expect(statusOf(review, "k1")).toBe("proposed");
    expect(review.getState().pending).toEqual({ k1: "approve" });
    press["approve k1"].resolve(ok({ ...key("k1"), status: "approved" }));
    await p;
    expect(statusOf(review, "k1")).toBe("approved");
    expect(review.getState().pending).toEqual({});
  });

  it("keeps the old status when the API answers with some other item", async () => {
    const { review, press } = await loaded([key("k1"), key("k2")]);
    const p = review.press(review.getState().items[0], "approve");
    press["approve k1"].resolve(ok({ ...key("k2"), status: "approved" }));
    await p;
    expect(statusOf(review, "k1")).toBe("proposed");
    expect(statusOf(review, "k2")).toBe("proposed");
    expect(review.getState().itemNotice.k1).toMatch(/not this item/);
  });

  it("keeps the old status when the API refuses", async () => {
    const { review, press } = await loaded([key("k1")]);
    const p = review.press(review.getState().items[0], "approve");
    press["approve k1"].resolve({ ok: false, refusal: { kind: "error", status: 500 } });
    await p;
    expect(statusOf(review, "k1")).toBe("proposed");
    expect(review.getState().itemNotice.k1).toContain("500");
  });
});

describe("a measurement and a press never overlap", () => {
  it("refuses a press while a measurement is in flight", async () => {
    const { review, calls, measure } = await loaded([key("k1")]);
    const m = review.measure();
    void review.press(review.getState().items[0], "approve");
    expect(calls).toEqual(["measure"]);
    measure[0].resolve(ok(body([key("k1")])));
    await m;
  });

  it("refuses a measurement while a press is in flight, so a slow Measure cannot overwrite a confirmed status", async () => {
    const { review, calls, press } = await loaded([key("k1")]);
    const p = review.press(review.getState().items[0], "approve");
    void review.measure();
    expect(calls).toEqual(["approve k1"]);
    press["approve k1"].resolve(ok({ ...key("k1"), status: "approved" }));
    await p;
    expect(statusOf(review, "k1")).toBe("approved");
  });

  it("refuses a second measurement in the same tick (a ref-style guard, not render state)", async () => {
    const { review, calls, measure } = await loaded([key("k1")]);
    const a = review.measure();
    void review.measure();
    void review.measure();
    expect(calls).toEqual(["measure"]);
    measure[0].resolve(ok(body([key("k1")])));
    await a;
  });

  it("a measurement that finishes keeps the status the API confirmed before it began", async () => {
    const { review, press, measure } = await loaded([key("k1")]);
    const p = review.press(review.getState().items[0], "approve");
    press["approve k1"].resolve(ok({ ...key("k1"), status: "approved" }));
    await p;
    const m = review.measure();
    // The API's measurement answer carries the stored status, approved.
    measure[0].resolve(ok(body([key("k1", "approved")])));
    await m;
    expect(statusOf(review, "k1")).toBe("approved");
  });
});

describe("no_file_tables (409) on Measure", () => {
  it("keeps the 409 on screen and takes Approve away from stored items, Reject stays", async () => {
    const { review, measure } = await loaded([key("k1"), link("l1")]);
    const m = review.measure();
    measure[0].resolve({ ok: false, refusal: { kind: "no_file_tables", skipped: [] } });
    await m;
    const s = review.getState();
    expect(s.refusal?.kind).toBe("no_file_tables");
    expect(s.items).toHaveLength(2);
    expect(s.stale).toBe("all");
    const { canApprove, canReject } = await import("./ontologyReview");
    for (const item of s.items) {
      expect(canApprove(s, item)).toBe(false);
      expect(canReject(s, item)).toBe(true);
    }
  });

  it("refuses an Approve press on such an item without calling the API", async () => {
    const { review, calls, measure } = await loaded([key("k1")]);
    const m = review.measure();
    measure[0].resolve({ ok: false, refusal: { kind: "no_file_tables", skipped: [] } });
    await m;
    await review.press(review.getState().items[0], "approve");
    expect(calls).toEqual(["measure"]);
  });

  it("items the measurement did not re-measure are stale; a fresh one is not", async () => {
    const { review, measure } = await loaded([key("k1"), key("k2")]);
    const m = review.measure();
    measure[0].resolve(ok(body([key("k1")], { not_remeasured: ["k2"] })));
    await m;
    const { isStale } = await import("./ontologyReview");
    const s = review.getState();
    expect(isStale(s, s.items.find((i) => i.id === "k2")!)).toBe(true);
    expect(isStale(s, s.items.find((i) => i.id === "k1")!)).toBe(false);
  });
});

describe("403 and dispose", () => {
  it("a 403 steward_grant_required mid-session is shown and the status does not change", async () => {
    const { review, press } = await loaded([key("k1")]);
    const p = review.press(review.getState().items[0], "approve");
    press["approve k1"].resolve({ ok: false, refusal: { kind: "steward_grant_required" } });
    await p;
    expect(review.getState().refusal?.kind).toBe("steward_grant_required");
    expect(statusOf(review, "k1")).toBe("proposed");
  });

  it("a disposed review ignores a late press answer and a late measurement", async () => {
    const { review, press, measure } = await loaded([key("k1"), key("k2")]);
    const p = review.press(review.getState().items[0], "approve");
    review.dispose();
    press["approve k1"].resolve(ok({ ...key("k1"), status: "approved" }));
    await p;
    expect(statusOf(review, "k1")).toBe("proposed");

    const other = await loaded([key("k1")]);
    const m = other.review.measure();
    other.review.dispose();
    other.measure[0].resolve(ok(body([key("zz")])));
    await m;
    expect(other.review.getState().items.map((i) => i.id)).toEqual(["k1"]);
    void measure;
  });
});

describe("no steward grant", () => {
  it("canApprove and canReject are false, and a press makes no API call", async () => {
    const { review, calls, press } = await loaded([key("k1"), key("k2")]);
    const p = review.press(review.getState().items[0], "approve");
    press["approve k1"].resolve({ ok: false, refusal: { kind: "steward_grant_required" } });
    await p;
    const { canApprove, canReject } = await import("./ontologyReview");
    const s = review.getState();
    expect(canApprove(s, s.items[1])).toBe(false);
    expect(canReject(s, s.items[1])).toBe(false);
    await review.press(s.items[1], "approve");
    await review.press(s.items[1], "reject");
    void review.measure();
    expect(calls).toEqual(["approve k1"]);
  });
});

describe("edges the checker found", () => {
  it("an id the measurement both returned and listed as not re-measured is not stale", async () => {
    const { review, measure } = await loaded([key("k1")]);
    const m = review.measure();
    measure[0].resolve(ok(body([key("k1")], { not_remeasured: ["k1"] })));
    await m;
    expect(review.getState().stale).toEqual([]);
  });

  it("a second load() does not run, so a late load cannot revert a confirmed status", async () => {
    const { review, press } = await loaded([key("k1")]);
    await review.load();
    const p = review.press(review.getState().items[0], "approve");
    press["approve k1"].resolve(ok({ ...key("k1"), status: "approved" }));
    await p;
    await review.load();
    expect(statusOf(review, "k1")).toBe("approved");
  });
});
