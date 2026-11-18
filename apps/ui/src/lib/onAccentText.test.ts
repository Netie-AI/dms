import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Source pin (a grep, not a render: only the Playwright audit in iphone-contrast.spec.ts reads
 * computed colours). `text-white` on the accent fill is white on #00ff87 (1.34:1) in the graphite
 * mode. Below `lg` the text on an accent fill must use --color-on-accent, which is #ffffff in cream
 * and #050505 in graphite. Desktop keeps `text-white` until the product owner decides.
 */
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
function walk(dir: string, out: string[] = []) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (p.endsWith(".tsx")) out.push(p);
  }
  return out;
}

describe("text on the accent fill", () => {
  it("every text-white has a max-lg on-accent override on the same element", () => {
    const bad: string[] = [];
    for (const file of walk(root)) {
      readFileSync(file, "utf8")
        .split("\n")
        .forEach((line, i) => {
          if (!/(^|[\s"'`:])(hover:)?text-white\b/.test(line)) return;
          const hover = /hover:text-white\b/.test(line);
          const need = hover ? "max-lg:hover:text-[var(--color-on-accent)]" : "max-lg:text-[var(--color-on-accent)]";
          if (!line.includes(need)) bad.push(`${file.slice(root.length + 1)}:${i + 1}`);
        });
    }
    expect(bad).toEqual([]);
  });
});
