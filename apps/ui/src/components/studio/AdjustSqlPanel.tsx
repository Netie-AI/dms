import { useEffect, useState } from "react";
import { AnswerRowsTable } from "@/components/AnswerRowsTable";

/**
 * ADJUST-SQL-VQ-01. The button exists only after GET status says the flag is
 * on. The parent mounts this for a steward only. Flag off, the status route
 * is absent, this returns null, and the answer markup stays the same.
 * The question is the ask that produced the SQL. It is not scanned for words.
 */

const STATUS_PATH = "/api/v1/studio/adjust/status";
const RUN_PATH = "/api/v1/studio/adjust/run";
const SAVE_PATH = "/api/v1/studio/adjust/save";
const REASON = /^[A-Za-z_][A-Za-z0-9_.:-]{0,120}$/;

export type AdjustRows = {
  rows: Record<string, unknown>[];
  rowCount: number;
  truncated: boolean;
};

type RunOutcome =
  | { ok: true; rows: AdjustRows }
  | { ok: false; reason: string };

export function adjustFailureCode(body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && REASON.test(detail)) return detail;
  }
  return "adjust_failed";
}

export async function fetchAdjustEnabled(signal?: AbortSignal): Promise<boolean> {
  const res = await fetch(STATUS_PATH, { signal });
  if (!res.ok) return false;
  const body = (await res.json()) as { enabled?: boolean };
  return body.enabled === true;
}

async function readFailure(res: Response): Promise<string> {
  try {
    return adjustFailureCode(await res.json());
  } catch {
    return "adjust_failed";
  }
}

export async function postAdjustRun(spaceId: string, sql: string): Promise<RunOutcome> {
  const res = await fetch(RUN_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ space_id: spaceId || "company-default", sql }),
  });
  if (!res.ok) return { ok: false, reason: await readFailure(res) };
  const data = (await res.json()) as {
    rows?: unknown;
    row_count?: number;
    truncated?: boolean;
  };
  const rows = Array.isArray(data.rows)
    ? data.rows.filter((row): row is Record<string, unknown> => !!row && typeof row === "object")
    : [];
  return {
    ok: true,
    rows: {
      rows,
      rowCount: typeof data.row_count === "number" ? data.row_count : rows.length,
      truncated: data.truncated === true,
    },
  };
}

export async function postAdjustSave(
  spaceId: string,
  question: string,
  sql: string,
): Promise<{ ok: true; assetId: string } | { ok: false; reason: string }> {
  const res = await fetch(SAVE_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      space_id: spaceId || "company-default",
      question,
      sql,
    }),
  });
  if (!res.ok) return { ok: false, reason: await readFailure(res) };
  const data = (await res.json()) as { asset_id?: unknown };
  const assetId = typeof data.asset_id === "string" ? data.asset_id : "";
  if (!assetId) return { ok: false, reason: "not_stored" };
  return { ok: true, assetId };
}

type EditorProps = {
  question: string;
  open: boolean;
  draft: string;
  running: boolean;
  saving: boolean;
  ranSql: string | null;
  rows: AdjustRows | null;
  error: string | null;
  savedId: string | null;
  onOpen: () => void;
  onDraft: (value: string) => void;
  onRun: () => void;
  onSave: () => void;
};

export function AdjustSqlEditorView({
  question,
  open,
  draft,
  running,
  saving,
  ranSql,
  rows,
  error,
  savedId,
  onOpen,
  onDraft,
  onRun,
  onSave,
}: EditorProps) {
  if (!open) {
    return (
      <button
        type="button"
        data-testid="studio-adjust-open"
        onClick={onOpen}
        className="mt-2 border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1 text-xs text-[var(--color-ink)] hover:border-[var(--color-accent)]"
      >
        Adjust SQL
      </button>
    );
  }
  const canSave =
    !running &&
    !saving &&
    rows !== null &&
    ranSql !== null &&
    ranSql === draft &&
    question.trim().length > 0;
  return (
    <div data-testid="studio-adjust-panel" className="mt-3 border border-[var(--color-line)] px-3 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
        Adjust SQL
      </p>
      <textarea
        data-testid="studio-adjust-sql"
        value={draft}
        rows={5}
        onChange={(event) => onDraft(event.target.value)}
        className="mt-2 w-full border border-[var(--color-line)] bg-[var(--color-paper)] p-3 font-mono text-xs text-[var(--color-ink)]"
      />
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          type="button"
          data-testid="studio-adjust-run"
          disabled={running || draft.trim().length === 0}
          onClick={onRun}
          className="border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-1 text-sm text-[var(--color-ink)] hover:border-[var(--color-accent)] disabled:opacity-50"
        >
          Run
        </button>
        <button
          type="button"
          data-testid="studio-adjust-save"
          disabled={!canSave}
          onClick={onSave}
          className="border border-[var(--color-accent)] bg-[var(--color-accent)] px-3 py-1 text-sm text-white max-lg:text-[var(--color-on-accent)] disabled:opacity-50"
        >
          Save as verified for this question
        </button>
      </div>
      {error ? (
        <p data-testid="studio-adjust-error" className="mt-2 text-xs text-[var(--color-danger)]">
          {error}
        </p>
      ) : null}
      {rows ? (
        <div data-testid="studio-adjust-rows" className="mt-3">
          <AnswerRowsTable rows={rows.rows.slice(0, 10)} totalRows={rows.rowCount} pageOffset={0} pageSize={10} />
          {rows.truncated ? (
            <p className="mt-1 text-xs text-[var(--color-ink-muted)]">Showing the first rows only.</p>
          ) : null}
        </div>
      ) : null}
      {savedId ? (
        <p data-testid="studio-adjust-saved" className="mt-2 text-xs text-[var(--color-ink)]">
          Saved {savedId}
        </p>
      ) : null}
    </div>
  );
}

type PanelProps = {
  sql: string;
  question: string;
  spaceId: string | null;
};

export function AdjustSqlPanel({ sql, question, spaceId }: PanelProps) {
  const [enabled, setEnabled] = useState(false);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(sql);
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [ranSql, setRanSql] = useState<string | null>(null);
  const [rows, setRows] = useState<AdjustRows | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    fetchAdjustEnabled(ctrl.signal).then(setEnabled).catch(() => setEnabled(false));
    return () => ctrl.abort();
  }, []);

  if (!enabled) return null;

  return (
    <AdjustSqlEditorView
      question={question}
      open={open}
      draft={draft}
      running={running}
      saving={saving}
      ranSql={ranSql}
      rows={rows}
      error={error}
      savedId={savedId}
      onOpen={() => {
        setDraft(sql);
        setOpen(true);
      }}
      onDraft={(value) => {
        setDraft(value);
        setRows(null);
        setRanSql(null);
        setSavedId(null);
        setError(null);
      }}
      onRun={() => {
        setRunning(true);
        setError(null);
        setSavedId(null);
        const sent = draft;
        void postAdjustRun(spaceId ?? "", sent)
          .then((result) => {
            if (!result.ok) {
              setRows(null);
              setRanSql(null);
              setError(result.reason);
              return;
            }
            setRows(result.rows);
            setRanSql(sent);
          })
          .catch(() => {
            setRows(null);
            setRanSql(null);
            setError("adjust_failed");
          })
          .finally(() => setRunning(false));
      }}
      onSave={() => {
        if (!question.trim() || ranSql !== draft) return;
        setSaving(true);
        setError(null);
        void postAdjustSave(spaceId ?? "", question, draft)
          .then((result) => {
            if (!result.ok) {
              setError(result.reason);
              return;
            }
            setSavedId(result.assetId);
          })
          .catch(() => setError("adjust_failed"))
          .finally(() => setSaving(false));
      }}
    />
  );
}
