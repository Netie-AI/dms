"use client";

import { useEffect, useState } from "react";
import AppShell from "../../components/AppShell";
import { useRole } from "../../context/RoleContext";
import {
  ApiOfflineError,
  createSpace,
  fetchSpaces,
  loginDms,
  postQuery,
  setApiRoleKey,
} from "../../lib/api";

export default function SpacesPage() {
  const { role } = useRole();
  const [spaces, setSpaces] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState(null);
  const [error, setError] = useState("");
  const [newName, setNewName] = useState("");
  const [loginEmail, setLoginEmail] = useState("admin@dms.local");
  const [loginPassword, setLoginPassword] = useState("admin");
  const [busy, setBusy] = useState(false);

  const active = spaces.find((s) => s.id === activeId) || null;

  async function refresh() {
    try {
      const res = await fetchSpaces();
      setSpaces(res.spaces || []);
      if (!activeId && res.spaces?.[0]) setActiveId(res.spaces[0].id);
    } catch (e) {
      if (!(e instanceof ApiOfflineError)) setError(String(e.message || e));
    }
  }

  useEffect(() => {
    setApiRoleKey(role.id === "ANALYST" ? "ANALYST" : role.id);
    refresh();
  }, [role.id]);

  async function onLogin() {
    setBusy(true);
    setError("");
    try {
      await loginDms({ email: loginEmail, password: loginPassword, org_slug: "default" });
      await refresh();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onCreate() {
    if (!newName.trim()) return;
    setBusy(true);
    setError("");
    try {
      setApiRoleKey("STEWARD");
      const sp = await createSpace({
        name: newName.trim(),
        sources: [{ kind: "table", ref: "silver.inventory", scope: "company" }],
      });
      setNewName("");
      await refresh();
      setActiveId(sp.id);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onAsk() {
    if (!question.trim()) return;
    setBusy(true);
    setError("");
    try {
      const res = await postQuery(question.trim(), { spaceId: activeId || undefined });
      setAnswer(res);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell>
      <div style={{ display: "grid", gridTemplateColumns: "240px 1fr 280px", minHeight: "calc(100vh - 48px)" }}>
        <aside style={{ borderRight: "1px solid rgba(255,255,255,0.1)", padding: "1rem" }}>
          <h2 style={{ fontSize: "0.85rem", letterSpacing: "0.08em" }}>SPACES</h2>
          <ul style={{ listStyle: "none", padding: 0, margin: "0.75rem 0" }}>
            <li>
              <button
                type="button"
                onClick={() => setActiveId(null)}
                style={{
                  width: "100%",
                  textAlign: "left",
                  background: !activeId ? "rgba(110,251,203,0.12)" : "transparent",
                  border: "none",
                  color: "inherit",
                  padding: "0.4rem",
                  cursor: "pointer",
                }}
              >
                Company (all ACL)
              </button>
            </li>
            {spaces.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => setActiveId(s.id)}
                  style={{
                    width: "100%",
                    textAlign: "left",
                    background: activeId === s.id ? "rgba(110,251,203,0.12)" : "transparent",
                    border: "none",
                    color: "inherit",
                    padding: "0.4rem",
                    cursor: "pointer",
                  }}
                >
                  {s.name}
                </button>
              </li>
            ))}
          </ul>
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="New space name"
            style={{ width: "100%", marginBottom: 8 }}
          />
          <button type="button" disabled={busy} onClick={onCreate}>
            Create space
          </button>
        </aside>

        <main style={{ padding: "1.25rem" }}>
          <h1 style={{ marginTop: 0, fontSize: "1.35rem" }}>Central chat</h1>
          <p style={{ opacity: 0.75, fontSize: "0.9rem" }}>
            {active
              ? `Sandbox: ${active.name} (${active.sources?.length || 0} sources)`
              : "Company scope — not sandboxed"}
          </p>
          {error ? <p style={{ color: "#f87171" }}>{error}</p> : null}
          <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
            <input
              style={{ flex: 1 }}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onAsk()}
              placeholder="Ask about Excel / warehouse data…"
            />
            <button type="button" disabled={busy} onClick={onAsk}>
              Ask
            </button>
          </div>
          {answer ? (
            <div style={{ marginTop: 24 }}>
              <div style={{ fontSize: "0.7rem", opacity: 0.7, marginBottom: 8 }}>
                {[answer.badge || answer.layer, answer.query_source].filter(Boolean).join(" · ")}
              </div>
              <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit" }}>{answer.answer}</pre>
              {answer.sql_used ? (
                <pre style={{ fontSize: "0.75rem", opacity: 0.8, marginTop: 12 }}>{answer.sql_used}</pre>
              ) : null}
            </div>
          ) : null}
        </main>

        <aside style={{ borderLeft: "1px solid rgba(255,255,255,0.1)", padding: "1rem", fontSize: "0.8rem" }}>
          <h3 style={{ marginTop: 0 }}>Sources in scope</h3>
          {active?.sources?.length ? (
            <ul>
              {active.sources.map((s, i) => (
                <li key={i}>
                  <code>
                    {s.scope}.{s.kind}:{s.ref}
                  </code>
                </li>
              ))}
            </ul>
          ) : (
            <p style={{ opacity: 0.6 }}>All company ACL sources</p>
          )}
          <h3>Login</h3>
          <p style={{ opacity: 0.7 }}>Seed: admin@dms.local / admin</p>
          <input value={loginEmail} onChange={(e) => setLoginEmail(e.target.value)} style={{ width: "100%", marginBottom: 6 }} />
          <input
            type="password"
            value={loginPassword}
            onChange={(e) => setLoginPassword(e.target.value)}
            style={{ width: "100%", marginBottom: 6 }}
          />
          <button type="button" disabled={busy} onClick={onLogin}>
            Sign in
          </button>
          <p style={{ marginTop: 16, opacity: 0.6 }}>
            Role switcher still works via demo API keys. Pointer is external.
          </p>
        </aside>
      </div>
    </AppShell>
  );
}
