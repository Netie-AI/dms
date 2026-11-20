import { afterEach, describe, expect, it, vi } from "vitest";
import {
  APPROVED_NOTE,
  LINK_NOT_USABLE,
  MEMORY_NOTICE_HINT,
  MEMORY_NOTICE_TITLE,
  approveItem,
  boundSentences,
  classifyRefusal,
  fetchSpaceItems,
  keyConflictSentence,
  keyEvidenceSentence,
  linkEvidenceSentence,
  linkUsability,
  linkUsabilitySentence,
  measureSpace,
  observationSentence,
  refusalSentence,
  rejectItem,
  skippedSentence,
  statusLabel,
  tableReviews,
  unkeyedSentence,
  withItem,
  type Bounds,
  type KeyItem,
  type LinkItem,
  type Observation,
  type ProfileItem,
} from "./ontologyReview";

// Shapes and counts below are copied from a real `POST /v1/ontology/data/profile`
// answer for the five retail files of tests/test_onto_profile_01.py (dms#514).

function key(id: string, relation: string, columns: string[], status = "proposed"): KeyItem {
  return {
    id,
    kind: "object",
    status,
    payload: { name: relation, relation, key: columns },
    provenance: {
      profiler: "onto_profile/1",
      evidence: { row_count: 300, distinct_count: 300, null_count: 0, truncated: false },
    },
  };
}

const SHIP_LINK: LinkItem = {
  id: "l1",
  kind: "link",
  status: "proposed",
  payload: {
    name: "profile:bronze.shipments.order_id->bronze.orders.order_id",
    from_object: "bronze.shipments",
    from_columns: ["order_id"],
    to_object: "bronze.orders",
    to_columns: ["order_id"],
    one_to_one: false,
  },
  provenance: {
    evidence: {
      child_rows: 120,
      child_null_rows: 0,
      child_distinct: 71,
      parent_distinct: 300,
      matched_parent_keys: 71,
      orphan_rows: 0,
      orphan_keys: 0,
      max_fanout: 4,
      truncated: false,
    },
  },
};

const ORPHANS: Observation = {
  child_table: "bronze.orders",
  child_columns: ["customer_id"],
  parent_table: "bronze.customers",
  parent_columns: ["customer_id"],
  evidence: {
    child_rows: 300,
    child_distinct: 63,
    parent_distinct: 60,
    matched_parent_keys: 58,
    orphan_rows: 5,
    orphan_keys: 5,
    max_fanout: 11,
  },
  reason: "orphans",
};

const NO_CHILD_KEY: Observation = {
  child_table: "bronze.order_items",
  child_columns: ["order_id"],
  parent_table: "bronze.orders",
  parent_columns: ["order_id"],
  evidence: { child_rows: 800, orphan_rows: 0 },
  reason: "child_has_no_key",
};

const BOUNDS: Bounds = {
  max_columns_per_table: 60,
  columns_not_measured: {},
  pair_search: {
    when: "only for a table where no single column is a key",
    max_columns: 20,
    wider_than_pairs: "not searched",
    tables: {
      "bronze.order_items": {
        pairs_searched: 6,
        columns_with_nulls_not_paired: [],
        columns_over_pair_cap_not_paired: [],
      },
    },
  },
  link_search: {
    shape: "one child column to one single-column key of another table",
    type_rule: "identical landed type, compared as landed, no cast or trim",
    self_references: "not searched",
    max_pairs: 2000,
    pairs_measured: 71,
    pairs_type_incompatible: 35,
    pairs_child_all_null: 0,
    pairs_below_orphan_threshold: 67,
    pairs_over_cap_not_measured: 0,
    pair_keys_not_searched_as_parents: 0,
  },
  orphan_observation_min_match_ratio: 0.5,
  truncated_tables: [],
  unmeasured: [],
};

describe("evidence in words", () => {
  it("says a key's counts the way the ticket words them", () => {
    expect(keyEvidenceSentence({ row_count: 300, distinct_count: 300, null_count: 0 })).toBe(
      "300 rows, 300 distinct, 0 empty",
    );
    expect(keyEvidenceSentence({ row_count: 1, distinct_count: 1, null_count: 0 })).toBe(
      "1 row, 1 distinct, 0 empty",
    );
  });

  it("never shows a missing count as 0", () => {
    expect(keyEvidenceSentence(undefined)).toBe(
      "not measured rows, not measured distinct, not measured empty",
    );
    expect(linkEvidenceSentence({ child_rows: 120 })).toContain("not measured of not measured");
  });

  it("says truncation on the evidence line", () => {
    expect(keyEvidenceSentence({ row_count: 9, distinct_count: 9, null_count: 0, truncated: true }))
      .toContain("truncated table");
  });

  it("says a link's rows, distinct values, matched parent keys and fan-out", () => {
    expect(linkEvidenceSentence(SHIP_LINK.provenance?.evidence)).toBe(
      "120 rows, 71 distinct values, 71 of 300 parent keys matched, largest fan-out 4, 0 empty",
    );
  });

  it("says an orphan observation in plain words with its counts", () => {
    expect(observationSentence(ORPHANS)).toBe(
      "5 rows of bronze.orders.customer_id point at a bronze.customers.customer_id that " +
        "does not exist (5 distinct values with no match), so no link is proposed.",
    );
  });

  it("says a keyless child in plain words", () => {
    expect(observationSentence(NO_CHILD_KEY)).toContain(
      "bronze.order_items has no column that identifies a row",
    );
  });

  it("shows an unknown reason code instead of inventing words for it", () => {
    expect(observationSentence({ ...ORPHANS, reason: "new_reason" })).toContain(
      "reason code: new_reason",
    );
    expect(unkeyedSentence({ table: "t", reason: "new_reason" })).toContain(
      "reason code: new_reason",
    );
    expect(skippedSentence({ table: "t", reason: "new_reason" })).toContain(
      "reason code: new_reason",
    );
  });

  it("says why a table is keyless and how far the search went", () => {
    expect(
      unkeyedSentence({
        table: "bronze.order_items",
        row_count: 800,
        reason: "no_unique_column_or_pair",
        searched: { single_columns: 4, pairs: 6 },
      }),
    ).toBe(
      "This table has no column that identifies a row. 4 columns and 6 pairs of columns " +
        "were checked across 800 rows.",
    );
    expect(unkeyedSentence({ table: "t", reason: "empty_table" })).toContain("no rows");
  });

  it("says a SQL-source table is not measured here", () => {
    expect(skippedSentence({ table: "main.orders", reason: "sql_source" })).toContain(
      "comes from a SQL source",
    );
  });
});

describe("status words", () => {
  it("labels the three statuses and never claims more than a steward's approval", () => {
    expect(statusLabel("proposed")).toBe("proposed");
    expect(statusLabel("approved")).toBe("approved");
    expect(statusLabel("revoked")).toBe("rejected");
    expect(statusLabel("weird")).toBe("unknown status: weird");
  });

  it("never uses the words verified or certified in any sentence it can produce", () => {
    const everything = [
      APPROVED_NOTE,
      LINK_NOT_USABLE,
      MEMORY_NOTICE_TITLE,
      MEMORY_NOTICE_HINT,
      statusLabel("approved"),
      statusLabel("proposed"),
      statusLabel("revoked"),
      keyEvidenceSentence({ row_count: 1, distinct_count: 1, null_count: 0, truncated: true }),
      linkEvidenceSentence(SHIP_LINK.provenance?.evidence),
      observationSentence(ORPHANS),
      observationSentence(NO_CHILD_KEY),
      unkeyedSentence({ table: "t", reason: "no_unique_column_or_pair" }),
      unkeyedSentence({ table: "t", reason: "empty_table" }),
      skippedSentence({ table: "t", reason: "sql_source" }),
      ...boundSentences(BOUNDS),
      refusalSentence({ kind: "steward_grant_required" }),
      refusalSentence({ kind: "no_file_tables", skipped: [] }),
      refusalSentence({ kind: "space_not_found" }),
      refusalSentence({ kind: "item_not_found" }),
      refusalSentence({ kind: "gate_refused", reason: "gate_unavailable" }),
      refusalSentence({ kind: "error", status: 500, message: "x" }),
      keyConflictSentence({ kind: "key_already_approved", relation: "t", approvedItemId: null }, []),
    ].join("\n");
    expect(everything).not.toMatch(/verif|certif/i);
  });

  it("states that an approved item is re-measured at every question", () => {
    expect(APPROVED_NOTE).toContain("re-measured at every question");
  });

  it("says approvals are in memory and lost on restart", () => {
    expect(`${MEMORY_NOTICE_TITLE}. ${MEMORY_NOTICE_HINT}`).toMatch(/kept in memory.*lost when.*restart/);
  });
});

describe("bounds", () => {
  it("states every bound the profiler returned", () => {
    const lines = boundSentences(BOUNDS);
    const text = lines.join("\n");
    for (const want of [
      "At most 60 columns per table are measured.",
      "No column was left unmeasured by the column limit.",
      "Two-column keys are searched only for a table where no single column is a key.",
      "At most 20 columns per table are paired.",
      "Keys wider than two columns: not searched.",
      "bronze.order_items: 6 pairs searched.",
      "Links searched: one child column to one single-column key of another table.",
      "Type rule: identical landed type, compared as landed, no cast or trim.",
      "Links from a table to itself: not searched.",
      "At most 2000 column pairs are measured for links.",
      "71 column pairs measured.",
      "35 column pairs left out because the landed types differ.",
      "0 column pairs left out because the child column is empty.",
      "67 column pairs left out because too few values matched.",
      "0 column pairs over the limit and not measured.",
      "0 two-column keys not searched as a parent.",
      "at least 50% of its distinct values match",
      "Tables measured on truncated data: none.",
      "No measurement failed.",
    ]) {
      expect(text).toContain(want);
    }
    // One line per leaf the profiler stated: 2 + 4 + 10 + 3.
    expect(lines).toHaveLength(19);
  });

  it("lists a bound it has no sentence for, as stated, instead of dropping it", () => {
    const lines = boundSentences({
      new_cap: 7,
      pair_search: { new_rule: "x", tables: { t: { pairs_searched: 1, extra_note: "y" } } },
      link_search: { new_count: 3 },
    });
    expect(lines).toContain("new_cap: 7");
    expect(lines).toContain("pair search new_rule: x");
    expect(lines).toContain("t extra_note: y");
    expect(lines).toContain("link search new_count: 3");
  });

  it("names unmeasured columns, truncated tables and failed measurements", () => {
    const text = boundSentences({
      columns_not_measured: { "bronze.wide": ["c61", "c62"] },
      truncated_tables: ["bronze.big"],
      unmeasured: [
        { table: "bronze.bad", error: "IOException" },
        {
          child_table: "bronze.a",
          child_columns: ["x"],
          parent_table: "bronze.b",
          parent_columns: ["id"],
          error: "ConversionException",
        },
      ],
    }).join("\n");
    expect(text).toContain("bronze.wide: columns over the limit and not measured: c61, c62.");
    expect(text).toContain("Tables measured on truncated data: bronze.big.");
    expect(text).toContain("Could not be measured: bronze.bad (error class: IOException).");
    expect(text).toContain("Could not be measured: bronze.a.x to bronze.b.id (error class: ConversionException).");
  });

  it("returns nothing when no bounds were sent (after a reload)", () => {
    expect(boundSentences(undefined)).toEqual([]);
  });
});

describe("link usability", () => {
  const shipKey = key("k-ship", "bronze.shipments", ["shipment_id"]);
  const orderKey = key("k-ord", "bronze.orders", ["order_id"]);

  it("is not usable until both keys are approved, and says which is missing", () => {
    const none: ProfileItem[] = [shipKey, orderKey, SHIP_LINK];
    expect(linkUsability(SHIP_LINK, none).usable).toBe(false);
    expect(linkUsabilitySentence(SHIP_LINK, none)).toBe(
      "not usable until both keys are approved. Not approved yet: a key of bronze.shipments " +
        "and order_id as the key of bronze.orders.",
    );
    const onlyParent = [shipKey, { ...orderKey, status: "approved" }, SHIP_LINK];
    expect(linkUsabilitySentence(SHIP_LINK, onlyParent)).toBe(
      "not usable until both keys are approved. Not approved yet: a key of bronze.shipments.",
    );
    const onlyChild = [{ ...shipKey, status: "approved" }, orderKey, SHIP_LINK];
    expect(linkUsabilitySentence(SHIP_LINK, onlyChild)).toContain(
      "Not approved yet: order_id as the key of bronze.orders.",
    );
  });

  it("is usable when both keys are approved", () => {
    const both = [
      { ...shipKey, status: "approved" },
      { ...orderKey, status: "approved" },
      SHIP_LINK,
    ];
    expect(linkUsability(SHIP_LINK, both).usable).toBe(true);
    expect(linkUsabilitySentence(SHIP_LINK, both)).toBeNull();
  });

  it("does not count a different approved key of the parent table", () => {
    const wrongParentKey = [
      { ...shipKey, status: "approved" },
      { ...key("k-other", "bronze.orders", ["order_date"]), status: "approved" },
      SHIP_LINK,
    ];
    expect(linkUsability(SHIP_LINK, wrongParentKey).parentKeyApproved).toBe(false);
  });

  it("does not count a rejected key", () => {
    const rejected = [
      { ...shipKey, status: "revoked" },
      { ...orderKey, status: "approved" },
      SHIP_LINK,
    ];
    expect(linkUsability(SHIP_LINK, rejected).usable).toBe(false);
  });
});

describe("refusals", () => {
  it("names 403 steward_grant_required", () => {
    const r = classifyRefusal(403, JSON.stringify({ detail: "steward_grant_required" }));
    expect(r).toEqual({ kind: "steward_grant_required" });
    expect(refusalSentence(r)).toContain("A steward grant is needed");
  });

  it("keeps a gate refusal apart from a missing grant", () => {
    const r = classifyRefusal(403, JSON.stringify({ detail: "gate_unavailable" }));
    expect(r).toEqual({ kind: "gate_refused", reason: "gate_unavailable" });
    expect(refusalSentence(r)).toContain("Nothing was changed");
  });

  it("names 409 no_file_tables and where a SQL-source Space is shown", () => {
    const r = classifyRefusal(
      409,
      JSON.stringify({ detail: { code: "no_file_tables", skipped: [{ table: "main.t", reason: "sql_source" }] } }),
    );
    expect(r).toEqual({ kind: "no_file_tables", skipped: [{ table: "main.t", reason: "sql_source" }] });
    const text = refusalSentence(r);
    expect(text).toContain("no table landed from a file");
    expect(text).toContain("Spaces page");
  });

  it("names 409 key_already_approved and says which key holds the table", () => {
    const r = classifyRefusal(
      409,
      JSON.stringify({
        detail: { code: "key_already_approved", relation: "bronze.customers", approved_item_id: "k1" },
      }),
    );
    expect(r).toEqual({ kind: "key_already_approved", relation: "bronze.customers", approvedItemId: "k1" });
    if (r.kind !== "key_already_approved") throw new Error("unreachable");
    const items = [
      key("k1", "bronze.customers", ["customer_id"], "approved"),
      key("k2", "bronze.customers", ["name"]),
    ];
    expect(keyConflictSentence(r, items)).toBe(
      "customer_id is already approved as the key of bronze.customers. A table has one " +
        "approved key: reject that one first. This item stays proposed.",
    );
    // The approved item is not in the list this page holds: still say the table.
    expect(keyConflictSentence(r, [])).toContain("Another key is already approved for bronze.customers");
  });

  it("names the two 404s and keeps any other answer as an error", () => {
    expect(classifyRefusal(404, '{"detail":"space_not_found"}').kind).toBe("space_not_found");
    expect(classifyRefusal(404, '{"detail":"ontology_item_not_found"}').kind).toBe("item_not_found");
    expect(classifyRefusal(500, "<html>")).toEqual({
      kind: "error",
      status: 500,
      message: "the API answered 500",
    });
  });
});

describe("table grouping", () => {
  it("puts keys, links, observations and the keyless reason under their table", () => {
    const items: ProfileItem[] = [
      key("a", "bronze.customers", ["customer_id"]),
      key("b", "bronze.customers", ["name"]),
      key("c", "bronze.shipments", ["shipment_id"]),
      SHIP_LINK,
    ];
    const rows = tableReviews(items, {
      tables: [
        { table: "bronze.customers", row_count: 60, columns: [{ name: "customer_id", type: "VARCHAR" }] },
        { table: "bronze.order_items", row_count: 800, columns: [] },
        { table: "bronze.orders", row_count: 300 },
        { table: "bronze.shipments", row_count: 120 },
      ],
      observations: [ORPHANS, NO_CHILD_KEY],
      unkeyed_tables: [{ table: "bronze.order_items", reason: "no_unique_column_or_pair" }],
    });
    expect(rows.map((r) => r.table)).toEqual([
      "bronze.customers",
      "bronze.order_items",
      "bronze.orders",
      "bronze.shipments",
    ]);
    const by = Object.fromEntries(rows.map((r) => [r.table, r]));
    expect(by["bronze.customers"].keys.map((k) => k.id)).toEqual(["a", "b"]);
    expect(by["bronze.customers"].rowCount).toBe(60);
    expect(by["bronze.customers"].columnCount).toBe(1);
    expect(by["bronze.shipments"].links.map((l) => l.id)).toEqual(["l1"]);
    expect(by["bronze.orders"].observations).toEqual([ORPHANS]);
    expect(by["bronze.order_items"].observations).toEqual([NO_CHILD_KEY]);
    expect(by["bronze.order_items"].unkeyed?.reason).toBe("no_unique_column_or_pair");
    expect(by["bronze.order_items"].keys).toEqual([]);
  });

  it("groups from stored items alone after a reload", () => {
    const rows = tableReviews([key("a", "bronze.customers", ["customer_id"]), SHIP_LINK], null);
    expect(rows.map((r) => r.table)).toEqual(["bronze.customers", "bronze.shipments"]);
    expect(rows[0].rowCount).toBeNull();
  });
});

describe("withItem", () => {
  it("replaces exactly the returned item and leaves every other status alone", () => {
    const items: ProfileItem[] = [
      key("a", "t", ["x"]),
      key("b", "t", ["y"]),
      key("c", "u", ["z"]),
    ];
    const next = withItem(items, key("b", "t", ["y"], "approved"));
    expect(next.map((i) => i.status)).toEqual(["proposed", "approved", "proposed"]);
    expect(items.map((i) => i.status)).toEqual(["proposed", "proposed", "proposed"]);
  });
});

describe("the calls the screen makes", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubFetch(status: number, body: unknown): ReturnType<typeof vi.fn> {
    const fn = vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      text: async () => JSON.stringify(body),
    });
    vi.stubGlobal("fetch", fn);
    return fn;
  }

  it("measures with POST /v1/ontology/data/profile for one Space", async () => {
    const fn = stubFetch(200, { status: "proposed", items: [] });
    await measureSpace("sp 1");
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn.mock.calls[0][0]).toBe("/api/v1/ontology/data/profile?space_id=sp%201");
    expect(fn.mock.calls[0][1].method).toBe("POST");
  });

  it("reads stored items with GET /v1/ontology/data/spaces/{id}/items", async () => {
    const fn = stubFetch(200, { items: [] });
    await fetchSpaceItems("sp1");
    expect(fn.mock.calls[0][0]).toBe("/api/v1/ontology/data/spaces/sp1/items");
    expect(fn.mock.calls[0][1]?.method).toBeUndefined();
  });

  it("approves and rejects one item id per call, on the per-item routes", async () => {
    const fn = stubFetch(200, key("k1", "t", ["x"], "approved"));
    await approveItem("k1");
    await rejectItem("k1");
    expect(fn.mock.calls.map((c) => [c[0], c[1].method])).toEqual([
      ["/api/v1/ontology/data/items/k1/approve", "POST"],
      ["/api/v1/ontology/data/items/k1/revoke", "POST"],
    ]);
    // No request body: nothing can carry a second id or a version.
    expect(fn.mock.calls.every((c) => c[1].body === undefined)).toBe(true);
  });

  it("returns the refusal, not a guessed status, when approve is refused", async () => {
    stubFetch(409, {
      detail: { code: "key_already_approved", relation: "t", approved_item_id: "k0" },
    });
    const result = await approveItem("k1");
    expect(result).toEqual({
      ok: false,
      refusal: { kind: "key_already_approved", relation: "t", approvedItemId: "k0" },
    });
  });

  it("reports an unreachable API as an error, not as an empty Space", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("failed to fetch")));
    const result = await fetchSpaceItems("sp1");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.refusal.kind).toBe("error");
  });
});
