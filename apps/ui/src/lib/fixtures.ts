import type { SpaceSummary } from "./types";

export const FIXTURE_SPACES: SpaceSummary[] = [
  {
    id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    name: "Finance",
    source_count: 3,
    member_count: 1,
  },
  {
    id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
    name: "Warehouse Ops",
    source_count: 2,
    member_count: 1,
  },
];

/** Exact certified questions a CEO can click, plus one typo trap that must abstain. */
export const SUGGESTED_QUESTIONS = [
  "What is our total spend by supplier country?",
  "What is total stock value by category?",
  "Top 5 selling SKUs by revenue",
  "How many SKUs do we have in inventory?",
  "Show warehouse capacity utilisation",
  "Show top 3 categoty sales",
];
