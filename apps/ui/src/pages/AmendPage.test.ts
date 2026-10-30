import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { pendingProposal } from "../lib/actionFixtures";
import type { AmendProposal } from "../lib/api";

// ACT03_PROOF=1 (scripts/verify/act_03_ui_demo.py) prints the rendered strings.
function proof(label: string, value: unknown): void {
  if (process.env.ACT03_PROOF) {
    console.log(`[ACT03] ${label}: ${typeof value === "string" ? value : JSON.stringify(value)}`);
  }
}

const probe = vi.hoisted(() => ({ impl: (): Promise<"on" | "off"> => new Promise(() => {}) }));

vi.mock("@/context/AppContext", () => ({
  useApp: () => ({ activeSpaceId: "33333333-3333-4333-8333-333333333333" }),
}));

vi.mock("@/lib/actions", async (importOriginal) => {
  const real = await importOriginal<typeof import("../lib/actions")>();
  return { ...real, probeActions: vi.fn(() => probe.impl()) };
});

const here = dirname(fileURLToPath(import.meta.url));
// Markup of AmendPage at origin/main 58fcaa5 (empty list), rendered from that
// commit's file unmodified, with the same AppContext mock. See the fixture header.
const MAIN_EMPTY = readFileSync(
  join(here, "__fixtures__/AmendPage.main-58fcaa5.empty.html"),
  "utf8",
).trim();

const legacy: AmendProposal = {
  id: "44444444-4444-4444-8444-444444444444",
  version_num: 1,
  status: "pending_confirm",
  idempotency_token: "tok_legacy",
  diff: { summary: "Correct quantity on demo row", plain: "Propose: Correct quantity on demo row" },
};

const actionRow = {
  id: pendingProposal.proposal_id,
  version_num: 1,
  status: "pending_confirm",
  idempotency_token: pendingProposal.idempotency_token,
  diff: pendingProposal.diff,
} as unknown as AmendProposal;

async function page(props: Record<string, unknown>): Promise<string> {
  const { AmendPage } = await import("./AmendPage");
  return renderToStaticMarkup(createElement(AmendPage, props));
}

beforeEach(() => {
  vi.resetModules();
  vi.doUnmock("@/components/GovernedActions");
  probe.impl = () => new Promise(() => {});
});

describe("AmendPage flag off", () => {
  it("before the probe resolves (null) and when it says 'off', no Governed actions, same markup as the section stubbed out", async () => {
    probe.impl = () => new Promise(() => {}); // never resolves: state stays null
    const pending = await page({ initialProposals: [legacy] });
    probe.impl = async () => "off";
    const off = await page({ initialActionsAvailable: "off", initialProposals: [legacy] });

    vi.resetModules();
    vi.doMock("@/components/GovernedActions", () => ({ GovernedActions: () => null }));
    const stubbed = await page({ initialActionsAvailable: "off", initialProposals: [legacy] });

    proof("flag null contains 'Governed actions'", pending.includes("Governed actions"));
    proof("flag off contains 'Governed actions'", off.includes("Governed actions"));
    proof("flag off markup === section stubbed out", off === stubbed);
    for (const html of [pending, off]) {
      expect(html).not.toContain("Governed actions");
      expect(html).not.toContain("Apply in Governed actions");
      expect(html).toContain(">Confirm</button>");
      expect(html).toBe(stubbed);
    }
  });

  it("flag-off markup equals origin/main's AmendPage, byte for byte", async () => {
    proof("flag off markup === origin/main 58fcaa5 markup", (await page({})) === MAIN_EMPTY);
    expect(await page({})).toBe(MAIN_EMPTY);
    expect(await page({ initialActionsAvailable: "off" })).toBe(MAIN_EMPTY);
  });

  it("an action-kind row still shows the legacy Confirm while the flag is off", async () => {
    // Before ACT-02's on-only refusal, legacy propose accepted any diff. Off,
    // the page renders it exactly as main does; the server refuses its confirm.
    const html = await page({ initialActionsAvailable: "off", initialProposals: [actionRow] });
    expect(html).toContain(">Confirm</button>");
    expect(html).not.toContain("Governed actions");
  });
});

describe("AmendPage flag on", () => {
  it("renders Governed actions and routes the action row there instead of Confirm", async () => {
    probe.impl = async () => "on";
    const html = await page({
      initialActionsAvailable: "on",
      initialProposals: [legacy, actionRow],
      initialActionProposals: [pendingProposal],
    });
    proof("flag on contains 'Governed actions'", html.includes("Governed actions"));
    expect(html).toContain("Governed actions");
    expect(html).toContain('data-testid="action-propose-form"');
    expect(html).toContain('data-testid="action-card"');
    const rows = html.split("<li ");
    const actionLi = rows.find((r) => r.includes("reorder_level_kg for inventory_item SKU-BETA")) ?? "";
    proof("flag on action row", actionLi.includes("Apply in Governed actions") ? "Apply in Governed actions" : "MISSING");
    expect(actionLi).toContain("Apply in Governed actions");
    expect(actionLi).not.toContain(">Confirm</button>");
    const legacyLi = rows.find((r) => r.includes("Correct quantity on demo row</p>")) ?? "";
    expect(legacyLi).toContain(">Confirm</button>");
  });
});
