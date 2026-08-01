import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useApp } from "@/context/AppContext";
import {
  fetchBronzePreview,
  fetchLibraryTree,
  fetchWarehousePreview,
  previewForNode,
  type LibraryTree,
  type TablePreview,
  type TreeNode,
} from "@/lib/api";

type FileRow = {
  file: string;
  classification?: string;
  reason?: string;
  fix?: string;
  table?: string | null;
  header_row?: number | null;
  ingested?: boolean;
};

type Receipt = {
  files_seen: number;
  ingested: number;
  quarantined: number;
  need_attention?: number;
  reasons: { file: string; reason: string; fix?: string }[];
  ingest_id: string;
  table?: string | null;
  summary?: string | null;
  per_class?: Record<string, number> | null;
  files?: FileRow[] | null;
};

/** Flatten the tree to the leaves that can actually be previewed and asked about. */
function askableLeaves(nodes: TreeNode[], out: TreeNode[] = []): TreeNode[] {
  for (const n of nodes) {
    if (n.kind === "leaf" && previewForNode(n.id)) out.push(n);
    if (n.children?.length) askableLeaves(n.children, out);
  }
  return out;
}

export function StudioPage() {
  const { setActivity, activeSpaceId } = useApp();
  const navigate = useNavigate();

  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [tree, setTree] = useState<LibraryTree | null>(null);
  const [treeErr, setTreeErr] = useState<string | null>(null);
  const [openFolders, setOpenFolders] = useState<Set<string>>(new Set(["folder:sources"]));
  const [activeId, setActiveId] = useState<string | null>(null);
  const [preview, setPreview] = useState<TablePreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  // Files the next question should be grounded in. Empty means the whole Space.
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const fileInput = useRef<HTMLInputElement | null>(null);
  const folderInput = useRef<HTMLInputElement | null>(null);

  const loadTree = useCallback(async () => {
    try {
      setTree(await fetchLibraryTree(activeSpaceId));
      setTreeErr(null);
    } catch (e) {
      setTreeErr(e instanceof Error ? e.message : "could not load the repository");
    }
  }, [activeSpaceId]);

  useEffect(() => {
    void loadTree();
  }, [loadTree]);

  const openPreview = useCallback(async (node: TreeNode) => {
    const target = previewForNode(node.id);
    setActiveId(node.id);
    setPreview(null);
    setPreviewErr(null);
    if (!target) {
      // Sources have no preview yet: the original bytes are not retained for
      // tabular uploads, so saying so beats rendering an empty grid.
      setPreviewErr("No stored copy of this file yet — preview its bronze table instead.");
      return;
    }
    setPreviewBusy(true);
    try {
      setPreview(
        target.kind === "bronze"
          ? await fetchBronzePreview(target.table)
          : await fetchWarehousePreview(target.table),
      );
    } catch (e) {
      setPreviewErr(e instanceof Error ? e.message : "preview failed");
    } finally {
      setPreviewBusy(false);
    }
  }, []);

  const onFiles = async (list: FileList | null) => {
    if (!list?.length) return;
    setBusy(true);
    setErr(null);
    setActivity({
      label: `Ingesting ${list.length} file${list.length === 1 ? "" : "s"}…`,
      progress: 20,
    });
    try {
      const fd = new FormData();
      const files = Array.from(list);
      setActivity({ label: "Classifying sheets…", progress: 45 });
      if (files.length === 1) {
        fd.append("file", files[0]);
        const res = await fetch("/api/v1/studio/ingest", { method: "POST", body: fd });
        if (!res.ok) throw new Error(await res.text());
        setReceipt((await res.json()) as Receipt);
      } else {
        for (const f of files) fd.append("files", f);
        const res = await fetch("/api/v1/studio/ingest-batch", { method: "POST", body: fd });
        if (!res.ok) throw new Error(await res.text());
        setReceipt((await res.json()) as Receipt);
      }
      setActivity({ label: "Receipt ready", progress: 100 });
      await loadTree();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "ingest failed");
    } finally {
      setBusy(false);
      window.setTimeout(() => setActivity(null), 600);
    }
  };

  const toggleSelected = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const leaves = useMemo(() => (tree ? askableLeaves(tree.nodes) : []), [tree]);
  const selectedLabels = useMemo(
    () => leaves.filter((l) => selected.has(l.id)).map((l) => l.label),
    [leaves, selected],
  );

  const askAboutSelection = () => {
    // Carried to Chat as an explicit grounding scope. The answer path still
    // decides what it can answer; this only narrows what it may look at.
    const tables = leaves
      .filter((l) => selected.has(l.id))
      .map((l) => previewForNode(l.id)?.table)
      .filter(Boolean) as string[];
    navigate("/", { state: { groundedTables: tables, groundedLabels: selectedLabels } });
  };

  const attention = receipt?.need_attention ?? receipt?.quarantined ?? 0;
  const fileRows: FileRow[] = receipt?.files?.length
    ? receipt.files
    : (receipt?.reasons.map((r) => ({
        file: r.file,
        reason: r.reason,
        fix: r.fix,
        classification: "NEED_ATTENTION",
        table: null,
      })) ?? []);

  const renderNode = (node: TreeNode, depth = 0) => {
    const isFolder = node.kind === "folder";
    const open = openFolders.has(node.id);
    const askable = !isFolder && !!previewForNode(node.id);
    return (
      <li key={node.id}>
        <div
          className={`flex items-center gap-2 py-1 pr-2 text-sm ${
            activeId === node.id ? "bg-[var(--color-accent)]/10" : ""
          }`}
          style={{ paddingLeft: `${depth * 0.85 + 0.25}rem` }}
        >
          {askable && (
            <input
              type="checkbox"
              aria-label={`Ground answers in ${node.label}`}
              checked={selected.has(node.id)}
              onChange={() => toggleSelected(node.id)}
              className="h-3.5 w-3.5 shrink-0 accent-[var(--color-accent)]"
            />
          )}
          <button
            type="button"
            onClick={() =>
              isFolder
                ? setOpenFolders((prev) => {
                    const next = new Set(prev);
                    if (next.has(node.id)) next.delete(node.id);
                    else next.add(node.id);
                    return next;
                  })
                : void openPreview(node)
            }
            className={`flex-1 truncate text-left hover:text-[var(--color-accent)] ${
              isFolder
                ? "font-medium text-[var(--color-ink)]"
                : "text-[var(--color-ink-muted)]"
            }`}
          >
            {isFolder ? (open ? "▾ " : "▸ ") : ""}
            {node.label}
            {isFolder && node.children?.length ? (
              <span className="ml-1 text-[10px] text-[var(--color-ink-muted)]">
                ({node.children.length})
              </span>
            ) : null}
          </button>
        </div>
        {isFolder && open && node.children?.length ? (
          <ul>{node.children.map((c) => renderNode(c, depth + 1))}</ul>
        ) : null}
      </li>
    );
  };

  return (
    <div className="h-full overflow-y-auto px-8 py-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
            U3 · T13
          </p>
          <h1 className="mt-2 font-[family-name:var(--font-display)] text-3xl font-semibold tracking-tight">
            Studio
          </h1>
          <p className="mt-3 max-w-xl text-[var(--color-ink-muted)]">
            Your files, and what the warehouse made of them. Every sheet is classified before
            bronze write — unstructured routes to the blob tier, never a silent partial success.
            Tick files to ground your next question in just those.
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            title="Upload a whole folder, including nested workbooks"
            disabled={busy}
            onClick={() => folderInput.current?.click()}
            className="border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-2 text-xs font-medium text-[var(--color-ink)] hover:border-[var(--color-accent)] disabled:opacity-50"
          >
            Folder
          </button>
          <button
            type="button"
            aria-label="Add files"
            title="Add CSV or Excel files"
            disabled={busy}
            onClick={() => fileInput.current?.click()}
            className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--color-accent)] text-xl font-semibold leading-none text-white hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "…" : "+"}
          </button>
        </div>
      </div>

      <input
        ref={fileInput}
        type="file"
        accept=".csv,.xlsx,.xlsm,.tsv"
        multiple
        className="hidden"
        disabled={busy}
        onChange={(e) => void onFiles(e.target.files)}
      />
      <input
        type="file"
        multiple
        className="hidden"
        disabled={busy}
        ref={(el) => {
          folderInput.current = el;
          // Not a React prop — must be set on the element to pick a directory.
          if (el) el.setAttribute("webkitdirectory", "");
        }}
        onChange={(e) => void onFiles(e.target.files)}
      />

      {err && <p className="mt-4 text-sm text-[var(--color-danger)]">{err}</p>}

      {selected.size > 0 && (
        <div className="mt-6 flex flex-wrap items-center gap-3 border border-[var(--color-accent)]/40 bg-[var(--color-accent)]/5 px-4 py-3 text-sm">
          <span className="text-[var(--color-ink)]">
            Grounding next question in <strong>{selected.size}</strong>{" "}
            {selected.size === 1 ? "file" : "files"}: {selectedLabels.slice(0, 3).join(", ")}
            {selectedLabels.length > 3 ? ` +${selectedLabels.length - 3}` : ""}
          </span>
          <button
            type="button"
            onClick={askAboutSelection}
            className="border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] hover:bg-[var(--color-accent)] hover:text-white"
          >
            Ask about these →
          </button>
          <button
            type="button"
            onClick={() => setSelected(new Set())}
            className="text-xs text-[var(--color-ink-muted)] hover:underline"
          >
            Clear
          </button>
        </div>
      )}

      <div className="mt-6 grid gap-6 lg:grid-cols-[22rem_1fr]">
        {/* Repository ------------------------------------------------------ */}
        <div className="border border-[var(--color-line)] bg-[var(--color-surface)]/60">
          <div className="flex items-center justify-between border-b border-[var(--color-line)] px-3 py-2">
            <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
              {tree?.space_name ? `${tree.space_name} · files` : "Files"}
            </span>
            <button
              type="button"
              onClick={() => void loadTree()}
              className="text-[11px] text-[var(--color-ink-muted)] hover:text-[var(--color-accent)]"
            >
              Refresh
            </button>
          </div>
          {treeErr && <p className="px-3 py-3 text-xs text-[var(--color-danger)]">{treeErr}</p>}
          {!tree && !treeErr && (
            <p className="px-3 py-3 text-xs text-[var(--color-ink-muted)]">Loading…</p>
          )}
          {tree && tree.nodes.length === 0 && (
            <p className="px-3 py-6 text-center text-xs text-[var(--color-ink-muted)]">
              Nothing here yet. Press + to add a CSV or workbook.
            </p>
          )}
          {tree && tree.nodes.length > 0 && (
            <ul className="max-h-[32rem] overflow-y-auto py-2">
              {tree.nodes.map((n) => renderNode(n))}
            </ul>
          )}
        </div>

        {/* Preview --------------------------------------------------------- */}
        <div className="border border-[var(--color-line)] bg-[var(--color-surface)]/60">
          <div className="border-b border-[var(--color-line)] px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
            {activeId ? activeId.split(":").slice(1).join(":") : "Preview"}
          </div>
          {!activeId && (
            <p className="px-4 py-10 text-center text-sm text-[var(--color-ink-muted)]">
              Pick a file on the left to see what is actually in it.
            </p>
          )}
          {previewBusy && (
            <p className="px-4 py-6 text-sm text-[var(--color-ink-muted)]">Reading…</p>
          )}
          {previewErr && (
            <p className="px-4 py-6 text-sm text-[var(--color-warn)]">{previewErr}</p>
          )}
          {preview && preview.rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-xs">
                <thead>
                  <tr className="border-b border-[var(--color-line)] text-[10px] uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
                    {preview.columns.map((c) => (
                      <th key={c} className="whitespace-nowrap px-3 py-2 font-semibold">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map((r, i) => (
                    <tr
                      key={i}
                      className="border-b border-[var(--color-line)]/60 text-[var(--color-ink)]"
                    >
                      {preview.columns.map((c) => (
                        <td key={c} className="whitespace-nowrap px-3 py-1.5">
                          {r[c] === null || r[c] === undefined ? (
                            <span className="text-[var(--color-ink-muted)]">—</span>
                          ) : (
                            String(r[c])
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="px-4 py-2 text-[11px] text-[var(--color-ink-muted)]">
                Showing {preview.rows.length} row{preview.rows.length === 1 ? "" : "s"}
                {preview.total_count != null ? ` of ${preview.total_count}` : ""}.
              </p>
            </div>
          )}
          {preview && preview.rows.length === 0 && (
            <p className="px-4 py-6 text-sm text-[var(--color-ink-muted)]">
              This table is empty.
            </p>
          )}
        </div>
      </div>

      {receipt && (
        <div className="mt-6 border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-4 py-4 text-sm">
          <p className="font-medium text-[var(--color-ink)]">
            {receipt.summary ??
              `Receipt · ${receipt.files_seen} files · ${receipt.ingested} ingested · ${attention} need attention`}
          </p>
          {receipt.per_class && (
            <p className="mt-1 text-[var(--color-ink-muted)]">
              {Object.entries(receipt.per_class)
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ")}
            </p>
          )}

          {fileRows.length > 0 && (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[36rem] border-collapse text-left text-xs">
                <thead>
                  <tr className="border-b border-[var(--color-line)] text-[10px] uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
                    <th className="py-2 pr-3 font-semibold">File</th>
                    <th className="py-2 pr-3 font-semibold">Class</th>
                    <th className="py-2 pr-3 font-semibold">Reason / fix</th>
                    <th className="py-2 font-semibold">Table</th>
                  </tr>
                </thead>
                <tbody>
                  {fileRows.map((f) => (
                    <tr
                      key={`${f.file}-${f.classification ?? ""}`}
                      className="border-b border-[var(--color-line)]/70"
                    >
                      <td className="py-2 pr-3 font-medium text-[var(--color-ink)]">{f.file}</td>
                      <td className="py-2 pr-3 tabular-nums text-[var(--color-ink-muted)]">
                        {f.classification ?? "—"}
                      </td>
                      <td className="py-2 pr-3 text-[var(--color-warn)]">
                        {[f.reason, f.fix].filter(Boolean).join(" — ") || "—"}
                      </td>
                      <td className="py-2 font-mono text-[var(--color-ink-muted)]">
                        {f.table ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="mt-4">
            <Link to="/library" className="text-sm text-[var(--color-accent)] hover:underline">
              View in Library →
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
