import { useEffect, type RefObject } from "react";

const TABBABLE =
  'a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex="-1"])';

/**
 * The controls Tab can reach inside `root`, in DOM order: rendered (not
 * display:none, not visibility:hidden, so a closed <details> body drops out),
 * not disabled, not `inert`, not tabindex="-1". A radio group is one tab stop,
 * the checked one.
 */
function tabbable(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(TABBABLE)].filter((el) => {
    if ((el as HTMLButtonElement).disabled) return false;
    // `button` and `a[href]` match the selector whatever their tabindex; -1 takes them out of Tab order.
    if (el.getAttribute("tabindex") === "-1") return false;
    if (el.closest("[inert]")) return false;
    if (el.getClientRects().length === 0) return false;
    if (getComputedStyle(el).visibility === "hidden") return false;
    if (el instanceof HTMLInputElement) {
      if (el.type === "hidden") return false;
      if (el.type === "radio" && el.name && !el.checked) {
        const group = el.form ?? root;
        const checked = [...group.querySelectorAll<HTMLInputElement>('input[type="radio"]')].some(
          (r) => r.name === el.name && r.checked,
        );
        if (checked) return false;
      }
    }
    return true;
  });
}

/**
 * Keyboard and screen-reader behaviour for a phone overlay (the nav drawer, the
 * Sources sheet): focus moves in when it opens, Tab and Shift+Tab wrap inside
 * it, Escape closes it, and focus goes back to the control that opened it.
 * Without this, focus stays on a control that is now behind the backdrop, or
 * Tab walks out of the dialog into the top bar and the browser chrome.
 *
 * `open` means "modal right now": callers pass `open && !isLg`, so at lg, where
 * the same component is a docked column and not a dialog, nothing here runs.
 */
export function useModalFocus(
  open: boolean,
  container: RefObject<HTMLElement | null>,
  onClose: () => void,
  /** Selector of the opener. It is re-rendered or un-inerted by the close, so it is looked up late. */
  opener: string,
) {
  useEffect(() => {
    if (!open) return;
    container.current?.querySelector<HTMLElement>("a[href], button, select, input")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab" || e.altKey || e.ctrlKey || e.metaKey) return;
      const root = container.current;
      if (!root) return;
      const stops = tabbable(root);
      // The stops are read now: the sheet grows and shrinks as sources open.
      if (stops.length === 0) {
        e.preventDefault();
        return;
      }
      const first = stops[0];
      const last = stops[stops.length - 1];
      const active = document.activeElement;
      if (!active || !root.contains(active)) {
        // Focus strayed out (a tap on the top bar): bring it back in at the end Tab was heading for.
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      } else if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      requestAnimationFrame(() => document.querySelector<HTMLElement>(opener)?.focus());
    };
  }, [open, container, onClose, opener]);
}
