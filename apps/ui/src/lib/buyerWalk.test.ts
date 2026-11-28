import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

describe("Studio starter questions", () => {
  it("keeps five supply-chain asks and drops the refuse demo", () => {
    const studio = readFileSync(join(here, "../pages/StudioPage.tsx"), "utf8");
    const starters = readFileSync(join(here, "starterQuestions.ts"), "utf8");
    const fixtures = readFileSync(join(here, "fixtures.ts"), "utf8");
    expect(studio).toMatch(/data-testid="starter-questions"/);
    expect(studio).toMatch(/starterQuestions/);
    expect(fixtures).toMatch(/starterQuestions/);
    expect(studio).not.toMatch(/data-testid="buyer-walk"/);
    expect(studio).not.toMatch(/data-testid="buyer-walk-refuse"/);
    expect(studio).not.toContain("Buyer walk");
    expect(studio).not.toContain("Not COMPLETE");
    expect(studio).not.toContain("No ARR");
    expect(studio).not.toContain("No buyer logos");
    expect(starters).toContain("What is our total spend by supplier country?");
    expect(starters).toContain("What is total stock value by category?");
    expect(starters).toContain("Which SKUs are below reorder level in warehouse A?");
    expect(starters).toContain("Show warehouse capacity utilisation");
    expect(starters).toContain("Which locations are cold storage?");
    expect(starters).not.toContain("Just give me last month's number");
    const chat = readFileSync(join(here, "../pages/ChatPage.tsx"), "utf8");
    expect(chat).toMatch(/draftQuestion/);
  });
});
