import { NavLink } from "react-router-dom";
import { useApp } from "@/context/AppContext";
import type { NavId } from "@/lib/types";

type NavItem = { id: NavId; label: string; to: string; short: string; hint: string };

/**
 * Grouped by the question the user is holding, not by the module that serves it.
 * Ten surfaces in one flat list reads as a settings menu; four groups of two or
 * three reads as a product with a shape.
 */
const GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "Ask",
    items: [{ id: "chat", label: "Chat", to: "/", short: "C", hint: "Ask about your data" }],
  },
  {
    title: "Data",
    items: [
      { id: "spaces", label: "Spaces", to: "/spaces", short: "Sp", hint: "What a question can see" },
      {
        id: "library",
        label: "Library",
        to: "/library",
        short: "L",
        hint: "Every source and where it lives",
      },
      {
        id: "studio",
        label: "Studio",
        to: "/studio",
        short: "St",
        hint: "Ingest, promote, quarantine",
      },
      {
        id: "ontology",
        label: "Ontology",
        to: "/ontology",
        short: "O",
        hint: "Objects, links, actions, metrics",
      },
    ],
  },
  {
    title: "Govern",
    items: [
      {
        id: "amend",
        label: "Amend",
        to: "/amend",
        short: "Am",
        hint: "Propose and confirm a change",
      },
      {
        id: "audit",
        label: "Audit",
        to: "/audit",
        short: "Au",
        hint: "Ledger and chain verification",
      },
      { id: "trust", label: "Trust", to: "/trust", short: "T", hint: "Evidence behind the badges" },
    ],
  },
  {
    title: "Operate",
    items: [
      { id: "runs", label: "Runs", to: "/runs", short: "R", hint: "Ingest and query progress" },
      {
        id: "admin",
        label: "Admin",
        to: "/admin",
        short: "Ad",
        hint: "People, access, compute pools",
      },
    ],
  },
];

const ADMIN_ONLY: NavId[] = ["admin"];

function linkClass(isActive: boolean): string {
  return `block px-3 py-1.5 text-sm font-medium ${
    isActive
      ? "border-l-2 border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-ink)]"
      : "border-l-2 border-transparent text-[var(--color-ink-muted)] hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]"
  }`;
}

export function LeftNav() {
  const { navCollapsed, role } = useApp();
  const visible = GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((i) => !ADMIN_ONLY.includes(i.id) || role === "admin"),
  })).filter((g) => g.items.length);

  if (navCollapsed) {
    return (
      <nav
        aria-label="Primary"
        className="flex w-12 shrink-0 flex-col items-center gap-1 overflow-y-auto border-r border-[var(--color-line)] bg-[var(--color-panel)] py-3"
      >
        {visible.map((group, gi) => (
          <div key={group.title} className="flex w-full flex-col items-center gap-1">
            {gi > 0 && <span className="my-1 h-px w-6 bg-[var(--color-line)]" aria-hidden />}
            {group.items.map((item) => (
              <NavLink
                key={item.id}
                to={item.to}
                end={item.to === "/"}
                title={`${item.label} — ${item.hint}`}
                className={({ isActive }) =>
                  `flex h-8 w-8 items-center justify-center text-[11px] font-semibold tracking-wide ${
                    isActive
                      ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                      : "text-[var(--color-ink-muted)] hover:bg-[var(--color-paper-2)]"
                  }`
                }
              >
                {item.short}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
    );
  }

  return (
    <nav
      aria-label="Primary"
      className="flex w-52 shrink-0 flex-col overflow-y-auto border-r border-[var(--color-line)] bg-[var(--color-panel)]"
    >
      <div className="border-b border-[var(--color-line)] px-4 py-4">
        <p className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-tight text-[var(--color-ink)]">
          netie
        </p>
        <p className="mt-0.5 text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
          DMS
        </p>
      </div>
      <div className="flex flex-1 flex-col gap-3 p-2">
        {visible.map((group) => (
          <div key={group.title}>
            <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--color-ink-muted)]">
              {group.title}
            </p>
            <ul className="flex flex-col gap-0.5">
              {group.items.map((item) => (
                <li key={item.id}>
                  <NavLink
                    to={item.to}
                    end={item.to === "/"}
                    title={item.hint}
                    className={({ isActive }) => linkClass(isActive)}
                  >
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      {role !== "admin" && (
        <p className="border-t border-[var(--color-line)] px-3 py-2 text-[10px] leading-snug text-[var(--color-ink-muted)]">
          Admin is hidden for the {role} role.
        </p>
      )}
    </nav>
  );
}
