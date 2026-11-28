import type { ReactNode } from "react";
import { isAbstain, namedAbstainReason, type AbstainFields } from "@/components/studio/abstainReason";
import { isBareCode, plainShownReason, recordedModel } from "@/lib/reasonCopy";

export type StampEnvelope = AbstainFields & {
  served_model?: unknown;
  reconfirm?: { reason_code?: unknown } | null;
};

function detailCode(envelope: StampEnvelope): string {
  const raw = isAbstain(envelope) ? namedAbstainReason(envelope) : "";
  if (raw && isBareCode(raw)) return raw;
  const offer = envelope.reconfirm;
  const code = typeof offer?.reason_code === "string" ? offer.reason_code.trim() : "";
  return code && isBareCode(code) ? code : "";
}

export function assumptionNotes(assumptions: unknown): string[] {
  if (!Array.isArray(assumptions)) return [];
  return assumptions.map((item) => String(item).trim()).filter(Boolean);
}

export function ServedModelLine({ envelope }: { envelope: StampEnvelope }) {
  const name = recordedModel(envelope);
  if (!name) return null;
  return (
    <p data-testid="served-model" className="mt-1 text-xs text-[var(--color-ink-muted)]">
      Answered by {name}
    </p>
  );
}

export function AbstainNote({ envelope }: { envelope: StampEnvelope }) {
  if (!isAbstain(envelope)) return null;
  return (
    <div
      data-testid="abstain-reason"
      className="mb-3 border border-[var(--color-line)] bg-[var(--color-paper)] px-3 py-2.5"
    >
      <p data-testid="abstain-label" className="text-sm text-[var(--color-ink)]">
        {plainShownReason(namedAbstainReason(envelope))}
      </p>
    </div>
  );
}

export function AnswerDetails({
  envelope,
  children,
}: {
  envelope: StampEnvelope;
  children?: ReactNode;
}) {
  const notes = assumptionNotes(envelope.assumptions);
  const showCode = detailCode(envelope);
  const model = recordedModel(envelope);
  if (notes.length === 0 && children == null && !showCode && !model) return null;
  return (
    <details data-testid="answer-details" className="mt-4">
      <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
        Details
      </summary>
      {model ? <ServedModelLine envelope={envelope} /> : null}
      {showCode ? (
        <p
          data-testid="abstain-code"
          className="mt-1 break-all font-mono text-[11px] text-[var(--color-ink-muted)]"
        >
          {showCode}
        </p>
      ) : null}
      {notes.length > 0 && (
        <ul
          data-testid="answer-assumptions"
          className="mt-1 list-inside list-disc text-sm text-[var(--color-ink-muted)]"
        >
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
      {children}
    </details>
  );
}
