"use client";

import { useState } from "react";
import useSWR from "swr";
import AppShell from "../../components/AppShell";
import {
  ApiOfflineError,
  deactivateSkill,
  fetchSkills,
  fetchSkillCaptureConfig,
  setSkillCaptureConfig,
  findSkills,
} from "../../lib/api";
import { useRole } from "../../context/RoleContext";

export default function SkillsPage() {
  const { role } = useRole();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [goal, setGoal] = useState("playwright e2e testing");
  const [findResult, setFindResult] = useState(null);
  const [evolve, setEvolve] = useState(false);

  const { data: config, mutate: mutateConfig } = useSWR("skill-config", fetchSkillCaptureConfig);
  const { data: skills, isLoading, mutate: mutateSkills } = useSWR("skills", () => fetchSkills(false));

  const canManage = role.canApprove;

  async function toggleCapture() {
    if (!canManage) return;
    setBusy(true);
    setError("");
    try {
      await setSkillCaptureConfig(!config?.capture_enabled);
      mutateConfig();
    } catch (e) {
      if (!(e instanceof ApiOfflineError)) setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onDeactivate(skillId) {
    if (!canManage) return;
    setBusy(true);
    setError("");
    try {
      await deactivateSkill(skillId);
      mutateSkills();
    } catch (e) {
      if (!(e instanceof ApiOfflineError)) setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onFindSkills(e) {
    e?.preventDefault?.();
    const q = (goal || "").trim();
    if (!q) return;
    setBusy(true);
    setError("");
    try {
      const res = await findSkills(q, { topK: 8, evolve });
      setFindResult(res);
    } catch (err) {
      if (!(err instanceof ApiOfflineError)) setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }

  const captureOn = Boolean(config?.capture_enabled);
  const best = findResult?.best;

  return (
    <AppShell loading={isLoading || busy}>
      <div className="cx-label" style={{ marginBottom: 8 }}>
        FIND SKILLS
      </div>
      <p className="cx-empty-desc" style={{ marginBottom: 12 }}>
        Are there any good skills for [{goal || "GOAL"}]? Skills first from GitHub awesome-lists + local
        cards, then MCP/subagents. Evolve with SkillOpt when enabled.
      </p>

      <form onSubmit={onFindSkills} style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
        <input
          data-testid="find-skills-goal"
          className="cx-input"
          style={{ flex: "1 1 280px", minWidth: 200 }}
          value={goal}
          onChange={(ev) => setGoal(ev.target.value)}
          placeholder="e.g. playwright testing, PDF extract, GitHub MCP"
          aria-label="Skill discovery goal"
        />
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
          <input
            data-testid="find-skills-evolve"
            type="checkbox"
            checked={evolve}
            onChange={(ev) => setEvolve(ev.target.checked)}
          />
          SkillOpt seed
        </label>
        <button data-testid="find-skills-submit" type="submit" className="cx-entry-btn">
          FIND SKILLS
        </button>
      </form>

      {best && (
        <div data-testid="find-skills-best" style={{ marginBottom: 20 }}>
          <div className="cx-label" style={{ marginBottom: 6 }}>
            BEST MATCH
          </div>
          <p style={{ margin: "0 0 4px" }}>
            <strong>{best.name}</strong>{" "}
            <span style={{ color: "var(--cx-muted)", fontSize: 12 }}>
              {best.kind} · score {best.score}
            </span>
          </p>
          <p className="cx-empty-desc" style={{ marginBottom: 6 }}>
            {best.description}
          </p>
          <p className="cx-empty-desc" style={{ marginBottom: 6 }}>
            {best.install_hint}
          </p>
          {best.url ? (
            <a href={best.url} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
              {best.url}
            </a>
          ) : null}
        </div>
      )}

      {findResult?.matches?.length > 0 && (
        <table className="cx-data-table" data-testid="find-skills-results" style={{ marginBottom: 28 }}>
          <thead>
            <tr>
              <th>NAME</th>
              <th>KIND</th>
              <th>SCORE</th>
              <th>SOURCE</th>
              <th>INSTALL</th>
            </tr>
          </thead>
          <tbody>
            {findResult.matches.map((m, i) => (
              <tr key={m.id} className={i % 2 === 1 ? "row-alt" : ""}>
                <td>{m.name}</td>
                <td>{m.kind}</td>
                <td>{m.score}</td>
                <td>{m.source}</td>
                <td style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis" }}>
                  {m.install_hint}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="cx-label" style={{ marginBottom: 8 }}>
        CAPTURED SKILLS (F6)
      </div>
      <p className="cx-empty-desc" style={{ marginBottom: 16 }}>
        Internal-only behaviour cards from successful gated task chains. Opt-in recording — never leaves the box.
      </p>

      <div className="cx-stats-row" style={{ marginBottom: 20 }}>
        <span>
          Recording:{" "}
          <strong style={{ color: captureOn ? "var(--cx-green)" : "var(--cx-muted)" }}>
            {captureOn ? "ON" : "OFF"}
          </strong>
        </span>
        {canManage && (
          <button type="button" className="cx-entry-btn" onClick={toggleCapture} style={{ marginLeft: 16 }}>
            {captureOn ? "TURN OFF" : "ENABLE CAPTURE"}
          </button>
        )}
      </div>

      {error && <p className="cx-perm-error">{error}</p>}

      {skills?.length ? (
        <table className="cx-data-table">
          <thead>
            <tr>
              <th>INTENT</th>
              <th>TASK</th>
              <th>TRIGGER</th>
              <th>SUPPORT</th>
              <th>SUCCESS</th>
              <th>ACTIVE</th>
              {canManage && <th>ACTION</th>}
            </tr>
          </thead>
          <tbody>
            {skills.map((sk, i) => (
              <tr key={sk.id} className={i % 2 === 1 ? "row-alt" : ""}>
                <td>{sk.intent}</td>
                <td>{sk.task_id}</td>
                <td style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis" }}>
                  {sk.trigger_pattern}
                </td>
                <td>{sk.support_count}</td>
                <td>{sk.success_count}</td>
                <td>{sk.active ? "yes" : "no"}</td>
                {canManage && (
                  <td>
                    {sk.active && (
                      <button type="button" className="cx-approve-btn" onClick={() => onDeactivate(sk.id)}>
                        DEACTIVATE
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        !isLoading && (
          <p className="cx-empty-desc">
            No skills captured yet. Enable recording and complete a gated task with outcome success.
          </p>
        )
      )}
    </AppShell>
  );
}
