import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AnswerRowsTable } from "@/components/AnswerRowsTable";
import { useApp } from "@/context/AppContext";

type TreeMeta = {
  kind?: string;
  scope?: string;
  row_count?: number;
  table?: string;
  ref?: string;
  space_name?: string | null;
};

type TreeNode = {
  id: string;
  kind: "folder" | "leaf";
  label: string;
  node_type?: "source" | "bronze" | "warehouse";
  meta?: TreeMeta;
  children?: TreeNode[];
};

type LibraryTree = {
  space_id?: string | null;
  space_name?: string | null;
  nodes: TreeNode[];
};

type Preview = {
  table: string;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  note?: string;
  kind?: string;
};

type ActiveRef =
  | { kind: "warehouse"; table: string }
  | { kind: "bronze"; table: string }
  | { kind: "source"; id: string; label: string; meta?: TreeMeta }
  | null;

function TreeRows({
  nodes,
  depth,
  expanded,
  toggle,
  active,
  onSelect,
}: {
  nodes: TreeNode[];
  depth: number;
  expanded: Set<string>;
  toggle: (id: string) => void;
  active: ActiveRef;
  onSelect: (n: TreeNode) => void;
}) {
  return (
    <ul role={depth === 0 ? "tree" : "group"}>
      {nodes.map((n) => {
        const isFolder = n.kind === "folder";
        const open = expanded.has(n.id);
        const selected =
          (active?.kind === "warehouse" && n.node_type === "warehouse" && active.table === n.meta?.table) ||
          (active?.kind === "bronze" && n.node_type === "bronze" && active.table === n.meta?.table) ||
          (active?.kind === "source" && n.node_type === "source" && active.id === n.id);
        const pad = 8 + depth * 12;
        return (
          <li key={n.id} role="treeitem" aria-expanded={isFolder ? open : undefined}>
            <button
              type="button"
              onClick={() => (isFolder ? toggle(n.id) : onSelect(n))}
              className={`flex w-full items-center gap-1 py-1.5 pr-2 text-left text-sm ${
                selected
                  ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                  : "text-[var(--color-ink)] hover:bg-[var(--color-paper-2)]"
              }`}
              style={{ paddingLeft: pad }}
            >
              <span className="w-3 shrink-0 font-mono text-[10px] text-[var(--color-ink-muted)]">
                {isFolder ? (open ? "▾" : "▸") : "·"}
              </span>
              <span className="min-w-0 flex-1 truncate font-medium">{n.label}</span>
              {typeof n.meta?.row_count === "number" && (
                <span className="tabular-nums text-[10px] text-[var(--color-ink-muted)]">
                  {n.meta.row_count}
                </span>
              )}
            </button>
            {isFolder && open && n.children && n.children.length > 0 && (
              <TreeRows
                nodes={n.children}
                depth={depth + 1}
                expanded={expanded}
                toggle={toggle}
                active={active}
                onSelect={onSelect}
              />
            )}
            {isFolder && open && (!n.children || n.children.length === 0) && (
              <p
                className="py-1 text-[11px] text-[var(--color-ink-muted)]"
                style={{ paddingLeft: pad + 14 }}
              >
                Empty
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function LibraryPage() {
  const { databaseConfigured, activeSpaceId, activeSpace } = useApp();
  const [tree, setTree] = useState<LibraryTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [active, setActive] = useState<ActiveRef>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(["folder:sources", "folder:bronze", "folder:warehouse"]),
  );
  const [filter, setFilter] = useState("");

  useEffect(() => {
    setLoading(true);
    setErr(null);
    const q = activeSpaceId ? `?space_id=${encodeURIComponent(activeSpaceId)}` : "";
    void fetch(`/api/v1/library/tree${q}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then((body: LibraryTree) => {
        setTree(body);
        const firstWh = body.nodes
          .find((n) => n.id === "folder:warehouse")
          ?.children?.find((c) => c.node_type === "warehouse");
        if (firstWh?.meta?.table) {
          setActive({ kind: "warehouse", table: firstWh.meta.table });
        }
      })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [activeSpaceId]);

  useEffect(() => {
    if (!active || active.kind === "source") {
      if (active?.kind === "source") {
        setPreview(null);
        setPreviewErr(null);
      }
      return;
    }
    setPreviewErr(null);
    setPreviewBusy(true);
    const path =
      active.kind === "warehouse"
        ? `/api/v1/library/warehouse/${encodeURIComponent(active.table)}/preview?limit=100`
        : `/api/v1/library/bronze/${encodeURIComponent(active.table)}/preview?limit=100`;
    void fetch(path)
      .then(async (r) => {
        if (!r.ok) throw new Error(await r.text());
        return r.json();
      })
      .then(setPreview)
      .catch((e) => {
        setPreview(null);
        setPreviewErr(String(e));
      })
      .finally(() => setPreviewBusy(false));
  }, [active]);

  const filteredNodes = useMemo(() => {
    if (!tree) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return tree.nodes;

    const filterNode = (n: TreeNode): TreeNode | null => {
      if (n.kind === "leaf") {
        return n.label.toLowerCase().includes(q) ? n : null;
      }
      const kids = (n.children || []).map(filterNode).filter(Boolean) as TreeNode[];
      if (kids.length || n.label.toLowerCase().includes(q)) {
        return { ...n, children: kids };
      }
      return null;
    };
    return tree.nodes.map(filterNode).filter(Boolean) as TreeNode[];
  }, [tree, filter]);

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const onSelect = (n: TreeNode) => {
    if (n.node_type === "warehouse" && n.meta?.table) {
      setActive({ kind: "warehouse", table: n.meta.table });
    } else if (n.node_type === "bronze" && n.meta?.table) {
      setActive({ kind: "bronze", table: n.meta.table });
    } else if (n.node_type === "source") {
      setActive({ kind: "source", id: n.id, label: n.label, meta: n.meta });
      setPreview(null);
    }
  };

  const dbOk = databaseConfigured;
  const scopeLabel = activeSpace?.name ?? tree?.space_name ?? "Company (default ACL)";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-[var(--color-line)] px-8 py-5">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
          U2 · Space repository
        </p>
        <h1 className="mt-2 font-[family-name:var(--font-display)] text-3xl font-semibold tracking-tight">
          Library
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-[var(--color-ink-muted)]">
          Folders and files for <strong className="text-[var(--color-ink)]">{scopeLabel}</strong> —
          expand Sources / Bronze / Warehouse like DbGate. Excel stays source-only.
        </p>
        {dbOk === false && (
          <p className="mt-3 border border-[var(--color-warn)]/40 bg-[var(--color-warn-soft)] px-3 py-2 text-xs text-[var(--color-warn)]">
            Postgres sources need <code className="font-mono">DATABASE_URL</code>. Warehouse + bronze
            still browse the local DuckDB lake.
          </p>
        )}
        {err && <p className="mt-3 text-sm text-[var(--color-danger)]">{err}</p>}
      </div>

      <div className="flex min-h-0 flex-1">
        <aside className="flex w-64 shrink-0 flex-col border-r border-[var(--color-line)] bg-[var(--color-panel)]">
          <div className="border-b border-[var(--color-line)] px-2 py-2">
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter files…"
              className="h-8 w-full border border-[var(--color-line)] bg-transparent px-2 text-xs"
            />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto py-1">
            {loading && (
              <p className="px-3 py-3 text-xs text-[var(--color-ink-muted)]">Loading tree…</p>
            )}
            {!loading && filteredNodes.length === 0 && (
              <p className="px-3 py-3 text-xs text-[var(--color-ink-muted)]">
                No nodes —{" "}
                <Link to="/studio" className="text-[var(--color-accent)] hover:underline">
                  ingest in Studio
                </Link>
              </p>
            )}
            {!loading && (
              <TreeRows
                nodes={filteredNodes}
                depth={0}
                expanded={expanded}
                toggle={toggle}
                active={active}
                onSelect={onSelect}
              />
            )}
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto px-6 py-4">
          {!active && (
            <p className="text-sm text-[var(--color-ink-muted)]">
              Select a file or table in the tree.
            </p>
          )}
          {active?.kind === "source" && (
            <div className="max-w-xl border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-4 py-4">
              <h2 className="text-lg font-semibold">{active.label}</h2>
              <p className="mt-2 text-sm text-[var(--color-ink-muted)]">
                Source file — Excel/CSV stay source-only. Preview lands after bronze promote.
              </p>
              <dl className="mt-4 space-y-1 text-xs text-[var(--color-ink-muted)]">
                <div>
                  <dt className="inline font-semibold text-[var(--color-ink)]">kind </dt>
                  <dd className="inline">{active.meta?.kind ?? "—"}</dd>
                </div>
                <div>
                  <dt className="inline font-semibold text-[var(--color-ink)]">scope </dt>
                  <dd className="inline">{active.meta?.scope ?? "—"}</dd>
                </div>
                <div>
                  <dt className="inline font-semibold text-[var(--color-ink)]">ref </dt>
                  <dd className="inline break-all font-mono">{active.meta?.ref ?? "—"}</dd>
                </div>
              </dl>
              <Link
                to="/studio"
                className="mt-4 inline-block text-sm text-[var(--color-accent)] hover:underline"
              >
                Open Studio to ingest →
              </Link>
            </div>
          )}
          {previewBusy && (
            <p className="mb-3 text-xs text-[var(--color-ink-muted)]">Loading preview…</p>
          )}
          {previewErr && <p className="text-sm text-[var(--color-danger)]">{previewErr}</p>}
          {preview && active?.kind !== "source" && (
            <>
              <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-lg font-semibold text-[var(--color-ink)]">{preview.table}</h2>
                <p className="text-xs text-[var(--color-ink-muted)]">
                  {preview.row_count.toLocaleString()} rows · {preview.columns.length} columns
                  {preview.kind === "bronze" ? " · bronze" : ""}
                </p>
              </div>
              {preview.note && (
                <p className="mb-3 text-xs text-[var(--color-ink-muted)]">{preview.note}</p>
              )}
              <AnswerRowsTable rows={preview.rows} maxRows={100} />
            </>
          )}
        </main>
      </div>
    </div>
  );
}
