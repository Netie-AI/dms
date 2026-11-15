import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { useModalFocus } from "@/lib/useModalFocus";

/**
 * Test-only harness for e2e/iphone-a11y-02.spec.ts (C6). It is served by the Vite dev server and is
 * not imported by the app, so it is not part of the build.
 *
 * The container is always mounted and holds focus BEFORE `open` turns true, a state the real sheet
 * and drawer cannot be in (they mount when they open). useModalFocus must not take that focused
 * control, which is inside the container, as the invoker to return to.
 */
function Harness({ setOpen }: { setOpen: (set: (o: boolean) => void) => void }) {
  const [open, setOpenState] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  setOpen(setOpenState);
  useModalFocus(open, box, () => setOpenState(false), "#harness-opener");
  return (
    <div>
      <button id="harness-opener">opener</button>
      <div ref={box} id="harness-box">
        <button id="inside-first">first</button>
        <button id="inside-second">second</button>
      </div>
    </div>
  );
}

export async function runInsideFocusCase(): Promise<{ whileOpen: string; afterClose: string }> {
  const host = document.createElement("div");
  document.body.appendChild(host);
  let setOpen: (o: boolean) => void = () => {};
  const root = createRoot(host);
  flushSync(() => root.render(<Harness setOpen={(f) => (setOpen = f)} />));
  document.getElementById("inside-second")!.focus();
  flushSync(() => setOpen(true));
  // On open the hook moves focus to the first control inside the container.
  const whileOpen = document.activeElement?.id ?? "";
  flushSync(() => setOpen(false));
  // The hand-back runs in a requestAnimationFrame.
  await new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r())));
  const after = document.activeElement?.id ?? "";
  root.unmount();
  host.remove();
  return {
    whileOpen: whileOpen === "inside-first" ? "inside-first" : whileOpen,
    afterClose: after === "harness-opener" ? "opener" : after,
  };
}
