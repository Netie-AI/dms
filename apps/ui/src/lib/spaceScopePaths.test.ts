import { describe, expect, it } from "vitest";
import { amendProposalsPath, runsPath } from "./api";

/**
 * SPACE-UI-ALL leftover: API already scoped Runs/Amend; the UI helpers must put
 * space_id on the wire whenever an active Space is selected. These paths are the
 * shared gate — a page that forgets activeSpaceId fails here before a browser.
 */
describe("space-scoped list paths", () => {
  it("runsPath carries space_id when a Space is active", () => {
    expect(runsPath(undefined, "sp_finance")).toBe("/v1/runs?space_id=sp_finance");
    expect(runsPath("ingest", "sp_ops")).toBe("/v1/runs?kind=ingest&space_id=sp_ops");
  });

  it("runsPath omits space_id for Company (default ACL)", () => {
    expect(runsPath()).toBe("/v1/runs");
    expect(runsPath("query", null)).toBe("/v1/runs?kind=query");
    expect(runsPath("query", undefined)).toBe("/v1/runs?kind=query");
  });

  it("amendProposalsPath carries space_id when a Space is active", () => {
    expect(amendProposalsPath("sp_finance")).toBe(
      "/v1/amend/proposals?space_id=sp_finance",
    );
  });

  it("amendProposalsPath omits space_id for Company (default ACL)", () => {
    expect(amendProposalsPath()).toBe("/v1/amend/proposals");
    expect(amendProposalsPath(null)).toBe("/v1/amend/proposals");
  });
});
