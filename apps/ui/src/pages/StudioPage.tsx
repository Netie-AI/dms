import { useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "@/context/AppContext";

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

type InferResult = {
  source?: string;
  proposed?: Record<string, unknown>;
  columns?: string[];
  [key: string]: unknown;
};

export function StudioPage() {
  const { setActivity } = useApp();
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [inferBusy, setInferBusy] = useState(false);
  const [inferOut, setInferOut] = useState<InferResult | null>(null);
  const [inferErr, setInferErr] = useState<string | null>(null);

  const onFiles = async (list: FileList | null) => {
    if (!list?.length) return;
    setBusy(true);
    setErr(null);
    setInferOut(null);
    setActivity({ label: `Ingesting ${list.length} file${list.length === 1 ? "" : "s"}…`, progress: 20 });
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
    } catch (e) {
      setErr(e instanceof Error ? e.message : "ingest failed");
    } finally {
      setBusy(false);
      window.setTimeout(() => setActivity(null), 600);
    }
  };

  const runInfer = async (source: string) => {
    setInferBusy(true);
    setInferErr(null);
    setActivity({ label: "Inferring relation keys…", progress: null });
    try {
      const res = await fetch("/api/v1/pipelines/infer-contract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source }),
      });
      if (!res.ok) throw new Error(await res.text());
      setInferOut((await res.json()) as InferResult);
    } catch (e) {
      setInferErr(e instanceof Error ? e.message : "infer failed");
    } finally {
      setInferBusy(false);
      setActivity(null);
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
  const bronzeTable =
    receipt?.table || fileRows.find((f) => f.table)?.table || null;

  return (
    <div className="h-full overflow-y-auto px-8 py-8">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
        U3 · T13
      </p>
      <h1 className="mt-2 font-[family-name:var(--font-display)] text-3xl font-semibold tracking-tight">
        Studio
      </h1>
      <p className="mt-3 max-w-xl text-[var(--color-ink-muted)]">
        Drop CSV or Excel. Every sheet is classified before bronze write. Unstructured routes to
        the blob tier — never a silent partial success. On Windows, use folder upload to pull a
        whole directory of workbooks.
      </p>

      <div className="mt-8 grid gap-3 sm:grid-cols-2">
        <label className="flex h-40 cursor-pointer flex-col items-center justify-center border border-dashed border-[var(--color-line)] bg-[var(--color-surface)]/50 text-sm text-[var(--color-ink-muted)] hover:border-[var(--color-accent)]">
          {busy ? "Ingesting…" : "Drop files or click to upload"}
          <input
            type="file"
            accept=".csv,.xlsx,.xlsm,.tsv"
            multiple
            className="hidden"
            disabled={busy}
            onChange={(e) => void onFiles(e.target.files)}
          />
        </label>
        <label className="flex h-40 cursor-pointer flex-col items-center justify-center border border-dashed border-[var(--color-line)] bg-[var(--color-surface)]/50 text-sm text-[var(--color-ink-muted)] hover:border-[var(--color-accent)]">
          {busy ? "Ingesting…" : "Upload Windows folder"}
          <span className="mt-1 text-[11px]">Includes nested .csv / .xlsx</span>
          <input
            type="file"
            multiple
            className="hidden"
            disabled={busy}
            ref={(el) => {
              if (el) el.setAttribute("webkitdirectory", "");
            }}
            onChange={(e) => void onFiles(e.target.files)}
          />
        </label>
      </div>

      {err && <p className="mt-4 text-sm text-[var(--color-danger)]">{err}</p>}

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
                    <tr key={`${f.file}-${f.classification ?? ""}`} className="border-b border-[var(--color-line)]/70">
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

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Link
              to="/library"
              className="text-sm text-[var(--color-accent)] hover:underline"
            >
              View in Library →
            </Link>
            {bronzeTable && (
              <button
                type="button"
                disabled={inferBusy}
                onClick={() => void runInfer(String(bronzeTable))}
                className="border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-1.5 text-xs font-medium text-[var(--color-ink)] hover:border-[var(--color-accent)] disabled:opacity-50"
              >
                {inferBusy ? "Inferring…" : "Infer relation keys"}
              </button>
            )}
          </div>

          {inferErr && <p className="mt-3 text-xs text-[var(--color-danger)]">{inferErr}</p>}
          {inferOut && (
            <pre className="mt-3 max-h-64 overflow-auto border border-[var(--color-line)] bg-[var(--color-paper-2)]/40 p-3 text-[11px] leading-relaxed text-[var(--color-ink)]">
              {JSON.stringify(inferOut, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
