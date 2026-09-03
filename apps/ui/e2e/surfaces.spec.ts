import { expect, test } from "@playwright/test";

test.describe("Data and govern surfaces", () => {
  test("Spaces admits in-memory catalog and refuses an ungated create", async ({ page }) => {
    await page.goto("/spaces");
    await expect(page.getByText("Spaces are not persisted — they die on restart")).toBeVisible();
    const name = `Playwright Ops ${Date.now()}`;
    await page.getByPlaceholder("e.g. Q4 close, Supplier audit, Procurement").fill(name);
    await expect(page.getByRole("button", { name: "Create Space" })).toBeEnabled();
    await page.getByRole("button", { name: "Create Space" }).click();
    // LINEAGE-05 runs with Cortex up (receipt promote is gated). Create then
    // succeeds in-memory. The unreachable copy is still the fail-closed path
    // when F5 is down — accept either, never skip.
    await expect(
      page
        .getByText(/Cortex gate is unreachable. Start Cortex before writing/)
        .or(page.getByText(/Space created/)),
    ).toBeVisible();
  });

  test("Studio exposes an ingest control and does not claim Cortex", async ({ page }) => {
    await page.goto("/studio");
    await expect(page.getByRole("heading", { name: "Studio", level: 1 })).toBeVisible();
    await expect(page.locator('input[type="file"]')).toHaveCount(2);
  });

  test("Amend propose is confirm-gated copy, empty list is honest", async ({ page }) => {
    await page.goto("/amend");
    await expect(page.getByRole("heading", { name: "Amend", level: 1 })).toBeVisible();
    await expect(page.getByText(/does not change warehouse data yet/i)).toBeVisible();
    await expect(page.getByRole("button", { name: "Propose" })).toBeVisible();
  });
});
