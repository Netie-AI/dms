import { expect, test } from "@playwright/test";

test.describe("ADJUST-SQL-VQ-01 flag off", () => {
  test("chat and studio do not show Adjust SQL", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("studio-adjust-open")).toHaveCount(0);
    await page.goto("/studio");
    await expect(page.getByTestId("studio-adjust-open")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Adjust SQL" })).toHaveCount(0);
  });
});
