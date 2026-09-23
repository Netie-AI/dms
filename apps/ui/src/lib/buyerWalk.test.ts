import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

describe("AGI-BUYER-WALK-01 Studio copy", () => {
  it("shows five supply-chain asks, one honest refuse, and artifact copy", () => {
    const studio = readFileSync(join(here, "../pages/StudioPage.tsx"), "utf8");
    expect(studio).toMatch(/data-testid="buyer-walk"/);
    expect(studio).toMatch(/data-testid="buyer-walk-refuse"/);
    expect(studio).toMatch(/Buyer walk · 5-day AGI-for-DB/);
    expect(studio).toContain("What is our total spend by supplier country?");
    expect(studio).toContain("What is total stock value by category?");
    expect(studio).toContain("Which SKUs are below reorder level in warehouse A?");
    expect(studio).toContain("Show warehouse capacity utilisation");
    expect(studio).toContain("Which locations are cold storage?");
    expect(studio).toContain("Just give me last month's number");
    expect(studio).toContain("Honest ABSTAIN is the product.");
    expect(studio).toContain("Download Excel");
    expect(studio).toContain("Not COMPLETE");
    expect(studio).toContain("No buyer logos");
    expect(studio).toContain("No ARR");
    expect(studio).not.toMatch(/ARR:\s*\$/);
    expect(studio).not.toMatch(/logo\.png|dashboard\.png/);
    const chat = readFileSync(join(here, "../pages/ChatPage.tsx"), "utf8");
    expect(chat).toMatch(/draftQuestion/);
  });
});
