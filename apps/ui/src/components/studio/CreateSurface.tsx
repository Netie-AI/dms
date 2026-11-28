import { useEffect, useState } from "react";
import "./createSurface.css";

const STATUS_PATH = "/api/v1/studio/create/status";

type Intent = {
  goal?: string;
  audience?: string;
  decision?: string;
  clarify?: string | null;
};

type Citation = { value?: string; sql?: string; source_row?: Record<string, unknown> };
type ForecastPoint = { time: string; value: number; low?: number; high?: number };
type Forecast = {
  id?: string;
  title?: string;
  method?: string;
  assumptions?: string;
  backtest_error?: number;
  history?: { time: string; value: number }[];
  forecast?: ForecastPoint[];
  citations?: Citation[];
};

type Artifact = {
  kind?: string;
  title?: string;
  exec_summary?: string;
  findings?: { text: string; sql?: string; citations?: Citation[] }[];
  actions?: string[];
  slides?: { title: string; insight: string; notes?: string; citations?: Citation[] }[];
  widgets?: { id: string; title: string; rows: Record<string, unknown>[]; citations?: Citation[] }[];
  filters?: { id: string; options: string[] }[];
  forecasts?: Forecast[];
};

type Spend = { tokens?: number | null; usd?: number | null; steps?: number };

type Session = {
  session_id: string;
  step: string;
  shown: boolean;
  final?: boolean;
  intent: Intent | null;
  plan: {
    tables?: { name: string }[];
    joins?: { left: string; right: string; verified?: boolean }[];
    metrics?: { name: string }[];
    outline?: { id: string; title: string }[];
  } | null;
  artifact: Artifact | null;
  issues: string[];
  changes: { path: string; before: string; after: string }[];
  estimate?: { tokens?: number | null; cost?: number | null };
  spend?: Spend;
  message?: string | null;
  warning?: string | null;
  outline?: { id: string; title: string }[];
};

type LiveEvent = {
  step: string;
  label: string;
  status: string;
  session?: Session;
  spend?: Spend;
  warning?: string;
};

function meter(spend?: Spend): string {
  if (!spend) return "n/a";
  const tokens = spend.tokens == null ? "n/a" : String(spend.tokens);
  const usd = spend.usd == null ? "n/a" : String(spend.usd);
  return `${tokens} tokens, ${usd} USD`;
}

const STEPS = ["Intent", "Plan", "Fetch", "SQL", "Check", "Draft", "File"] as const;

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
  const [kind, setKind] = useState<"app" | null>(null);
  const [goal, setGoal] = useState("");
  const [audience, setAudience] = useState("");
  const [answer, setAnswer] = useState("");
  const [tweak, setTweak] = useState("");
  const [instruction, setInstruction] = useState("");
  const [dmsKey, setDmsKey] = useState("");
  const [ovKey, setOvKey] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [progress, setProgress] = useState<LiveEvent[]>([]);
  const [thinking, setThinking] = useState("");
  const [error, setError] = useState("");

  function headers(json = true): Record<string, string> {
    const out: Record<string, string> = {};
    if (json) out["Content-Type"] = "application/json";
    if (dmsKey.trim()) out.Authorization = `Bearer ${dmsKey.trim()}`;
    if (ovKey.trim()) out["X-OpenVault-Key"] = ovKey.trim();
    return out;
  }

  async function post(path: string, body?: unknown): Promise<Session> {
    const res = await fetch(path, {
      method: "POST",
      headers: headers(),
      body: body ? JSON.stringify(body) : "{}",
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(typeof detail.detail === "string" ? detail.detail : "create_failed");
    }
    return (await res.json()) as Session;
  }

  async function streamRun(sessionId: string): Promise<Session> {
    const res = await fetch(`/api/v1/studio/create/sessions/${sessionId}/run`, {
      method: "POST",
      headers: headers(false),
    });
    if (!res.ok || !res.body) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(typeof detail.detail === "string" ? detail.detail : "create_failed");
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let last: Session | null = null;
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.split("\n").find((row) => row.startsWith("data: "));
        if (!line) continue;
        const event = JSON.parse(line.slice(6)) as LiveEvent;
        setProgress((prev) => [...prev, event]);
        setThinking(event.label);
        if (event.session) {
          last = event.session;
          setSession(event.session);
        }
        if (event.status === "fail") throw new Error(event.label || "create_failed");
      }
    }
    if (!last) throw new Error("create_failed");
    return last;
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

  const step = session?.step ?? (kind ? "intake" : "");
  const active =
    step === "intent" || step === "intent_confirmed"
      ? "Intent"
      : step === "plan"
        ? "Plan"
        : step === "check"
          ? "Check"
          : step === "draft"
            ? "Draft"
            : step === "ready"
              ? "File"
              : thinking
                ? "Fetch"
                : "";

  return (
    <section className="create-surface" data-testid="create-surface">
      <p className="create-kicker">Create</p>
      <h2 className="create-title">Make the work from this Space</h2>
      <p className="create-lede">
        A live dashboard. Your OpenVault key routes the model. Your DMS key opens the run.
      </p>
      {!spaceId ? <p className="create-note">Pick a Space.</p> : null}
      <div className="create-columns">
        <label className="create-field">
          DMS API key
          <input
            type="password"
            autoComplete="off"
            value={dmsKey}
            data-testid="create-dms-key"
            onChange={(event) => setDmsKey(event.target.value)}
          />
        </label>
        <label className="create-field">
          OpenVault key
          <input
            type="password"
            autoComplete="off"
            value={ovKey}
            data-testid="create-ov-key"
            onChange={(event) => setOvKey(event.target.value)}
          />
        </label>
      </div>
      <div className="create-actions">
        <Action label="Create app" note="A live dashboard" testId="create-app" disabled={!spaceId} onClick={() => setKind("app")} />
      </div>
      {kind ? (
        <ol className="create-steps" aria-label="Create steps">
          {STEPS.map((name) => (
            <li key={name} className={name === active ? "create-step is-active" : "create-step"}>
              {name}
            </li>
          ))}
        </ol>
      ) : null}
      {progress.length > 0 ? (
        <ol className="create-steps" aria-label="Live progress" data-testid="create-live">
          {progress.map((row, index) => (
            <li
              key={`${row.label}-${index}`}
              data-testid="create-progress"
              className={
                index === progress.length - 1 && row.status !== "done" && row.status !== "pass"
                  ? "create-step is-active create-thinking"
                  : "create-step"
              }
            >
              {row.label}
              <span className="create-spend" data-testid="create-spend">
                {meter(row.spend)}
              </span>
              {row.warning ? (
                <span className="create-spend" data-testid="create-warn">
                  {row.warning}
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
      {thinking ? (
        <p className="create-thinking" data-testid="create-thinking">
          {thinking}
        </p>
      ) : null}
      {error ? <p className="create-note">{error}</p> : null}
      {kind && !session ? (
        <form
          className="create-card"
          onSubmit={(event) => {
            event.preventDefault();
            if (!spaceId) return;
            void run("Understanding your question", () =>
              post("/api/v1/studio/create/sessions", {
                space_id: spaceId,
                kind,
                goal,
                audience,
              }),
            );
          }}
        >
          <label className="create-field">
            Goal
            <textarea value={goal} onChange={(event) => setGoal(event.target.value)} required rows={3} />
          </label>
          <label className="create-field">
            Audience
            <input value={audience} onChange={(event) => setAudience(event.target.value)} required />
          </label>
          <button className="create-go" type="submit" data-testid="create-goal-submit">
            Continue
          </button>
        </form>
      ) : null}
      {session?.intent ? (
        <article className="create-card" data-testid="create-intent">
          <h3>Intent</h3>
          <p className="create-note">Goal. {session.intent.goal}</p>
          <p className="create-note">Audience. {session.intent.audience}</p>
          <p className="create-note">Decision. {session.intent.decision}</p>
          {session.intent.clarify ? (
            <label className="create-field">
              {session.intent.clarify}
              <input value={answer} onChange={(event) => setAnswer(event.target.value)} />
            </label>
          ) : null}
          {session.step === "intent" ? (
            <button
              className="create-go"
              type="button"
              data-testid="create-confirm"
              onClick={() =>
                void run("Understanding your question", async () => {
                  const next = await post(
                    `/api/v1/studio/create/sessions/${session.session_id}/intent`,
                    { answer: answer || null },
                  );
                  if (next.step === "intent_confirmed") {
                    setThinking("Drafting the plan");
                    return post(`/api/v1/studio/create/sessions/${next.session_id}/plan`, {});
                  }
                  return next;
                })
              }
            >
              Confirm intent
            </button>
          ) : null}
        </article>
      ) : null}
      {session?.plan && session.step !== "ready" ? (
        <article className="create-card" data-testid="create-plan">
          <h3>Plan</h3>
          <p className="create-note" data-testid="create-estimate">
            Estimate. {session.estimate?.tokens ?? "n/a"} tokens, {session.estimate?.cost ?? "n/a"} USD
          </p>
          {(session.outline ?? []).map((piece) => (
            <p key={piece.id}>{piece.title}</p>
          ))}
          {session.warning ? (
            <p className="create-note" data-testid="create-warn">
              {session.warning}
            </p>
          ) : null}
          {session.message ? (
            <p className="create-note" data-testid="create-budget">
              {session.message}
            </p>
          ) : null}
          <div className="create-columns">
            <div>
              <p className="create-kicker">Tables</p>
              {(session.plan.tables ?? []).map((row) => (
                <p key={row.name}>{row.name}</p>
              ))}
            </div>
            <div>
              <p className="create-kicker">Joins</p>
              {(session.plan.joins ?? []).length === 0 ? <p>None</p> : null}
              {(session.plan.joins ?? []).map((row) => (
                <p key={`${row.left}-${row.right}`}>
                  {row.left} to {row.right}
                </p>
              ))}
            </div>
            <div>
              <p className="create-kicker">Metrics</p>
              {(session.plan.metrics ?? []).map((row) => (
                <p key={row.name}>{row.name}</p>
              ))}
            </div>
          </div>
          <label className="create-field">
            Tweak
            <input value={tweak} onChange={(event) => setTweak(event.target.value)} />
          </label>
          <button
            className="create-quiet"
            type="button"
            onClick={() =>
              void run("Drafting the plan", () =>
                post(`/api/v1/studio/create/sessions/${session.session_id}/plan`, {
                  tweak: tweak || null,
                }),
              )
            }
          >
            Update plan
          </button>
          {session.step === "plan" || session.step === "check" ? (
            <button
              className="create-go"
              type="button"
              data-testid="create-generate"
              onClick={() => {
                setProgress([]);
                void run("Fetching tables", () => streamRun(session.session_id));
              }}
            >
              Generate
            </button>
          ) : null}
        </article>
      ) : null}
      {session?.shown && session.artifact && session.step === "draft" ? (
        <>
          <ArtifactView artifact={session.artifact} changes={session.changes} />
          <button
            className="create-go"
            type="button"
            data-testid="create-confirm-file"
            onClick={() =>
              void run("Writing the file", () =>
                post(`/api/v1/studio/create/sessions/${session.session_id}/confirm`),
              )
            }
          >
            Confirm and write the file
          </button>
        </>
      ) : null}
      {session?.step === "ready" && session.artifact ? (
        <>
          <div className="create-ready" data-testid="create-ready">
            <svg className="create-ready-mark" viewBox="0 0 48 48" aria-hidden="true">
              <circle cx="24" cy="24" r="20" />
              <path d="M16 25 l5 5 l11-13" />
            </svg>
            <p>Ready</p>
          </div>
          <ArtifactView artifact={session.artifact} changes={session.changes} />
          <form
            className="create-card"
            onSubmit={(event) => {
              event.preventDefault();
              void run("Revising", () =>
                post(`/api/v1/studio/create/sessions/${session.session_id}/improve`, {
                  instruction,
                }),
              );
            }}
          >
            <label className="create-field">
              Improve
              <input
                value={instruction}
                onChange={(event) => setInstruction(event.target.value)}
                placeholder="Say what to change"
              />
            </label>
            <button className="create-go" type="submit">
              Apply
            </button>
          </form>
        </>
      ) : null}
      {session && session.issues.length > 0 && !session.shown ? (
        <p className="create-note" data-testid="create-issues">
          Held back: {session.issues.join(", ")}
        </p>
      ) : null}
    </section>
  );
}

function Action({
  label,
  note,
  testId,
  disabled,
  onClick,
}: {
  label: string;
  note: string;
  testId: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button className="create-action" type="button" data-testid={testId} disabled={disabled} onClick={onClick}>
      <strong>{label}</strong>
      <span>{note}</span>
    </button>
  );
}

function Citations({ rows }: { rows?: Citation[] }) {
  if (!rows || rows.length === 0) return null;
  return (
    <div>
      {rows.slice(0, 4).map((row, index) => (
        <p key={`${row.value}-${index}`} className="create-note" data-testid="create-citation">
          {row.value} from {row.sql}
        </p>
      ))}
    </div>
  );
}

function ArtifactView({
  artifact,
  changes,
}: {
  artifact: Artifact;
  changes: Session["changes"];
}) {
  return (
    <article className="create-artifact" data-testid="create-artifact">
      <h3>{artifact.title}</h3>
      {artifact.exec_summary ? <p className="create-note">{artifact.exec_summary}</p> : null}
      {(artifact.findings ?? []).map((row) => (
        <div key={row.text}>
          <p className="create-note">{row.text}</p>
          <Citations rows={row.citations} />
        </div>
      ))}
      {(artifact.actions ?? []).map((row) => (
        <p key={row} className="create-note">
          {row}
        </p>
      ))}
      {(artifact.slides ?? []).map((row) => (
        <div key={row.title}>
          <p>{row.title}</p>
          <p className="create-note">{row.insight}</p>
          <Citations rows={row.citations} />
        </div>
      ))}
      {(artifact.widgets ?? []).map((row) => (
        <div key={row.id}>
          <p className="create-note">{row.title}</p>
          <Citations rows={row.citations} />
        </div>
      ))}
      {(artifact.forecasts ?? []).map((row) => (
        <ForecastView key={row.id ?? row.title} block={row} />
      ))}
      {changes.length > 0 ? (
        <p className="create-note" data-testid="create-changes">
          Changed {changes.length}. {changes[0].path} {changes[0].before} to {changes[0].after}
        </p>
      ) : null}
    </article>
  );
}

function ForecastView({ block }: { block: Forecast }) {
  const history = block.history ?? [];
  const future = block.forecast ?? [];
  const points = [...history.map((row) => row.value), ...future.map((row) => row.value)];
  const max = Math.max(...points, 1);
  const min = Math.min(...points, 0);
  const span = Math.max(max - min, 1);
  const all = [...history, ...future];
  const coords = all.map((row, index) => {
    const x = all.length === 1 ? 0 : (index / (all.length - 1)) * 280;
    const y = 100 - ((row.value - min) / span) * 80;
    return `${x},${y}`;
  });
  const band = future.map((row, index) => {
    const x = ((history.length + index) / Math.max(all.length - 1, 1)) * 280;
    const y = 100 - ((row.value - min) / span) * 80;
    return `${x},${y}`;
  });
  return (
    <div className="create-forecast" data-testid="create-forecast">
      <p className="create-kicker">{block.title ?? "Forecast"}</p>
      <svg viewBox="0 0 280 110" role="img" aria-label="Forecast">
        <polyline fill="none" stroke="var(--color-accent)" strokeWidth="1.6" points={coords.join(" ")} />
        <polyline
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth="1"
          strokeDasharray="3 3"
          points={band.join(" ")}
        />
      </svg>
      <p className="create-note">{block.assumptions}</p>
      <p className="create-note">Backtest error {block.backtest_error}</p>
      <Citations rows={block.citations} />
    </div>
  );
}
