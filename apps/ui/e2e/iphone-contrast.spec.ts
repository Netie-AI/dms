import { expect, test, type Page } from "@playwright/test";

/**
 * IPHONE-CONTRAST-01: on a phone, every ENABLED piece of text meets WCAG AA (4.5:1, 3:1 for large
 * text) in both modes, on the pages a phone user lands on. Read from computed styles, not class
 * strings: the effective background is composited up the ancestor chain, opacity is applied, and
 * disabled controls are exempt (WCAG 1.4.3 exempts inactive components).
 *
 * Measured before the change (graphite, 390x844, enabled text only): Studio "+" 1.34:1 (white on
 * #00ff87), Library selected-row count and bullet 3.43:1 (muted on accent-soft), Studio refusal
 * reason 3.9:1 (muted on warn-soft). Cream had no enabled failure on a phone.
 * Desktop is deliberately not asserted here: it is pinned unchanged and has its own failures.
 */

const ROUTES = ["/", "/library", "/studio", "/spaces", "/ontology", "/audit", "/runs", "/trust", "/amend", "/admin"];

type Fail = { text: string; ratio: number; need: number; tag: string };

/** Runs in the page. Returns the enabled text nodes below their WCAG AA ratio. */
const audit = (): Fail[] => {
  type C = { r: number; g: number; b: number; a: number };
  const parse = (s: string): C | null => {
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p[3] ?? 1 };
  };
  const over = (f: C, b: C): C => ({
    r: f.r * f.a + b.r * (1 - f.a),
    g: f.g * f.a + b.g * (1 - f.a),
    b: f.b * f.a + b.b * (1 - f.a),
    a: 1,
  });
  const lum = (c: C) => {
    const t = [c.r, c.g, c.b].map((x) => {
      x /= 255;
      return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * t[0] + 0.7152 * t[1] + 0.0722 * t[2];
  };
  const bgOf = (el: Element): C => {
    const stack: C[] = [];
    for (let n: Element | null = el; n; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0) stack.push(c);
      if (c && c.a === 1) break;
    }
    let base: C = { r: 255, g: 255, b: 255, a: 1 };
    for (const c of stack.reverse()) base = over(c, base);
    return base;
  };
  const out: Fail[] = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set<Element>();
  for (let t = w.nextNode(); t; t = w.nextNode()) {
    const text = (t.textContent ?? "").trim();
    const el = t.parentElement;
    if (!text || !el || seen.has(el)) continue;
    seen.add(el);
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || el.closest('[inert],[aria-hidden="true"]') || !el.getClientRects().length) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    if (el.closest("button,input,select,textarea,[aria-disabled='true']")?.matches(":disabled,[aria-disabled='true']")) continue;
    const fg0 = parse(cs.color);
    if (!fg0) continue;
    let op = 1;
    for (let n: Element | null = el; n; n = n.parentElement) op *= Number(getComputedStyle(n).opacity);
    const bg = bgOf(el);
    const fg = over({ ...fg0, a: fg0.a * op }, bg);
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
    const ratio = (a + 0.05) / (b + 0.05);
    const px = parseFloat(cs.fontSize);
    const large = px >= 24 || (px >= 18.66 && Number(cs.fontWeight) >= 700);
    const need = large ? 3 : 4.5;
    if (ratio < need) out.push({ text: text.slice(0, 40), ratio: Math.round(ratio * 100) / 100, need, tag: el.tagName.toLowerCase() });
  }
  return out;
};

async function open(page: Page, route: string, mode: "cream" | "graphite") {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(route);
  await page.waitForLoadState("networkidle");
  if (mode === "graphite") {
    await page.getByRole("button", { name: "Switch to operator mode" }).click();
    await expect(page.getByRole("button", { name: "Switch to operator mode" })).toHaveCount(0);
  }
}

for (const mode of ["cream", "graphite"] as const) {
  for (const route of ROUTES) {
    test(`${mode} ${route}: enabled text meets AA at 390x844`, async ({ page }) => {
      await open(page, route, mode);
      // Polled: the mode switch fades colours in over a few frames, and a frame mid-fade is not a fail.
      await expect.poll(() => page.evaluate(audit), { timeout: 4000 }).toEqual([]);
    });
  }
}

test("the audit itself can fail: a low-contrast enabled paragraph is reported, a disabled one is not", async ({ page }) => {
  await open(page, "/", "graphite");
  await page.evaluate(() => {
    const p = document.createElement("p");
    p.id = "probe-low";
    p.textContent = "low contrast probe";
    p.style.cssText = "color:#555;background:#111;font-size:14px";
    document.body.append(p);
    const b = document.createElement("button");
    b.disabled = true;
    b.textContent = "disabled probe";
    b.style.cssText = "color:#555;background:#111;font-size:14px";
    document.body.append(b);
  });
  const rows = await page.evaluate(audit);
  expect(rows.map((r) => r.text)).toContain("low contrast probe");
  expect(rows.map((r) => r.text)).not.toContain("disabled probe");
});
