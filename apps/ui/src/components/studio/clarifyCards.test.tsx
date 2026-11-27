import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AccessCard } from "@/components/AccessCard";
import { AnswerMessage } from "@/components/AnswerMessage";
import { AppProvider } from "@/context/AppContext";
import { ChatTurn } from "@/components/studio/ChatTurn";
import { ClarifyCard } from "@/components/studio/ClarifyCard";
import {
  namedAbstainReason,
  readAccess,
  readClarify,
  serverSpaceIds,
  switchableSpaces,
} from "@/components/studio/abstainReason";
import type { AnswerEnvelope, ClarifyBlock } from "@/lib/types";

const BLOCK: ClarifyBlock = {
  clarify_id: "c1",
  question: "Which reading?",
  readings: [
    { reading_id: "r_month", label: "Revenue by month" },
    { reading_id: "r_country", label: "Revenue by country" },
  ],
};

function base(over: Partial<AnswerEnvelope> = {}): AnswerEnvelope {
  return {
    answer_id: "ans_1",
    text: "No figure.",
    values: [],
    badge: "ABSTAIN",
    abstained: true,
    assumptions: [],
    as_of: "2026-11-27T00:00:00Z",
    contributing_sources: [],
    ...over,
  };
}

function message(envelope: AnswerEnvelope): string {
  return renderToStaticMarkup(
    createElement(
      MemoryRouter,
      null,
      createElement(AppProvider, null, createElement(AnswerMessage, { envelope, question: "How are sales" })),
    ),
  );
}

describe("ClarifyCard", () => {
  it("renders the question and one button per reading", () => {
    const html = renderToStaticMarkup(
      createElement(ClarifyCard, { block: BLOCK, onPick: () => undefined }),
    );
    expect(html).toContain('data-testid="clarify-card"');
    expect(html).toContain('role="region"');
    expect(html).toContain("Which reading?");
    expect(html).toContain(">Revenue by month<");
    expect(html).toContain(">Revenue by country<");
    expect(html).toContain('data-testid="clarify-reading-r_month"');
    expect(html).toContain('type="button"');
    expect(html.match(/<button/g)).toHaveLength(2);
  });

  it("does not render a card for 0 or 4 readings", () => {
    expect(readClarify({ clarify: { ...BLOCK, readings: [] } })).toBeNull();
    expect(
      readClarify({
        clarify: {
          ...BLOCK,
          readings: [
            ...BLOCK.readings,
            { reading_id: "r3", label: "Third" },
            { reading_id: "r4", label: "Fourth" },
          ],
        },
      }),
    ).toBeNull();
    const html = message(base({ clarify: { ...BLOCK, readings: [] } }));
    expect(html).not.toContain("clarify-card");
  });
});

describe("AccessCard", () => {
  const access = {
    table: "orders",
    granted_in_spaces: [{ id: "sp_ops", name: "Warehouse Ops" }],
    can_request: true,
  };

  it("names the space and both buttons when the route exists", () => {
    const html = renderToStaticMarkup(
      createElement(AccessCard, {
        access,
        serverSpaceIds: ["sp_ops"],
        onSwitch: () => undefined,
      }),
    );
    expect(html).toContain('data-testid="access-card"');
    expect(html).toContain("orders");
    expect(html).toContain("Warehouse Ops");
    expect(html).toContain(">Switch to that Space<");
    expect(html).toContain(">Ask the steward for access<");
    expect(html).toContain("min-h-[44px]");
  });

  it("hides Ask the steward when the route is not available", () => {
    const off = renderToStaticMarkup(
      createElement(AccessCard, {
        access: { ...access, can_request: false },
        serverSpaceIds: ["sp_ops"],
        onSwitch: () => undefined,
      }),
    );
    expect(off).toContain(">Switch to that Space<");
    expect(off).not.toContain("Ask the steward for access");
    const missing = renderToStaticMarkup(
      createElement(AccessCard, { access, onSwitch: () => undefined, initialRouteMissing: true }),
    );
    expect(missing).not.toContain("Ask the steward for access");
    expect(readAccess({ access_needed: { ...access, can_request: "yes" } })?.can_request).toBe(false);
  });

  it("does not offer Switch for a Space the server list does not include", () => {
    const granted = [
      { id: "sp_ops", name: "Warehouse Ops" },
      { id: "sp_secret", name: "Other Ledger" },
    ];
    const clientOnly = [{ id: "sp_secret", name: "Other Ledger" }];
    expect(serverSpaceIds(clientOnly, false)).toEqual([]);
    expect(switchableSpaces(granted, serverSpaceIds(clientOnly, false))).toEqual([]);
    expect(switchableSpaces(granted, ["sp_ops"]).map((s) => s.id)).toEqual(["sp_ops"]);
    const html = renderToStaticMarkup(
      createElement(AccessCard, {
        access: { ...access, granted_in_spaces: granted },
        serverSpaceIds: ["sp_ops"],
        onSwitch: () => undefined,
      }),
    );
    expect(html).toContain('data-testid="access-switch-sp_ops"');
    expect(html).not.toContain("access-switch-sp_secret");
    expect(html).not.toContain("Switch to that Space, Other Ledger");
    const noServer = renderToStaticMarkup(
      createElement(AccessCard, {
        access: { ...access, granted_in_spaces: granted },
        onSwitch: () => undefined,
      }),
    );
    expect(noServer).not.toContain("access-switch-");
  });
});

describe("Chat and Studio flag off", () => {
  it("AnswerMessage markup matches when the fields are absent", () => {
    const plain = message(base());
    const nulled = message(base({ clarify: null, access_needed: null }));
    expect(nulled).toBe(plain);
    expect(plain).not.toContain("clarify-card");
    expect(plain).not.toContain("access-card");
  });

  it("shows the readings on the chat answer when the envelope has them", () => {
    const html = message(base({ clarify: BLOCK }));
    expect(html).toContain('data-testid="clarify-reading-r_month"');
    expect(html).toContain("Which reading?");
    expect(namedAbstainReason(base({ clarify: BLOCK }))).toBe("Which reading?");
    expect(namedAbstainReason(base({ text: "first line" }))).toBe("first line");
  });

  it("Studio ask renders no card until an envelope carries one", () => {
    const html = renderToStaticMarkup(
      createElement(
        MemoryRouter,
        null,
        createElement(AppProvider, null, createElement(ChatTurn, { spaceId: "sp" })),
      ),
    );
    expect(html).toContain('data-testid="studio-chat-turn"');
    expect(html).not.toContain("clarify-card");
    expect(html).not.toContain("access-card");
    expect(html).not.toContain("Ask the steward for access");
  });
});
