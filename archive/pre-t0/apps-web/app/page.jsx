"use client";

import Link from "next/link";
import { useRef, useState, useEffect } from "react";
import AppShell from "../components/AppShell";
import ResultChart from "../components/ResultChart";
import ResultGrid from "../components/ResultGrid";
import PromptCategories from "../components/PromptCategories";
import SqlBlock from "../components/SqlBlock";
import { ApiOfflineError, fetchTablePreview, fetchTables, postQuery } from "../lib/api";
import { formatTimestamp, shortAuditId } from "../lib/profile";
import { useRole } from "../context/RoleContext";

export default function QueryPage() {
  const { role } = useRole();
  const [exchanges, setExchanges] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(null);
  const [sqlOpen, setSqlOpen] = useState(true);
  const [tablesMeta, setTablesMeta] = useState(null);
  const [gridRows, setGridRows] = useState([]);
  const [gridSource, setGridSource] = useState(null);
  const threadRef = useRef(null);

  useEffect(() => {
    fetchTables()
      .then((d) => setTablesMeta(d))
      .catch(() => {});
  }, []);

  async function send(question) {
    const q = (question ?? input).trim();
    if (!q || loading) return;
    setInput("");
    setLoading(true);
    try {
      const res = await postQuery(q);
      const entry = { question: q, ...res, sentAt: new Date().toISOString() };
      setExchanges((prev) => [...prev, entry]);
      setActive(entry);
      setGridRows(res.rows || []);
      setGridSource({
        name: res.source_table || "inventory",
        row_count: res.row_count ?? res.rows?.length ?? 0,
        column_count: res.rows?.[0] ? Object.keys(res.rows[0]).length : 0,
      });
      setSqlOpen(!!res.sql_used);
      setTimeout(() => {
        threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: "smooth" });
      }, 50);
    } catch (e) {
      if (e instanceof ApiOfflineError) return;
      const entry = {
        question: q,
        answer: String(e.message || e),
        violations_blocked: [],
        audit_id: "error",
        sentAt: new Date().toISOString(),
        apiError: true,
      };
      setExchanges((prev) => [...prev, entry]);
      setActive(entry);
    } finally {
      setLoading(false);
    }
  }

  async function handleLoadRange(table, from, to, cols) {
    const res = await fetchTablePreview(table, from, to, cols);
    setGridRows(res.rows || []);
    setGridSource({
      name: table,
      row_count: res.total_count,
      column_count: res.columns?.length || 0,
    });
  }

  const blocked = active?.violations_blocked?.length > 0 && !active?.apiError;
  const guardStatus = blocked ? "BLOCKED" : "PASS";
  const alerts = active?.alerts_summary;
  const plan = active?.query_plan;

  const tableMetaForGrid = gridSource
    ? {
        ...gridSource,
        allTables: tablesMeta?.tables || [],
        columns: gridRows[0] ? Object.keys(gridRows[0]) : [],
      }
    : null;

  return (
    <AppShell loading={loading}>
      <div className="cx-query-layout">
        <div className="cx-query-left">
          <div className="cx-query-thread" ref={threadRef}>
            {exchanges.length === 0 && (
              <p className="cx-empty-desc" style={{ margin: 0 }}>
                Submit a query to inspect warehouse inventory data.
              </p>
            )}
            {exchanges.map((ex, i) => {
              const isBlocked = ex.violations_blocked?.length > 0 && !ex.apiError;
              return (
                <div key={i} className="cx-exchange">
                  <div className="cx-exchange-label cx-label">QUERY</div>
                  <div className="cx-exchange-text">{ex.question}</div>
                  <div className="cx-exchange-label cx-label" style={{ marginTop: 16 }}>
                    CORTEX OS
                  </div>
                  {isBlocked ? (
                    <div className="cx-blocked-box">
                      <div className="cx-blocked-type">
                        BLOCKED&nbsp;&nbsp;{ex.violations_blocked.join(" · ")}
                      </div>
                      <div className="cx-blocked-msg">This operation is not permitted.</div>
                      <div className="cx-blocked-audit">
                        Logged to audit → {shortAuditId(ex.audit_id)}
                      </div>
                    </div>
                  ) : (
                    <>
                      {(ex.badge || ex.layer || ex.query_source) && (
                        <div
                          className="cx-label"
                          style={{ marginBottom: 8, opacity: 0.75, fontSize: "0.7rem" }}
                        >
                          {[ex.badge || ex.layer, ex.query_source].filter(Boolean).join(" · ")}
                        </div>
                      )}
                      <div className="cx-exchange-text" style={{ whiteSpace: "pre-wrap" }}>
                        {ex.answer}
                      </div>
                      {ex.suggestions?.length ? (
                        <div style={{ marginTop: 8, fontSize: "0.8rem", opacity: 0.8 }}>
                          Try: {ex.suggestions.join(" · ")}
                        </div>
                      ) : null}
                    </>
                  )}
                </div>
              );
            })}
          </div>

          <div className="cx-query-input-wrap">
            <div className="cx-query-input-row">
              <input
                className="cx-query-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && send()}
                placeholder="Ask about your data..."
              />
              <button type="button" className="cx-query-send" onClick={() => send()}>
                ⏎ SEND
              </button>
            </div>
            <PromptCategories onSelect={(p) => setInput(p)} />
            {role.isAdmin && (
              <div className="cx-admin-contact">
                Need a custom vertical? →{" "}
                <a href="mailto:jianhong@netie.ai">jianhong@netie.ai</a>
              </div>
            )}
          </div>
        </div>

        <div className="cx-result-panel">
          {!active ? (
            <p className="cx-empty-desc">Results appear here after a query.</p>
          ) : (
            <>
              {alerts && (
                <div className="cx-alerts-card">
                  <Link href="/audit" className="cx-alert-critical">
                    {alerts.critical} ACTIVE CRITICAL ALERTS
                  </Link>
                  <Link href="/audit" className="cx-alert-high">
                    {alerts.high} ACTIVE HIGH ALERTS
                  </Link>
                </div>
              )}

              {(active.sql_used || blocked) && (
                <div className="cx-result-section">
                  <button
                    type="button"
                    className="cx-section-header"
                    onClick={() => setSqlOpen((o) => !o)}
                    style={{ width: "100%", background: "none", border: "none", padding: 0 }}
                  >
                    <span className="cx-label">SQL EXECUTED</span>
                    <span className="cx-section-toggle">{sqlOpen ? "−" : "+"}</span>
                  </button>
                  {sqlOpen && (
                    <>
                      {active.sql_used ? (
                        <SqlBlock sql={active.sql_used} />
                      ) : (
                        <div className="cx-sql-block" style={{ color: "var(--netie-muted)" }}>
                          No SQL executed — request blocked by guardrail.
                        </div>
                      )}
                      <div className="cx-sql-meta">
                        {active.row_count != null ? `${active.row_count} rows` : "0 rows"}
                        {" · "}
                        guardrail{" "}
                        <span className={blocked ? "blocked" : "pass"}>{guardStatus}</span>
                      </div>
                    </>
                  )}
                </div>
              )}

              {plan && (
                <div className="cx-result-section">
                  <div className="cx-label" style={{ marginBottom: 12 }}>
                    AI PLAN
                  </div>
                  <div className="cx-plan-card">
                    <div className="cx-plan-line">
                      <span>INTENT</span>
                      <strong>{plan.intent}</strong>
                    </div>
                    {(plan.layer || active?.layer) && (
                      <div className="cx-plan-line">
                        <span>LAYER</span>
                        <strong>{plan.layer || active.layer}</strong>
                      </div>
                    )}
                    {(plan.metric_id || active?.metric_id) && (
                      <div className="cx-plan-line">
                        <span>METRIC</span>
                        <strong>{plan.metric_id || active.metric_id}</strong>
                      </div>
                    )}
                    <div className="cx-plan-line">
                      <span>CONFIDENCE</span>
                      <strong>{Math.round((plan.confidence || 0) * 100)}%</strong>
                    </div>
                    {plan.skill_score != null && (
                      <div className="cx-plan-line">
                        <span>SKILL MATCH</span>
                        <strong>{Math.round(plan.skill_score * 100)}%</strong>
                      </div>
                    )}
                    {plan.limit && (
                      <div className="cx-plan-line">
                        <span>LIMIT</span>
                        <strong>{plan.limit}</strong>
                      </div>
                    )}
                    {plan.sort && (
                      <div className="cx-plan-line">
                        <span>SORT</span>
                        <strong>{plan.sort}</strong>
                      </div>
                    )}
                    <div className="cx-plan-runtime">
                      {plan.runtime?.name || "cortex-secured-dms-runtime"} ·{" "}
                      {plan.runtime?.tier || "T1"} · guardrailed SQL
                    </div>
                    {plan.candidates?.length > 0 && (
                      <div className="cx-plan-candidates">
                        {plan.candidates.slice(0, 3).map((candidate) => (
                          <div key={candidate.intent} className="cx-plan-candidate">
                            <span>{candidate.intent}</span>
                            <span>{Math.round((candidate.score || 0) * 100)}%</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {!blocked && active.chart_spec && (
                <div className="cx-result-section">
                  <div className="cx-label" style={{ marginBottom: 12 }}>
                    RESULT
                  </div>
                  <ResultChart spec={active.chart_spec} />
                  {active.chart_spec?.type === "bignum" && active.chart_spec?.data?.length > 0 && (
                    <ResultChart
                      spec={{
                        type: "bar",
                        title: "",
                        data: active.chart_spec.data,
                      }}
                    />
                  )}
                </div>
              )}

              {!blocked && gridRows.length > 0 && (
                <div className="cx-result-section">
                  <ResultGrid
                    rows={gridRows}
                    sourceTable={gridSource?.name}
                    tableMeta={tableMetaForGrid}
                    onLoadRange={handleLoadRange}
                    queryPlan={plan}
                  />
                </div>
              )}

              <div className="cx-result-section">
                <div className="cx-label" style={{ marginBottom: 12 }}>
                  AUDIT ENTRY
                </div>
                <div className="cx-audit-line">
                  <span className="cx-audit-key">ID</span>
                  <span>{shortAuditId(active.audit_id)}</span>
                </div>
                <div className="cx-audit-line">
                  <span className="cx-audit-key">TIME</span>
                  <span>{formatTimestamp(active.audit?.timestamp || active.sentAt)}</span>
                </div>
                <div className="cx-audit-line">
                  <span className="cx-audit-key">STATUS</span>
                  <span className={`cx-audit-val ${blocked ? "blocked" : "pass"}`}>
                    {guardStatus}
                  </span>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </AppShell>
  );
}
