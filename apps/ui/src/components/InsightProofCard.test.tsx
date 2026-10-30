import { describe, expect, it } from "vitest";
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Navigate } from "react-router-dom";
import { InsightProofCard } from "./InsightProofCard";
import { InsightsPageView, resolveStatus, statusToView } from "@/pages/InsightsPage";
import {
  confirmPin,
  createApprovalFlow,
  previewPin,
  type ApprovalState,
  type InsightItem,
} from "@/lib/insightProof";
import { fixtureItem } from "@/lib/insightProofFixtures";

/**
 * vitest runs in node with no DOM (no jsdom in the lockfile; adding one is a new
 * dependency). A "click" here calls the onClick of the element the component
 * rendered, found by data-testid in its element tree. Components without hooks
 * are expanded by calling them.
 */
type AnyProps = Record<string, unknown> & { children?: ReactNode };

function find(node: ReactNode, testId: string): ReactElement<AnyProps> | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const hit = find(child, testId);
      if (hit) return hit;
    }
    return null;
  }
  if (!isValidElement(node)) return null;
  const el = node as ReactElement<AnyProps>;
  if (typeof el.type === "function") {
    return find((el.type as (p: AnyProps) => ReactNode)(el.props), testId);
  }
  if (el.props["data-testid"] === testId) return el;
  return find(el.props.children, testId);
}

function card(props: { item: InsightItem; approval?: ApprovalState } & AnyProps): ReactNode {
  return InsightProofCard(props as Parameters<typeof InsightProofCard>[0]);
}

describe("InsightProofCard", () => {
  it("renders the headline, the rows and the proof lines", () => {
    const html = renderToStaticMarkup(createElement(InsightProofCard, { item: fixtureItem }));
    expect(html).toContain('data-testid="insight-proof-card"');
    expect(html).toContain(fixtureItem.envelope.text);
    expect(html).toContain("North");
    expect(html).toContain("86.4");
    expect(html).toContain("Grouped total 1,100.00 = raw total with no join 1,100.00");
    expect(html).toContain("Ledger entry led_000123");
    expect(html).toContain("How this is proven");
  });

  it("renders the abstain card, with no rows, when the proof does not conserve", () => {
    const item = { envelope: fixtureItem.envelope, proof: { ...fixtureItem.proof, conserves: false } };
    const html = renderToStaticMarkup(createElement(InsightProofCard, { item }));
    expect(html).toContain('data-testid="insight-abstain-card"');
    expect(html).not.toContain('data-testid="insight-rows"');
    expect(html).not.toContain("North");
    expect(html).not.toContain("86.4");
  });

  it("'Approve as steward' POSTs confirm only after the dialog's confirm click", async () => {
    const calls: string[] = [];
    const impl = async (url: string) => {
      calls.push(url);
      const body = url.endsWith("/pins/preview")
        ? { ...fixtureItem, token: "ipctok", expires_at: "x" }
        : { asset_id: "pin_0123456789abcdef" };
      return new Response(JSON.stringify(body), { status: 200 });
    };
    let approval: ApprovalState = { phase: "idle" };
    const flow = createApprovalFlow(
      { preview: (s, i) => previewPin(s, i, impl), confirm: (t) => confirmPin(t, impl) },
      null,
      "ins_156d4c0dd99d3b10",
      (s) => {
        approval = s;
      },
    );
    const handlers = {
      onApprove: () => flow.approve(),
      onConfirm: () => flow.confirm(),
      onCancel: () => flow.cancel(),
    };

    const idle = card({ item: fixtureItem, approval, ...handlers });
    expect(find(idle, "approve-dialog")).toBeNull();
    const approve = find(idle, "approve-steward");
    expect(approve).not.toBeNull();
    await (approve!.props.onClick as () => Promise<void>)();
    expect(calls).toEqual(["/api/v1/insight-proof/pins/preview"]);
    expect(approval.phase).toBe("confirming");

    const open = card({ item: fixtureItem, approval, ...handlers });
    const dialog = find(open, "approve-dialog");
    expect(dialog).not.toBeNull();
    expect(dialog!.props.role).toBe("dialog");
    expect(calls).toEqual(["/api/v1/insight-proof/pins/preview"]); // still no confirm
    await (find(open, "approve-confirm")!.props.onClick as () => Promise<void>)();
    expect(calls).toEqual([
      "/api/v1/insight-proof/pins/preview",
      "/api/v1/insight-proof/pins/confirm",
    ]);
    expect(approval).toEqual({ phase: "pinned", assetId: "pin_0123456789abcdef" });
  });

  it("cancel closes the dialog without a confirm", async () => {
    const calls: string[] = [];
    const impl = async (url: string) => {
      calls.push(url);
      return new Response(JSON.stringify({ ...fixtureItem, token: "ipctok", expires_at: "x" }), {
        status: 200,
      });
    };
    let approval: ApprovalState = { phase: "idle" };
    const flow = createApprovalFlow(
      { preview: (s, i) => previewPin(s, i, impl), confirm: (t) => confirmPin(t, impl) },
      null,
      "ins_156d4c0dd99d3b10",
      (s) => {
        approval = s;
      },
    );
    await flow.approve();
    const open = card({ item: fixtureItem, approval, onCancel: () => flow.cancel() });
    (find(open, "approve-cancel")!.props.onClick as () => void)();
    expect(approval.phase).toBe("idle");
    expect(calls).toEqual(["/api/v1/insight-proof/pins/preview"]);
  });
});

describe("InsightsPage", () => {
  it("given a status 404 renders the same redirect to '/' as the catch-all", () => {
    expect(statusToView(404)).toBe("missing");
    expect(statusToView(200)).toBe("ready");
    const el = InsightsPageView({ view: { kind: "missing" } }) as ReactElement<{
      to: string;
      replace: boolean;
    }>;
    expect(el.type).toBe(Navigate);
    expect(el.props.to).toBe("/");
    expect(el.props.replace).toBe(true);
  });

  it("redirects like main when the status call fails any other way, and shows nothing first", async () => {
    // A proxy 502, an enabled:false body, and a rejected fetch (API down) all
    // behave as the catch-all does on main: a redirect, never the feature's header.
    expect(statusToView(502)).toBe("missing");
    expect(statusToView(500)).toBe("missing");
    const resp = (status: number, body: unknown) =>
      Promise.resolve(new Response(JSON.stringify(body), { status }));
    expect(await resolveStatus(() => resp(502, {}))).toBe("missing");
    expect(await resolveStatus(() => resp(200, { enabled: false }))).toBe("missing");
    expect(await resolveStatus(() => Promise.reject(new TypeError("Failed to fetch")))).toBe(
      "missing",
    );
    expect(await resolveStatus(() => resp(200, { enabled: true }))).toBe("ready");
    expect(InsightsPageView({ view: { kind: "loading" } })).toBeNull();
  });
});
