import { describe, expect, it } from "vitest";
import {
  LG_MIN_WIDTH_PX,
  isLgViewport,
  navStartsCollapsed,
  shouldExpandSourcesDock,
  sourcesStartOpen,
} from "./viewport";

describe("viewport (STUDIO-MOBILE-01)", () => {
  it("treats phone-width and the 640px band as below lg", () => {
    expect(isLgViewport(375)).toBe(false);
    expect(isLgViewport(390)).toBe(false);
    expect(isLgViewport(430)).toBe(false);
    expect(isLgViewport(640)).toBe(false);
    expect(isLgViewport(LG_MIN_WIDTH_PX - 1)).toBe(false);
    expect(isLgViewport(LG_MIN_WIDTH_PX)).toBe(true);
  });

  it("starts Sources closed below lg and open on desktop", () => {
    expect(sourcesStartOpen(undefined)).toBe(true);
    expect(sourcesStartOpen(() => ({ matches: false }))).toBe(false);
    expect(sourcesStartOpen(() => ({ matches: true }))).toBe(true);
  });

  it("does not expand the Sources dock after ask on phone-width", () => {
    expect(shouldExpandSourcesDock(375)).toBe(false);
    expect(shouldExpandSourcesDock(390)).toBe(false);
    expect(shouldExpandSourcesDock(430)).toBe(false);
    expect(shouldExpandSourcesDock(LG_MIN_WIDTH_PX)).toBe(true);
  });

  it("starts Operate nav collapsed below lg and expanded on desktop", () => {
    const phone = () => ({ matches: false });
    const desktop = () => ({ matches: true });
    expect(navStartsCollapsed(phone, "graphite")).toBe(true);
    expect(navStartsCollapsed(phone, "cream")).toBe(true);
    expect(navStartsCollapsed(desktop, "graphite")).toBe(false);
    expect(navStartsCollapsed(desktop, "cream")).toBe(true);
    expect(navStartsCollapsed(undefined, "graphite")).toBe(false);
    expect(navStartsCollapsed(undefined, "cream")).toBe(true);
  });
});
