import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ActionCardView, decide } from "./ActionCard";
import { pendingProposal } from "../lib/actionFixtures";
import type { RefusalInfo } from "../lib/actions";

// #489 MEDIUM: an apply that answered 5xx or lost its connection must not be
// shown as "Refused" and must not re-arm Apply: the write may have committed.

function raw(status: number, body: string, type = "text/plain"): Response {
  return new Response(body, { status, headers: { "Content-Type": type } });
}

function view(refusal: RefusalInfo, locked: boolean): string {
  return renderToStaticMarkup(
    createElement(ActionCardView, {
      proposal: pendingProposal,
      approvalRef: "CHG-1234",
      onApprovalRefChange: () => {},
      busy: false,
      locked,
      refusal,
      onApply: () => {},
      onDecline: () => {},
    }),
  );
}

function button(html: string, testid: string): string {
  const m = html.match(new RegExp(`<button[^>]*data-testid="${testid}"[^>]*>`));
  expect(m).not.toBeNull();
  return m![0];
}

function textOf(html: string, testid: string): string {
  const after = html.split(`data-testid="${testid}"`)[1] ?? "";
  return after.slice(after.indexOf(">") + 1, after.indexOf("</p>"));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ActionCard: outcome unknown", () => {
  it("apply 504: card locks, says outcome unknown, points to Recent decisions", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => raw(504, "Gateway Timeout")));
    const out = await decide("apply", pendingProposal, "CHG-1234");
    expect(out.ok).toBe(false);
    if (out.ok) return;
    expect(out.rearm).toBe(false);
    const html = view(out.refusal, !out.rearm);
    expect(textOf(html, "action-outcome-unknown-text")).toBe(
      "The server answered HTTP 504 without a result, so it is not known whether this decision was recorded. Check Recent decisions before doing anything else.",
    );
    expect(html).toContain("Outcome unknown - this was not confirmed either way.");
    expect(html).toContain("Recent decisions below has been refreshed.");
    expect(html).toContain("Apply stays off on this card until the page is reloaded.");
    expect(button(html, "action-apply")).toContain(' disabled=""');
    expect(button(html, "action-decline")).toContain(' disabled=""');
    expect(html).not.toContain('data-testid="action-refusal"');
    expect(html).not.toContain("Refused");
    expect(html).not.toContain("Applied");
    expect(html).not.toContain("http_504");
  });

  it("apply with a dropped connection locks the same way", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const out = await decide("apply", pendingProposal, "CHG-1234");
    expect(out.ok).toBe(false);
    if (out.ok) return;
    expect(out.rearm).toBe(false);
    const html = view(out.refusal, !out.rearm);
    expect(textOf(html, "action-outcome-unknown-text")).toBe(
      "The connection dropped before the server answered, so it is not known whether this decision was recorded. Check Recent decisions before doing anything else.",
    );
    expect(button(html, "action-apply")).toContain(' disabled=""');
    expect(html).not.toContain("Nothing is known to have changed");
  });

  it("commit_outcome_unknown from the route also locks the card", async () => {
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
    const out = await decide("apply", pendingProposal, "CHG-1234");
    expect(out.ok).toBe(false);
    if (out.ok) return;
    expect(out.rearm).toBe(false);
    const html = view(out.refusal, true);
    expect(textOf(html, "action-outcome-unknown-text")).toBe(
      "Ledger entry e9 was written but the database could not confirm whether the change committed. Check Recent decisions before retrying.",
    );
    expect(button(html, "action-apply")).toContain(' disabled=""');
  });

  it("a definite refusal (ledger_unavailable) still re-arms Apply", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        raw(503, JSON.stringify({ detail: { code: "ledger_unavailable" } }), "application/json"),
      ),
    );
    const out = await decide("apply", pendingProposal, "CHG-1234");
    expect(out.ok).toBe(false);
    if (out.ok) return;
    expect(out.rearm).toBe(true);
    const html = view(out.refusal, !out.rearm);
    expect(textOf(html, "action-refusal")).toBe(
      "The Cortex ledger is unreachable. Nothing was written, because there would be no receipt.",
    );
    expect(button(html, "action-apply")).not.toContain(' disabled=""');
    expect(html).not.toContain("Outcome unknown");
  });

  it("an F5 'blocked' refusal re-arms and names the gate", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => raw(403, JSON.stringify({ detail: "blocked" }), "application/json")),
    );
    const out = await decide("apply", pendingProposal, "CHG-1234");
    expect(out.ok).toBe(false);
    if (out.ok) return;
    expect(out.rearm).toBe(true);
    expect(textOf(view(out.refusal, false), "action-refusal")).toBe(
      "The compliance gate refused this action (Cortex F5 status: blocked), so nothing was written.",
    );
  });
});
