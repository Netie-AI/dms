import { describe, expect, it } from "vitest";
import { describeApiError } from "./api";

describe("describeApiError", () => {
  it("translates a fail-closed gate into an action", () => {
    expect(describeApiError('{"detail":"gate_unavailable"}')).toMatch(/Start Cortex before writing/);
    expect(describeApiError('{"detail":"gate_task_unknown"}')).toMatch(/does not know this task/);
  });

  it("does not echo a password that landed in a 422 body", () => {
    const secret = "p;w}d";
    expect(describeApiError(`{"detail":"bad ${secret}"}`, secret)).toBe("bad [redacted]");
  });
});
