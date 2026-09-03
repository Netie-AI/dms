import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AnswerRowsTable } from "@/components/AnswerRowsTable";
import { useApp } from "@/context/AppContext";
import {
  fetchBronzePreview,
  fetchPromoteReceipt,
  fetchWarehousePreview,
  PREVIEW_PAGE_SIZE,
  previewForNode,
  type PromoteReceiptState,
  type TablePreview,
  type TreeNode as ApiTreeNode,
} from "@/lib/api";
import { bronzeWhenLabel } from "@/lib/bronzeProvenance";
import { PromoteReceiptPanel } from "@/components/PromoteReceiptPanel";

type TreeMeta = {
  kind?: string;
  scope?: string;
  row_count?: number;
  table?: string;
  ref?: string;
  space_name?: string | null;
  source?: string | null;
  extracted_at?: string | null;
  truncated?: boolean | null;
  source_kind?: string | null;
  target?: string | null;
};

type TreeNode = ApiTreeNode & {
  node_type?: "source" | "bronze" | "warehouse" | "silver" | "gold";
  meta?: TreeMeta | Record<string, unknown> | null;
};

type LibraryTree = {
  space_id?: string | null;
  space_name?: string | null;
  nodes: ApiTreeNode[];
};

type Preview = TablePreview & { table: string; row_count: number };

function metaField(meta: TreeMeta | Record<string, unknown> | null | undefined, key: string): string {
  const v = meta?.[key as keyof typeof meta];
  return v == null || v === "" ? "—" : String(v);
}

type ActiveRef =
  | { kind: "warehouse"; table: string }
  | { kind: "bronze"; table: string }
  | { kind: "source"; id: string; label: string; meta?: TreeMeta | Record<string, unknown> | null }
  | { kind: "promote"; id: string; target: string }
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
        const target = previewForNode(n.id);
        const selected =
          (target &&
            active &&
            (active.kind === "bronze" || active.kind === "warehouse") &&
            target.kind === active.kind &&
            target.table === active.table) ||
          (active?.kind === "source" && n.id === active.id) ||
          (active?.kind === "promote" && n.id === active.id);
        const pad = 8 + depth * 12;
        return (
          <li key={n.id} role="treeitem" aria-expanded={isFolder ? open : undefined}>
            <button
              type="button"
              onClick={() => (isFolder ? toggle(n.id) : onSelect(n))}
              data-testid={
                n.node_type === "silver" || n.node_type === "gold"
                  ? `promote-node-${String((n.meta as TreeMeta | undefined)?.target ?? n.id)}`
                  : undefined
              }
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
              {Boolean(
                n.meta &&
                  "truncated" in n.meta &&
                  (n.meta as TreeMeta).truncated,
              ) && (
                <span
                  data-testid="bronze-node-truncated"
                  className="text-[10px] text-[var(--color-danger)]"
                >
                  capped at max_rows
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
  const { databaseConfigured, activeSpaceId, activeSpace, productMode } = useApp();
  const navigate = useNavigate();
  const [tree, setTree] = useState<LibraryTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [active, setActive] = useState<ActiveRef>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [previewOffset, setPreviewOffset] = useState(0);
  const [receipt, setReceipt] = useState<PromoteReceiptState | null>(null);
  const [receiptBusy, setReceiptBusy] = useState(false);
  const [receiptErr, setReceiptErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(
    () =>
      new Set([
        "folder:sources",
        "folder:bronze",
        "folder:warehouse",
        "folder:silver",
        "folder:gold",
      ]),
  );
  const [filter, setFilter] = useState("");

  useEffect(() => {
    // Drop previous Space's tree/selection/preview before the next fetch lands.
    setTree(null);
    setActive(null);
    setPreview(null);
    setPreviewErr(null);
    setPreviewOffset(0);
    setReceipt(null);
    setReceiptErr(null);
    setLoading(true);
    setErr(null);
    const ctrl = new AbortController();
    const q = activeSpaceId ? `?space_id=${encodeURIComponent(activeSpaceId)}` : "";
    void fetch(`/api/v1/library/tree${q}`, { signal: ctrl.signal })
      .then(async (r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.json();
      })
      .then((body: LibraryTree) => {
        if (ctrl.signal.aborted) return;
        setTree(body);
        const firstWh = body.nodes
          .find((n) => n.id === "folder:warehouse")
          ?.children?.find((c) => previewForNode(c.id));
        if (firstWh) {
          const target = previewForNode(firstWh.id);
          if (target) setActive({ kind: target.kind, table: target.table });
        }
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setErr(String(e));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [activeSpaceId]);

  useEffect(() => {
    if (!active || active.kind === "source" || active.kind === "promote") {
      if (active?.kind === "source" || active?.kind === "promote") {
        setPreview(null);
        setPreviewErr(null);
        setPreviewBusy(false);
      }
      return;
    }
    setPreviewErr(null);
    setPreviewBusy(true);
    const load =
      active.kind === "warehouse"
        ? fetchWarehousePreview(active.table, PREVIEW_PAGE_SIZE, previewOffset, activeSpaceId)
        : fetchBronzePreview(active.table, PREVIEW_PAGE_SIZE, previewOffset, activeSpaceId);
    void load
      .then((p) => setPreview(p as Preview))
      .catch((e) => {
        setPreview(null);
        setPreviewErr(String(e));
      })
      .finally(() => setPreviewBusy(false));
  }, [active, previewOffset, activeSpaceId]);

  useEffect(() => {
    if (active?.kind !== "promote") {
      setReceipt(null);
      setReceiptErr(null);
      setReceiptBusy(false);
      return;
    }
    const ctrl = new AbortController();
    setReceiptBusy(true);
    setReceiptErr(null);
    void fetchPromoteReceipt(active.target, activeSpaceId, ctrl.signal)
      .then((s) => {
        if (!ctrl.signal.aborted) setReceipt(s);
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setReceipt(null);
        setReceiptErr(String(e));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setReceiptBusy(false);
      });
    return () => ctrl.abort();
  }, [active, activeSpaceId]);

  const filteredNodes = useMemo(() => {
    if (!tree) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return tree.nodes;

    const filterNode = (n: ApiTreeNode): ApiTreeNode | null => {
      if (n.kind === "leaf") {
        return n.label.toLowerCase().includes(q) ? n : null;
      }
      const kids = (n.children || []).map(filterNode).filter(Boolean) as ApiTreeNode[];
      if (kids.length || n.label.toLowerCase().includes(q)) {
        return { ...n, children: kids };
      }
      return null;
    };
    return tree.nodes.map(filterNode).filter(Boolean) as ApiTreeNode[];
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
    const previewTarget = previewForNode(n.id);
    if (previewTarget) {
      setPreviewOffset(0);
      setActive({ kind: previewTarget.kind, table: previewTarget.table });
      return;
    }
    if (n.node_type === "silver" || n.node_type === "gold") {
      const meta = n.meta as TreeMeta | undefined;
      const target = typeof meta?.target === "string" ? meta.target : "";
      if (!target) return;
      setActive({ kind: "promote", id: n.id, target });
      setPreview(null);
      setPreviewErr(null);
      return;
    }
    if (n.node_type === "source" || n.id.startsWith("source:")) {
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
          expand Sources / Bronze / Warehouse / Silver / Gold like DbGate. Excel stays source-only.
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
                No nodes
                {productMode === "cream" ? (
                  " — switch to Operate in the top bar to ingest files."
                ) : (
                  <>
                    {" — "}
                    <Link to="/studio" className="text-[var(--color-accent)] hover:underline">
                      ingest in Studio
                    </Link>
                  </>
                )}
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
                  <dd className="inline">{metaField(active.meta, "kind")}</dd>
                </div>
                <div>
                  <dt className="inline font-semibold text-[var(--color-ink)]">scope </dt>
                  <dd className="inline">{metaField(active.meta, "scope")}</dd>
                </div>
                <div>
                  <dt className="inline font-semibold text-[var(--color-ink)]">ref </dt>
                  <dd className="inline break-all font-mono">{metaField(active.meta, "ref")}</dd>
                </div>
              </dl>
              {productMode === "graphite" ? (
                <Link
                  to="/studio"
                  className="mt-4 inline-block text-sm text-[var(--color-accent)] hover:underline"
                >
                  Open Studio to ingest →
                </Link>
              ) : (
                <p className="mt-4 text-sm text-[var(--color-ink-muted)]">
                  Switch to Operate to ingest this file.
                </p>
              )}
            </div>
          )}
          {active?.kind === "promote" && (
            <div className="max-w-xl border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-4 py-4">
              <h2 className="text-lg font-semibold">{active.target}</h2>
              {receiptBusy && (
                <p className="mt-2 text-xs text-[var(--color-ink-muted)]">Loading receipt…</p>
              )}
              {receiptErr && (
                <p className="mt-2 text-sm text-[var(--color-danger)]">{receiptErr}</p>
              )}
              {receipt ? <PromoteReceiptPanel state={receipt} /> : null}
            </div>
          )}
          {previewBusy && active?.kind !== "promote" && (
            <p className="mb-3 text-xs text-[var(--color-ink-muted)]">Loading preview…</p>
          )}
          {previewErr && active?.kind !== "promote" && (
            <p className="text-sm text-[var(--color-danger)]">{previewErr}</p>
          )}
          {preview && (active?.kind === "bronze" || active?.kind === "warehouse") && (
            <>
              <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-lg font-semibold text-[var(--color-ink)]">{preview.table}</h2>
                <div className="flex flex-wrap items-center gap-3">
                  <p className="text-xs text-[var(--color-ink-muted)]">
                    {preview.row_count.toLocaleString()} rows · {preview.columns.length} columns
                    {preview.kind === "bronze" ? " · bronze" : ""}
                  </p>
                  {preview.kind === "bronze" && (
                    <p
                      className="text-xs text-[var(--color-ink-muted)]"
                      data-testid="bronze-extracted-at"
                    >
                      {preview.source_kind === "sql" && preview.source ? (
                        <span data-testid="bronze-source">{preview.source} · </span>
                      ) : null}
                      {bronzeWhenLabel(preview)}
                    </p>
                  )}
                  {preview.kind === "bronze" && preview.truncated ? (
                    <p
                      data-testid="bronze-truncated-flag"
                      className="text-xs text-[var(--color-danger)]"
                    >
                      capped at max_rows
                    </p>
                  ) : null}
                  <button
                    type="button"
                    onClick={() =>
                      navigate("/", {
                        state: {
                          groundedTables: [preview.table],
                          groundedLabels: [preview.table],
                        },
                      })
                    }
                    className="border border-[var(--color-line)] px-2.5 py-1 text-xs hover:border-[var(--color-accent)]"
                  >
                    Ask about this table
                  </button>
                </div>
              </div>
              {preview.note && (
                <p className="mb-3 text-xs text-[var(--color-ink-muted)]">{preview.note}</p>
              )}
              <AnswerRowsTable
                rows={preview.rows}
                totalRows={preview.row_count}
                pageOffset={preview.offset ?? previewOffset}
                pageSize={preview.limit ?? PREVIEW_PAGE_SIZE}
                onPageChange={setPreviewOffset}
              />
            </>
          )}
        </main>
      </div>
    </div>
  );
}
