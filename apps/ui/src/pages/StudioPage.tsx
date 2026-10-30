import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AnswerRowsTable } from "@/components/AnswerRowsTable";
import { useApp } from "@/context/AppContext";
import {
  describeApiError,
  fetchLibraryTree,
  fetchTablePreview,
  fetchVerifiedQueries,
  ingestSqlSource,
  publishObject,
  PREVIEW_PAGE_SIZE,
  previewForNode,
  registerVerifiedQuery,
  type LibraryTree,
  type SqlSourceKind,
  type SqlSourceReceipt,
  type TablePreview,
  type TreeNode,
  type VerifiedQueryAsset,
} from "@/lib/api";
import { bronzeWhenLabel } from "@/lib/bronzeProvenance";
import {
  asObjectIdent,
  bronzeRelationFromPreview,
  objectDraftFromBronze,
} from "@/lib/objectDraft";
import { objectAskNavigateState } from "@/lib/objectAskHandoff";
import { sqlLinksLabel } from "@/lib/sqlSourceLinks";

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

const SQL_ENGINE_CARDS: {
  kind: SqlSourceKind;
  title: string;
  blurb: string;
  port: number;
}[] = [
  {
    kind: "mysql",
    title: "MySQL",
    blurb: "Copy tables into bronze. Ask never hits their engine.",
    port: 3306,
  },
  {
    kind: "sqlserver",
    title: "SQL Server",
    blurb: "Extract-only. DuckDB answers after the copy lands.",
    port: 1433,
  },
  {
    kind: "postgres",
    title: "Postgres",
    blurb: "Landed copy only. No live scanner, no ATTACH.",
    port: 5432,
  },
];

export function StudioPage() {
  const { setActivity, activeSpaceId } = useApp();
  const navigate = useNavigate();

  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [vqQuestion, setVqQuestion] = useState("");
  const [vqSql, setVqSql] = useState("");
  const [vqSynonyms, setVqSynonyms] = useState("");
  const [vqBusy, setVqBusy] = useState(false);
  const [vqErr, setVqErr] = useState<string | null>(null);
  const [vqOk, setVqOk] = useState<string | null>(null);
  const [vqAssets, setVqAssets] = useState<VerifiedQueryAsset[]>([]);
  const [sqlKind, setSqlKind] = useState<SqlSourceKind>("mysql");
  const [sqlHost, setSqlHost] = useState("");
  const [sqlPort, setSqlPort] = useState("");
  const [sqlDatabase, setSqlDatabase] = useState("");
  const [sqlUser, setSqlUser] = useState("");
  const [sqlPassword, setSqlPassword] = useState("");
  const [sqlTables, setSqlTables] = useState("");
  const [sqlMaxRows, setSqlMaxRows] = useState("");
  const [sqlBusy, setSqlBusy] = useState(false);
  const [sqlErr, setSqlErr] = useState<string | null>(null);
  const [sqlProgress, setSqlProgress] = useState<string | null>(null);
  const [sqlReceipt, setSqlReceipt] = useState<SqlSourceReceipt | null>(null);
  const [objName, setObjName] = useState("");
  const [objAliases, setObjAliases] = useState<Record<string, string>>({});
  const [objBusy, setObjBusy] = useState(false);
  const [objErr, setObjErr] = useState<string | null>(null);
  const [objOk, setObjOk] = useState<string | null>(null);
  const [objToolSql, setObjToolSql] = useState("");
  const [objectRelation, setObjectRelation] = useState<string | null>(null);
  const [publishedTool, setPublishedTool] = useState<string | null>(null);

  const [tree, setTree] = useState<LibraryTree | null>(null);
  const [treeErr, setTreeErr] = useState<string | null>(null);
  const [openFolders, setOpenFolders] = useState<Set<string>>(
    new Set(["folder:sources", "folder:bronze"]),
  );
  const [activeId, setActiveId] = useState<string | null>(null);
  const [preview, setPreview] = useState<TablePreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [previewOffset, setPreviewOffset] = useState(0);
  const [previewTarget, setPreviewTarget] = useState<{
    kind: "bronze" | "warehouse";
    table: string;
  } | null>(null);
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

  const loadVerified = useCallback(async () => {
    try {
      setVqAssets(await fetchVerifiedQueries(activeSpaceId));
    } catch (e) {
      setVqErr(e instanceof Error ? e.message : "could not list certified questions");
    }
  }, [activeSpaceId]);

  useEffect(() => {
    // Switching Space must not leave the previous Space's tree/preview/selection.
    setTree(null);
    setTreeErr(null);
    setActiveId(null);
    setPreview(null);
    setPreviewErr(null);
    setPreviewTarget(null);
    setPreviewOffset(0);
    setSelected(new Set());
    setVqQuestion("");
    setVqSql("");
    setVqErr(null);
    setVqOk(null);
    setVqAssets([]);
    setSqlErr(null);
    setSqlReceipt(null);
    setObjErr(null);
    setObjOk(null);
    setObjName("");
    setObjToolSql("");
    setObjectRelation(null);
    setPublishedTool(null);
    void loadTree();
    void loadVerified();
  }, [loadTree, loadVerified]);

  const openPreview = useCallback((node: TreeNode) => {
    const target = previewForNode(node.id);
    setActiveId(node.id);
    setPreview(null);
    setPreviewErr(null);
    setPreviewOffset(0);
    if (!target) {
      setPreviewTarget(null);
      setPreviewErr("No stored copy of this file yet — preview its bronze table instead.");
      return;
    }
    setPreviewTarget(target);
  }, []);

  useEffect(() => {
    if (!previewTarget) return;
    setPreviewBusy(true);
    setPreviewErr(null);
    void fetchTablePreview(previewTarget, PREVIEW_PAGE_SIZE, previewOffset, activeSpaceId)
      .then(setPreview)
      .catch((e) => {
        setPreview(null);
        setPreviewErr(e instanceof Error ? e.message : "preview failed");
      })
      .finally(() => setPreviewBusy(false));
  }, [previewTarget, previewOffset, activeSpaceId]);

  const applyObjectDraft = useCallback(
    (
      relation: string,
      columns: string[],
      rows?: Record<string, unknown>[],
    ) => {
      const draft = objectDraftFromBronze(relation, columns, rows);
      setObjectRelation(relation);
      setObjName(draft.name);
      setObjAliases(draft.aliases);
      setObjToolSql(draft.toolSql);
      setPublishedTool(null);
      setObjOk(null);
      setObjErr(null);
    },
    [],
  );

  useEffect(() => {
    const rel = bronzeRelationFromPreview(previewTarget);
    if (!rel || !preview) return;
    if (objectRelation === rel) return;
    const cols = preview.columns.length
      ? preview.columns
      : Object.keys(preview.rows[0] ?? {});
    applyObjectDraft(rel, cols, preview.rows);
  }, [previewTarget, preview, objectRelation, applyObjectDraft]);

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
      if (activeSpaceId) {
        fd.append("space_id", activeSpaceId);
      }
      if (files.length === 1) {
        fd.append("file", files[0]);
        const res = await fetch("/api/v1/studio/ingest", { method: "POST", body: fd });
        if (!res.ok) throw new Error(describeApiError(await res.text()));
        setReceipt((await res.json()) as Receipt);
      } else {
        for (const f of files) fd.append("files", f);
        const res = await fetch("/api/v1/studio/ingest-batch", { method: "POST", body: fd });
        if (!res.ok) throw new Error(describeApiError(await res.text()));
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

  const registerCertified = async () => {
    const question = vqQuestion.trim();
    const sql = vqSql.trim();
    if (!question || !sql) return;
    setVqBusy(true);
    setVqErr(null);
    setVqOk(null);
    try {
      const synonyms = vqSynonyms
        .split(/[\n,]/)
        .map((s) => s.trim())
        .filter(Boolean);
      const asset = await registerVerifiedQuery({
        spaceId: activeSpaceId,
        question,
        sql,
        synonyms,
      });
      setVqOk(`Registered ${asset.asset_id} for this Space.`);
      setVqQuestion("");
      setVqSql("");
      setVqSynonyms("");
      await loadVerified();
    } catch (e) {
      setVqErr(e instanceof Error ? e.message : "register failed");
    } finally {
      setVqBusy(false);
    }
  };

  const extractSql = async () => {
    if (!sqlHost.trim() || !sqlDatabase.trim()) return;
    const rawCap = sqlMaxRows.trim();
    let maxRows: number | undefined;
    if (rawCap) {
      const n = Number(rawCap);
      if (!Number.isFinite(n) || n < 1) {
        setSqlErr("Max rows must be at least 1.");
        return;
      }
      maxRows = Math.floor(n);
    }
    setSqlBusy(true);
    setSqlErr(null);
    setSqlReceipt(null);
    setSqlProgress("Connecting…");
    try {
      const receipt = await ingestSqlSource({
        kind: sqlKind,
        host: sqlHost.trim(),
        database: sqlDatabase.trim(),
        user: sqlUser.trim(),
        password: sqlPassword,
        port: sqlPort.trim() ? Number(sqlPort) : null,
        tables: sqlTables,
        spaceId: activeSpaceId,
        maxRows,
        onProgress: setSqlProgress,
      });
      setSqlReceipt(receipt);
      const landed = receipt.tables[0];
      if (landed) {
        let rows: Record<string, unknown>[] = [];
        try {
          const prev = await fetchTablePreview(
            { kind: "bronze", table: landed.bronze_table },
            PREVIEW_PAGE_SIZE,
            0,
            activeSpaceId,
          );
          rows = prev.rows;
        } catch {
          rows = [];
        }
        applyObjectDraft(
          landed.bronze_table,
          landed.columns?.length ? landed.columns : Object.keys(rows[0] ?? {}),
          rows,
        );
      }
      setSqlPassword("");
      await loadTree();
    } catch (e) {
      setSqlErr(e instanceof Error ? e.message : "extract failed");
    } finally {
      setSqlBusy(false);
      setSqlProgress(null);
    }
  };

  const publishLandedObject = async (thenAsk = false) => {
    const relation =
      objectRelation || sqlReceipt?.tables[0]?.bronze_table || null;
    const name = objName.trim();
    if (!relation || !name) return;
    const columns: Record<string, string> = {};
    const seen = new Set<string>();
    for (const [src, humanRaw] of Object.entries(objAliases)) {
      if (src.startsWith("_")) continue;
      const human = asObjectIdent(humanRaw, src);
      if (seen.has(human)) {
        setObjErr(`Duplicate object column name: ${human}`);
        return;
      }
      seen.add(human);
      columns[human] = src;
    }
    if (!Object.keys(columns).length) {
      setObjErr("Map at least one source column to a human name.");
      return;
    }
    setObjBusy(true);
    setObjErr(null);
    setObjOk(null);
    try {
      const out = await publishObject({
        name,
        relation,
        columns,
        spaceId: activeSpaceId,
        toolName: `${name}_metric`,
        toolSql:
          objToolSql.trim() || `SELECT COUNT(*) AS row_count FROM objects.${name}`,
        toolDescription: `Authored SELECT on objects.${name} (not model SQL).`,
      });
      const tool = out.tool?.name ?? `${name}_metric`;
      setObjOk(
        `Published ${out.objects.join(", ")}` +
          (out.silver ? ` (arranged ${out.silver})` : "") +
          `. Ask Chat: ${tool}`,
      );
      setPublishedTool(tool);
      await loadTree();
      if (thenAsk) navigate("/", { state: objectAskNavigateState(name, tool) });
    } catch (e) {
      setObjErr(e instanceof Error ? e.message : "publish failed");
    } finally {
      setObjBusy(false);
    }
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
                : openPreview(node)
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

      <div className="mt-6 border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-4 py-4">
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
          SQL extract
        </p>
        <p className="mt-2 max-w-2xl text-sm text-[var(--color-ink-muted)]">
          Pull MySQL, SQL Server, or Postgres into bronze. Queries never run in their
          engine. Password is not stored.
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {SQL_ENGINE_CARDS.map((card) => {
            const on = sqlKind === card.kind;
            return (
              <button
                key={card.kind}
                type="button"
                aria-pressed={on}
                aria-label={`Select ${card.title} extract`}
                onClick={() => {
                  setSqlKind(card.kind);
                  setSqlPort((prev) => (prev.trim() ? prev : String(card.port)));
                }}
                className={
                  on
                    ? "border border-[var(--color-accent)] bg-[var(--color-accent-soft)] px-3 py-3 text-left"
                    : "border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-3 text-left hover:border-[var(--color-accent)]"
                }
              >
                <p className="text-sm font-semibold text-[var(--color-ink)]">{card.title}</p>
                <p className="mt-1 text-xs text-[var(--color-ink-muted)]">{card.blurb}</p>
              </button>
            );
          })}
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <label className="text-xs text-[var(--color-ink-muted)]">
            Engine
            <select
              aria-label="SQL engine"
              className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm text-[var(--color-ink)]"
              value={sqlKind}
              onChange={(e) => setSqlKind(e.target.value as SqlSourceKind)}
            >
              <option value="mysql">MySQL</option>
              <option value="sqlserver">SQL Server</option>
              <option value="postgres">Postgres</option>
            </select>
          </label>
          <label className="text-xs text-[var(--color-ink-muted)]">
            Host
            <input
              aria-label="SQL host"
              className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm text-[var(--color-ink)]"
              value={sqlHost}
              onChange={(e) => setSqlHost(e.target.value)}
            />
          </label>
          <label className="text-xs text-[var(--color-ink-muted)]">
            Port
            <input
              aria-label="SQL port"
              className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm text-[var(--color-ink)]"
              value={sqlPort}
              onChange={(e) => setSqlPort(e.target.value)}
              placeholder="default"
            />
          </label>
          <label className="text-xs text-[var(--color-ink-muted)]">
            Database
            <input
              aria-label="SQL database"
              className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm text-[var(--color-ink)]"
              value={sqlDatabase}
              onChange={(e) => setSqlDatabase(e.target.value)}
            />
          </label>
          <label className="text-xs text-[var(--color-ink-muted)]">
            User
            <input
              aria-label="SQL user"
              className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm text-[var(--color-ink)]"
              value={sqlUser}
              onChange={(e) => setSqlUser(e.target.value)}
            />
          </label>
          <label className="text-xs text-[var(--color-ink-muted)]">
            Password
            <input
              aria-label="SQL password"
              type="password"
              className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm text-[var(--color-ink)]"
              value={sqlPassword}
              onChange={(e) => setSqlPassword(e.target.value)}
            />
          </label>
          <label className="text-xs text-[var(--color-ink-muted)] sm:col-span-2">
            Tables (optional, comma-separated)
            <input
              aria-label="SQL tables"
              className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm text-[var(--color-ink)]"
              value={sqlTables}
              onChange={(e) => setSqlTables(e.target.value)}
              placeholder="orders, customers"
            />
          </label>
          <label className="text-xs text-[var(--color-ink-muted)]">
            Max rows (optional)
            <input
              aria-label="SQL max rows"
              inputMode="numeric"
              className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm text-[var(--color-ink)]"
              value={sqlMaxRows}
              onChange={(e) => setSqlMaxRows(e.target.value)}
              placeholder="500000"
            />
          </label>
        </div>
        <button
          type="button"
          disabled={sqlBusy || !sqlHost.trim() || !sqlDatabase.trim()}
          onClick={() => void extractSql()}
          className="mt-4 border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] hover:bg-[var(--color-accent)] hover:text-white disabled:opacity-50"
        >
          {sqlBusy ? "Extracting…" : "Extract to bronze"}
        </button>
        {sqlProgress && (
          <p className="mt-3 text-sm text-[var(--color-ink-muted)]" aria-live="polite">
            {sqlProgress}
          </p>
        )}
        {sqlErr && <p className="mt-3 text-sm text-[var(--color-danger)]">{sqlErr}</p>}
        {(sqlReceipt || objectRelation) && (
          <div className="mt-3 text-sm text-[var(--color-ink)]">
            {sqlReceipt ? (
              <>
            <p>
              Landed {sqlReceipt.tables.length} table
              {sqlReceipt.tables.length === 1 ? "" : "s"} from {sqlReceipt.source}
              {sqlReceipt.skipped.length ? `; skipped ${sqlReceipt.skipped.join(", ")}` : ""}.
              {sqlReceipt.tables[0]?.truncated ? " Pull was capped." : ""}{" "}
              {bronzeWhenLabel({
                source_kind: "sql",
                extracted_at: sqlReceipt.tables[0]?.extracted_at,
              })}
              . Declared {sqlReceipt.declared_primary_keys} PK
              {sqlReceipt.declared_primary_keys === 1 ? "" : "s"},{" "}
              {sqlReceipt.declared_foreign_keys} FK
              {sqlReceipt.declared_foreign_keys === 1 ? "" : "s"}.{" "}
              {sqlLinksLabel(sqlReceipt.links)}.
            </p>
            {(sqlReceipt.links?.violations ?? []).slice(0, 5).map((v) => (
              <li
                key={`${v.check}:${v.subject}`}
                className="mt-1 max-w-lg text-xs text-[var(--color-danger)]"
              >
                {v.check} on {v.subject}: {v.detail}
              </li>
            ))}
              </>
            ) : (
            <p>
              Publishing from {objectRelation} already in bronze. Map human names, then an
              authored SELECT. Gold stays a steward-signed promote.
            </p>
            )}
            {sqlReceipt && sqlReceipt.tables.length > 1 && (
              <label className="mt-3 block text-xs text-[var(--color-ink-muted)]">
                Table to publish
                <select
                  aria-label="Table to publish"
                  className="mt-1 w-full max-w-xs border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm text-[var(--color-ink)]"
                  value={objectRelation ?? sqlReceipt.tables[0]?.bronze_table ?? ""}
                  onChange={(e) => {
                    const t = sqlReceipt.tables.find(
                      (row) => row.bronze_table === e.target.value,
                    );
                    if (!t) return;
                    void fetchTablePreview(
                      { kind: "bronze", table: t.bronze_table },
                      PREVIEW_PAGE_SIZE,
                      0,
                      activeSpaceId,
                    )
                      .then((prev) =>
                        applyObjectDraft(
                          t.bronze_table,
                          t.columns?.length ? t.columns : prev.columns,
                          prev.rows,
                        ),
                      )
                      .catch(() =>
                        applyObjectDraft(t.bronze_table, t.columns ?? []),
                      );
                  }}
                >
                  {sqlReceipt.tables.map((t) => (
                    <option key={t.bronze_table} value={t.bronze_table}>
                      {t.bronze_table}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="mt-3 block text-xs text-[var(--color-ink-muted)]">
              Object name
              <input
                aria-label="Object name"
                className="mt-1 w-full max-w-xs border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm text-[var(--color-ink)]"
                value={objName}
                onChange={(e) => setObjName(e.target.value)}
              />
            </label>
            {Object.keys(objAliases).length > 0 && (
              <div className="mt-3 max-w-lg">
                <p className="text-xs text-[var(--color-ink-muted)]">
                  Object columns (human name, not the OLTP name)
                </p>
                <div className="mt-1 grid grid-cols-2 gap-2 text-xs text-[var(--color-ink-muted)]">
                  <span>Source</span>
                  <span>Human name</span>
                </div>
                {Object.entries(objAliases).map(([src, human]) => (
                  <div key={src} className="mt-1 grid grid-cols-2 gap-2">
                    <span className="truncate px-2 py-1.5 font-mono text-xs text-[var(--color-ink)]">
                      {src}
                    </span>
                    <input
                      aria-label={`Human name for ${src}`}
                      className="border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 text-sm text-[var(--color-ink)]"
                      value={human}
                      onChange={(e) =>
                        setObjAliases((prev) => ({ ...prev, [src]: e.target.value }))
                      }
                    />
                  </div>
                ))}
              </div>
            )}
            <label className="mt-3 block text-xs text-[var(--color-ink-muted)]">
              Tool SQL (SELECT only; GROUP BY a human column for a pie)
              <textarea
                aria-label="Tool SQL"
                rows={3}
                className="mt-1 w-full max-w-lg border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1.5 font-mono text-xs text-[var(--color-ink)]"
                value={objToolSql}
                onChange={(e) => setObjToolSql(e.target.value)}
              />
            </label>
            <p className="mt-2 max-w-lg text-xs text-[var(--color-ink-muted)]">
              Publishes silver.{objName.trim() || "name"} now. Gold stays a steward-signed
              promote.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={objBusy || !objName.trim()}
                onClick={() => void publishLandedObject()}
                className="border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] hover:bg-[var(--color-accent)] hover:text-white disabled:opacity-50"
              >
                {objBusy ? "Publishing…" : "Publish object + tool"}
              </button>
              <button
                type="button"
                disabled={objBusy || !objName.trim()}
                onClick={() => void publishLandedObject(true)}
                className="border border-[var(--color-line)] px-3 py-1.5 text-xs font-medium text-[var(--color-ink)] hover:border-[var(--color-accent)] disabled:opacity-50"
              >
                {objBusy ? "Publishing…" : "Publish and ask"}
              </button>
              {objOk && objName.trim() && (
                <button
                  type="button"
                  onClick={() =>
                    navigate("/", {
                      state: objectAskNavigateState(
                        objName.trim(),
                        publishedTool || `${objName.trim()}_metric`,
                      ),
                    })
                  }
                  className="border border-[var(--color-line)] px-3 py-1.5 text-xs"
                >
                  Ask this object
                </button>
              )}
            </div>
            {objErr && <p className="mt-2 text-[var(--color-danger)]">{objErr}</p>}
            {objOk && <p className="mt-2">{objOk}</p>}
          </div>
        )}
      </div>

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
            <div className="px-2 py-2">
              <AnswerRowsTable
                rows={preview.rows}
                totalRows={preview.row_count ?? preview.rows.length}
                pageOffset={preview.offset ?? previewOffset}
                pageSize={preview.limit ?? PREVIEW_PAGE_SIZE}
                onPageChange={setPreviewOffset}
              />
            </div>
          )}
          {preview && preview.rows.length === 0 && (
            <p className="px-4 py-6 text-sm text-[var(--color-ink-muted)]">
              This table is empty.
            </p>
          )}
        </div>
      </div>

      <div className="mt-6 border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-4 py-4">
        <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
          Certified question
        </p>
        <p className="mt-2 max-w-2xl text-sm text-[var(--color-ink-muted)]">
          Register a verified question and the SQL that answers it. Chat in this Space
          will use that SQL under a confident badge. Other Spaces do not inherit it.
        </p>
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          <label className="block text-xs text-[var(--color-ink-muted)]">
            Question
            <textarea
              value={vqQuestion}
              onChange={(e) => setVqQuestion(e.target.value)}
              rows={3}
              className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-2 text-sm text-[var(--color-ink)]"
              placeholder="What is the capacity of warehouse A?"
            />
          </label>
          <label className="block text-xs text-[var(--color-ink-muted)]">
            SQL
            <textarea
              value={vqSql}
              onChange={(e) => setVqSql(e.target.value)}
              rows={3}
              className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-2 font-mono text-sm text-[var(--color-ink)]"
              placeholder="SELECT name, capacity_kg FROM locations WHERE location_id = 'WH-A'"
            />
          </label>
          <label className="block text-xs text-[var(--color-ink-muted)] lg:col-span-2">
            Synonyms (comma or newline)
            <textarea
              value={vqSynonyms}
              onChange={(e) => setVqSynonyms(e.target.value)}
              rows={2}
              className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-2 text-sm text-[var(--color-ink)]"
              placeholder="capacity of WH-A, warehouse A capacity"
            />
          </label>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={vqBusy || !vqQuestion.trim() || !vqSql.trim()}
            onClick={() => void registerCertified()}
            className="border border-[var(--color-accent)] bg-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {vqBusy ? "Registering…" : "Register for this Space"}
          </button>
          {vqOk && <span className="text-xs text-[var(--color-ink)]">{vqOk}</span>}
          {vqErr && <span className="text-xs text-[var(--color-danger)]">{vqErr}</span>}
        </div>
        {vqAssets.length > 0 && (
          <ul className="mt-4 space-y-2 text-xs">
            {vqAssets.map((a) => (
              <li key={a.asset_id} className="border-t border-[var(--color-line)]/70 pt-2">
                <p className="font-medium text-[var(--color-ink)]">{a.question}</p>
                <p className="mt-1 truncate font-mono text-[var(--color-ink-muted)]">{a.sql}</p>
                {a.synonyms.length > 0 && (
                  <p className="mt-1 text-[var(--color-ink-muted)]">
                    also: {a.synonyms.join(" · ")}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
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
