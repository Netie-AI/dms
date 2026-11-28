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
type Section = { id: string; title: string; kind: string };
type Version = {
  id: string;
  filename: string;
  pdf_sha256: string | null;
  judge: { ok: boolean; problems: string[] };
  cost: { prompt_tokens: number | null; completion_tokens: number | null; usd: number | null };
  sign: { status?: string; sha256?: string };
};
type RunView = {
  run_id: string;
  steps: number;
  stopped: string | null;
  spend: { prompt_tokens: number | null; completion_tokens: number | null; usd: number | null };
  sections: Section[];
  xml: string;
  versions: Version[];
};

const SKILLS = ["pptx", "docx", "xlsx", "pdf"] as const;
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
  const [format, setFormat] = useState("pptx");
  const [skillId, setSkillId] = useState("pptx");
  const [promptVersion, setPromptVersion] = useState("draft");
  const [promptText, setPromptText] = useState("");
  const [superGuide, setSuperGuide] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [brandName, setBrandName] = useState("");
  const [brandPrimary, setBrandPrimary] = useState("");
  const [brandFont, setBrandFont] = useState("");
  const [brandNotes, setBrandNotes] = useState("");
  const [route, setRoute] = useState("openvault");
  const [fewShots, setFewShots] = useState<FewShot[]>([{ title: "", text: "" }]);
  const [maxSteps, setMaxSteps] = useState(8);
  const [maxUsd, setMaxUsd] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<RunView | null>(null);
  const [target, setTarget] = useState("s0");
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
    format,
    skill_id: skillId,
    prompt_version: promptVersion,
    prompt_text: promptText,
    super_guide: superGuide,
    template_id: templateId,
    brand: { name: brandName, primary: brandPrimary, font: brandFont, notes: brandNotes },
    few_shots: fewShots.filter((shot) => shot.title || shot.text),
    route,
  };
  const posted = seeds.map((seed) => ({
    ...seed,
    space_id: seed.space_id || spaceId || null,
  }));

  async function preview() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/v1/create-docs/runs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          answers: posted,
          style,
          max_steps: maxSteps,
          max_usd: maxUsd.trim() === "" ? null : Number(maxUsd),
        }),
      });
      if (!res.ok) throw new Error(await readError(res));
      const body = (await res.json()) as RunView;
      setRun(body);
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
      const res = await fetch(`/api/v1/create-docs/runs/${run.run_id}/generate`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          style,
          max_steps: maxSteps,
          max_usd: maxUsd.trim() === "" ? null : Number(maxUsd),
        }),
      });
      if (!res.ok) throw new Error(await readError(res));
      setRun((await res.json()) as RunView);
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
      const res = await fetch(`/api/v1/create-docs/runs/${run.run_id}/edit`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ target, instruction: instruction.trim() }),
      });
      if (!res.ok) throw new Error(await readError(res));
      setRun((await res.json()) as RunView);
    } catch (err) {
      setError(err instanceof Error ? err.message : "edit failed");
    } finally {
      setBusy(false);
    }
  }

  async function download(which: "artifact" | "pdf", version: Version) {
    if (!run) return;
    const res = await fetch(
      `/api/v1/create-docs/runs/${run.run_id}/file?which=${which}&version=${version.id}`,
      { method: "POST" },
    );
    if (!res.ok) {
      setError(await readError(res));
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = which === "pdf" ? "netie-answers.pdf" : version.filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  if (!open) {
    return (
      <div className="mt-3 flex flex-wrap gap-2" data-testid="create-docs-hook">
        <button
          type="button"
          className="text-xs text-[var(--color-accent)] underline-offset-2 hover:underline"
          onClick={() => {
            setFormat("pptx");
            setSkillId("pptx");
            setOpen(true);
          }}
        >
          Create slides
        </button>
        <button
          type="button"
          className="text-xs text-[var(--color-accent)] underline-offset-2 hover:underline"
          onClick={() => {
            setFormat("docx");
            setSkillId("docx");
            setOpen(true);
          }}
        >
          Create doc
        </button>
      </div>
    );
  }

  return (
    <section className="mt-4 border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-3" data-testid="create-docs-panel">
      <h2 className="text-sm font-semibold">Create doc or slides</h2>
      <p className="mt-1 text-xs text-[var(--color-ink-muted)]">
        The provider writes the file. OpenVault meters the cost. A 402 stops the run.
        One repair if the judge fails. Pick a section to edit after that.
      </p>
      {posted.length === 0 ? (
        <p className="mt-2 text-xs">Ask a question in this chat first. The file can only use those answers.</p>
      ) : null}
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-xs">
          Format
          <select className={field} value={format} onChange={(e) => setFormat(e.target.value)}>
            <option value="pptx">pptx</option>
            <option value="docx">docx</option>
            <option value="pdf">pdf</option>
            <option value="html">html</option>
            <option value="xlsx">xlsx</option>
          </select>
        </label>
        <label className="text-xs">
          Skill
          <select className={field} value={skillId} onChange={(e) => setSkillId(e.target.value)}>
            {SKILLS.map((skill) => (
              <option key={skill} value={skill}>
                {skill}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs">
          Prompt version
          <input className={field} value={promptVersion} onChange={(e) => setPromptVersion(e.target.value)} />
        </label>
        <label className="text-xs">
          Template
          <input className={field} value={templateId} onChange={(e) => setTemplateId(e.target.value)} />
        </label>
        <label className="text-xs sm:col-span-2">
          Style prompt
          <textarea className={field} rows={3} value={promptText} onChange={(e) => setPromptText(e.target.value)} />
        </label>
        <label className="text-xs sm:col-span-2">
          Super guide
          <textarea className={field} rows={3} value={superGuide} onChange={(e) => setSuperGuide(e.target.value)} />
        </label>
        <label className="text-xs">
          Brand name
          <input className={field} value={brandName} onChange={(e) => setBrandName(e.target.value)} />
        </label>
        <label className="text-xs">
          Primary color
          <input className={field} value={brandPrimary} onChange={(e) => setBrandPrimary(e.target.value)} />
        </label>
        <label className="text-xs">
          Font
          <input className={field} value={brandFont} onChange={(e) => setBrandFont(e.target.value)} />
        </label>
        <label className="text-xs">
          Brand notes
          <input className={field} value={brandNotes} onChange={(e) => setBrandNotes(e.target.value)} />
        </label>
        <label className="text-xs">
          Route
          <select className={field} value={route} onChange={(e) => setRoute(e.target.value)}>
            <option value="openvault">openvault</option>
            <option value="cursor">cursor</option>
          </select>
        </label>
        <label className="text-xs">
          Max steps (1-8)
          <input
            className={field}
            type="number"
            min={1}
            max={8}
            value={maxSteps}
            onChange={(e) => setMaxSteps(Math.min(8, Math.max(1, Number(e.target.value) || 1)))}
          />
        </label>
        <label className="text-xs sm:col-span-2">
          Max USD, optional. Blank leaves the stop to OpenVault.
          <input className={field} value={maxUsd} onChange={(e) => setMaxUsd(e.target.value)} />
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
          </p>
          <ul className="mt-2 list-disc pl-4">
            {run.sections.map((section) => (
              <li key={section.id}>
                {section.id}: {section.title}
              </li>
            ))}
          </ul>
          <label className="mt-2 block">
            Context sent to the provider
            <textarea className={field} rows={4} readOnly value={run.xml} />
          </label>
          {run.versions.length > 0 ? (
            <ul className="mt-3 space-y-2">
              {run.versions.map((version) => (
                <li key={version.id} className="border border-[var(--color-line)] px-2 py-2">
                  <p>
                    {version.id} | judge {version.judge.ok ? "pass" : version.judge.problems.join(", ")} | sha256{" "}
                    {version.pdf_sha256} | sign {version.sign?.status || "none"}
                  </p>
                  <div className="mt-1 flex gap-2">
                    <button type="button" className="underline" onClick={() => void download("artifact", version)}>
                      Download file
                    </button>
                    <button type="button" className="underline" onClick={() => void download("pdf", version)}>
                      Download PDF
                    </button>
                  </div>
                </li>
              ))}
            </ul>
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
