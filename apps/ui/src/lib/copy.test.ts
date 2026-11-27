import { createElement, type ReactNode } from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import {
  ApiOfflineBannerView,
  OFFLINE_BANNER_FLAG_OFF,
  runtimeProblem,
} from "@/components/ApiOfflineBanner";
import { AppProvider } from "@/context/AppContext";
import { AdminPage } from "@/pages/AdminPage";
import { AmendPage } from "@/pages/AmendPage";
import { AuditPage } from "@/pages/AuditPage";
import { InsightsPageView } from "@/pages/InsightsPage";
import { LibraryPage } from "@/pages/LibraryPage";
import { OntologyPage } from "@/pages/OntologyPage";
import { RunsPage } from "@/pages/RunsPage";
import { SpacesPage } from "@/pages/SpacesPage";
import { StudioPage } from "@/pages/StudioPage";
import { TrustPage } from "@/pages/TrustPage";

/**
 * UI string lint over our own copy. It does not look at user questions.
 */
const PHRASES = [
  "Not COMPLETE",
  "No ARR",
  "AGI-for-DB",
  "AGI-FOR-DB",
  "Buyer walk",
  "buyer logos",
  "walk_buyer_studio",
  "python scripts",
  "Refuse / ABSTAIN",
  "Honest ABSTAIN",
  "ABSTAIN demo",
  "compliance_gate",
  "ledger_ref",
  "DATABASE_URL",
  "score_answers",
  "score_curated",
  "Usability rule",
  "Invariant",
  "DbGate",
  ":8010",
  "hash chain",
  "control plane",
  "fixtures",
  "ACL",
];

const CODES = [/\bU[2-5]\b/, /\bT1[0-9]\b/, /\bT[34]\b/, /\bWRONG\b/, /\bP0\b/];

const here = dirname(fileURLToPath(import.meta.url));

const SOURCE_FILES = [
  "../pages/StudioPage.tsx",
  "../pages/SpacesPage.tsx",
  "../pages/TrustPage.tsx",
  "../pages/RunsPage.tsx",
  "../pages/AdminPage.tsx",
  "../pages/AuditPage.tsx",
  "../pages/AmendPage.tsx",
  "../pages/LibraryPage.tsx",
  "../components/studio/ChatTurn.tsx",
];

function render(node: ReactNode): string {
  return renderToStaticMarkup(
    createElement(MemoryRouter, null, createElement(AppProvider, null, node)),
  );
}

function textOf(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function headerText(html: string): string {
  const h1Start = html.indexOf("<h1");
  const h1Open = html.indexOf(">", h1Start);
  const h1Close = html.indexOf("</h1>", h1Open);
  const title = textOf(html.slice(h1Open + 1, h1Close));
  const before = html.slice(0, h1Start);
  const pOpen = before.lastIndexOf("<p");
  const pGt = before.indexOf(">", pOpen);
  const pClose = before.indexOf("</p>", pGt);
  const phase = textOf(before.slice(pGt + 1, pClose));
  const after = html.slice(h1Close);
  const bOpen = after.indexOf("<p");
  const bGt = after.indexOf(">", bOpen);
  const bClose = after.indexOf("</p>", bGt);
  const blurb = textOf(after.slice(bGt + 1, bClose));
  return `${phase}\n${title}\n${blurb}`;
}

function assertClean(label: string, value: string) {
  for (const phrase of PHRASES) {
    expect(value, `${label} contains ${phrase}`).not.toContain(phrase);
  }
  for (const code of CODES) {
    expect(value, `${label} matches ${code}`).not.toMatch(code);
  }
}

function literals(src: string): string {
  const noComments = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:\\])\/\/.*$/gm, "$1");
  const parts: string[] = [];
  const re = /"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`((?:[^`\\]|\\.)*)`/g;
  for (const match of noComments.matchAll(re)) {
    parts.push(match[1] ?? match[2] ?? match[3] ?? "");
  }
  return parts.join("\n");
}

const pages: { name: string; node: ReactNode }[] = [
  { name: "Studio", node: createElement(StudioPage) },
  { name: "Spaces", node: createElement(SpacesPage) },
  { name: "Ontology", node: createElement(OntologyPage) },
  { name: "Trust", node: createElement(TrustPage) },
  { name: "Runs", node: createElement(RunsPage) },
  { name: "Admin", node: createElement(AdminPage) },
  { name: "Audit", node: createElement(AuditPage) },
  { name: "Amend", node: createElement(AmendPage) },
  { name: "Library", node: createElement(LibraryPage) },
  {
    name: "Insights",
    node: createElement(InsightsPageView, {
      view: { kind: "ready", items: [], pins: [], replays: {}, approvals: {} },
    }),
  },
];

describe("product copy", () => {
  it.each(pages)("$name header is a plain sentence", ({ name, node }) => {
    const html = render(node);
    const header = headerText(html);
    expect(header, name).toMatchSnapshot();
    assertClean(`${name} header`, header);
    assertClean(`${name} page`, textOf(html));
  });

  it("keeps the five starter questions and does not advertise a refusal", () => {
    const html = textOf(render(createElement(StudioPage)));
    expect(html).toContain("What is our total spend by supplier country?");
    expect(html).toContain("What is total stock value by category?");
    expect(html).toContain("Which SKUs are below reorder level in warehouse A?");
    expect(html).toContain("Show warehouse capacity utilisation");
    expect(html).toContain("Which locations are cold storage?");
    expect(html).not.toContain("Just give me last month's number");
    expect(html).not.toContain("Refuse");
  });

  it("string literals on the owned pages stay free of internal jargon", () => {
    for (const rel of SOURCE_FILES) {
      const src = readFileSync(join(here, rel), "utf8");
      assertClean(rel, literals(src));
    }
  });
});

describe("runtime banner flag", () => {
  const legacy = renderToStaticMarkup(
    createElement(ApiOfflineBannerView, {
      apiOnline: false,
      selfCheck: false,
      problem: null,
    }),
  );

  it("flag off is the previous offline sentence", () => {
    expect(textOf(legacy)).toBe(OFFLINE_BANNER_FLAG_OFF);
    expect(legacy).toMatchSnapshot();
    expect(
      renderToStaticMarkup(
        createElement(ApiOfflineBannerView, {
          apiOnline: true,
          selfCheck: false,
          problem: "The server is not responding.",
        }),
      ),
    ).toBe("");
  });

  it("flag on states the problem in plain language", () => {
    const problem = runtimeProblem({
      apiOnline: false,
      cortexContractOk: null,
      cortexTrustOk: null,
      databaseConfigured: null,
    });
    expect(problem).toBeTruthy();
    const html = renderToStaticMarkup(
      createElement(ApiOfflineBannerView, {
        apiOnline: false,
        selfCheck: true,
        problem,
      }),
    );
    expect(html).not.toBe(legacy);
    expect(html).toContain('data-testid="runtime-degraded"');
    assertClean("runtime banner", textOf(html));
    expect(
      runtimeProblem({
        apiOnline: true,
        cortexContractOk: true,
        cortexTrustOk: true,
        databaseConfigured: true,
      }),
    ).toBeNull();
  });
});
