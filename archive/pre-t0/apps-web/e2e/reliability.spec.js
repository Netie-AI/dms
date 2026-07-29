const { test, expect } = require("@playwright/test");

const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

/**
 * Reliability steps for Cortex Find Skills:
 * 1. API health
 * 2. discovery sources loaded
 * 3. find_skills returns a best match
 * 4. MCP find_skills tool works
 * 5. Skills UI find panel (when UI+API both up)
 */
test.describe("Cortex discovery reliability", () => {
  test("API health is ok", async ({ request }) => {
    const res = await request.get(`${API}/health`);
    test.skip(!res.ok(), "API not running — start with PACK=dms uvicorn CortexOS.api.main:app");
    const body = await res.json();
    expect(body.status).toBe("ok");
  });

  test("find-skills API returns best match for playwright", async ({ request }) => {
    const health = await request.get(`${API}/health`);
    test.skip(!health.ok(), "API not running");
    const res = await request.post(`${API}/api/discovery/find-skills`, {
      data: { goal: "playwright e2e testing", top_k: 5 },
      headers: { "X-API-Key": "dms-demo-viewer-key" },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.best.name).toBeTruthy();
    expect(body.question).toContain("Are there any good skills for");
  });

  test("MCP find_skills tool", async ({ request }) => {
    const health = await request.get(`${API}/health`);
    test.skip(!health.ok(), "API not running");
    const res = await request.post(`${API}/mcp/call`, {
      data: { name: "find_skills", arguments: { goal: "github mcp", top_k: 3 } },
      headers: { "X-API-Key": "dms-demo-viewer-key" },
    });
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.ok).toBe(true);
    expect(body.result.matches.length).toBeGreaterThan(0);
  });

  test("Skills page Find Skills UI", async ({ page, request }) => {
    const health = await request.get(`${API}/health`);
    test.skip(!health.ok(), "API not running — UI find panel needs Cortex API");

    await page.goto("/skills");
    await expect(page.getByTestId("find-skills-goal")).toBeVisible();
    await page.getByTestId("find-skills-goal").fill("playwright reliability testing");
    await page.getByTestId("find-skills-submit").click();
    await expect(page.getByTestId("find-skills-best")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("find-skills-results")).toBeVisible();
  });
});
