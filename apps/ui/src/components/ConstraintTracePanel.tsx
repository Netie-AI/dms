import {
  countMalformedStages,
  parseConstraintTrace,
  STAGE_LABELS,
  summariseTrace,
  toneForStatus,
  traceHeadline,
  type ConstraintStage,
  type StageTone,
} from "@/lib/constraintTrace";

const TONE_COLOR: Record<StageTone, string> = {
  ok: "var(--color-badge-ok)",
  warn: "var(--color-warn)",
  danger: "var(--color-danger)",
};

function StageRow({ stage }: { stage: ConstraintStage }) {
  const tone = toneForStatus(stage.status);
  const certified = stage.status === "CERTIFIED";
  return (
    <li
      data-testid={`cca-stage-${stage.type}`}
      data-status={stage.status}
      className="px-3 py-3 text-sm"
    >
      <div className="flex items-baseline justify-between gap-3">
        <p className="font-medium text-[var(--color-ink)]">{STAGE_LABELS[stage.type]}</p>
        <span
          data-testid={`cca-status-${stage.type}`}
          className="border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]"
          style={{ color: TONE_COLOR[tone], borderColor: TONE_COLOR[tone] }}
        >
          {stage.status}
        </span>
      </div>
      <p className="mt-1 text-xs text-[var(--color-ink-muted)]">
        <span data-testid={`cca-candidate-${stage.type}`}>{stage.candidate || "-"}</span>
        {" → "}
        <span data-testid={`cca-binding-${stage.type}`}>
          {stage.binding ?? "not bound"}
        </span>
      </p>
      {stage.evidence.length > 0 ? (
        <ul
          data-testid={`cca-evidence-${stage.type}`}
          className="mt-2 list-disc pl-5 text-xs text-[var(--color-ink-muted)]"
        >
          {stage.evidence.map((line, i) => (
            <li key={`${stage.constraint_id}_ev_${i}`}>{line}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-[var(--color-ink-muted)]">No evidence recorded.</p>
      )}
      {!certified && (
        <p
          data-testid={`cca-blocked-reason-${stage.type}`}
          className="mt-2 border-l-2 pl-2 text-xs"
          style={{ color: TONE_COLOR[tone], borderColor: TONE_COLOR[tone] }}
        >
          {stage.reasons.filter((r) => r.trim()).join("; ") ||
            "No reason given by the cascade."}
        </p>
      )}
    </li>
  );
}

/**
 * Stage statuses exactly as the envelope reported them (CCA-01 schema, CCA-05
 * orchestrator). `trace` is taken as unknown on purpose: the wire value is
 * whatever the engine sent, and an item this UI cannot read is dropped and
 * counted rather than shown with a guessed status.
 */
export function ConstraintTracePanel({ trace }: { trace?: unknown }) {
  const stages = parseConstraintTrace(trace);
  const dropped = countMalformedStages(trace);
  const summary = summariseTrace(stages);
  const blockedStage = stages.find((s) => s.status !== "CERTIFIED") ?? null;

  return (
    <section data-testid="cca-panel" className="mt-10">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
        Constraint cascade
      </p>
      <p
        data-testid="cca-headline"
        className="mt-1 text-sm"
        style={{
          color: blockedStage
            ? TONE_COLOR[toneForStatus(blockedStage.status)]
            : "var(--color-ink)",
        }}
      >
        {traceHeadline(summary)}
      </p>
      {summary.ran === 0 ? (
        <p
          data-testid="cca-none"
          className="mt-3 border border-[var(--color-line)] bg-[var(--color-surface)]/70 px-3 py-4 text-sm text-[var(--color-ink-muted)]"
        >
          No cascade ran for the latest answer, so no stage is certified. Ask a question
          from Chat to produce a trace.
        </p>
      ) : (
        <ul className="mt-3 divide-y divide-[var(--color-line)] border border-[var(--color-line)] bg-[var(--color-surface)]/70">
          {stages.map((stage) => (
            <StageRow key={stage.constraint_id} stage={stage} />
          ))}
        </ul>
      )}
      {dropped > 0 && (
        <p
          data-testid="cca-dropped"
          className="mt-2 text-xs"
          style={{ color: TONE_COLOR.danger }}
        >
          {dropped} trace {dropped === 1 ? "item was" : "items were"} unreadable and dropped.
        </p>
      )}
    </section>
  );
}
