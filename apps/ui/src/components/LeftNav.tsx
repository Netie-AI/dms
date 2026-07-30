import { NavLink } from "react-router-dom";
import { useApp } from "@/context/AppContext";
import type { NavId } from "@/lib/types";

const PRIMARY: { id: NavId; label: string; to: string }[] = [
  { id: "chat", label: "Chat", to: "/" },
  { id: "library", label: "Library", to: "/library" },
  { id: "studio", label: "Studio", to: "/studio" },
  { id: "amend", label: "Amend", to: "/amend" },
  { id: "audit", label: "Audit", to: "/audit" },
  { id: "runs", label: "Runs", to: "/runs" },
];

export function LeftNav() {
  const { navCollapsed, role } = useApp();

  if (navCollapsed) {
    return (
      <nav
        aria-label="Primary"
        className="flex w-12 flex-col items-center gap-2 border-r border-[var(--color-line)] bg-[var(--color-panel)] py-3"
      >
        {PRIMARY.map((item) => (
          <NavLink
            key={item.id}
            to={item.to}
            end={item.to === "/"}
            title={item.label}
            className={({ isActive }) =>
              `flex h-9 w-9 items-center justify-center text-xs font-semibold tracking-wide ${
                isActive
                  ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                  : "text-[var(--color-ink-muted)] hover:bg-[var(--color-paper-2)]"
              }`
            }
          >
            {item.label.slice(0, 1)}
          </NavLink>
        ))}
        {role === "admin" && (
          <NavLink
            to="/admin"
            title="Admin"
            className={({ isActive }) =>
              `mt-auto flex h-9 w-9 items-center justify-center text-xs font-semibold ${
                isActive
                  ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                  : "text-[var(--color-ink-muted)]"
              }`
            }
          >
            A
          </NavLink>
        )}
      </nav>
    );
  }

  return (
    <nav
      aria-label="Primary"
      className="flex w-52 shrink-0 flex-col border-r border-[var(--color-line)] bg-[var(--color-panel)]"
    >
      <div className="border-b border-[var(--color-line)] px-4 py-4">
        <p className="font-[family-name:var(--font-display)] text-xl font-semibold tracking-tight text-[var(--color-ink)]">
          netie
        </p>
        <p className="mt-0.5 text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
          DMS
        </p>
      </div>
      <ul className="flex flex-1 flex-col gap-0.5 p-2">
        {PRIMARY.map((item) => (
          <li key={item.id}>
            <NavLink
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `block px-3 py-2 text-sm font-medium ${
                  isActive
                    ? "border-l-2 border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-ink)]"
                    : "border-l-2 border-transparent text-[var(--color-ink-muted)] hover:bg-[var(--color-paper-2)] hover:text-[var(--color-ink)]"
                }`
              }
            >
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
      <div className="border-t border-[var(--color-line)] p-2">
        <NavLink
          to="/admin"
          className={({ isActive }) =>
            `block px-3 py-2 text-sm font-medium ${
              isActive
                ? "border-l-2 border-[var(--color-accent)] bg-[var(--color-accent-soft)]"
                : "border-l-2 border-transparent text-[var(--color-ink-muted)] hover:bg-[var(--color-paper-2)]"
            }`
          }
        >
          Admin
        </NavLink>
      </div>
    </nav>
  );
}
