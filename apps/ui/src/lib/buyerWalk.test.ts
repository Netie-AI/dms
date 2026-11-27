import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

describe("Studio starter questions", () => {
  it("keeps five supply-chain asks and drops the refuse demo", () => {
    const studio = readFileSync(join(here, "../pages/StudioPage.tsx"), "utf8");
    expect(studio).toMatch(/data-testid="starter-questions"/);
    expect(studio).not.toMatch(/data-testid="buyer-walk"/);
    expect(studio).not.toMatch(/data-testid="buyer-walk-refuse"/);
    expect(studio).not.toContain("Buyer walk");
    expect(studio).not.toContain("Not COMPLETE");
    expect(studio).not.toContain("No ARR");
    expect(studio).not.toContain("No buyer logos");
    expect(studio).toContain("What is our total spend by supplier country?");
    expect(studio).toContain("What is total stock value by category?");
    expect(studio).toContain("Which SKUs are below reorder level in warehouse A?");
    expect(studio).toContain("Show warehouse capacity utilisation");
    expect(studio).toContain("Which locations are cold storage?");
    expect(studio).not.toContain("Just give me last month's number");
    const chat = readFileSync(join(here, "../pages/ChatPage.tsx"), "utf8");
    expect(chat).toMatch(/draftQuestion/);
  });
});
