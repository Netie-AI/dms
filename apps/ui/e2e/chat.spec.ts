import { expect, test } from "@playwright/test";

test.describe("Chat experience", () => {
  test("empty chat shows suggestions and API status", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Ask about your data" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Top 5 selling SKUs by revenue" })).toBeVisible();
    await expect(page.getByRole("button", { name: "What was revenue last month?" })).toBeVisible();
    const apiStatus = page.getByText(/API · (ok|offline|…)/);
    await expect(apiStatus).toBeVisible();
  });

  test("suggested ask returns an envelope or a visible failure, never a blank thread", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Top 5 selling SKUs by revenue" }).click();
    await expect(page.getByText("Top 5 selling SKUs by revenue").first()).toBeVisible();
    const thinking = page.getByText("Thinking…");
    const badge = page.getByText(
      /no answer — on purpose|certified|governed|generated — check sources|unusual result — verify|fallback → demo|Demo ask mode/i,
    );
    const error = page.getByText(/ask failed|API unreachable|DMS API unreachable/i);
    await expect(thinking.or(badge).or(error)).toBeVisible({ timeout: 20_000 });
    await expect(thinking).toHaveCount(0, { timeout: 20_000 });
    if (await error.count()) {
      await expect(error.first()).toBeVisible();
      return;
    }
    await expect(badge.first()).toBeVisible();
  });

  test("composer queues while asking is disabled only when empty", async ({ page }) => {
    await page.goto("/");
    const ask = page.getByRole("button", { name: "Ask" });
    await expect(ask).toBeDisabled();
    await page.getByPlaceholder(/Ask about your data/i).fill("What was total revenue?");
    await expect(ask).toBeEnabled();
  });
});
