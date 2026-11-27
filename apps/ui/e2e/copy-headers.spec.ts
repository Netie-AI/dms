import { expect, test } from "@playwright/test";

test.describe("Product copy headers", () => {
  test("Studio says what the page does and hides the refuse demo", async ({ page }) => {
    await page.goto("/studio");
    await expect(page.getByRole("heading", { name: "Studio", level: 1 })).toBeVisible();
    await expect(
      page.getByText(
        "Bring files and databases in, see what was made of them, and start a question from here.",
      ),
    ).toBeVisible();
    await expect(page.getByTestId("starter-questions")).toBeVisible();
    await expect(page.getByTestId("buyer-walk")).toHaveCount(0);
    await expect(page.getByText("Not COMPLETE")).toHaveCount(0);
    await expect(page.getByText("Refuse / ABSTAIN demo")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "What is our total spend by supplier country?" }),
    ).toBeVisible();
  });

  test("Spaces says what the page does", async ({ page }) => {
    await page.goto("/spaces");
    await expect(page.getByRole("heading", { name: "Spaces", level: 1 })).toBeVisible();
    await expect(page.getByText("A Space is the data a question is allowed to see.")).toBeVisible();
    await expect(page.getByText("T3")).toHaveCount(0);
  });
});
