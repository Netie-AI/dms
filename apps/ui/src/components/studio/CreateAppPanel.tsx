import { useEffect, useState } from "react";
import "./createSurface.css";

const STATUS_PATH = "/api/v1/studio/apps/status";

type Spend = { usd?: number; tokens?: number; calls?: number };
type Judge = { ok?: boolean; errors?: string[] };
type AppView = {
  ok?: boolean;
  code?: string;
  app_id?: string;
  app_token?: string;
  context_ref?: string;
  spend?: Spend;
  judge?: Judge;
  published?: boolean;
  actions?: { id: string; label?: string; connector?: string }[];
};

function meter(spend?: Spend): string {
  if (!spend) return "Live cost: n/a";
  const usd = spend.usd == null ? "n/a" : String(spend.usd);
  const tokens = spend.tokens == null ? "n/a" : String(spend.tokens);
  return `Live cost: ${tokens} tokens, ${usd} USD, ${spend.calls ?? 0} calls`;
}

export function CreateAppPanel({ spaceId }: { spaceId: string | null }) {
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
  return <CreateAppStudio spaceId={spaceId} />;
}

function CreateAppStudio({ spaceId }: { spaceId: string | null }) {
  const [goal, setGoal] = useState("Inventory reorder app with a create purchase order action");
  const [dmsKey, setDmsKey] = useState("");
  const [ovKey, setOvKey] = useState("");
  const [app, setApp] = useState<AppView | null>(null);
  const [preview, setPreview] = useState("");
  const [approval, setApproval] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  function headers(extra?: Record<string, string>): Record<string, string> {
    const out: Record<string, string> = { "Content-Type": "application/json", ...(extra ?? {}) };
    if (dmsKey.trim()) out.Authorization = `Bearer ${dmsKey.trim()}`;
    if (ovKey.trim()) out["X-OpenVault-Key"] = ovKey.trim();
    return out;
  }

  async function generate() {
    setError("");
    setNote("");
    const res = await fetch("/api/v1/studio/apps", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ space_id: spaceId || "space", goal }),
    });
    const body = (await res.json().catch(() => ({}))) as AppView & { detail?: string };
    if (!res.ok) {
      setError(typeof body.detail === "string" ? body.detail : "create_app_failed");
      return;
    }
    setApp(body);
    if (!body.app_id) return;
    const view = await fetch(`/api/v1/studio/apps/${body.app_id}/preview`, { headers: headers() });
    if (view.ok) setPreview(await view.text());
  }

  async function confirm() {
    if (!app?.app_id || !app.actions?.length) return;
    setError("");
    const res = await fetch(`/api/v1/studio/apps/${app.app_id}/actions/confirm`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ action_id: app.actions[0].id, approval_ref: approval }),
    });
    const body = (await res.json().catch(() => ({}))) as { detail?: string; code?: string; applied?: boolean };
    if (!res.ok) {
      setError(typeof body.detail === "string" ? body.detail : "confirm_failed");
      return;
    }
    setNote(body.applied ? "Applied" : body.code || "Held for confirmation");
  }

  async function chain(kind: "doc" | "slides" | "research") {
    if (!app?.app_id) return;
    setError("");
    const res = await fetch(`/api/v1/studio/apps/${app.app_id}/chain`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ kind, goal: `Next create: ${kind}` }),
    });
    const body = (await res.json().catch(() => ({}))) as { detail?: string; context_ref?: string; code?: string };
    if (!res.ok) {
      setError(typeof body.detail === "string" ? body.detail : "chain_failed");
      return;
    }
    setNote(`${body.context_ref || "handoff"} ${body.code || ""}`.trim());
  }

  async function publish() {
    if (!app?.app_id) return;
    const res = await fetch(`/api/v1/studio/apps/${app.app_id}/publish`, {
      method: "POST",
      headers: headers(),
    });
    const body = (await res.json().catch(() => ({}))) as AppView & { detail?: string };
    if (!res.ok) {
      setError(typeof body.detail === "string" ? body.detail : "publish_failed");
      return;
    }
    setApp(body);
  }

  const judge = app?.judge?.ok ? "Judge: booted, queries returned, actions dry-ran" : app?.judge?.errors?.join(", ");

  return (
    <section className="create-surface" data-testid="create-app-panel">
      <p className="create-kicker">Create app</p>
      <h2 className="create-title">Run a data app from this Space</h2>
      <p className="create-lede">
        The provider writes the frontend and the backend. DMS checks grants, confirms actions, and
        previews the sandbox result. {meter(app?.spend)}
      </p>
      <label className="create-field">
        Goal
        <input value={goal} onChange={(event) => setGoal(event.target.value)} />
      </label>
      <label className="create-field">
        DMS key
        <input value={dmsKey} onChange={(event) => setDmsKey(event.target.value)} />
      </label>
      <label className="create-field">
        OpenVault key
        <input value={ovKey} onChange={(event) => setOvKey(event.target.value)} />
      </label>
      <button type="button" className="create-action" onClick={() => void generate()}>
        Generate
      </button>
      {error ? <p className="create-thinking">{error}</p> : null}
      {judge ? <p className="create-lede">{judge}</p> : null}
      {preview ? (
        <iframe title="App preview" sandbox="" srcDoc={preview} className="create-surface" />
      ) : null}
      {app?.actions?.length ? (
        <label className="create-field">
          Approval ref
          <input value={approval} onChange={(event) => setApproval(event.target.value)} />
        </label>
      ) : null}
      {app?.actions?.length ? (
        <button type="button" className="create-action" onClick={() => void confirm()}>
          Confirm {app.actions[0].label || app.actions[0].id}
        </button>
      ) : null}
      {app?.app_id ? (
        <div className="create-actions">
          <button type="button" className="create-action" onClick={() => void chain("doc")}>
            Chain doc
          </button>
          <button type="button" className="create-action" onClick={() => void chain("slides")}>
            Chain slides
          </button>
          <button type="button" className="create-action" onClick={() => void chain("research")}>
            Chain research
          </button>
        </div>
      ) : null}
      {app?.ok ? (
        <button type="button" className="create-action" onClick={() => void publish()}>
          {app.published ? "Published" : "Publish"}
        </button>
      ) : null}
      {note ? <p className="create-lede">{note}</p> : null}
    </section>
  );
}
