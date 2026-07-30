import { useState } from "react";

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
};

export function StudioPage() {
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const onFiles = async (list: FileList | null) => {
    if (!list?.length) return;
    setBusy(true);
    setErr(null);
    try {
      const fd = new FormData();
      const files = Array.from(list);
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
    } catch (e) {
      setErr(e instanceof Error ? e.message : "ingest failed");
    } finally {
      setBusy(false);
    }
  };

  const attention = receipt?.need_attention ?? receipt?.quarantined ?? 0;

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
        the blob tier — never a silent partial success.
      </p>
      <label className="mt-8 flex h-40 cursor-pointer flex-col items-center justify-center border border-dashed border-[var(--color-line)] bg-white/50 text-sm text-[var(--color-ink-muted)] hover:border-[var(--color-accent)]">
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
      {err && <p className="mt-4 text-sm text-[var(--color-danger)]">{err}</p>}
      {receipt && (
        <div className="mt-6 border border-[var(--color-line)] bg-white/70 px-4 py-4 text-sm">
          <p className="font-medium text-[var(--color-ink)]">
            {receipt.summary ??
              `Receipt · ${receipt.files_seen} files · ${receipt.ingested} ingested · ${attention} need attention`}
          </p>
          {receipt.table && (
            <p className="mt-1 text-[var(--color-ink-muted)]">Table {receipt.table}</p>
          )}
          {receipt.per_class && (
            <p className="mt-1 text-[var(--color-ink-muted)]">
              {Object.entries(receipt.per_class)
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ")}
            </p>
          )}
          {receipt.reasons.length > 0 && (
            <ul className="mt-3 list-inside list-disc text-[var(--color-warn)]">
              {receipt.reasons.map((r) => (
                <li key={r.file + r.reason}>
                  {r.file}: {r.reason}
                  {r.fix ? ` — ${r.fix}` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
