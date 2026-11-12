import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { NAV_GROUPS } from "@/components/LeftNav";
import { useApp } from "@/context/AppContext";
import { navIdsForMode } from "@/lib/productMode";
import type { NavId } from "@/lib/types";
import { useIsLg } from "@/lib/viewport";

/**
 * Phone navigation (IPHONE-PWA-02). The hamburger sits top-left, the hardest
 * spot for a right thumb on a tall phone. The surfaces a person switches
 * between most sit here instead, in the bottom thumb zone; everything else
 * stays one tap away behind More, which opens the same drawer.
 */
const TABS: Record<"cream" | "graphite", NavId[]> = {
  cream: ["chat", "library", "spaces", "trust"],
  graphite: ["chat", "library", "studio", "ontology"],
};

const ITEMS = NAV_GROUPS.flatMap((g) => g.items);

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

const ICONS: Partial<Record<NavId | "more", ReactNode>> = {
  chat: <path d="M4 5h16v11H9l-5 4z" {...stroke} />,
  library: <path d="M4 6h16M4 12h16M4 18h16M8 3v6M14 9v6M10 15v6" {...stroke} />,
  spaces: <path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" {...stroke} />,
  trust: <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM8.5 12l2.5 2.5 4.5-5" {...stroke} />,
  studio: <path d="M12 16V4M7 9l5-5 5 5M4 20h16" {...stroke} />,
  ontology: (
    <path
      d="M6 6m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M18 6m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M12 18m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0M8.5 6h7M7.2 8.2l3.6 7.6M16.8 8.2l-3.6 7.6"
      {...stroke}
    />
  ),
  more: <path d="M5 12h.01M12 12h.01M19 12h.01" {...stroke} strokeWidth={3} />,
};

const cell =
  "flex min-h-[3.25rem] min-w-0 flex-1 flex-col items-center justify-center gap-0.5 border-t-2 px-1 pt-1.5 pb-1";
// On the span, not the cell: index.css sets `button { font: inherit }` unlayered, which beats a
// text-size utility on the More button itself.
const label = "max-w-full truncate text-[11px] font-semibold tracking-wide";
const idle = "border-transparent text-[var(--color-ink-muted)]";
const active = "border-[var(--color-accent)] text-[var(--color-accent)]";

function Icon({ id }: { id: NavId | "more" }) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden>
      {ICONS[id]}
    </svg>
  );
}

export function TabBar() {
  const isLg = useIsLg();
  const { productMode, navCollapsed, toggleNav } = useApp();
  if (isLg) return null;

  const allowed = navIdsForMode(productMode);
  const tabs = TABS[productMode].map((id) => ITEMS.find((i) => i.id === id)!);
  const tabIds = new Set<string>(tabs.map((t) => t.id));
  const hasMore = ITEMS.some((i) => !tabIds.has(i.id) && (allowed == null || allowed.has(i.id)));

  return (
    <nav
      aria-label="Tabs"
      data-testid="tab-bar"
      // The open drawer is modal: what sits behind its backdrop must not take focus or taps.
      inert={!navCollapsed}
      aria-hidden={!navCollapsed || undefined}
      className="flex shrink-0 border-t border-[var(--color-line)] bg-[var(--color-panel)]"
    >
      {tabs.map((t) => (
        <NavLink
          key={t.id}
          to={t.to}
          end={t.to === "/"}
          className={({ isActive }) => `${cell} ${isActive ? active : idle}`}
        >
          <Icon id={t.id} />
          <span className={label}>{t.label}</span>
        </NavLink>
      ))}
      {hasMore && (
        <button
          type="button"
          onClick={toggleNav}
          aria-label="More pages"
          aria-controls="primary-nav"
          aria-expanded={!navCollapsed}
          className={`${cell} ${idle}`}
        >
          <Icon id="more" />
          <span className={label}>More</span>
        </button>
      )}
    </nav>
  );
}
