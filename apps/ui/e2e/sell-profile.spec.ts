import { expect, test } from "@playwright/test";

const QUESTIONS = [
  "How much stock is in each warehouse?",
  "Who are the suppliers?",
  "What are the warehouse names?",
];

const SQL = ["inventory", "suppliers", "locations"];
const ROW = ["WH-A", "SUP-01", "Warehouse A"];

test.describe("sell profile demo warehouse", () => {
  test("three asks stream steps, SQL, and rows", async ({ page }) => {
    await page.goto("/studio");
    const ask = page.getByTestId("studio-sell-ask");
    await expect(ask).toBeVisible({ timeout: 30_000 });
    const box = page.getByTestId("studio-chat-question");
    for (let i = 0; i < QUESTIONS.length; i += 1) {
      await box.fill(QUESTIONS[i]);
      await ask.click();
      await expect(page.getByText("Understanding question")).toBeVisible();
      await expect(page.getByText("Finding tables")).toBeVisible();
      await expect(page.getByText("Writing SQL")).toBeVisible();
      await expect(page.getByText("Running on database")).toBeVisible();
      await expect(page.getByText("Checking")).toBeVisible();
      await expect(page.getByText("Answer", { exact: true })).toBeVisible();
      const card = page.getByTestId("sell-answer").last();
      await expect(card).toBeVisible();
      await card.getByTestId("sell-see-sql").click();
      await expect(card.getByTestId("sell-sql")).toContainText(SQL[i]);
      await card.getByTestId("sell-see-rows").click();
      await expect(card.getByTestId("sell-rows")).toContainText(ROW[i]);
    }
  });
});
