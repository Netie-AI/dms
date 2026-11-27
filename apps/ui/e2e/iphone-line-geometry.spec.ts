import { expect, test, type Page } from "@playwright/test";

/**
 * IPHONE-LINE-01: a phone line chart draws one polyline through its own dots. The polyline's svg
 * was as wide as the scroller's visible box while the columns (each at least 3rem) overflowed it, so
 * past about six points the dots sat in the scrolled-out columns and the line was squeezed into the
 * first screenful: a trend shape that no point on the chart supports. Measured here from the
 * rendered boxes: the screen position of each polyline vertex against the centre of its dot.
 */

const PHONE = { width: 390, height: 844 };

async function line(page: Page, n: number, values?: number[]) {
  await page.route("**/v1/chat/ask", async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    const vs = values ?? Array.from({ length: n }, (_, i) => 100 + ((i * 37) % 11) * 90);
    body.rows = vs.map((v, i) => ({ day: `2026-10-${String(i + 1).padStart(2, "0")}`, revenue_myr: v }));
    body.chart = { kind: "line", x: "day", y: "revenue_myr", title: "Daily revenue" };
    await route.fulfill({ response: res, json: body });
  });
  await page.setViewportSize(PHONE);
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await page.getByPlaceholder(/Ask about your data/).fill("Top 5 selling SKUs by revenue");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const chart = page.getByTestId("answer-message").last().getByTestId("answer-chart").first();
  await expect(chart).toBeVisible({ timeout: 20_000 });
  await chart.scrollIntoViewIfNeeded();
  return chart;
}

/** Per point: where the polyline vertex lands on screen, and where its dot is. */
const geometry = (chart: ReturnType<Page["getByTestId"]>) =>
  chart.evaluate((root) => {
    const svg = root.querySelector("svg")!;
    const poly = svg.querySelector("polyline")!;
    const vb = svg.viewBox.baseVal;
    const r = svg.getBoundingClientRect();
    const verts = (poly.getAttribute("points") ?? "")
      .trim()
      .split(/\s+/)
      .map((p) => p.split(",").map(Number));
    const dots = [...root.querySelectorAll<HTMLElement>('[data-testid="chart-scroll"] button span.rounded-full')];
    return dots.map((d, i) => {
      const dr = d.getBoundingClientRect();
      const [vx, vy] = verts[i];
      return {
        i,
        dx: Math.round((dr.left + dr.width / 2 - (r.left + (vx / vb.width) * r.width)) * 10) / 10,
        dy: Math.round((dr.top + dr.height / 2 - (r.top + (vy / vb.height) * r.height)) * 10) / 10,
      };
    });
  });

for (const n of [4, 6, 7, 12, 30]) {
  test(`${n} points: every polyline vertex sits on its dot`, async ({ page }) => {
    const chart = await line(page, n);
    const g = await geometry(chart);
    expect(g).toHaveLength(n);
    for (const p of g) {
      expect(Math.abs(p.dx), `point ${p.i} horizontal offset`).toBeLessThanOrEqual(1.5);
      expect(Math.abs(p.dy), `point ${p.i} vertical offset`).toBeLessThanOrEqual(1.5);
    }
  });
}

test("12 points: the svg spans the whole scrolling content, not only the visible box", async ({ page }) => {
  const chart = await line(page, 12);
  const m = await chart.evaluate((root) => {
    const sc = root.querySelector<HTMLElement>('[data-testid="chart-scroll"]')!;
    const svg = root.querySelector("svg")!.getBoundingClientRect();
    return { svg: Math.round(svg.width), content: sc.scrollWidth, visible: sc.clientWidth };
  });
  expect(m.content).toBeGreaterThan(m.visible);
  expect(m.svg).toBeGreaterThanOrEqual(m.content - 1);
});
