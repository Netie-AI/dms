import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useApp } from "@/context/AppContext";
import type { AppRole } from "@/lib/types";

const ROLES: AppRole[] = ["viewer", "steward", "admin"];
type Theme = "cream" | "graphite";

export function TopBar() {
  const {
    toggleNav,
    spaces,
    activeSpaceId,
    setActiveSpaceId,
    role,
    setRole,
    apiOnline,
  } = useApp();
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [newOpen, setNewOpen] = useState(false);
  const [theme, setTheme] = useState<Theme>(() =>
    window.localStorage.getItem("dms-theme") === "graphite" ? "graphite" : "cream",
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("dms-theme", theme);
  }, [theme]);

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-[var(--color-line)] bg-[var(--color-panel)]/90 px-3 backdrop-blur-sm">
      <button
        type="button"
        onClick={toggleNav}
        className="flex h-8 w-8 items-center justify-center border border-[var(--color-line)] text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
        aria-label="Toggle sidebar"
      >
        <span className="block h-3 w-3.5 border-y border-[var(--color-ink)] border-opacity-70" />
      </button>

      <label className="sr-only" htmlFor="space-switcher">
        Space
      </label>
      <select
        id="space-switcher"
        value={activeSpaceId ?? ""}
        onChange={(e) =>
          setActiveSpaceId(e.target.value === "" ? null : e.target.value)
        }
        className="h-8 max-w-[12rem] border border-[var(--color-line)] bg-transparent px-2 text-sm"
      >
        <option value="">Company (default ACL)</option>
        {spaces.map((s) => (
          <option key={s.id} value={s.id}>
            Space: {s.name}
          </option>
        ))}
      </select>

      <div className="relative">
        <button
          type="button"
          onClick={() => setNewOpen((o) => !o)}
          className="h-8 border border-[var(--color-accent)] bg-[var(--color-accent)] px-3 text-sm font-medium text-[var(--color-on-accent)]"
        >
          + New
        </button>
        {newOpen && (
          <div className="absolute left-0 top-full z-20 mt-1 min-w-[11rem] border border-[var(--color-line)] bg-[var(--color-panel)] py-1 shadow-sm">
            <button
              type="button"
              className="block w-full px-3 py-1.5 text-left text-sm hover:bg-[var(--color-paper-2)]"
              onClick={() => {
                setNewOpen(false);
                navigate("/studio");
              }}
            >
              Upload source
            </button>
            <button
              type="button"
              className="block w-full px-3 py-1.5 text-left text-sm hover:bg-[var(--color-paper-2)]"
              onClick={() => {
                setNewOpen(false);
                navigate("/amend");
              }}
            >
              New amend proposal
            </button>
            <button
              type="button"
              className="block w-full px-3 py-1.5 text-left text-sm hover:bg-[var(--color-paper-2)]"
              onClick={() => {
                setNewOpen(false);
                navigate("/spaces");
              }}
            >
              New Space
            </button>
          </div>
        )}
      </div>

      <Link
        to="/spaces"
        className="flex h-8 items-center border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-2.5 text-sm text-[var(--color-ink)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
        title="Manage Spaces — scope, sources, members"
      >
        Manage
      </Link>

      <Link
        to="/library"
        className="flex h-8 items-center gap-1.5 border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-2.5 text-sm text-[var(--color-ink)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
        title="Data Map — warehouse & bronze browser"
        aria-label="Open Database Library"
      >
        <span className="font-mono text-[11px] leading-none" aria-hidden>
          DB
        </span>
        <span className="hidden sm:inline">Library</span>
      </Link>

      <div className="mx-auto flex w-full max-w-md flex-1">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search Spaces, sources, proposals…"
          className="h-8 w-full border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-3 text-sm placeholder:text-[var(--color-ink-muted)]"
        />
      </div>

      <span
        className={`hidden text-xs sm:inline ${
          apiOnline === true
            ? "text-[var(--color-badge-ok)]"
            : apiOnline === false
              ? "text-[var(--color-danger)]"
              : "text-[var(--color-ink-muted)]"
        }`}
      >
        {apiOnline === true ? "API · ok" : apiOnline === false ? "API · offline" : "API · …"}
      </span>

      <button
        type="button"
        onClick={() => setTheme((current) => (current === "cream" ? "graphite" : "cream"))}
        className="h-8 border border-[var(--color-line)] px-2 text-xs text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
        aria-label={`Switch to ${theme === "cream" ? "graphite" : "cream"} theme`}
      >
        {theme === "cream" ? "Graphite" : "Cream"}
      </button>

      <select
        aria-label="Role"
        value={role}
        onChange={(e) => setRole(e.target.value as AppRole)}
        className="h-8 border border-[var(--color-line)] bg-transparent px-2 text-sm capitalize"
      >
        {ROLES.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>

      <button
        type="button"
        className="h-8 border border-[var(--color-line)] px-2 text-sm text-[var(--color-ink-muted)]"
        title="User menu (U0 stub)"
      >
        aminah@
      </button>
    </header>
  );
}
