import { useEffect, useRef } from "react";
import { lgMedia } from "@/lib/viewport";

/** In the document and laid out: a node a browser can keep focus on. */
function focusable(el: HTMLElement): boolean {
  return el.isConnected && el.getClientRects().length > 0;
}

type Resolver = (lost: HTMLElement) => HTMLElement | null;
const resolvers = new Set<Resolver>();
let lost: HTMLElement | null = null;
let frame = 0;

/**
 * After React has committed the new width (every subscriber of the media query commits on its own,
 * so no single component can know when the whole tree has swapped): if the control that had focus
 * is gone or hidden and focus fell to <body>, hand focus to the control its owner names. Focus that
 * is on a real control, a focused composer included, is never moved.
 */
function restore() {
  frame = 0;
  const was = lost;
  lost = null;
  if (!was || focusable(was)) return;
  const now = document.activeElement;
  // A destroyed control leaves body; a control hidden by the new width can stay the active element.
  if (now && now !== document.body && now !== was) return;
  for (const resolve of resolvers) {
    const target = resolve(was);
    if (target) {
      target.focus();
      return;
    }
  }
}

// The last control that took focus, because the browser can blur a control before the crossing is
// announced: a width that hides the focused control with CSS (the drawer's Role select under
// `lg:hidden`) triggers Chrome's focus fix-up in the same frame, and `activeElement` may already be
// <body> when the media query's change event runs. A deliberate blur (a tap on empty space, the
// window losing focus) leaves the control laid out, so it is forgotten; a control that is hidden or
// removed is kept.
let last: HTMLElement | null = null;
if (typeof document !== "undefined") {
  document.addEventListener(
    "focusin",
    (e) => {
      last = e.target instanceof HTMLElement ? e.target : null;
    },
    true,
  );
  document.addEventListener(
    "focusout",
    (e) => {
      const el = e.target;
      if (e.relatedTarget !== null || !(el instanceof HTMLElement)) return;
      // After the task: a removal finishes first, so a removed control is no longer connected here.
      queueMicrotask(() => {
        if (last === el && focusable(el)) last = null;
      });
    },
    true,
  );
}

// Registered when this module loads, which is before any component subscribes to the same
// MediaQueryList, so it runs first on a crossing: the old DOM is still there. (A listener added
// later could run after a subscriber has already committed.) A control that was removed before the
// crossing is not this crossing's doing and is dropped.
lgMedia()?.addEventListener("change", () => {
  const a = document.activeElement;
  lost =
    a instanceof HTMLElement && a !== document.body ? a : last?.isConnected ? last : null;
  if (frame) cancelAnimationFrame(frame);
  frame = requestAnimationFrame(restore);
});

/**
 * Crossing `lg` swaps whole subtrees (the phone drawer is `div[dialog] > nav`, the docked nav is a
 * lone `nav`; the Sources dock and the composer's Sources button are different nodes). A control
 * that held focus is destroyed or hidden, and the browser drops focus on <body>: a keyboard or
 * screen-reader user is thrown back to the top of the page by resizing a window.
 *
 * Call this next to the code that swaps the subtree. `resolve` gets the control that had focus
 * (detached, but its ancestors still readable) and returns the equivalent control now on the page,
 * or null when the control is not this component's to hand over. Read the width inside `resolve`
 * with `isLgNow()`.
 */
export function useFocusHandoff(resolve: Resolver): void {
  const latest = useRef(resolve);
  latest.current = resolve;
  useEffect(() => {
    const r: Resolver = (l) => latest.current(l);
    resolvers.add(r);
    return () => {
      resolvers.delete(r);
    };
  }, []);
}

/** The phone tab bar's More button, which opens the same drawer the lg nav docks. */
export const MORE_BUTTON = '[data-testid="tab-bar"] button[aria-controls="primary-nav"]';

/** The chat composer: the last stop when no equivalent control exists at the new width. */
export function composerEl(): HTMLElement | null {
  return document.querySelector<HTMLElement>("textarea");
}

/** The control a nav control at lg becomes on a phone: the same page's tab, else More, else the composer. */
export function phoneNavTarget(href: string | null): HTMLElement | null {
  const tab = href
    ? [...document.querySelectorAll<HTMLElement>('[data-testid="tab-bar"] a[href]')].find(
        (a) => a.getAttribute("href") === href,
      )
    : undefined;
  return tab ?? document.querySelector<HTMLElement>(MORE_BUTTON) ?? composerEl();
}
