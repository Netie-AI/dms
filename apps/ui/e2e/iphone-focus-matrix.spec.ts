import { expect, test, type Page } from "@playwright/test";

/**
 * Focus survives a crossing of lg, for EVERY focusable control (round 10 of the PR #510 checker).
 *
 * Round 9 fixed the controls a person listed (nav links, Sources, the sidebar toggle) and a checker
 * then found the next ten by hand. This spec does not list controls. For each state it asks the
 * page for every element the browser itself would put in the tab order (`tabIndex >= 0`) that is
 * visible, not inert, not aria-hidden and not hidden, focuses each one in turn, crosses the lg
 * breakpoint, waits for the hand-off, and asserts:
 *
 *   1. focus is not on <body> (unless the control is in EXEMPT, with a reason);
 *   2. the element that has focus is visible, not inert, not aria-hidden, not hidden, and has an
 *      accessible name;
 *   3. a control that survived the crossing still has focus (it was never moved);
 *   4. where the new DOM has an equivalent control (same data-testid, else same role + accessible
 *      name, else same href) focus is on one of them, and on one in the same landmark when the new
 *      DOM has one there.
 *
 * Nothing here knows which components swap on `useIsLg`. A control added tomorrow is in the matrix
 * tomorrow. MUST (per state) is a canary: names that have to be in the enumeration, so a filter
 * that quietly skipped a kind of control fails here instead of passing with fewer rows.
 *
 * No waitForTimeout. `cross` waits for the media-query change and three painted frames.
 */

type Size = { width: number; height: number };
const PHONE: Size = { width: 390, height: 844 };
const MID: Size = { width: 800, height: 800 };
const LG_LOW: Size = { width: 1024, height: 800 };
const LG: Size = { width: 1280, height: 800 };
const ASK = /Ask about your data/;

/** A four-row answer with a chart, so the table, the figure button, the chart bars and every tool exist. */
const DEMO = {
  answer_id: "ans_demo_revenue",
  text: "Total outbound revenue was RM 25,654.50.",
  values: [{ id: "v_rev", value: 25654.5, unit: "MYR", label: "Total revenue" }],
  badge: "L2_VALIDATED",
  abstained: false,
  sql_used:
    "SELECT sku, SUM(quantity_kg * unit_cost_myr) AS revenue_myr FROM transactions WHERE txn_type = 'outbound' GROUP BY sku",
  assumptions: ["outbound transactions only", "demo warehouse, not a live Cortex answer"],
  as_of: "2026-11-14T03:00:00Z",
  contributing_sources: [
    {
      ref_id: "ref_txn",
      container: "transactions",
      kind: "sql",
      row_count: 10,
      contribution: 1,
      origin_uri: "duckdb://dms_demo/transactions",
      space_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
      extracted_at: null,
      source_kind: null,
    },
  ],
  drillthrough_token: "demo_no_drillthrough",
  audit_id: "ans_demo_revenue",
  ask_mode: "demo",
  demo_fallback_used: false,
  space_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  rows: [
    { sku: "SKU-ALPHA", revenue_myr: 12000.5 },
    { sku: "SKU-BETA", revenue_myr: 8654 },
    { sku: "SKU-GAMMA", revenue_myr: 3500 },
    { sku: "SKU-DELTA", revenue_myr: 1500 },
  ],
  chart: { kind: "bar", x: "sku", y: "revenue_myr", title: "Revenue by SKU" },
  suggestions: ["What was total revenue?", "Top 5 selling SKUs by revenue"],
  route: null,
  audit_receipt: {
    include: { status: "rows", row_count: 4, rows: [{ sku: "SKU-ALPHA", revenue_myr: 12000.5 }], why: "executed result rows" },
    exclude: { status: "filters", reasons: [{ kind: "where", detail: "txn_type = 'outbound'" }], why: "extracted from executed SQL" },
    unsure: { status: "none", abstained: false, badge: "L2_VALIDATED", why: "not abstained; badge=L2_VALIDATED" },
  },
};

// ---------------------------------------------------------------------------------------------
// In the page
// ---------------------------------------------------------------------------------------------

type Desc = {
  key: string;
  tag: string;
  role: string;
  name: string;
  testid: string | null;
  href: string | null;
  controls: string | null;
  lm: string;
  occ: number;
};

type Verdict = {
  body: boolean;
  survived: boolean;
  kept: boolean;
  active: Desc | null;
  problems: string[];
  eqN: number;
  inEq: boolean;
  sameLmN: number;
  inSameLm: boolean;
};

/**
 * Installed once per page load as window.__fm. Written as one function so Playwright serialises it
 * whole. The filter is deliberately independent of the product code under test.
 */
function installHelpers() {
  const LM =
    'nav,main,header,aside,footer,article,section,[role="dialog"],[role="region"],[role="navigation"],[data-testid="tab-bar"]';
  const txt = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();
  const roleOf = (el: Element): string => {
    const r = el.getAttribute("role");
    if (r) return r;
    const t = el.tagName.toLowerCase();
    if (t === "a") return "link";
    if (t === "button") return "button";
    if (t === "summary") return "summary";
    if (t === "select") return "combobox";
    if (t === "textarea") return "textbox";
    if (t === "input") return (el as HTMLInputElement).type === "checkbox" ? "checkbox" : "textbox";
    return t;
  };
  const nameOf = (el: Element): string => {
    const al = txt(el.getAttribute("aria-label"));
    if (al) return al;
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const t = txt(
        lb
          .split(/\s+/)
          .map((id) => document.getElementById(id)?.textContent ?? "")
          .join(" "),
      );
      if (t) return t;
    }
    const labels = (el as HTMLInputElement).labels;
    if (labels && labels.length) {
      const t = txt([...labels].map((l) => l.textContent).join(" "));
      if (t) return t;
    }
    if (!["SELECT", "TEXTAREA", "INPUT"].includes(el.tagName)) {
      const t = txt(el.textContent);
      if (t) return t;
    }
    return txt(el.getAttribute("title")) || txt(el.getAttribute("placeholder"));
  };
  const lmOf = (el: Element): string => {
    const n = el.parentElement?.closest(LM) ?? null;
    if (!n) return "root";
    if (n.getAttribute("data-testid") === "tab-bar") return "tabbar";
    if (n.tagName === "NAV" && n.parentElement?.closest('[role="dialog"]')) return "nav";
    return n.getAttribute("role") === "dialog" ? "dialog" : n.tagName.toLowerCase();
  };
  const navish = (lm: string) => lm === "nav" || lm === "tabbar";
  const problems = (el: Element): string[] => {
    const p: string[] = [];
    if (!el.isConnected) p.push("detached");
    if (el.getClientRects().length === 0) p.push("not-laid-out");
    // Also false for content a closed <details> or content-visibility skips, and for visibility:hidden.
    if (!el.checkVisibility({ visibilityProperty: true, contentVisibilityAuto: true })) p.push("not-visible");
    if (el.closest("[inert]")) p.push("inert");
    if (el.closest('[aria-hidden="true"]')) p.push("aria-hidden");
    if (el.closest("[hidden]")) p.push("hidden");
    if ((el as HTMLButtonElement).disabled) p.push("disabled");
    if (!nameOf(el)) p.push("no-accessible-name");
    return p;
  };
  const usable = (el: Element) => problems(el).filter((x) => x !== "no-accessible-name").length === 0;
  const controls = (): Element[] =>
    [...document.querySelectorAll("*")].filter(
      (el) => (el as HTMLElement).tabIndex >= 0 && usable(el) && !el.closest("[data-fm-decoy]"),
    );
  const keyOf = (el: Element) =>
    [el.tagName.toLowerCase(), roleOf(el), el.getAttribute("data-testid") ?? "", nameOf(el), el.getAttribute("href") ?? ""].join("|");
  const describe = (el: Element, all: Element[]): Desc => {
    const key = keyOf(el);
    return {
      key,
      tag: el.tagName.toLowerCase(),
      role: roleOf(el),
      name: nameOf(el),
      testid: el.getAttribute("data-testid"),
      href: el.getAttribute("href"),
      controls: el.getAttribute("aria-controls"),
      lm: lmOf(el),
      occ: all.filter((c) => keyOf(c) === key).indexOf(el),
    };
  };
  const w = window as unknown as Record<string, unknown>;
  w.__fm = {
    list(): Desc[] {
      const all = controls();
      return all.map((c) => describe(c, all));
    },
    /** Focus the control with this key and occurrence; remember it as the lost control. */
    focusOne(want: Desc): { ok: boolean; why: string; desc: Desc | null } {
      const all = controls();
      const hit = all.filter((c) => keyOf(c) === want.key)[want.occ];
      if (!hit) return { ok: false, why: "not found at the start width", desc: null };
      (hit as HTMLElement).focus();
      w.__lostEl = hit;
      if (document.activeElement !== hit) return { ok: false, why: "focus() did not take", desc: null };
      return { ok: true, why: "", desc: describe(hit, all) };
    },
    verdict(lost: Desc): Verdict {
      const a = document.activeElement;
      const lostEl = w.__lostEl as Element | undefined;
      const survived = !!lostEl && usable(lostEl);
      const v: Verdict = {
        body: !a || a === document.body,
        survived,
        kept: survived && a === lostEl,
        active: null,
        problems: [],
        eqN: 0,
        inEq: false,
        sameLmN: 0,
        inSameLm: false,
      };
      if (v.body || !a) return v;
      const all = controls();
      v.active = describe(a, all);
      v.problems = problems(a);
      const eq = all.filter(
        (c) =>
          (lost.testid !== null && c.getAttribute("data-testid") === lost.testid) ||
          (lost.name !== "" && roleOf(c) === lost.role && nameOf(c) === lost.name) ||
          // A button that opens the same region (the phone's More and the lg sidebar toggle).
          (lost.controls !== null && c.getAttribute("aria-controls") === lost.controls) ||
          // An href only says "the same page's link" for navigation: the audit link inside an answer
          // is not where the Audit nav link goes, whatever its href.
          (lost.href !== null &&
            navish(lost.lm) &&
            navish(lmOf(c)) &&
            c.getAttribute("href") === lost.href),
      );
      v.eqN = eq.length;
      v.inEq = eq.includes(a);
      const same = eq.filter((c) => lmOf(c) === lost.lm);
      v.sameLmN = same.length;
      v.inSameLm = same.includes(a);
      return v;
    },
  };
}

type Fm = {
  list(): Desc[];
  focusOne(want: Desc): { ok: boolean; why: string; desc: Desc | null };
  verdict(lost: Desc): Verdict;
};
const fmList = (page: Page) => page.evaluate(() => (window as unknown as { __fm: Fm }).__fm.list());
const fmFocus = (page: Page, want: Desc) =>
  page.evaluate((d) => (window as unknown as { __fm: Fm }).__fm.focusOne(d), want);
const fmVerdict = (page: Page, lost: Desc) =>
  page.evaluate((d) => (window as unknown as { __fm: Fm }).__fm.verdict(d), lost);

/** Resize across lg; resolve when the page has seen the change and painted three frames. */
async function cross(page: Page, size: Size) {
  await page.evaluate(() => {
    (window as unknown as { __crossed: Promise<void> }).__crossed = new Promise<void>((resolve) =>
      window.matchMedia("(min-width: 1024px)").addEventListener("change", () => resolve(), { once: true }),
    );
  });
  await page.setViewportSize(size);
  await page.evaluate(async () => {
    await (window as unknown as { __crossed: Promise<void> }).__crossed;
    for (let i = 0; i < 3; i++) await new Promise<void>((r) => requestAnimationFrame(() => r()));
  });
}

/** Counts every programmatic focus() so a test can tell "left alone" from "moved and moved back". */
async function countFocusCalls(page: Page) {
  await page.addInitScript(() => {
    const w = window as unknown as { __focusCalls: number };
    w.__focusCalls = 0;
    for (const proto of [HTMLElement.prototype, SVGElement.prototype]) {
      const orig = proto.focus;
      proto.focus = function (this: HTMLElement | SVGElement, ...a: []) {
        w.__focusCalls++;
        return orig.apply(this, a);
      };
    }
  });
}
const focusCalls = (page: Page) =>
  page.evaluate(() => (window as unknown as { __focusCalls: number }).__focusCalls);

const activeInfo = (page: Page) =>
  page.evaluate(() => {
    const a = document.activeElement as HTMLElement;
    return {
      body: a === document.body,
      tag: a.tagName.toLowerCase(),
      href: a.getAttribute("href"),
      label: a.getAttribute("aria-label"),
      inNav: !!a.closest("nav#primary-nav"),
      inTabs: !!a.closest('[data-testid="tab-bar"]'),
      inArticle: !!a.closest('[data-testid="answer-message"]'),
    };
  });

// ---------------------------------------------------------------------------------------------
// States
// ---------------------------------------------------------------------------------------------

type Mode = "cream" | "graphite";
type State = {
  name: string;
  mode: Mode;
  answered?: boolean;
  drawer?: boolean;
  sheet?: boolean;
  pairs: [Size, Size][];
  /** Names that must appear in the enumeration at the start width: the filter's canary. */
  must: (string | RegExp)[];
};

const ANSWER_MUST = [
  "Download CSV",
  "Download Excel",
  "Power BI",
  "Superset",
  "Share answer",
  "Check accuracy",
  "Trust runs",
  "Copilot prompts",
  "Show me why",
  "Details",
  "RM 25,654.50",
  /^SKU-ALPHA: /,
  /^⟨sql⟩$/,
  /^⟨audit⟩$/,
];

const STATES: State[] = [
  { name: "lg cream idle", mode: "cream", pairs: [[LG, MID], [LG_LOW, PHONE]], must: ["Toggle sidebar", "Space", "Manage Spaces", "Collapse sources", "Switch to operator mode"] },
  { name: "lg graphite idle", mode: "graphite", pairs: [[LG, MID], [LG_LOW, PHONE]], must: ["Toggle sidebar", "Role", "Manage Spaces", "Switch to ask mode", /Ontology/, /Amend/] },
  { name: "lg cream answered", mode: "cream", answered: true, pairs: [[LG, MID], [LG_LOW, PHONE]], must: [...ANSWER_MUST, "Toggle sidebar"] },
  { name: "lg graphite answered", mode: "graphite", answered: true, pairs: [[LG, MID], [LG_LOW, PHONE]], must: [...ANSWER_MUST, "Role"] },
  { name: "phone cream idle", mode: "cream", pairs: [[PHONE, LG], [PHONE, LG_LOW], [MID, LG]], must: ["Library", "Switch to operator mode"] },
  { name: "phone graphite idle", mode: "graphite", pairs: [[PHONE, LG], [PHONE, LG_LOW], [MID, LG]], must: ["More pages", "Library", "Switch to ask mode"] },
  { name: "phone graphite drawer open", mode: "graphite", drawer: true, pairs: [[PHONE, LG], [PHONE, LG_LOW]], must: [/Library/, "Role", /Ontology/] },
  { name: "phone cream answered", mode: "cream", answered: true, pairs: [[PHONE, LG], [PHONE, LG_LOW], [MID, LG]], must: [...ANSWER_MUST, "Library"] },
  { name: "phone graphite answered", mode: "graphite", answered: true, pairs: [[PHONE, LG], [PHONE, LG_LOW], [MID, LG]], must: [...ANSWER_MUST, "More pages"] },
  { name: "phone cream sources sheet open", mode: "cream", sheet: true, pairs: [[PHONE, LG], [PHONE, LG_LOW]], must: ["Collapse sources"] },
];

/**
 * Controls that may end on <body> after a crossing. Each entry says why. Matched against
 * `${state} :: ${from}->${to} :: ${key}`. Empty unless a real case needs it: an entry is a promise
 * that no control at the other width can take the focus, composer included.
 */
const EXEMPT: { match: RegExp; reason: string }[] = [];

const exemptReason = (id: string) => EXEMPT.find((e) => e.match.test(id))?.reason;

/**
 * Controls whose focus lands on something other than the equivalent control, on purpose. Focus is
 * still required to be on a usable control, just not on the "same" one.
 */
const IDENTITY_EXEMPT: { match: RegExp; reason: string }[] = [
  {
    match: /phone graphite drawer open :: \d+->\d+ :: select\|combobox\|\|Role\|/,
    reason:
      "the drawer's Role select has a twin in the lg top bar, but iphone-focus-cross.spec.ts " +
      "('widen with focus on the drawer's select') pins the owner contract: the LeftNav hands any " +
      "drawer control with no matching link to the docked nav's first link",
  },
];
const identityExempt = (id: string) => IDENTITY_EXEMPT.some((e) => e.match.test(id));

async function arm(page: Page, st: State) {
  if (st.drawer) {
    const dialog = page.getByRole("dialog", { name: "Primary navigation" });
    if (!(await dialog.isVisible())) await page.getByRole("button", { name: "More pages" }).click();
    await expect(dialog).toBeVisible();
  }
  if (st.sheet) {
    if (!(await page.getByRole("dialog").isVisible())) await page.getByTestId("source-panel-open").click();
    await expect(page.getByRole("dialog")).toBeVisible();
  }
}

async function setup(page: Page, st: State, start: Size, mock = true) {
  await countFocusCalls(page);
  if (st.answered && mock) {
    await page.route("**/v1/chat/ask", (route) => route.fulfill({ json: DEMO }));
  }
  await page.setViewportSize(start);
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  if (st.mode === "graphite") {
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    await expect(page.getByRole("button", { name: "Switch to operator mode" })).toHaveCount(0);
  }
  if (st.answered) {
    await page.getByPlaceholder(ASK).fill("What was total revenue?");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByTestId("answer-message").last()).toBeVisible({ timeout: 20_000 });
    await page.waitForLoadState("networkidle");
  }
  await page.evaluate(installHelpers);
  await arm(page, st);
}

const label = (d: Desc) => `${d.tag}[${d.role}] "${d.name}"${d.href ? ` href=${d.href}` : ""}${d.testid ? ` #${d.testid}` : ""}${d.occ ? ` (#${d.occ})` : ""}`;

test.use({ video: "off", screenshot: "off" });

test.describe("every focusable control survives a crossing of lg", () => {
  test.setTimeout(240_000);

  for (const st of STATES) {
    for (const [from, to] of st.pairs) {
      test(`${st.name}: ${from.width} to ${to.width}`, async ({ page }) => {
        await setup(page, st, from);
        const start = await fmList(page);

        // The canary: the filter must not have skipped a kind of control.
        const names = start.map((d) => d.name);
        for (const m of st.must) {
          const hit = names.some((n) => (typeof m === "string" ? n === m : m.test(n)));
          expect(hit, `${st.name} @${from.width}: no control named ${m} in the enumeration of ${names.length}: ${names.join(" | ")}`).toBe(true);
        }
        expect(start.length, "enumeration is empty").toBeGreaterThanOrEqual(2);

        const failures: string[] = [];
        let exempted = 0;
        for (const want of start) {
          const id = `${st.name} :: ${from.width}->${to.width} :: ${want.key}`;
          const f = await fmFocus(page, want);
          if (!f.ok || !f.desc) {
            failures.push(`SETUP ${label(want)}: ${f.why}`);
            continue;
          }
          await cross(page, to);
          const v = await fmVerdict(page, f.desc);
          const bad: string[] = [];
          if (v.body) {
            const why = exemptReason(id);
            if (why) exempted++;
            else bad.push("focus fell to <body>");
          } else {
            if (v.problems.length) bad.push(`focus is on an unusable control ${v.active ? label(v.active) : "?"}: ${v.problems.join(",")}`);
            if (v.survived && !v.kept) bad.push(`the control survived but focus moved to ${v.active ? label(v.active) : "?"}`);
            const sameOne = !identityExempt(id);
            if (sameOne && !v.survived && v.eqN > 0 && !v.inEq) bad.push(`an equivalent exists (${v.eqN}) but focus is on ${v.active ? label(v.active) : "?"}`);
            if (sameOne && !v.survived && v.sameLmN > 0 && !v.inSameLm) bad.push(`an equivalent exists in the same landmark (${f.desc.lm}) but focus is on ${v.active ? label(v.active) : "?"} in ${v.active?.lm}`);
          }
          if (bad.length) failures.push(`${label(f.desc)} [${f.desc.lm}]: ${bad.join("; ")}`);
          await cross(page, from);
          await arm(page, st);
        }
        const line = `MATRIX ${st.name} ${from.width}->${to.width}: ${failures.length} of ${start.length} controls fail (${exempted} exempt)`;
        console.log(line);
        expect(failures, `${line}\n${failures.join("\n")}`).toEqual([]);
      });
    }
  }
});

// ---------------------------------------------------------------------------------------------
// A control that no owner and no equivalent can take: the generic fallback must stay honest
// ---------------------------------------------------------------------------------------------

test.describe("the fallback never lands on a target a person could not use", () => {
  test("an inert or aria-hidden decoy with the same name is skipped for the real control", async ({ page }) => {
    // Decoys sit first in the document and are laid out, so a fallback that matched by name alone
    // would take them. The real equivalent is the nav link; the decoys are not controls anyone has.
    const st: State = { name: "decoy", mode: "cream", pairs: [], must: [] };
    await setup(page, st, PHONE);
    await page.evaluate(() => {
      const mk = (configure: (d: HTMLElement) => void) => {
        const d = document.createElement("div");
        d.setAttribute("data-fm-decoy", "");
        d.setAttribute("style", "position:fixed;top:0;left:0;z-index:99999");
        d.innerHTML = '<a href="/library">Library</a>';
        configure(d);
        document.body.prepend(d);
      };
      mk((d) => d.setAttribute("inert", ""));
      mk((d) => d.setAttribute("aria-hidden", "true"));
      mk((d) => (d.style.visibility = "hidden"));
    });
    const tab = page.getByTestId("tab-bar").getByRole("link", { name: "Library" });
    await tab.focus();
    await expect(tab).toBeFocused();
    await cross(page, LG);
    const a = await activeInfo(page);
    expect(a, JSON.stringify(a)).toMatchObject({ body: false, tag: "a", href: "/library", inNav: true });
  });

  test("a same-named control in another landmark does not beat the one in the lost control's landmark", async ({ page }) => {
    const st: State = { name: "landmark", mode: "cream", answered: true, pairs: [], must: [] };
    await setup(page, st, LG);
    // A decoy that is usable (visible, not inert) but in a different landmark and earlier in the DOM.
    await page.evaluate(() => {
      const nav = document.createElement("nav");
      nav.setAttribute("data-fm-decoy", "");
      nav.setAttribute("aria-label", "Decoy");
      nav.setAttribute("style", "position:fixed;top:0;left:0;z-index:99999");
      nav.innerHTML = '<button type="button">Download CSV</button>';
      document.body.prepend(nav);
    });
    const csv = page.getByTestId("answer-message").getByRole("button", { name: "Download CSV" });
    await csv.focus();
    await expect(csv).toBeFocused();
    await cross(page, PHONE);
    const a = await activeInfo(page);
    expect(a.inArticle, JSON.stringify(a)).toBe(true);
    await expect(csv).toBeFocused();
  });
});

// ---------------------------------------------------------------------------------------------
// A person who is on nothing is left on nothing
// ---------------------------------------------------------------------------------------------

/** A point on the page whose element is not, and is not inside, anything focusable. */
async function blankPoint(page: Page) {
  return page.evaluate(() => {
    const interactive = 'a,button,input,select,textarea,summary,label,[tabindex],[role="button"],[role="dialog"]';
    for (let y = 60; y < innerHeight - 10; y += 14) {
      for (let x = innerWidth - 14; x > 8; x -= 14) {
        const el = document.elementFromPoint(x, y);
        if (el && el !== document.documentElement && !el.closest(interactive)) return { x, y };
      }
    }
    return null;
  });
}

test.describe("a deliberate blur is not undone by a later crossing", () => {
  for (const [from, to, where] of [
    [LG, PHONE, "a link in the lg nav"],
    [PHONE, LG, "a tab bar link"],
  ] as const) {
    test(`click on blank space, then ${from.width} to ${to.width}: nothing is focused afterwards (${where})`, async ({ page }) => {
      const st: State = { name: "blank", mode: "graphite", pairs: [], must: [] };
      await setup(page, st, from);
      const link = from.width >= 1024 ? page.getByTestId("left-nav").getByRole("link").nth(1) : page.getByTestId("tab-bar").getByRole("link").nth(1);
      await link.focus();
      await expect(link).toBeFocused();
      const pt = await blankPoint(page);
      expect(pt, "no blank point to click").not.toBeNull();
      await page.mouse.click(pt!.x, pt!.y);
      expect((await activeInfo(page)).body, "the click should have blurred the link").toBe(true);
      const before = await focusCalls(page);
      await cross(page, to);
      expect((await activeInfo(page)).body, "a crossing must not give focus to a person who is on nothing").toBe(true);
      expect(await focusCalls(page)).toBe(before);
    });
  }

  test("an explicit blur() of a control, then a crossing: nothing is focused afterwards", async ({ page }) => {
    const st: State = { name: "blur", mode: "graphite", pairs: [], must: [] };
    await setup(page, st, LG);
    const link = page.getByTestId("left-nav").getByRole("link", { name: "Amend" });
    await link.focus();
    await link.evaluate((el) => (el as HTMLElement).blur());
    const before = await focusCalls(page);
    await cross(page, PHONE);
    expect((await activeInfo(page)).body).toBe(true);
    expect(await focusCalls(page)).toBe(before);
  });
});

test.describe("a stale last-focused control cannot pull focus", () => {
  test("a control the app hid (no crossing), then a crossing: focus stays on nothing", async ({ page }) => {
    // The control is hidden, not removed, and not by a crossing. The browser drops focus on <body>.
    // Later the width changes. The person was on nothing, so nothing may be focused.
    const st: State = { name: "stale", mode: "graphite", pairs: [], must: [] };
    await setup(page, st, LG);
    const link = page.getByTestId("left-nav").getByRole("link", { name: "Amend" });
    await link.focus();
    await expect(link).toBeFocused();
    await page.evaluate(() => {
      (document.querySelector("nav#primary-nav") as HTMLElement).style.display = "none";
    });
    await page.waitForFunction(() => document.activeElement === document.body);
    // Let the focus-out task settle before the width changes.
    await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))));
    await page.evaluate(() => {
      (document.querySelector("nav#primary-nav") as HTMLElement).style.display = "";
    });
    await page.waitForFunction(() => document.activeElement === document.body);
    const before = await focusCalls(page);
    await cross(page, PHONE);
    expect((await activeInfo(page)).body, "stale last-focused control took focus").toBe(true);
    expect(await focusCalls(page)).toBe(before);
  });

  test("a control that was removed (app, not crossing) and focus on body: a crossing takes nothing", async ({ page }) => {
    const st: State = { name: "stale-removed", mode: "cream", answered: true, pairs: [], must: [] };
    await setup(page, st, LG);
    const csv = page.getByTestId("answer-message").getByRole("button", { name: "Download CSV" });
    await csv.focus();
    await csv.evaluate((el) => el.remove());
    await page.waitForFunction(() => document.activeElement === document.body);
    const before = await focusCalls(page);
    await cross(page, PHONE);
    expect((await activeInfo(page)).body).toBe(true);
    expect(await focusCalls(page)).toBe(before);
  });
});

// ---------------------------------------------------------------------------------------------
// Repeated round trips
// ---------------------------------------------------------------------------------------------

test.describe("a repeated round trip keeps the person on the same page's link", () => {
  test("50 round trips from lg with no waits, then a settle: still on the Library link", async ({ page }) => {
    const st: State = { name: "round50-lg", mode: "graphite", pairs: [], must: [] };
    await setup(page, st, LG);
    const lib = page.getByTestId("left-nav").getByRole("link", { name: "Library" });
    await lib.focus();
    await expect(lib).toBeFocused();
    for (let i = 0; i < 50; i++) {
      await page.setViewportSize(PHONE);
      await page.setViewportSize(LG);
    }
    // The settle: one observed crossing, so the last hand-off has run.
    await cross(page, PHONE);
    const a = await activeInfo(page);
    expect(a, JSON.stringify(a)).toMatchObject({ body: false, tag: "a", href: "/library", inTabs: true });
    await cross(page, LG);
    const b = await activeInfo(page);
    expect(b, JSON.stringify(b)).toMatchObject({ body: false, tag: "a", href: "/library", inNav: true });
  });

  test("drawer Library link at 390, then 1280, 390, 1280, 390, 1280: every crossing hands it over", async ({ page }) => {
    const st: State = { name: "round", mode: "graphite", pairs: [], must: [] };
    await setup(page, st, PHONE);
    await page.getByRole("button", { name: "More pages" }).click();
    const lib = page.getByRole("dialog", { name: "Primary navigation" }).locator('a[href="/library"]');
    await lib.focus();
    await expect(lib).toBeFocused();
    const sequence = [LG, PHONE, LG, PHONE, LG, PHONE];
    for (const [i, size] of sequence.entries()) {
      const before = await focusCalls(page);
      await cross(page, size);
      const a = await activeInfo(page);
      expect(a, `crossing ${i + 1} to ${size.width}: ${JSON.stringify(a)}`).toMatchObject({ body: false, tag: "a", href: "/library" });
      expect(size.width >= 1024 ? a.inNav : a.inTabs, `crossing ${i + 1}: ${JSON.stringify(a)}`).toBe(true);
      expect(await focusCalls(page), `crossing ${i + 1} made no focus() call`).toBeGreaterThan(before);
    }
  });

  test("50 round trips with no waits, then a settle: still on the Library link", async ({ page }) => {
    const st: State = { name: "round50", mode: "graphite", pairs: [], must: [] };
    await setup(page, st, PHONE);
    await page.getByRole("button", { name: "More pages" }).click();
    const lib = page.getByRole("dialog", { name: "Primary navigation" }).locator('a[href="/library"]');
    await lib.focus();
    await expect(lib).toBeFocused();
    for (let i = 0; i < 50; i++) {
      await page.setViewportSize(LG);
      await page.setViewportSize(PHONE);
    }
    // The settle: one observed crossing, so the last hand-off has run.
    await cross(page, LG);
    const a = await activeInfo(page);
    expect(a, JSON.stringify(a)).toMatchObject({ body: false, tag: "a", href: "/library", inNav: true });
    await cross(page, PHONE);
    const b = await activeInfo(page);
    expect(b, JSON.stringify(b)).toMatchObject({ body: false, tag: "a", href: "/library", inTabs: true });
  });
});

test.describe("a hand-off is never given an unusable target", () => {
  test("an owner's target that is inert at the new width is not used: focus goes to the composer", async ({ page }) => {
    // The LeftNav hands a lost nav link to its phone tab. Here the tab bar is inert the moment it
    // appears (as it is under an open drawer), so focusing it would do nothing and leave <body>.
    const st: State = { name: "inert-owner", mode: "cream", pairs: [], must: [] };
    await setup(page, st, LG);
    const link = page.locator('nav#primary-nav a[href="/library"]');
    await link.focus();
    await expect(link).toBeFocused();
    await page.evaluate(() => {
      new MutationObserver(() => {
        document.querySelector('[data-testid="tab-bar"]')?.setAttribute("inert", "");
      }).observe(document.body, { childList: true, subtree: true });
    });
    await cross(page, PHONE);
    const a = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement;
      return { tag: el.tagName.toLowerCase(), inert: !!el.closest("[inert]") };
    });
    expect(a, JSON.stringify(a)).toEqual({ tag: "textarea", inert: false });
  });
});
