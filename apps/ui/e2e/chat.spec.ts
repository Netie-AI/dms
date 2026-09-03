import { expect, test } from "@playwright/test";

test.describe("Chat experience", () => {
  test("empty chat shows suggestions and a live API", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: /Ask (about your|your company's) data/ }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Top 5 selling SKUs by revenue" })).toBeVisible();
    await expect(page.getByText("API · ok")).toBeVisible();
    await expect(page.getByText(/DMS API unreachable/i)).toHaveCount(0);
  });

  test("suggested ask returns an envelope, never a blank thread", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("API · ok")).toBeVisible();
    await page.getByRole("button", { name: "Top 5 selling SKUs by revenue" }).click();
    await expect(page.getByText("Top 5 selling SKUs by revenue").first()).toBeVisible();
    await expect(page.getByText("Thinking…")).toHaveCount(0, { timeout: 20_000 });
    await expect(
      page.getByRole("button", {
        name: /^(no answer — on purpose|certified|governed|generated — check sources|unusual result — verify)$/,
      }),
    ).toBeVisible();
  });

  test("composer submit is disabled only when empty", async ({ page }) => {
    await page.goto("/");
    const ask = page.getByRole("button", { name: "Ask", exact: true });
    await expect(ask).toBeDisabled();
    await page.getByPlaceholder(/Ask about your data/i).fill("What was total revenue?");
    await expect(ask).toBeEnabled();
  });
});
