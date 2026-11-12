import { useState } from "react";

const KEY = "dms-install-hint-dismissed";

/**
 * iOS has no install prompt: the only route to the home screen is Safari's
 * Share menu, and nothing tells the user it exists. Shown once, in iOS Safari
 * only, never when the app already runs from the home screen.
 */
function shouldOffer(): boolean {
  if (typeof navigator === "undefined") return false;
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const standalone =
    (navigator as Navigator & { standalone?: boolean }).standalone === true ||
    window.matchMedia("(display-mode: standalone)").matches;
  if (!ios || standalone) return false;
  try {
    return window.localStorage.getItem(KEY) !== "1";
  } catch {
    return true;
  }
}

export function InstallHint() {
  const [show, setShow] = useState(shouldOffer);
  if (!show) return null;
  return (
    <div
      data-testid="install-hint"
      className="mt-4 flex items-center gap-2 border border-[var(--color-accent)]/40 bg-[var(--color-accent-soft)]/60 py-1 pr-1 pl-3 text-sm text-[var(--color-ink)]"
    >
      <p className="min-w-0 flex-1">
        Install this app: tap <strong>Share</strong>, then <strong>Add to Home Screen</strong>.
      </p>
      <button
        type="button"
        aria-label="Dismiss install hint"
        onClick={() => {
          try {
            window.localStorage.setItem(KEY, "1");
          } catch {
            // Private mode: the hint returns next visit, which is acceptable.
          }
          setShow(false);
        }}
        className="flex min-h-11 min-w-11 shrink-0 items-center justify-center text-[var(--color-ink-muted)]"
      >
        ✕
      </button>
    </div>
  );
}
