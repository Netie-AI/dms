import { describe, expect, it } from "vitest";
import { describeApiError } from "./api";

describe("describeApiError", () => {
  it("translates a fail-closed gate into an action", () => {
    expect(describeApiError('{"detail":"gate_unavailable"}')).toMatch(/Start Cortex before writing/);
    expect(describeApiError('{"detail":"gate_task_unknown"}')).toMatch(/does not know this task/);
  });
});
