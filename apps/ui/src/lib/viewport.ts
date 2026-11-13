import { useCallback, useSyncExternalStore } from "react";

/** Tailwind `lg`. Below this, a docked 22rem Sources column covers Chat. */
export const LG_MIN_WIDTH_PX = 1024;

const queries = new Map<string, MediaQueryList>();

function media(query: string): MediaQueryList {
  let m = queries.get(query);
  if (!m) {
    m = window.matchMedia(query);
    queries.set(query, m);
  }
  return m;
}

/** Live media-query match. `onServer` is the answer where there is no window. */
export function useMedia(query: string, onServer: boolean): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const m = media(query);
      m.addEventListener("change", onChange);
      return () => m.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => media(query).matches,
    () => onServer,
  );
}

/** Live `lg` match, for the few places where phone and desktop render different nodes. */
export function useIsLg(): boolean {
  return useMedia(`(min-width: ${LG_MIN_WIDTH_PX}px)`, true);
}

/** Under 360px (iPhone SE, small Android): the idle placeholder needs a shorter line. */
export function useIsNarrowPhone(): boolean {
  return useMedia("(max-width: 359.98px)", false);
}

export function isLgViewport(widthPx: number): boolean {
  return widthPx >= LG_MIN_WIDTH_PX;
}

function matchesLg(
  matchMedia: ((query: string) => { matches: boolean }) | undefined,
): boolean {
  if (!matchMedia) return true;
  return matchMedia(`(min-width: ${LG_MIN_WIDTH_PX}px)`).matches;
}

/** Desktop keeps the dock open; phone-width starts closed. Missing window => desktop. */
export function sourcesStartOpen(
  matchMedia: ((query: string) => { matches: boolean }) | undefined,
): boolean {
  return matchesLg(matchMedia);
}

/**
 * Below lg, LeftNav is a closed drawer (Operate w-52 must not cover chat).
 * Desktop: cream starts as the icon rail; graphite starts expanded.
 * Missing window => desktop.
 */
export function navStartsCollapsed(
  matchMedia: ((query: string) => { matches: boolean }) | undefined,
  productMode: "cream" | "graphite",
): boolean {
  if (!matchesLg(matchMedia)) return true;
  return productMode === "cream";
}

/**
 * Expand the docked 22rem Sources column (after ask, or tracing a value).
 * False below lg so an answer cannot zero the chat column (live 390px: 352px dock -> main=0).
 */
export function shouldExpandSourcesDock(widthPx: number): boolean {
  return isLgViewport(widthPx);
}
