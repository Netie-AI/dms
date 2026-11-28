import { useEffect, useState } from "react";
import "./createSurface.css";

const STATUS_PATH = "/api/v1/studio/create/status";

type Part = { id: string; title: string; body?: string; sql?: string };
type Artifact = {
  kind?: string;
  title?: string;
  parts?: Part[];
  style?: { colours?: string; fonts?: string; logo?: string; tone?: string };
};
type Spend = { tokens?: number | null; usd?: number | null; steps?: number };
type Session = {
  session_id: string;
  step: string;
  shown: boolean;
  kind?: string;
  catalog?: { name: string }[];
  artifact: Artifact | null;
  versions?: { id: number; note: string }[];
  current_version?: number;
  issues: string[];
  estimate?: { tokens?: number | null; cost?: number | null };
  spend?: Spend;
  message?: string | null;
  warning?: string | null;
  stopped?: string | null;
};

const FORMATS = ["app", "slides", "doc"] as const;
const TEMPLATES = ["plain", "brief", "bold"] as const;
const TOOLS = ["run_sql", "fetch_ontology", "chart", "render_doc", "sign_request", "save_artifact"];

function meter(spend?: Spend): string {
  if (!spend) return "n/a";
  const tokens = spend.tokens == null ? "n/a" : String(spend.tokens);
  const usd = spend.usd == null ? "n/a" : String(spend.usd);
  return `${tokens} tokens, ${usd} USD, ${spend.steps ?? 0} steps`;
}

export function CreateSurface({ spaceId }: { spaceId: string | null }) {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    fetch(STATUS_PATH, { signal: ctrl.signal })
      .then((res) => (res.ok ? res.json() : { enabled: false }))
      .then((body: { enabled?: boolean }) => setEnabled(body.enabled === true))
      .catch(() => setEnabled(false));
    return () => ctrl.abort();
  }, []);

  if (!enabled) return null;
  return <CreateStudio spaceId={spaceId} />;
}

export function CreateStudio({ spaceId }: { spaceId: string | null }) {
  const [kind, setKind] = useState<(typeof FORMATS)[number]>("app");
  const [goal, setGoal] = useState("");
  const [audience, setAudience] = useState("");
  const [style, setStyle] = useState("");
  const [template, setTemplate] = useState<(typeof TEMPLATES)[number]>("plain");
  const [colours, setColours] = useState("");
  const [fonts, setFonts] = useState("");
  const [logo, setLogo] = useState("");
  const [tone, setTone] = useState("");
  const [example, setExample] = useState("");
  const [tools, setTools] = useState<Record<string, boolean>>({});
  const [inputs, setInputs] = useState<string[]>([]);
  const [sectionId, setSectionId] = useState("");
  const [instruction, setInstruction] = useState("");
  const [dmsKey, setDmsKey] = useState("");
  const [ovKey, setOvKey] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [thinking, setThinking] = useState("");
  const [error, setError] = useState("");

  function headers(): Record<string, string> {
    const out: Record<string, string> = { "Content-Type": "application/json" };
    if (dmsKey.trim()) out.Authorization = `Bearer ${dmsKey.trim()}`;
    if (ovKey.trim()) out["X-OpenVault-Key"] = ovKey.trim();
    return out;
  }

  async function post(path: string, body?: unknown, method = "POST"): Promise<Session> {
    const res = await fetch(path, {
      method,
      headers: headers(),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const detail = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(typeof detail.detail === "string" ? detail.detail : "create_failed");
    }
    return detail as Session;
  }

  async function run(label: string, work: () => Promise<Session>) {
    setThinking(label);
    setError("");
    try {
      setSession(await work());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "create_failed");
    } finally {
      setThinking("");
    }
  }

  function toggleTool(name: string) {
    setTools((prev) => ({ ...prev, [name]: prev[name] === false }));
  }

  function toggleInput(name: string) {
    setInputs((prev) => (prev.includes(name) ? prev.filter((item) => item !== name) : [...prev, name]));
  }

  const catalog = session?.catalog ?? [];
  const parts = session?.artifact?.parts ?? [];

  return (
    <section className="create-surface" data-testid="create-surface">
      <p className="create-kicker">Create</p>
      <h2 className="create-title">Make the work from this Space</h2>
      <p className="create-lede">
        The provider writes the {kind}. DMS checks the result. Your OpenVault key routes the model.
      </p>
      {thinking ? <p className="create-thinking">{thinking}</p> : null}
      <div className="create-actions">
        {FORMATS.map((item) => (
          <button
            key={item}
            type="button"
            className="create-action"
            disabled={Boolean(session)}
            onClick={() => setKind(item)}
          >
            <strong>{item}</strong>
            <span>{item === kind ? "Selected" : "Format"}</span>
          </button>
        ))}
      </div>
      <label className="create-field">
        Goal
        <input value={goal} onChange={(event) => setGoal(event.target.value)} />
      </label>
      <label className="create-field">
        Audience
        <input value={audience} onChange={(event) => setAudience(event.target.value)} />
      </label>
      <label className="create-field">
        Style prompt
        <textarea value={style} onChange={(event) => setStyle(event.target.value)} />
      </label>
      <label className="create-field">
        Template
        <select value={template} onChange={(event) => setTemplate(event.target.value as typeof template)}>
          {TEMPLATES.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </label>
      <label className="create-field">
        Brand colours
        <input value={colours} onChange={(event) => setColours(event.target.value)} />
      </label>
      <label className="create-field">
        Brand fonts
        <input value={fonts} onChange={(event) => setFonts(event.target.value)} />
      </label>
      <label className="create-field">
        Logo
        <input value={logo} onChange={(event) => setLogo(event.target.value)} />
      </label>
      <label className="create-field">
        Tone
        <input value={tone} onChange={(event) => setTone(event.target.value)} />
      </label>
      <label className="create-field">
        Example output
        <textarea value={example} onChange={(event) => setExample(event.target.value)} />
      </label>
      <fieldset className="create-card">
        <legend>Tools</legend>
        {TOOLS.map((name) => (
          <label key={name}>
            <input
              type="checkbox"
              checked={tools[name] !== false}
              onChange={() => toggleTool(name)}
            />
            {name}
          </label>
        ))}
      </fieldset>
      <label className="create-field">
        DMS key
        <input value={dmsKey} onChange={(event) => setDmsKey(event.target.value)} />
      </label>
      <label className="create-field">
        OpenVault key
        <input value={ovKey} onChange={(event) => setOvKey(event.target.value)} />
      </label>
      <button
        type="button"
        className="create-go"
        disabled={!spaceId || !goal.trim() || !audience.trim()}
        onClick={() =>
          run("Opening", () =>
            post("/api/v1/studio/create/sessions", {
              space_id: spaceId,
              kind,
              goal,
              audience,
              style,
              template,
              brand_colours: colours,
              brand_fonts: fonts,
              brand_logo: logo,
              brand_tone: tone,
              inputs,
              examples: example.trim() ? [{ name: "example", body: example }] : [],
              tools,
            })
          )
        }
      >
        Continue
      </button>
      {catalog.length ? (
        <fieldset className="create-card">
          <legend>Inputs</legend>
          {catalog.map((row) => (
            <label key={row.name}>
              <input
                type="checkbox"
                checked={inputs.includes(row.name)}
                onChange={() => toggleInput(row.name)}
              />
              {row.name}
            </label>
          ))}
        </fieldset>
      ) : null}
      {session ? (
        <button
          type="button"
          className="create-go"
          onClick={() =>
            run("Asking the generator", () =>
              post(`/api/v1/studio/create/sessions/${session.session_id}/generate`, { inputs })
            )
          }
        >
          Preview
        </button>
      ) : null}
      {session?.estimate ? (
        <p className="create-estimate">
          Estimate {session.estimate.tokens ?? "n/a"} tokens, {session.estimate.cost ?? "n/a"} USD
        </p>
      ) : null}
      {session?.spend ? <p className="create-spend">{meter(session.spend)}</p> : null}
      {session?.warning ? <p className="create-warn">{session.warning}</p> : null}
      {session?.message ? <p className="create-note">{session.message}</p> : null}
      {session?.artifact ? (
        <article className="create-artifact">
          <h3>{session.artifact.title}</h3>
          {parts.map((part) => (
            <p key={part.id}>
              <button type="button" className="create-quiet" onClick={() => setSectionId(part.id)}>
                {part.title}
              </button>
            </p>
          ))}
        </article>
      ) : null}
      {session?.shown ? (
        <>
          <label className="create-field">
            Section
            <input value={sectionId} onChange={(event) => setSectionId(event.target.value)} />
          </label>
          <label className="create-field">
            Edit instruction
            <textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} />
          </label>
          <button
            type="button"
            className="create-go"
            disabled={!sectionId.trim() || !instruction.trim()}
            onClick={() =>
              run("Editing", () =>
                post(`/api/v1/studio/create/sessions/${session.session_id}/edit`, {
                  section_id: sectionId,
                  instruction,
                })
              )
            }
          >
            Apply edit
          </button>
          <ul>
            {(session.versions ?? []).map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="create-quiet"
                  onClick={() =>
                    run("Restoring", () =>
                      post(`/api/v1/studio/create/sessions/${session.session_id}/restore`, {
                        version_id: item.id,
                      })
                    )
                  }
                >
                  Restore {item.id}
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            className="create-go"
            onClick={() =>
              run("Saving", () => post(`/api/v1/studio/create/sessions/${session.session_id}/confirm`))
            }
          >
            Confirm
          </button>
        </>
      ) : null}
      {error ? <p className="create-note">{error}</p> : null}
    </section>
  );
}
