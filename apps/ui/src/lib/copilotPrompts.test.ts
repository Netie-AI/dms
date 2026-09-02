import { describe, expect, it, vi } from "vitest";
import { COPILOT_SYSTEM, copyText, copilotClipboard, promptPackGuards } from "./copilotPrompts";

describe("Excel Copilot prompt pack", () => {
  it("system prompt forbids inventing numbers and warehouse writes", () => {
    const g = promptPackGuards(COPILOT_SYSTEM);
    expect(g.noInvent).toBe(true);
    expect(g.noWrite).toBe(true);
  });

  it("clipboard prepends system guards to each Copilot step", () => {
    const text = copilotClipboard("Make a bar chart.");
    const g = promptPackGuards(text);
    expect(text.startsWith(COPILOT_SYSTEM)).toBe(true);
    expect(g.noInvent).toBe(true);
    expect(g.noWrite).toBe(true);
    expect(text).toContain("Make a bar chart.");
  });

  it("copyText falls back to execCommand when clipboard is denied", async () => {
    const origClip = navigator.clipboard;
    const origDoc = (globalThis as { document?: unknown }).document;
    const exec = vi.fn().mockReturnValue(true);
    Object.assign(globalThis, {
      document: {
        execCommand: exec,
        createElement: () => ({
          value: "",
          setAttribute: () => undefined,
          style: {},
          select: () => undefined,
        }),
        body: { appendChild: () => undefined, removeChild: () => undefined },
      },
    });
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: () => Promise.reject(new Error("denied")) },
    });
    try {
      await expect(copyText('{"kind":"dms.space"}')).resolves.toBe(true);
      expect(exec).toHaveBeenCalledWith("copy");
    } finally {
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: origClip });
      if (origDoc === undefined) {
        Reflect.deleteProperty(globalThis, "document");
      } else {
        Object.assign(globalThis, { document: origDoc });
      }
    }
  });
});
