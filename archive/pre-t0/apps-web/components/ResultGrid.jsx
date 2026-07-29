"use client";

import { useMemo, useState } from "react";
import { proposeEdits } from "../lib/api";
import { useRole } from "../context/RoleContext";

function downloadCsv(rows, columns, sourceTable) {
  if (!rows?.length) return;
  const cols = columns.length ? columns : Object.keys(rows[0]);
  const lines = [cols.join(",")];
  for (const row of rows) {
    lines.push(cols.map((c) => JSON.stringify(row[c] ?? "")).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  a.download = `cortex_${sourceTable || "query"}_${stamp}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function ResultGrid({
  rows = [],
  sourceTable = "inventory",
  tableMeta = null,
  onLoadRange,
  queryPlan = null,
}) {
  const { role } = useRole();
  const [sortCol, setSortCol] = useState(null);
  const [sortDir, setSortDir] = useState("asc");
  const [editMode, setEditMode] = useState(false);
  const [pivot, setPivot] = useState(false);
  const [edited, setEdited] = useState({});
  const [exploreOpen, setExploreOpen] = useState(false);
  const [rangeTable, setRangeTable] = useState(null);
  const [fromRow, setFromRow] = useState("0");
  const [toRow, setToRow] = useState("100");
  const [selectedCols, setSelectedCols] = useState([]);
  const [proposeMsg, setProposeMsg] = useState("");

  const columns = useMemo(() => {
    if (!rows?.length) return [];
    return Object.keys(rows[0]);
  }, [rows]);

  const displayRows = useMemo(() => {
    let data = rows.map((r, i) => ({ ...r, __row_id: i }));
    if (sortCol) {
      data = [...data].sort((a, b) => {
        const av = a[sortCol];
        const bv = b[sortCol];
        const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true });
        return sortDir === "asc" ? cmp : -cmp;
      });
    }
    return data;
  }, [rows, sortCol, sortDir]);

  const gridCols = pivot
    ? displayRows.map((_, i) => `ROW_${i}`)
    : columns;
  const gridRows = pivot
    ? columns.map((col) => {
        const row = { __label: col };
        displayRows.forEach((r, i) => {
          row[`ROW_${i}`] = r[col];
        });
        return row;
      })
    : displayRows;

  function toggleSort(col) {
    if (sortCol === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(col);
      setSortDir("asc");
    }
  }

  function cellValue(row, col) {
    const key = `${row.__row_id}-${col}`;
    if (edited[key] !== undefined) return edited[key];
    return row[col];
  }

  function onCellEdit(row, col, val) {
    const key = `${row.__row_id}-${col}`;
    setEdited((prev) => ({ ...prev, [key]: val }));
  }

  async function handlePropose() {
    if (!role.canApprove) return;
    const changes = Object.entries(edited).map(([key, new_val]) => {
      const [rowId, field] = key.split("-");
      const row = displayRows[Number(rowId)];
      return {
        row_id: Number(rowId),
        field,
        old_val: row?.[field],
        new_val,
        table: sourceTable,
      };
    });
    if (!changes.length) return;
    try {
      const res = await proposeEdits(changes, role.id);
      setProposeMsg(`Proposed ${res.count} change(s) → ${res.proposed_id.slice(0, 8)}`);
      setEdited({});
    } catch (e) {
      setProposeMsg(String(e.message || e));
    }
  }

  async function loadRange() {
    if (!onLoadRange || !rangeTable) return;
    const cols =
      selectedCols.length > 0 ? selectedCols.join(",") : (tableMeta?.columns || []).join(",");
    await onLoadRange(rangeTable, Number(fromRow), Number(toRow), cols);
    setExploreOpen(false);
  }

  if (!rows?.length && !tableMeta) return null;

  const meta = tableMeta || { name: sourceTable, row_count: rows.length, column_count: columns.length };

  return (
    <div className="cx-result-grid">
      <div className="cx-grid-source-bar">
        <span>
          SOURCE <strong>{meta.name}</strong> · {meta.row_count?.toLocaleString()} rows ·{" "}
          {meta.column_count || columns.length} columns ·{" "}
          {queryPlan?.sort ? `sorted by ${queryPlan.sort} · ` : ""}
        </span>
        <button type="button" className="cx-grid-link-btn" onClick={() => setExploreOpen(true)}>
          EXPLORE →
        </button>
      </div>

      <div className="cx-grid-toolbar">
        <span className="cx-label">RESULT GRID</span>
        <div className="cx-grid-actions">
          <button
            type="button"
            className={`cx-grid-btn ${editMode ? "active" : ""}`}
            onClick={() => setEditMode((v) => !v)}
          >
            EDIT ROWS
          </button>
          <button
            type="button"
            className={`cx-grid-btn ${pivot ? "active" : ""}`}
            onClick={() => setPivot((v) => !v)}
          >
            PIVOT
          </button>
          <button type="button" className="cx-grid-btn" onClick={() => downloadCsv(displayRows, columns, sourceTable)}>
            DOWNLOAD SORTED CSV
          </button>
        </div>
      </div>

      <div className="cx-grid-scroll">
        <table className="cx-grid-table">
          <thead>
            <tr>
              {(pivot ? ["FIELD", ...gridCols] : columns).map((col) => (
                <th key={col} onClick={() => !pivot && toggleSort(col)}>
                  {col}
                  {!pivot && sortCol === col && (sortDir === "asc" ? " ▲" : " ▼")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {gridRows.map((row, ri) => (
              <tr key={ri}>
                {(pivot ? ["__label", ...gridCols] : columns).map((col) => {
                  const val = pivot ? row[col] : cellValue(row, col);
                  const changed =
                    !pivot && edited[`${row.__row_id}-${col}`] !== undefined;
                  return (
                    <td key={col} className={changed ? "cx-grid-changed" : ""}>
                      {editMode && !pivot ? (
                        <input
                          className="cx-grid-cell-input"
                          value={val ?? ""}
                          onChange={(e) => onCellEdit(row, col, e.target.value)}
                        />
                      ) : (
                        val ?? "—"
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editMode && role.canApprove && (
        <button type="button" className="cx-grid-propose" onClick={handlePropose}>
          PROPOSE CHANGES →
        </button>
      )}
      {editMode && !role.canApprove && (
        <div className="cx-grid-hint">DATA STEWARD+ required to propose changes.</div>
      )}
      {proposeMsg && <div className="cx-grid-hint">{proposeMsg}</div>}

      {exploreOpen && (
        <div className="cx-explore-panel">
          <div className="cx-explore-header">
            <span className="cx-label">AVAILABLE TABLES</span>
            <button type="button" className="cx-grid-link-btn" onClick={() => setExploreOpen(false)}>
              CLOSE
            </button>
          </div>
          <div className="cx-explore-list">
            {(tableMeta?.allTables || []).map((t) => (
              <div key={t.name} className="cx-explore-row">
                <span className="cx-explore-name">{t.name}</span>
                <span className="cx-explore-meta">
                  {t.row_count?.toLocaleString()} rows · {t.column_count} cols
                </span>
                <button
                  type="button"
                  className="cx-grid-link-btn"
                  onClick={() => {
                    setRangeTable(t.name);
                    setSelectedCols([]);
                  }}
                >
                  SELECT RANGE
                </button>
              </div>
            ))}
          </div>
          {rangeTable && (
            <div className="cx-explore-range">
              <div className="cx-label">RANGE — {rangeTable}</div>
              <label>
                FROM ROW
                <input value={fromRow} onChange={(e) => setFromRow(e.target.value)} />
              </label>
              <label>
                TO ROW
                <input value={toRow} onChange={(e) => setToRow(e.target.value)} />
              </label>
              <button type="button" className="cx-grid-propose" onClick={loadRange}>
                LOAD THIS RANGE →
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
