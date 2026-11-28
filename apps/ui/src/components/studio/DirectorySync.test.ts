import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DirectorySyncView, emptyDirectoryForm, type DirectoryMap } from "./DirectorySync";

const row: DirectoryMap = {
  id: "1",
  kind: "department",
  source: "Finance",
  priority: 10,
  role: "analyst",
  gov_role: "member",
  spaces: ["space-1"],
  daily_tokens: 1000,
};

describe("directory sync panel", () => {
  it("lists a saved mapping and hides the token until one is issued", () => {
    const plain = renderToStaticMarkup(
      createElement(DirectorySyncView, { maps: [row], form: emptyDirectoryForm }),
    );
    expect(plain).toContain('data-testid="directory-sync"');
    expect(plain).toContain("Finance");
    expect(plain).toContain("maps to analyst");
    expect(plain).toContain("Save mapping");
    expect(plain).toContain("Issue token");
    expect(plain).not.toContain("Provisioning token");

    const issued = renderToStaticMarkup(
      createElement(DirectorySyncView, {
        maps: [],
        form: emptyDirectoryForm,
        issued: "shown-once",
      }),
    );
    expect(issued).toContain("shown-once");
    expect(issued).toContain("shown once");
  });

  it("is the directory panel inside the governance console", () => {
    const src = readFileSync(new URL("./GovConsole.tsx", import.meta.url), "utf8");
    expect(src).toContain("<DirectorySync />");
  });
});
