import { useEffect, useRef } from "react";
import { isLgNow, lgMedia } from "@/lib/viewport";

/** An element a browser can focus: HTML or SVG. */
type Focusable = Element & HTMLOrSVGElement;

function isFocusable(n: unknown): n is Focusable {
  return n instanceof Element && "focus" in n && "tabIndex" in n;
}

/** In the document and laid out: a node a browser can keep focus on. */
function laidOut(el: Element): boolean {
  return el.isConnected && el.getClientRects().length > 0;
}

/**
 * A control a person could be handed focus on: laid out, painted (not skipped by a closed
 * <details> or content-visibility, not visibility:hidden), not disabled, and not inside anything
 * inert, aria-hidden or hidden. Focus is never given to a node that fails this, so a hand-off can
 * not reach behind an open drawer or into a subtree assistive tech has been told to ignore.
 */
function usable(el: Element): el is Focusable {
  if (!isFocusable(el) || el.tabIndex < 0 || !laidOut(el)) return false;
  if (!el.checkVisibility({ visibilityProperty: true, contentVisibilityAuto: true })) return false;
  if ((el as HTMLButtonElement).disabled) return false;
  return !el.closest('[inert], [aria-hidden="true"], [hidden]');
}

type Resolver = (lost: HTMLElement) => HTMLElement | null;
const resolvers = new Set<Resolver>();

// ---------------------------------------------------------------------------------------------
// What the lost control was, read while the old DOM is still on the page
// ---------------------------------------------------------------------------------------------

/** Landmarks and regions: where on the page a control lives. */
const LANDMARK =
  'nav, main, header, aside, footer, article, section, [role="dialog"], [role="region"], [role="navigation"], [data-testid="tab-bar"]';

const squash = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim();

function roleOf(el: Element): string {
  const explicit = el.getAttribute("role");
  if (explicit) return explicit;
  const tag = el.tagName.toLowerCase();
  if (tag === "a") return "link";
  if (tag === "select") return "combobox";
  if (tag === "textarea") return "textbox";
  return tag;
}

/** aria-label, else aria-labelledby, else an associated <label>, else text content, else title. */
function nameOf(el: Element): string {
  const label = squash(el.getAttribute("aria-label"));
  if (label) return label;
  const by = el.getAttribute("aria-labelledby");
  if (by) {
    const t = squash(
      by
        .split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent)
        .join(" "),
    );
    if (t) return t;
  }
  const labels = (el as HTMLInputElement).labels;
  if (labels?.length) {
    const t = squash([...labels].map((l) => l.textContent).join(" "));
    if (t) return t;
  }
  if (!["SELECT", "TEXTAREA", "INPUT"].includes(el.tagName)) {
    const t = squash(el.textContent);
    if (t) return t;
  }
  return squash(el.getAttribute("title")) || squash(el.getAttribute("placeholder"));
}

/** A coarse name for the landmark an element sits in; the same landmark at two widths reads the same. */
function landmarkLabel(landmark: Element | null): string {
  return landmark ? `${landmark.tagName.toLowerCase()}:${squash(landmark.getAttribute("aria-label"))}` : "";
}

type Lost = {
  el: Focusable;
  testid: string | null;
  role: string;
  name: string;
  href: string | null;
  controls: string | null;
  landmarks: Element[];
};

function snapshot(el: Focusable): Lost {
  const landmarks: Element[] = [];
  for (let n = el.parentElement?.closest(LANDMARK); n; n = n.parentElement?.closest(LANDMARK) ?? null) {
    landmarks.push(n);
  }
  return {
    el,
    testid: el.getAttribute("data-testid"),
    role: roleOf(el),
    name: nameOf(el),
    href: el.getAttribute("href"),
    controls: el.getAttribute("aria-controls"),
    landmarks,
  };
}

/** Every control in the new DOM that a person could be handed focus on, in document order. */
function candidates(): Focusable[] {
  return [...document.querySelectorAll("*")].filter(usable);
}

/**
 * The control a lost one has become at the new width, found from what it was and not from a list of
 * controls: the same data-testid, else the same role and accessible name, else the same aria-controls
 * (a button that opens the same region), else, for a navigation link, the same href. Among several, the one in the same
 * landmark as the lost control. Failing that, the first control in the nearest landmark around the
 * lost one that is still on the page; failing that, the composer.
 */
function equivalent(was: Lost): Focusable | null {
  const all = candidates().filter((c) => c !== was.el);
  const inNav = (c: Element) => c.parentElement?.closest(LANDMARK)?.tagName === "NAV";
  const wasNav = was.href !== null && was.landmarks[0]?.tagName === "NAV";
  const tiers: ((c: Focusable) => boolean)[] = [
    (c) => was.testid !== null && c.getAttribute("data-testid") === was.testid,
    (c) => was.name !== "" && roleOf(c) === was.role && nameOf(c) === was.name,
    (c) => was.controls !== null && c.getAttribute("aria-controls") === was.controls,
    // An href is "the same page's link" only for navigation: the audit link inside an answer is not
    // where the Audit nav link goes, whatever its href.
    (c) => wasNav && inNav(c) && c.getAttribute("href") === was.href,
  ];
  const wasLandmark = landmarkLabel(was.landmarks[0] ?? null);
  for (const tier of tiers) {
    const hits = all.filter(tier);
    if (!hits.length) continue;
    return hits.find((c) => landmarkLabel(c.parentElement?.closest(LANDMARK) ?? null) === wasLandmark) ?? hits[0];
  }
  for (const landmark of was.landmarks) {
    if (!landmark.isConnected) continue;
    const first = all.find((c) => landmark.contains(c));
    if (first) return first;
  }
  const composer = composerEl();
  return composer && usable(composer) ? composer : null;
}

// ---------------------------------------------------------------------------------------------
// The crossing
// ---------------------------------------------------------------------------------------------

let lost: Lost | null = null;
let frame = 0;

/**
 * After React has committed the new width (every subscriber of the media query commits on its own,
 * so no single component can know when the whole tree has swapped): if the control that had focus
 * is gone or hidden and focus fell to <body>, hand focus to the control its owner names, else to the
 * equivalent control found from what the lost one was. Focus that is on a real control, a focused
 * composer included, is never moved.
 */
function restore() {
  frame = 0;
  const was = lost;
  lost = null;
  if (!was || laidOut(was.el)) return;
  const now = document.activeElement;
  // A destroyed control leaves body; a control hidden by the new width can stay the active element.
  if (now && now !== document.body && now !== was.el) return;
  let target: Focusable | null = null;
  for (const resolve of resolvers) {
    const t = resolve(was.el as HTMLElement);
    // An owner names a control; it is still never handed one a person could not use.
    if (t && usable(t)) {
      target = t;
      break;
    }
  }
  target ??= equivalent(was);
  if (target) target.focus();
  else last = null;
}

// The last control that took focus, because the browser can blur a control before the crossing is
// announced: a width that hides the focused control with CSS (the drawer's Role select under
// `lg:hidden`) triggers Chrome's focus fix-up in the same frame, and `activeElement` may already be
// <body> when the media query's change event runs. It is kept only while a crossing could explain
// losing it. When focus leaves it with no new target and the width is still the one it had when it
// took focus, nothing crossed: the person blurred it on purpose (a tap on empty space, the window
// losing focus) or the app hid it. Either way the person is on nothing, so it is forgotten and a
// later crossing leaves them there. (A crossing flips the width before the browser's fix-up blurs a
// control the new width hides, so that loss is kept.) A control removed from the page is dropped by
// the `isConnected` check at the crossing.
let last: { el: Focusable; lg: boolean } | null = null;
if (typeof document !== "undefined") {
  document.addEventListener(
    "focusin",
    (e) => {
      last = isFocusable(e.target) ? { el: e.target, lg: isLgNow() } : null;
    },
    true,
  );
  document.addEventListener(
    "focusout",
    (e) => {
      const el = e.target;
      if (e.relatedTarget !== null || !isFocusable(el)) return;
      // After the task: a removal finishes first, so a removed control is no longer connected here.
      queueMicrotask(() => {
        if (last?.el === el && last.lg === isLgNow()) last = null;
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
  const el = isFocusable(a) && a !== document.body ? a : last?.el.isConnected ? last.el : null;
  lost = el ? snapshot(el) : null;
  if (frame) cancelAnimationFrame(frame);
  frame = requestAnimationFrame(restore);
});

/**
 * Crossing `lg` swaps whole subtrees (the phone drawer is `div[dialog] > nav`, the docked nav is a
 * lone `nav`; the Sources dock and the composer's Sources button are different nodes). A control
 * that held focus is destroyed or hidden, and the browser drops focus on <body>: a keyboard or
 * screen-reader user is thrown back to the top of the page by resizing a window.
 *
 * Nothing needs to call this for the common case: the hand-off finds the equivalent control by
 * data-testid, role and accessible name, aria-controls or href, in the same landmark, and falls back
 * to the nearest landmark that survives and then the composer. Call it next to the code that swaps
 * a subtree only when the right target is not the equivalent one. `resolve` gets the control that
 * had focus (detached, but its ancestors still readable) and returns the control now on the page
 * that should take focus, or null when the control is not this component's to hand over. It is
 * tried before the generic search. Read the width inside `resolve` with `isLgNow()`.
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
