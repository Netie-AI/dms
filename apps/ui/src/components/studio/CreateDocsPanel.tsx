import { useEffect, useState } from "react";
import type { ChatMessage } from "@/context/AppContext";
import type { AnswerEnvelope } from "@/lib/types";

export type CreateDocsSeed = {
  question: string;
  text: string;
  sql: string;
  sources: string[];
  as_of: string;
  space_id?: string | null;
  rows: Record<string, string | number | boolean | null>[];
};

type FewShot = { title: string; text: string };
type Chart = { id?: string; title?: string; label?: string; sql?: string; mark?: string };
type Part = { id?: string; title?: string; body?: string; sql?: string };
type Version = {
  id: string;
  artifact: { parts?: Part[]; charts?: Chart[]; title?: string };
  charts: Chart[];
  issues: string[];
  cost: { prompt_tokens: number | null; completion_tokens: number | null; usd: number | null };
  sign: { tool?: string; digest?: string };
};
type RunView = {
  run_id: string;
  steps: number;
  stopped: string | null;
  spend: { prompt_tokens: number | null; completion_tokens: number | null; usd: number | null };
  sections: { id: string; title: string }[];
  charts: Chart[];
  skills: string[];
  style: { guide: string; template: string; template_text: string };
  versions: Version[];
};

const field =
  "mt-1 w-full border border-[var(--color-line)] bg-[var(--color-paper)] px-2 py-1 text-sm text-[var(--color-ink)]";

function plainRows(rows: Record<string, unknown>[] | undefined): CreateDocsSeed["rows"] {
  return (rows || []).slice(0, 20).map((row) => {
    const out: CreateDocsSeed["rows"][number] = {};
    for (const [key, value] of Object.entries(row).slice(0, 12)) {
      if (
        value === null ||
        typeof value === "string" ||
        typeof value === "number" ||
        typeof value === "boolean"
      ) {
        out[key] = value;
      }
    }
    return out;
  });
}

export function seedFromEnvelope(envelope: AnswerEnvelope, question?: string): CreateDocsSeed {
  return {
    question: question || "",
    text: envelope.text || "",
    sql: envelope.sql_used || "",
    sources: (envelope.contributing_sources || [])
      .map((source) => source.container || source.ref_id)
      .filter((source): source is string => Boolean(source)),
    as_of: envelope.as_of || "",
    space_id: envelope.space_id,
    rows: plainRows(envelope.rows),
  };
}

export function seedsFromMessages(messages: ChatMessage[]): CreateDocsSeed[] {
  const seeds: CreateDocsSeed[] = [];
  messages.forEach((message, index) => {
    if (message.role !== "assistant") return;
    let question = "";
    for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
      const prior = messages[cursor];
      if (prior.role === "user") {
        question = prior.text;
        break;
      }
    }
    seeds.push(seedFromEnvelope(message.envelope, question));
  });
  return seeds.slice(-12);
}

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    return `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

export function CreateDocsPanel({
  seeds,
  spaceId,
  startOpen = false,
}: {
  seeds: CreateDocsSeed[];
  spaceId?: string | null;
  startOpen?: boolean;
}) {
  const [enabled, setEnabled] = useState(false);
  const [open, setOpen] = useState(startOpen);
  const [kind, setKind] = useState("slides");
  const [promptVersion, setPromptVersion] = useState("1");
  const [promptText, setPromptText] = useState("");
  const [guide, setGuide] = useState("");
  const [template, setTemplate] = useState("plain");
  const [colours, setColours] = useState("");
  const [fonts, setFonts] = useState("");
  const [logo, setLogo] = useState("");
  const [tone, setTone] = useState("");
  const [ovKey, setOvKey] = useState("");
  const [fewShots, setFewShots] = useState<FewShot[]>([{ title: "", text: "" }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<RunView | null>(null);
  const [target, setTarget] = useState("");
  const [instruction, setInstruction] = useState("");

  useEffect(() => {
    let live = true;
    void fetch("/api/v1/create-docs/status")
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => {
        if (live && body?.enabled === true) setEnabled(true);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  if (!enabled) return null;

  const style = {
    kind,
    prompt_version: promptVersion,
    prompt_text: promptText,
    guide,
    template,
    brand: { colours, fonts, logo, tone },
    few_shots: fewShots.filter((shot) => shot.title || shot.text),
  };
  const posted = seeds.map((seed) => ({
    ...seed,
    space_id: seed.space_id || spaceId || null,
  }));

  async function send(path: string, payload: unknown): Promise<RunView> {
    const res = await fetch(path, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(ovKey.trim() ? { "x-openvault-key": ovKey.trim() } : {}),
      },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await readError(res));
    return (await res.json()) as RunView;
  }

  async function preview() {
    setBusy(true);
    setError(null);
    try {
      const body = await send("/api/v1/create-docs/runs", { answers: posted, style });
      setRun(body);
      if (!guide && body.style?.guide) setGuide(body.style.guide);
      if (body.sections[0]) setTarget(body.sections[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "preview failed");
    } finally {
      setBusy(false);
    }
  }

  async function generate() {
    if (!run) return;
    setBusy(true);
    setError(null);
    try {
      const body = await send(`/api/v1/create-docs/runs/${run.run_id}/generate`, { style });
      setRun(body);
      if (body.sections[0]) setTarget(body.sections[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "generate failed");
    } finally {
      setBusy(false);
    }
  }

  async function applyEdit() {
    if (!run || !instruction.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setRun(
        await send(`/api/v1/create-docs/runs/${run.run_id}/edit`, {
          target,
          instruction: instruction.trim(),
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "edit failed");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="mt-3 flex flex-wrap gap-2" data-testid="create-docs-hook">
        <button
          type="button"
          className="text-xs text-[var(--color-accent)] underline-offset-2 hover:underline"
          onClick={() => {
            setKind("slides");
            setOpen(true);
          }}
        >
          Create slides
        </button>
        <button
          type="button"
          className="text-xs text-[var(--color-accent)] underline-offset-2 hover:underline"
          onClick={() => {
            setKind("doc");
            setOpen(true);
          }}
        >
          Create doc
        </button>
      </div>
    );
  }

  const latest = run?.versions[run.versions.length - 1];

  return (
    <section
      className="mt-4 border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-3"
      data-testid="create-docs-panel"
    >
      <h2 className="text-sm font-semibold">Create doc or slides</h2>
      <p className="mt-1 text-xs text-[var(--color-ink-muted)]">
        The model designs the deck. The template and the style guide are context only.
        OpenVault meters the cost. A 402 stops the run.
      </p>
      {posted.length === 0 ? (
        <p className="mt-2 text-xs">Ask a question in this chat first. The file can only use those answers.</p>
      ) : null}
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-xs">
          Kind
          <select className={field} value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="slides">slides</option>
            <option value="doc">doc</option>
          </select>
        </label>
        <label className="text-xs">
          Template
          <select className={field} value={template} onChange={(e) => setTemplate(e.target.value)}>
            <option value="plain">plain</option>
            <option value="brief">brief</option>
            <option value="bold">bold</option>
          </select>
        </label>
        <label className="text-xs">
          Prompt version
          <input className={field} value={promptVersion} onChange={(e) => setPromptVersion(e.target.value)} />
        </label>
        <label className="text-xs">
          OpenVault key
          <input className={field} value={ovKey} onChange={(e) => setOvKey(e.target.value)} />
        </label>
        <label className="text-xs sm:col-span-2">
          Style prompt
          <textarea className={field} rows={2} value={promptText} onChange={(e) => setPromptText(e.target.value)} />
        </label>
        <label className="text-xs sm:col-span-2">
          Style guide
          <textarea className={field} rows={2} value={guide} onChange={(e) => setGuide(e.target.value)} />
        </label>
        <label className="text-xs">
          Colours
          <input className={field} value={colours} onChange={(e) => setColours(e.target.value)} />
        </label>
        <label className="text-xs">
          Fonts
          <input className={field} value={fonts} onChange={(e) => setFonts(e.target.value)} />
        </label>
        <label className="text-xs">
          Logo
          <input className={field} value={logo} onChange={(e) => setLogo(e.target.value)} />
        </label>
        <label className="text-xs">
          Tone
          <input className={field} value={tone} onChange={(e) => setTone(e.target.value)} />
        </label>
      </div>
      <div className="mt-3">
        <p className="text-xs font-medium">Few-shot examples</p>
        {fewShots.map((shot, index) => (
          <div key={index} className="mt-2 grid gap-2 sm:grid-cols-2">
            <input
              className={field}
              placeholder="Title"
              value={shot.title}
              onChange={(e) =>
                setFewShots((prev) => prev.map((item, i) => (i === index ? { ...item, title: e.target.value } : item)))
              }
            />
            <input
              className={field}
              placeholder="Example"
              value={shot.text}
              onChange={(e) =>
                setFewShots((prev) => prev.map((item, i) => (i === index ? { ...item, text: e.target.value } : item)))
              }
            />
          </div>
        ))}
        <button
          type="button"
          className="mt-2 text-xs underline"
          onClick={() => setFewShots((prev) => (prev.length >= 4 ? prev : [...prev, { title: "", text: "" }]))}
        >
          Add example
        </button>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy || posted.length === 0}
          className="border border-[var(--color-line)] px-3 py-1 text-xs disabled:opacity-50"
          onClick={() => void preview()}
        >
          Preview outline
        </button>
        <button
          type="button"
          disabled={busy || !run}
          className="border border-[var(--color-line)] px-3 py-1 text-xs disabled:opacity-50"
          onClick={() => void generate()}
        >
          Confirm and generate
        </button>
      </div>
      {error ? <p className="mt-2 text-xs text-[var(--color-danger)]">{error}</p> : null}
      {run ? (
        <div className="mt-3 text-xs">
          <p>
            Steps {run.steps}
            {run.spend.prompt_tokens != null ? ` | prompt tokens ${run.spend.prompt_tokens}` : ""}
            {run.spend.completion_tokens != null ? ` | completion tokens ${run.spend.completion_tokens}` : ""}
            {run.spend.usd != null ? ` | USD ${run.spend.usd}` : " | USD from OpenVault"}
            {run.stopped ? ` | stopped ${run.stopped}` : ""}
            {run.skills.length ? ` | skills ${run.skills.join(", ")}` : ""}
          </p>
          {latest ? (
            <ul className="mt-2 space-y-2">
              {(latest.artifact.parts || []).map((part) => (
                <li key={part.id} className="border border-[var(--color-line)] px-2 py-2">
                  <p className="font-medium">{part.title}</p>
                  <p className="mt-1">{part.body}</p>
                  {part.sql ? <p className="mt-1 font-mono">{part.sql}</p> : null}
                </li>
              ))}
            </ul>
          ) : null}
          {run.charts.length ? (
            <ul className="mt-2 list-disc pl-4">
              {run.charts.map((chart) => (
                <li key={chart.id || chart.title || chart.label}>
                  {chart.title || chart.label}
                  {chart.mark ? ` (${chart.mark})` : ""}
                </li>
              ))}
            </ul>
          ) : null}
          {latest ? (
            <p className="mt-2">
              Judge {latest.issues.length ? latest.issues.join(", ") : "pass"} | sign {latest.sign.digest}
            </p>
          ) : null}
          {run.versions.length > 0 ? (
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <label>
                Section
                <select className={field} value={target} onChange={(e) => setTarget(e.target.value)}>
                  {run.sections.map((section) => (
                    <option key={section.id} value={section.id}>
                      {section.title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Instruction
                <input className={field} value={instruction} onChange={(e) => setInstruction(e.target.value)} />
              </label>
              <button
                type="button"
                disabled={busy || !instruction.trim()}
                className="border border-[var(--color-line)] px-3 py-1 text-xs disabled:opacity-50"
                onClick={() => void applyEdit()}
              >
                Apply edit
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
