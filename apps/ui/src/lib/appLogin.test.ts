import { createElement } from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AppProvider } from "@/context/AppContext";
import { LeftNav } from "@/components/LeftNav";
import { surfaceForSession, withoutSecret } from "@/lib/appLogin";
import { SUGGESTED_QUESTIONS } from "@/lib/fixtures";
import { STARTER_QUESTIONS } from "@/lib/starterQuestions";
import { InsightsPageView } from "@/pages/InsightsPage";
import { SignInPage } from "@/pages/SignInPage";
import { StatusPage } from "@/pages/StatusPage";

const here = dirname(fileURLToPath(import.meta.url));

describe("app login surface", () => {
  it("flag off (404) stays in the app", () => {
    expect(surfaceForSession(404, null)).toBe("app");
    expect(surfaceForSession(0, null)).toBe("app");
  });

  it("an enabled session that is signed out shows sign-in", () => {
    expect(surfaceForSession(200, { enabled: true, authenticated: false })).toBe("sign-in");
    expect(surfaceForSession(401, { enabled: true })).toBe("sign-in");
  });

  it("a signed-in session stays in the app", () => {
    expect(
      surfaceForSession(200, { enabled: true, authenticated: true, email: "a@b.c", role: "viewer" }),
    ).toBe("app");
  });

  it("does not keep a password that a response echoed", () => {
    expect(withoutSecret("bad test-password", "test-password")).toBe("bad ");
  });
});

describe("sign-in and status pages", () => {
  it("renders email, password, and request access", () => {
    const html = renderToStaticMarkup(
      createElement(SignInPage, {
        error: null,
        filed: false,
        onSubmit: () => {},
        onRequest: () => {},
      }),
    );
    expect(html).toContain("netie");
    expect(html).toContain("sign-in-form");
    expect(html).toContain("request-access-form");
    expect(html).toContain("Email");
    expect(html).toContain("Password");
    expect(html).toContain("Request access");
    expect(html).toContain("Nothing is emailed");
    expect(html).not.toContain("401 Authorization Required");
  });

  it("renders a friendly 401 and 403", () => {
    const denied = renderToStaticMarkup(createElement(StatusPage, { status: 401 }));
    const forbidden = renderToStaticMarkup(createElement(StatusPage, { status: 403 }));
    expect(denied).toContain("page-401");
    expect(denied).toContain("Sign in to continue");
    expect(denied).toContain("browser password");
    expect(forbidden).toContain("page-403");
    expect(forbidden).toContain("You do not have access");
    expect(forbidden).toContain("Nothing is emailed");
  });
});

describe("ask nav labels and shared starters", () => {
  it("home and studio starters are the same list", () => {
    expect(SUGGESTED_QUESTIONS).toBe(STARTER_QUESTIONS);
    expect(STARTER_QUESTIONS).toContain("What is our total spend by supplier country?");
    expect(STARTER_QUESTIONS).toContain("Which locations are cold storage?");
    expect(STARTER_QUESTIONS.some((q) => q.includes("categoty"))).toBe(true);
  });

  it("ask-mode icons carry the names Chat, Spaces, Library, and Trust", () => {
    const html = renderToStaticMarkup(
      createElement(MemoryRouter, null, createElement(AppProvider, null, createElement(LeftNav))),
    );
    expect(html).toContain("nav-label-chat");
    expect(html).toContain("Chat");
    expect(html).toContain("Spaces");
    expect(html).toContain("Library");
    expect(html).toContain("Trust");
    expect(html).toContain('aria-label="Chat"');
    const src = readFileSync(join(here, "../components/LeftNav.tsx"), "utf8");
    expect(src).not.toContain("\u2014");
  });

  it("insights does not render the Ask home when the feature is off", () => {
    const html = renderToStaticMarkup(
      createElement(
        MemoryRouter,
        null,
        createElement(InsightsPageView, { view: { kind: "missing" } }),
      ),
    );
    expect(html).toContain("insights-unavailable");
    expect(html).toContain("Insights is not on");
    expect(html).not.toContain("Ask your company's data");
  });
});
