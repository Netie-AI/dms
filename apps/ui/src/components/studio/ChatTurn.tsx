import { useEffect, useState } from "react";
import { describeApiError, postAsk } from "@/lib/api";
import type { AnswerEnvelope } from "@/lib/types";

/** Same sentence as ``dms_core.studio_chat_turn.NOT_PROVIDED``. */
export const NOT_PROVIDED = "not provided by Cortex";

type Slot = {
  provided: boolean;
  text: string | null;
  items?: unknown[];
};

type QueryPlan = Record<string, unknown>;

export type StudioTurn = {
  question?: string;
  sql_ran?: boolean;
  plan: { provided: boolean; text: string | null; query_plan: QueryPlan | null };
  ontology: { tables: Slot; joins: Slot; metrics: Slot };
  clarify: { provided: boolean; text: string | null; blocks: boolean };
};

type JoinItem = {
  id?: string;
  from?: string;
  to?: string;
  from_property?: string;
  to_property?: string;
};

async function postPlan(question: string): Promise<StudioTurn> {
  const res = await fetch("/api/v1/studio/chat/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new Error(describeApiError(await res.text()));
  return (await res.json()) as StudioTurn;
}

async function postConfirm(
  question: string,
  clarifyReply: string,
  preview: StudioTurn,
): Promise<{ execute: boolean; question: string }> {
  const res = await fetch("/api/v1/studio/chat/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      clarify_reply: clarifyReply,
      preview,
    }),
  });
  if (!res.ok) throw new Error(describeApiError(await res.text()));
  return (await res.json()) as { execute: boolean; question: string };
}

function slotText(slot: { provided: boolean; text: string | null } | undefined): string {
  if (!slot || !slot.provided) return slot?.text || NOT_PROVIDED;
  return "";
}

function joinLabel(row: JoinItem): string {
  const left = row.from_property ? `${row.from ?? ""}.${row.from_property}` : (row.from ?? "");
  const right = row.to_property ? `${row.to ?? ""}.${row.to_property}` : (row.to ?? "");
  const link = left || right ? `${left} -> ${right}` : "";
  return row.id ? `${row.id}${link ? `: ${link}` : ""}` : link;
}

export function ChatTurn({ spaceId }: { spaceId: string | null }) {
  const [draft, setDraft] = useState("");
  const [edit, setEdit] = useState("");
  const [reply, setReply] = useState("");
  const [turn, setTurn] = useState<StudioTurn | null>(null);
  const [plannedQuestion, setPlannedQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<AnswerEnvelope | null>(null);

  useEffect(() => {
    setDraft("");
    setEdit("");
    setReply("");
    setTurn(null);
    setPlannedQuestion("");
    setErr(null);
    setResult(null);
  }, [spaceId]);

  const dirty = turn !== null && edit.trim() !== plannedQuestion.trim();
  const clarifyBlocks = Boolean(turn?.clarify.blocks) && !reply.trim();
  const canConfirm = Boolean(turn) && !dirty && !clarifyBlocks && !busy;

  const plan = async (question: string) => {
    const asked = question.trim();
    if (!asked) return;
    setBusy(true);
    setErr(null);
    setResult(null);
    setReply("");
    try {
      const next = await postPlan(asked);
      setTurn(next);
      setPlannedQuestion(asked);
      setEdit(asked);
      setDraft(asked);
    } catch (e) {
      setTurn(null);
      setErr(e instanceof Error ? e.message : "plan failed");
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!turn || !canConfirm) return;
    setBusy(true);
    setErr(null);
    try {
      const gate = await postConfirm(plannedQuestion, reply, turn);
      if (!gate.execute || !gate.question) {
        setErr("Confirm did not authorize a run.");
        return;
      }
      const env = await postAsk({
        question: gate.question,
        space_id: spaceId,
      });
      setResult(env);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "confirm failed");
    } finally {
      setBusy(false);
    }
  };

  const suggestions =
    result && Object.prototype.hasOwnProperty.call(result, "suggestions")
      ? result.suggestions ?? []
      : null;

  const tables = turn?.ontology.tables;
  const joins = turn?.ontology.joins;
  const metrics = turn?.ontology.metrics;

  return (
    <section
      data-testid="studio-chat-turn"
      className="mt-6 border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-4 py-4"
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
        Studio ask
      </p>
      <p className="mt-2 max-w-3xl text-sm text-[var(--color-ink-muted)]">
        Plan first. SQL runs only after confirm. An edit re-plans. Not COMPLETE.
      </p>
      <label className="mt-3 block text-sm text-[var(--color-ink)]" htmlFor="studio-chat-question">
        Question
        <textarea
          id="studio-chat-question"
          data-testid="studio-chat-question"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={2}
          className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-2 text-sm"
        />
      </label>
      <button
        type="button"
        data-testid="studio-chat-plan"
        disabled={busy || !draft.trim()}
        onClick={() => void plan(draft)}
        className="mt-2 border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)] hover:bg-[var(--color-accent)] hover:text-white disabled:opacity-50"
      >
        {busy ? "Working..." : "Show plan"}
      </button>
      {err && (
        <p className="mt-3 text-sm text-[var(--color-danger)]" data-testid="studio-chat-error">
          {err}
        </p>
      )}
      {turn && (
        <div className="mt-4 grid gap-3">
          <div data-testid="studio-chat-plan-body">
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
              Plan
            </p>
            {turn.plan.provided && turn.plan.query_plan ? (
              <dl className="mt-1 text-sm text-[var(--color-ink)]">
                {Object.entries(turn.plan.query_plan).map(([key, value]) => (
                  <div key={key} className="mt-1">
                    <dt className="inline font-medium">{key}: </dt>
                    <dd className="inline">
                      {typeof value === "string" ? value : JSON.stringify(value)}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="mt-1 text-sm text-[var(--color-ink)]">{slotText(turn.plan)}</p>
            )}
          </div>
          <div data-testid="studio-chat-ontology">
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
              Ontology picks
            </p>
            <p className="mt-1 text-sm" data-testid="studio-chat-ontology-tables">
              Tables:{" "}
              {tables?.provided
                ? ((tables.items as string[] | undefined) ?? []).join(", ") || "(none)"
                : slotText(tables)}
            </p>
            <ul className="mt-1 text-sm" data-testid="studio-chat-ontology-joins">
              <li>
                Joins:{" "}
                {joins?.provided
                  ? ((joins.items as JoinItem[] | undefined) ?? []).length
                    ? null
                    : "(none)"
                  : slotText(joins)}
              </li>
              {joins?.provided &&
                ((joins.items as JoinItem[] | undefined) ?? []).map((row, i) => (
                  <li key={`${row.id ?? "join"}-${i}`}>{joinLabel(row)}</li>
                ))}
            </ul>
            <p className="mt-1 text-sm" data-testid="studio-chat-ontology-metrics">
              Metrics:{" "}
              {metrics?.provided
                ? ((metrics.items as string[] | undefined) ?? []).join(", ") || "(none)"
                : slotText(metrics)}
            </p>
          </div>
          <div data-testid="studio-chat-clarify">
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
              Clarify
            </p>
            <p className="mt-1 text-sm text-[var(--color-ink)]">{slotText(turn.clarify) || turn.clarify.text}</p>
            {turn.clarify.blocks && (
              <label className="mt-2 block text-sm" htmlFor="studio-chat-clarify-reply">
                Reply
                <input
                  id="studio-chat-clarify-reply"
                  data-testid="studio-chat-clarify-reply"
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-2 text-sm"
                />
              </label>
            )}
          </div>
          <label className="block text-sm" htmlFor="studio-chat-edit">
            Edit question
            <textarea
              id="studio-chat-edit"
              data-testid="studio-chat-edit"
              value={edit}
              onChange={(e) => setEdit(e.target.value)}
              rows={2}
              className="mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-2 text-sm"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              data-testid="studio-chat-replan"
              disabled={busy || !edit.trim()}
              onClick={() => void plan(edit)}
              className="border border-[var(--color-line)] px-3 py-1.5 text-xs font-medium text-[var(--color-ink)] disabled:opacity-50"
            >
              Re-plan
            </button>
            <button
              type="button"
              data-testid="studio-chat-confirm"
              disabled={!canConfirm}
              onClick={() => void confirm()}
              className="border border-[var(--color-accent)] bg-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            >
              Confirm and run
            </button>
          </div>
        </div>
      )}
      {result && (
        <div className="mt-4" data-testid="studio-chat-result">
          <p className="text-sm text-[var(--color-ink)]">{result.text}</p>
          <div className="mt-2" data-testid="studio-chat-suggestions">
            <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
              Follow-up questions
            </p>
            {suggestions === null ? (
              <p className="mt-1 text-sm">{NOT_PROVIDED}</p>
            ) : suggestions.length === 0 ? (
              <p className="mt-1 text-sm text-[var(--color-ink-muted)]">
                Cortex returned no follow-up questions.
              </p>
            ) : (
              <ul className="mt-1 grid gap-1">
                {suggestions.map((q) => (
                  <li key={q}>
                    <button
                      type="button"
                      className="text-left text-sm text-[var(--color-accent)] hover:underline"
                      onClick={() => void plan(q)}
                    >
                      {q}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
