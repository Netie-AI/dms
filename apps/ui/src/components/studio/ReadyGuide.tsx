import { useEffect, useState } from "react";
import { useFeature } from "@/lib/features";

export type ReadyStep = {
  n: number;
  id: string;
  title: string;
  proves: string;
  done: boolean;
};

export type ReadyGuideBody = {
  steps: ReadyStep[];
  progress: { done: number; total: number };
  demo: boolean;
  demo_enabled: boolean;
  questions: string[];
  questions_status: string;
};

/** Numbered Studio guide. Renders nothing until the flag-on route answers. */
export function ReadyGuide({ initial }: { initial?: ReadyGuideBody | null }) {
  const [guide, setGuide] = useState<ReadyGuideBody | null>(initial ?? null);
  const [demoMsg, setDemoMsg] = useState<string | null>(null);
  const guideOn = useFeature("ready_guide");

  useEffect(() => {
    if (initial || !guideOn) return;
    let cancelled = false;
    void fetch("/api/v1/ready/guide")
      .then(async (res) => (res.ok ? ((await res.json()) as ReadyGuideBody) : null))
      .then((body) => {
        if (!cancelled && body && Array.isArray(body.steps)) setGuide(body);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [initial, guideOn]);

  const startDemo = async () => {
    setDemoMsg(null);
    const res = await fetch("/api/v1/ready/demo", { method: "POST" });
    if (!res.ok) {
      setDemoMsg("Demo did not start.");
      return;
    }
    const body = (await res.json()) as { guide?: ReadyGuideBody };
    if (body.guide && Array.isArray(body.guide.steps)) setGuide(body.guide);
    setDemoMsg("Demo data is ready. Confirm the tables, then the joins.");
  };

  if (!guide) return null;
  const { done, total } = guide.progress;
  return (
    <section
      data-testid="ready-guide"
      className="mt-6 border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-4 py-4"
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
        Getting started
      </p>
      <p className="mt-2 text-sm text-[var(--color-ink)]" data-testid="ready-progress">
        Step {done} of {total} done
      </p>
      <ol className="mt-3 space-y-3">
        {guide.steps.map((step) => (
          <li key={step.id} data-testid={`ready-step-${step.id}`}>
            <p className="text-sm text-[var(--color-ink)]">
              {step.n}. {step.title}
              {step.done ? " -- done" : ""}
            </p>
            <p className="text-xs text-[var(--color-ink-muted)]">Proves: {step.proves}</p>
          </li>
        ))}
      </ol>
      {guide.questions.length === 5 ? (
        <div data-testid="ready-questions" className="mt-4">
          <p className="text-sm text-[var(--color-ink)]">Try these 5 questions</p>
          <ul className="mt-2 space-y-1 text-sm">
            {guide.questions.map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {guide.demo_enabled && !guide.demo ? (
        <button
          type="button"
          data-testid="ready-demo"
          className="mt-4 border border-[var(--color-line)] px-3 py-1 text-xs"
          onClick={() => void startDemo()}
        >
          Start guided demo
        </button>
      ) : null}
      {demoMsg ? <p className="mt-2 text-xs text-[var(--color-ink-muted)]">{demoMsg}</p> : null}
    </section>
  );
}
