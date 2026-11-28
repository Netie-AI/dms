import { useEffect, useState } from "react";

/**
 * UX-FEEDBACK-01. Controls render only after GET status says the flag is on.
 * Flag off, the status route is absent, these return null, and the answer
 * markup stays the same. Nothing here decides SQL from the question text.
 */

const STATUS_PATH = "/api/v1/feedback/status";

export const DOWN_REASONS = [
  { id: "wrong_number", label: "Wrong number" },
  { id: "wrong_grain", label: "Wrong grain" },
  { id: "missing_data", label: "Missing data" },
  { id: "too_slow", label: "Too slow" },
  { id: "other", label: "Other" },
] as const;

export type DownReason = (typeof DOWN_REASONS)[number]["id"];

type Panel = "down" | "fix" | "review" | null;

export function thumbBody(input: {
  spaceId: string;
  question: string;
  sql: string;
  auditId: string;
  vote: "up" | "down";
  reason?: string;
  note?: string;
}) {
  return {
    space_id: input.spaceId || "company-default",
    question: input.question,
    sql: input.sql,
    audit_id: input.auditId,
    vote: input.vote,
    reason: input.reason ?? "",
    note: input.note ?? "",
  };
}

export function fixBody(input: {
  spaceId: string;
  question: string;
  sql: string;
  interpretation: string;
}) {
  return {
    space_id: input.spaceId || "company-default",
    question: input.question,
    sql: input.sql,
    interpretation: input.interpretation,
  };
}

export function reviewBody(input: {
  spaceId: string;
  question: string;
  sql: string;
  interpretation: string;
  note: string;
}) {
  return {
    space_id: input.spaceId || "company-default",
    question: input.question,
    sql: input.sql,
    interpretation: input.interpretation,
    note: input.note,
  };
}

async function readDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : "feedback_failed";
  } catch {
    return "feedback_failed";
  }
}

export async function fetchFeedbackEnabled(signal?: AbortSignal): Promise<boolean> {
  const res = await fetch(STATUS_PATH, { signal });
  if (!res.ok) return false;
  const body = (await res.json()) as { enabled?: boolean };
  return body.enabled === true;
}

type BarProps = {
  question: string;
  sql: string;
  spaceId: string | null;
  auditId: string;
  initialPanel?: Panel;
};

export function FeedbackBarView({
  question,
  sql,
  spaceId,
  auditId,
  initialPanel = null,
}: BarProps) {
  const [panel, setPanel] = useState<Panel>(initialPanel);
  const [reason, setReason] = useState<DownReason | "">("");
  const [note, setNote] = useState("");
  const [interpretation, setInterpretation] = useState(question);
  const [editedSql, setEditedSql] = useState(sql);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const send = async (path: string, payload: unknown, okText: string) => {
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        setMessage(await readDetail(res));
        return;
      }
      const body = (await res.json()) as { filed?: boolean; rows?: unknown };
      if (path.endsWith("/thumb") && body.filed) {
        setMessage("Filed as a verified example.");
      } else if (path.endsWith("/fix")) {
        const count = Array.isArray(body.rows) ? body.rows.length : 0;
        setMessage(`Ran ${count} rows. Not saved as an answer.`);
      } else {
        setMessage(okText);
      }
      setPanel(null);
    } catch {
      setMessage("feedback_failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="feedback-bar" className="mt-4 border border-[var(--color-line)] px-3 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
        Was this right?
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          type="button"
          data-testid="feedback-yes"
          disabled={busy || !question}
          aria-label="Thumbs up"
          onClick={() => {
            void send(
              "/api/v1/feedback/thumb",
              thumbBody({
                spaceId: spaceId ?? "",
                question,
                sql,
                auditId,
                vote: "up",
              }),
              "Recorded.",
            );
          }}
          className="border border-[var(--color-line)] px-3 py-1 text-sm"
        >
          Yes
        </button>
        <button
          type="button"
          data-testid="feedback-no"
          disabled={busy}
          aria-label="Thumbs down"
          onClick={() => setPanel("down")}
          className="border border-[var(--color-line)] px-3 py-1 text-sm"
        >
          No
        </button>
        <button
          type="button"
          data-testid="feedback-fix"
          disabled={busy}
          onClick={() => setPanel("fix")}
          className="border border-[var(--color-line)] px-3 py-1 text-sm"
        >
          Fix it
        </button>
        <button
          type="button"
          data-testid="feedback-review"
          disabled={busy || !question || !sql}
          onClick={() => setPanel("review")}
          className="border border-[var(--color-line)] px-3 py-1 text-sm"
        >
          Request review
        </button>
      </div>
      {panel === "down" ? (
        <div data-testid="feedback-reasons" className="mt-3 flex flex-col gap-2">
          {DOWN_REASONS.map((item) => (
            <label key={item.id} className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="feedback-reason"
                data-testid={`feedback-reason-${item.id}`}
                checked={reason === item.id}
                onChange={() => setReason(item.id)}
              />
              {item.label}
            </label>
          ))}
          {reason === "other" ? (
            <textarea
              data-testid="feedback-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              className="min-h-16 border border-[var(--color-line)] bg-[var(--color-paper)] p-2 text-sm"
            />
          ) : null}
          <button
            type="button"
            data-testid="feedback-no-send"
            disabled={busy || !reason || (reason === "other" && !note.trim())}
            onClick={() => {
              void send(
                "/api/v1/feedback/thumb",
                thumbBody({
                  spaceId: spaceId ?? "",
                  question,
                  sql,
                  auditId,
                  vote: "down",
                  reason,
                  note,
                }),
                "Recorded.",
              );
            }}
            className="self-start border border-[var(--color-line)] px-3 py-1 text-sm"
          >
            Send
          </button>
        </div>
      ) : null}
      {panel === "fix" ? (
        <div data-testid="feedback-fix-form" className="mt-3 flex flex-col gap-2">
          <label className="text-xs text-[var(--color-ink-muted)]">
            Interpretation
            <textarea
              data-testid="feedback-interpretation"
              value={interpretation}
              onChange={(event) => setInterpretation(event.target.value)}
              className="mt-1 min-h-16 w-full border border-[var(--color-line)] bg-[var(--color-paper)] p-2 text-sm text-[var(--color-ink)]"
            />
          </label>
          <label className="text-xs text-[var(--color-ink-muted)]">
            SQL
            <textarea
              data-testid="feedback-sql"
              value={editedSql}
              onChange={(event) => setEditedSql(event.target.value)}
              className="mt-1 min-h-16 w-full border border-[var(--color-line)] bg-[var(--color-paper)] p-2 font-mono text-xs text-[var(--color-ink)]"
            />
          </label>
          <button
            type="button"
            data-testid="feedback-fix-run"
            disabled={busy || !editedSql.trim()}
            onClick={() => {
              void send(
                "/api/v1/feedback/fix",
                fixBody({
                  spaceId: spaceId ?? "",
                  question,
                  sql: editedSql,
                  interpretation,
                }),
                "Ran.",
              );
            }}
            className="self-start border border-[var(--color-line)] px-3 py-1 text-sm"
          >
            Run
          </button>
        </div>
      ) : null}
      {panel === "review" ? (
        <div className="mt-3">
          <button
            type="button"
            data-testid="feedback-review-send"
            disabled={busy}
            onClick={() => {
              void send(
                "/api/v1/feedback/review",
                reviewBody({
                  spaceId: spaceId ?? "",
                  question,
                  sql,
                  interpretation,
                  note,
                }),
                "Queued for a steward.",
              );
            }}
            className="border border-[var(--color-line)] px-3 py-1 text-sm"
          >
            Queue for review
          </button>
        </div>
      ) : null}
      {message ? (
        <p data-testid="feedback-message" className="mt-2 text-xs text-[var(--color-ink-muted)]">
          {message}
        </p>
      ) : null}
    </div>
  );
}

export function FeedbackBar(props: Omit<BarProps, "initialPanel">) {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    const ac = new AbortController();
    void fetchFeedbackEnabled(ac.signal)
      .then(setEnabled)
      .catch(() => setEnabled(false));
    return () => ac.abort();
  }, []);
  if (!enabled) return null;
  return <FeedbackBarView {...props} />;
}

export function ReportProblemButton() {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  return (
    <div className="fixed bottom-4 right-4 z-20">
      <button
        type="button"
        data-testid="report-problem"
        onClick={() => setOpen((value) => !value)}
        className="border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-1.5 text-xs shadow-sm"
      >
        Report a problem
      </button>
      {open ? (
        <form
          data-testid="report-problem-form"
          className="mt-2 w-64 border border-[var(--color-line)] bg-[var(--color-surface)] p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void (async () => {
              const res = await fetch("/api/v1/feedback/report", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ note }),
              });
              setMessage(res.ok ? "Sent." : await readDetail(res));
              if (res.ok) {
                setNote("");
                setOpen(false);
              }
            })();
          }}
        >
          <textarea
            data-testid="report-problem-note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            className="min-h-16 w-full border border-[var(--color-line)] p-2 text-sm"
          />
          <button type="submit" className="mt-2 border border-[var(--color-line)] px-2 py-1 text-xs">
            Send
          </button>
          {message ? <p className="mt-1 text-xs">{message}</p> : null}
        </form>
      ) : null}
    </div>
  );
}

export function ReportProblem() {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    const ac = new AbortController();
    void fetchFeedbackEnabled(ac.signal)
      .then(setEnabled)
      .catch(() => setEnabled(false));
    return () => ac.abort();
  }, []);
  if (!enabled) return null;
  return <ReportProblemButton />;
}

type ReviewRow = {
  id: string;
  question?: string;
  status?: string;
  interpretation?: string;
};

export function FeedbackReviewList() {
  const [rows, setRows] = useState<ReviewRow[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = () => {
    void fetchFeedbackEnabled()
      .then(async (on) => {
        if (!on) {
          setRows(null);
          return;
        }
        const res = await fetch("/api/v1/feedback/reviews");
        if (!res.ok) {
          setRows(null);
          return;
        }
        const body = (await res.json()) as { reviews?: ReviewRow[] };
        setRows(Array.isArray(body.reviews) ? body.reviews : []);
      })
      .catch(() => setRows(null));
  };

  useEffect(() => {
    load();
  }, []);

  if (rows === null) return null;

  const approve = (id: string) => {
    void fetch(`/api/v1/feedback/reviews/${id}/approve`, { method: "POST" })
      .then(async (res) => {
        const body = (await res.json()) as { filed?: boolean; detail?: string };
        setMessage(res.ok && body.filed ? "Approved and filed as an example." : body.detail || "Recorded.");
        load();
      })
      .catch(() => setMessage("feedback_failed"));
  };

  return (
    <section data-testid="feedback-reviews" className="mt-8 max-w-xl">
      <h2 className="text-sm font-semibold">Feedback reviews</h2>
      <ul className="mt-3 divide-y divide-[var(--color-line)] border border-[var(--color-line)]">
        {rows.length === 0 ? (
          <li className="px-3 py-3 text-sm text-[var(--color-ink-muted)]">No feedback reviews.</li>
        ) : (
          rows.map((row) => (
            <li key={row.id} className="flex items-center justify-between gap-3 px-3 py-3 text-sm">
              <div>
                <p>{row.interpretation || row.question || row.id}</p>
                <p className="text-[var(--color-ink-muted)]">{row.status}</p>
              </div>
              {row.status === "queued" ? (
                <button
                  type="button"
                  data-testid={`feedback-approve-${row.id}`}
                  onClick={() => approve(row.id)}
                  className="border border-[var(--color-accent)] px-3 py-1 text-[var(--color-accent)]"
                >
                  Approve
                </button>
              ) : null}
            </li>
          ))
        )}
      </ul>
      {message ? <p className="mt-2 text-sm text-[var(--color-ink-muted)]">{message}</p> : null}
    </section>
  );
}

type AuditRow = {
  id: string;
  action?: string;
  reason?: string;
  question?: string;
  asset_id?: string | null;
};

export function FeedbackAuditList() {
  const [rows, setRows] = useState<AuditRow[] | null>(null);

  useEffect(() => {
    void fetchFeedbackEnabled()
      .then(async (on) => {
        if (!on) {
          setRows(null);
          return;
        }
        const res = await fetch("/api/v1/feedback/audit");
        if (!res.ok) {
          setRows(null);
          return;
        }
        const body = (await res.json()) as { rows?: AuditRow[] };
        setRows(Array.isArray(body.rows) ? body.rows : []);
      })
      .catch(() => setRows(null));
  }, []);

  if (rows === null) return null;

  return (
    <section data-testid="feedback-audit" className="mt-8 max-w-xl">
      <h2 className="text-sm font-semibold">Feedback</h2>
      <ul className="mt-3 divide-y divide-[var(--color-line)] border border-[var(--color-line)]">
        {rows.length === 0 ? (
          <li className="px-3 py-3 text-sm text-[var(--color-ink-muted)]">No feedback records.</li>
        ) : (
          rows.map((row) => (
            <li key={row.id} className="px-3 py-3 text-sm">
              <p>
                {row.action}
                {row.reason ? ` · ${row.reason}` : ""}
                {row.asset_id ? " · example filed" : ""}
              </p>
              {row.question ? (
                <p className="text-[var(--color-ink-muted)]">{row.question}</p>
              ) : null}
            </li>
          ))
        )}
      </ul>
    </section>
  );
}
