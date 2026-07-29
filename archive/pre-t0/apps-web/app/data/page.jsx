"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import AppShell from "../../components/AppShell";
import {
  addEntry,
  analyseEntry,
  ApiOfflineError,
  fetchData,
} from "../../lib/api";
import { aggregateChangelog, buildMessyHighlights } from "../../lib/profile";
import { useRole } from "../../context/RoleContext";

function profileIssues(profile) {
  if (!profile?.detected_issues?.length) return [];
  return profile.detected_issues.map((issue) => ({
    type: issue.issue_type,
    field: issue.col,
    detail: `${issue.examples?.length || 0} variants detected`,
    rows: issue.row_count,
  }));
}

export default function DataPage() {
  const { role } = useRole();
  const [variant, setVariant] = useState("messy");
  const [rawEntry, setRawEntry] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [entryLoading, setEntryLoading] = useState(false);
  const [permError, setPermError] = useState("");
  const [approveLoading, setApproveLoading] = useState(false);

  const { data: messyData, isLoading: messyLoading, mutate: mutateMessy } = useSWR(
    "data-messy",
    () => fetchData("messy", 250)
  );
  const { data: cleanData, isLoading: cleanLoading, mutate: mutateClean } = useSWR(
    "data-clean",
    () => fetchData("clean", 250)
  );

  const rows =
    variant === "messy"
      ? messyData?.rows?.slice(0, 50)
      : cleanData?.rows?.slice(0, 50);
  const loading = variant === "messy" ? messyLoading : cleanLoading;

  const issues = useMemo(
    () => (variant === "messy" ? profileIssues(messyData?.profile) : []),
    [variant, messyData]
  );

  const highlights = useMemo(() => {
    const previewRows = messyData?.rows?.slice(0, 50) || [];
    return buildMessyHighlights(messyData?.profile, previewRows);
  }, [messyData]);

  const changelogAgg = useMemo(
    () => aggregateChangelog(cleanData?.changelog),
    [cleanData]
  );

  const cols = rows?.length ? Object.keys(rows[0]) : [];
  const rowCount =
    variant === "messy" ? messyData?.total_count ?? messyData?.count : cleanData?.total_count ?? cleanData?.count;
  const colCount = messyData?.profile?.schema?.length || cols.length;
  const issueCount = messyData?.profile?.detected_issues?.length ?? issues.length;

  async function runAnalysis() {
    if (!rawEntry.trim()) return;
    setEntryLoading(true);
    setPermError("");
    setAnalysis(null);
    try {
      const result = await analyseEntry(rawEntry.trim());
      setAnalysis(result);
    } catch (e) {
      if (!(e instanceof ApiOfflineError)) {
        setPermError(String(e.message || e));
      }
    } finally {
      setEntryLoading(false);
    }
  }

  async function approveEntry() {
    if (!analysis?.proposed) return;
    if (!role.canApprove) {
      setPermError("Insufficient permissions. Contact your data steward.");
      return;
    }
    setApproveLoading(true);
    setPermError("");
    try {
      await addEntry(analysis.proposed, role.id === "ADMIN" ? "admin" : "data_steward");
      setAnalysis(null);
      setRawEntry("");
      mutateClean();
      mutateMessy();
    } catch (e) {
      if (!(e instanceof ApiOfflineError)) {
        setPermError(String(e.message || e));
      }
    } finally {
      setApproveLoading(false);
    }
  }

  return (
    <AppShell loading={loading || entryLoading || approveLoading}>
      <div className="cx-toggle-bar">
        <button
          type="button"
          className={`cx-toggle-btn${variant === "messy" ? " active" : ""}`}
          onClick={() => setVariant("messy")}
        >
          MESSY DATA
        </button>
        <button
          type="button"
          className={`cx-toggle-btn${variant === "clean" ? " active" : ""}`}
          onClick={() => setVariant("clean")}
        >
          CLEAN DATA
        </button>
      </div>

      <div className="cx-stats-row">
        {variant === "messy" ? (
          <span>
            {rowCount ?? "—"} rows &nbsp;·&nbsp; {colCount} columns &nbsp;·&nbsp;{" "}
            {issueCount} issues detected
          </span>
        ) : (
          <span>
            {rowCount ?? "—"} rows &nbsp;·&nbsp; {colCount} columns &nbsp;·&nbsp; 0 issues
          </span>
        )}
      </div>

      {messyData?.error && (
        <p className="cx-empty-desc" style={{ color: "var(--cx-amber)" }}>
          {messyData.error}
        </p>
      )}

      {variant === "messy" && issues.length > 0 && (
        <div className="cx-issues-panel">
          {issues.map((issue, i) => (
            <div key={i} className="cx-issue-row">
              [{issue.type}]&nbsp;&nbsp;{issue.field}&nbsp;&nbsp;·&nbsp;&nbsp;
              {issue.detail}&nbsp;&nbsp;·&nbsp;&nbsp;{issue.rows} rows affected
            </div>
          ))}
        </div>
      )}

      {rows?.length ? (
        <table className="cx-data-table">
          <thead>
            <tr>
              {cols.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className={i % 2 === 1 ? "row-alt" : ""}>
                {cols.map((c) => {
                  const val = String(row[c] ?? "");
                  const isMessy =
                    variant === "messy" &&
                    typeof highlights?.has === "function" &&
                    highlights.has(`${i}|${c}|${val}`);
                  return (
                    <td key={c} className={isMessy ? "messy-val" : ""}>
                      {val}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        !loading && <p className="cx-empty-desc">No data available.</p>
      )}

      {variant === "clean" && changelogAgg.length > 0 && (
        <div className="cx-changelog-section">
          <div className="cx-label" style={{ marginBottom: 12 }}>
            CLEANING CHANGELOG
          </div>
          <table className="cx-data-table">
            <thead>
              <tr>
                <th>RULE</th>
                <th>FIELD</th>
                <th>BEFORE</th>
                <th>AFTER</th>
                <th>ROWS</th>
                {role.canApprove && <th>ACTION</th>}
              </tr>
            </thead>
            <tbody>
              {changelogAgg.slice(0, 30).map((e, i) => (
                <tr key={i} className={i % 2 === 1 ? "row-alt" : ""}>
                  <td>{e.rule}</td>
                  <td>{e.field}</td>
                  <td>{e.before}</td>
                  <td>{e.after}</td>
                  <td>{e.rows}</td>
                  {role.canApprove && (
                    <td>
                      <button type="button" className="cx-approve-btn">
                        APPROVE
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {variant === "clean" && (
        <div className="cx-entry-section">
          <div className="cx-label" style={{ marginBottom: 12 }}>
            NEW DATA ENTRY
          </div>
          <div className="cx-entry-step">
            <textarea
              className="cx-entry-textarea"
              rows={6}
              value={rawEntry}
              onChange={(e) => setRawEntry(e.target.value)}
              placeholder={`Paste new inventory rows here — any format accepted\ne.g. SKU001, 50kg, Warehouse A, reorder at 10kg, supplier Acme`}
            />
            <button type="button" className="cx-entry-btn" onClick={runAnalysis}>
              ANALYSE ENTRY →
            </button>
          </div>

          {analysis && (
            <div className="cx-entry-result">
              <div className="cx-label" style={{ marginBottom: 12 }}>
                PROPOSED ENTRY
              </div>
              <table className="cx-data-table cx-proposed-table">
                <tbody>
                  {Object.entries(analysis.proposed).map(([key, val]) => {
                    const orig = analysis.originals?.[key];
                    const showOrig = orig && String(orig).toLowerCase() !== String(val).toLowerCase();
                    return (
                      <tr key={key}>
                        <td className="cx-proposed-key">{key}</td>
                        <td>{String(val)}</td>
                        <td className="messy-val">
                          {showOrig ? `was "${orig}"` : ""}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              {analysis.issues?.length > 0 && (
                <>
                  <div className="cx-label" style={{ margin: "16px 0 8px" }}>
                    ISSUES DETECTED
                  </div>
                  {analysis.issues.map((issue, i) => (
                    <div key={i} className="cx-entry-issue">
                      [!] {issue}
                    </div>
                  ))}
                </>
              )}

              {analysis.hidden_issues?.length > 0 && (
                <>
                  <div className="cx-label" style={{ margin: "16px 0 8px" }}>
                    HIDDEN ISSUES
                  </div>
                  {analysis.hidden_issues.map((issue, i) => (
                    <div key={i} className="cx-entry-hidden">
                      [WARN] {issue}
                    </div>
                  ))}
                </>
              )}

              <div className="cx-entry-actions">
                <button
                  type="button"
                  className={`cx-entry-btn primary${!role.canApprove ? " disabled" : ""}`}
                  onClick={approveEntry}
                  disabled={!role.canApprove || approveLoading}
                >
                  APPROVE & ADD →
                </button>
                <button
                  type="button"
                  className="cx-entry-btn"
                  onClick={() => {
                    setAnalysis(null);
                    setPermError("");
                  }}
                >
                  REJECT
                </button>
              </div>
            </div>
          )}

          {permError && (
            <p className="cx-perm-error">{permError}</p>
          )}
        </div>
      )}
    </AppShell>
  );
}
