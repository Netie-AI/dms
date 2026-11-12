import { useEffect, type RefObject } from "react";

/**
 * Keyboard and screen-reader behaviour for a phone overlay (the nav drawer, the
 * Sources sheet): focus moves in when it opens, Escape closes it, and focus
 * goes back to the control that opened it. Without this, focus stays on a
 * control that is now behind the backdrop.
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
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      requestAnimationFrame(() => document.querySelector<HTMLElement>(opener)?.focus());
    };
  }, [open, container, onClose, opener]);
}
