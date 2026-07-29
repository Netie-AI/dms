"use client";

import { Fragment, useState } from "react";
import useSWR from "swr";
import AppShell from "../../components/AppShell";
import { fetchAudit } from "../../lib/api";
import { formatTimestamp } from "../../lib/profile";

const ACCESS_POLICY = [
  {
    role: "ANALYST",
    access: "Query + View",
    note: "Read-only. No data modification.",
  },
  {
    role: "DATA STEWARD",
    access: "Query + View + Edit",
    note: "Can approve changes and new entries.",
  },
  {
    role: "ADMIN",
    access: "Full Access",
    note: "jianhong@netie.ai",
  },
];

function truncate(s, n = 40) {
  if (!s) return "—";
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

function entryType(e) {
  if (!e.passed) return "BLOCKED";
  if (e.safe_sql || e.original_sql) return "SQL";
  return "RAG";
}

function entryQuery(e) {
  return e.original_sql || e.safe_sql || "—";
}

export default function AuditPage() {
  const { data, isLoading, mutate } = useSWR("audit", fetchAudit, {
    refreshInterval: 5000,
  });
  const [expanded, setExpanded] = useState(null);

  const entries = Array.isArray(data) ? data : data?.entries || [];

  return (
    <AppShell loading={isLoading}>
      <div className="cx-result-section" style={{ marginBottom: 24 }}>
        <div className="cx-label" style={{ marginBottom: 12 }}>
          DATA ACCESS POLICY
        </div>
        <table className="cx-audit-table cx-policy-table">
          <thead>
            <tr>
              <th>ROLE</th>
              <th>ACCESS</th>
              <th>NOTES</th>
            </tr>
          </thead>
          <tbody>
            {ACCESS_POLICY.map((row) => (
              <tr key={row.role}>
                <td>{row.role}</td>
                <td>{row.access}</td>
                <td>{row.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="cx-policy-trust">
          All queries are logged. Data does not leave this environment.
        </p>
      </div>

      {entries.length === 0 && !isLoading ? (
        <div className="cx-empty-state">
          <div className="cx-empty-title">NO QUERIES YET</div>
          <div className="cx-empty-desc">Run a query from the Query page to begin.</div>
        </div>
      ) : (
        <table className="cx-audit-table">
          <thead>
            <tr>
              <th>TIME</th>
              <th>QUERY</th>
              <th>TYPE</th>
              <th>STATUS</th>
              <th>ROWS</th>
              <th>MS</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e, i) => {
              const isOpen = expanded === i;
              const rowClass = e.passed ? "cx-active-row" : "cx-blocked-row";
              return (
                <Fragment key={i}>
                  <tr
                    className={rowClass}
                    onClick={() => {
                      setExpanded(isOpen ? null : i);
                      mutate();
                    }}
                  >
                    <td>{formatTimestamp(e.timestamp)}</td>
                    <td>{truncate(entryQuery(e))}</td>
                    <td>{entryType(e)}</td>
                    <td className={e.passed ? "status-pass" : "status-blocked"}>
                      {e.passed ? "PASS" : "BLOCKED"}
                    </td>
                    <td>{e.row_count ?? "—"}</td>
                    <td>—</td>
                  </tr>
                  {isOpen && (
                    <tr className="cx-audit-expand">
                      <td colSpan={6}>
                        <div className="cx-label" style={{ marginBottom: 8 }}>
                          FULL SQL
                        </div>
                        <pre>{e.original_sql || e.safe_sql || "—"}</pre>
                        {e.safe_sql && e.safe_sql !== e.original_sql && (
                          <>
                            <div className="cx-label" style={{ margin: "12px 0 8px" }}>
                              SAFE SQL
                            </div>
                            <pre>{e.safe_sql}</pre>
                          </>
                        )}
                        {!e.passed && e.violations?.length > 0 && (
                          <>
                            <div className="cx-label" style={{ margin: "12px 0 8px" }}>
                              VIOLATIONS
                            </div>
                            <pre style={{ color: "var(--cx-red)" }}>
                              {e.violations.join(", ")}
                            </pre>
                          </>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </AppShell>
  );
}
