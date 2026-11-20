import { lgMedia } from "./viewport";

/*
 * On a phone an inline control in a sentence (.tap-pad) pads its hit area by 1rem above and
 * below (index.css). Lines of text are closer together than that, so the padded areas of two
 * controls on adjacent lines overlap, and the later one in the DOM wins the overlap, including
 * the lower edge of the other's own text: a tap on one figure opened the Sources of another.
 * A tap in a padded area goes to the nearest control that claims the point.
 */
const gap = (el: Element, x: number, y: number) =>
  Math.min(
    ...[...el.getClientRects()].map((r) =>
      Math.hypot(Math.max(r.left - x, 0, x - r.right), Math.max(r.top - y, 0, y - r.bottom)),
    ),
  );

export function nearestTapPad(hit: HTMLElement, x: number, y: number): HTMLElement {
  let best = hit;
  let bestGap = gap(hit, x, y);
  for (const el of document.elementsFromPoint(x, y)) {
    if (el === hit || !(el instanceof HTMLElement) || !el.matches(".tap-pad")) continue;
    const g = gap(el, x, y);
    if (g < bestGap) [best, bestGap] = [el, g];
  }
  return best;
}

if (typeof document !== "undefined") {
  document.addEventListener(
    "click",
    (e) => {
      // No pad at lg; a keyboard press (detail 0) and our own re-sent click have no pointer position.
      if (lgMedia()?.matches || !e.isTrusted || e.detail === 0) return;
      const hit = e.target instanceof Element ? e.target.closest<HTMLElement>(".tap-pad") : null;
      if (!hit) return;
      const near = nearestTapPad(hit, e.clientX, e.clientY);
      if (near === hit) return;
      e.preventDefault();
      e.stopPropagation();
      near.focus();
      near.click();
    },
    true,
  );
}
