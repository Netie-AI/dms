import type { AnswerEnvelope, SpaceSummary } from "./types";

export const FIXTURE_SPACES: SpaceSummary[] = [
  {
    id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
    name: "Q3 Audit",
    source_count: 3,
    member_count: 1,
  },
  {
    id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
    name: "Margin sandbox",
    source_count: 2,
    member_count: 1,
  },
];

/** Cortex L1 vocabulary — divide-by-5 is demo fallback only, not the hero. */
export const SUGGESTED_QUESTIONS = [
  "Top 5 selling SKUs by revenue",
  "What was revenue last month?",
  "Which SKUs are below reorder level?",
  "Show warehouse capacity utilisation",
  "What was total revenue?",
  "List active alerts across the warehouse network",
];

export const FIXTURE_ANSWER: AnswerEnvelope = {
  answer_id: "ans_fixture_offline",
  text: "Offline fixture — start the API for live or demo answers.",
  values: [],
  badge: "ABSTAIN",
  assumptions: ["offline fixture"],
  as_of: "2026-07-30T09:14:00Z",
  contributing_sources: [],
};
