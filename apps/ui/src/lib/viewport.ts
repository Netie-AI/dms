/** Tailwind `lg`. Below this, a docked 22rem Sources column covers Chat. */
export const LG_MIN_WIDTH_PX = 1024;

export function isLgViewport(widthPx: number): boolean {
  return widthPx >= LG_MIN_WIDTH_PX;
}

/** Desktop keeps the dock open; phone-width starts closed. Missing window => desktop. */
export function sourcesStartOpen(
  matchMedia: ((query: string) => { matches: boolean }) | undefined,
): boolean {
  if (!matchMedia) return true;
  return matchMedia(`(min-width: ${LG_MIN_WIDTH_PX}px)`).matches;
}

/** After ask, only auto-open when the dock will not cover the answer. */
export function shouldAutoOpenSourcesAfterAsk(widthPx: number): boolean {
  return isLgViewport(widthPx);
}
