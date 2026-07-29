"use client";

import { useEffect, useMemo, useState } from "react";
import AppShell from "../../components/AppShell";
import { useRole } from "../../context/RoleContext";
import {
  ApiOfflineError,
  fetchIngestLedger,
  fetchLakehouseStatus,
  fetchLakehouseTables,
  fetchPipelineEvents,
  fetchPipelines,
  ingestFileBase64,
  previewLakeTable,
  runPipeline,
  syncWarehouseFromSilver,
} from "../../lib/api";

const TABS = ["CATALOG", "INGEST", "PIPELINES"];

export default function StudioPage() {
  const { role } = useRole();
  const [tab, setTab] = useState("CATALOG");
  const [status, setStatus] = useState(null);
  const [tables, setTables] = useState(null);
  const [schema, setSchema] = useState("silver");
  const [table, setTable] = useState("");
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pipelines, setPipelines] = useState([]);
  const [events, setEvents] = useState([]);
  const [ledger, setLedger] = useState([]);
  const [ingestMsg, setIngestMsg] = useState("");

  const schemaTables = useMemo(() => {
    if (!tables) return [];
    return tables[schema] || [];
  }, [tables, schema]);

  async function refreshCatalog() {
    setError("");
    try {
      const [st, tb] = await Promise.all([fetchLakehouseStatus(), fetchLakehouseTables()]);
      setStatus(st);
      setTables(tb);
      const first = (tb[schema] && tb[schema][0]) || "";
      setTable((prev) => prev || first);
    } catch (e) {
      if (!(e instanceof ApiOfflineError)) setError(String(e.message || e));
      else setError("API offline — start Cortex on :8010 with PACK=dms");
    }
  }

  async function refreshPipelines() {
    try {
      const [p, ev] = await Promise.all([fetchPipelines(), fetchPipelineEvents(40)]);
      setPipelines(p.pipelines || []);
      setEvents(ev.events || []);
    } catch (e) {
      if (!(e instanceof ApiOfflineError)) setError(String(e.message || e));
    }
  }

  async function refreshLedger() {
    try {
      const led = await fetchIngestLedger();
      setLedger(led.entries || led.ledger || led || []);
    } catch {
      setLedger([]);
    }
  }

  useEffect(() => {
    refreshCatalog();
  }, []);

  useEffect(() => {
    if (tab === "PIPELINES") refreshPipelines();
    if (tab === "INGEST") refreshLedger();
  }, [tab]);

  useEffect(() => {
    if (!table || tab !== "CATALOG") return;
    setBusy(true);
    previewLakeTable(schema, table, 40)
      .then(setPreview)
      .catch((e) => setError(String(e.message || e)))
      .finally(() => setBusy(false));
  }, [schema, table, tab]);

  async function onUpload(file) {
    if (!file) return;
    if (!role.canApprove && role.id !== "STEWARD" && role.id !== "ADMIN") {
      setError("Ingest requires steward role");
      return;
    }
    setBusy(true);
    setIngestMsg("");
    setError("");
    try {
      const buf = await file.arrayBuffer();
      const bytes = new Uint8Array(buf);
      let binary = "";
      const chunk = 0x8000;
      for (let i = 0; i < bytes.length; i += chunk) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
      }
      const b64 = btoa(binary);
      const res = await ingestFileBase64(file.name, b64);
      setIngestMsg(JSON.stringify(res));
      await refreshLedger();
      await refreshCatalog();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onRunPipeline(id) {
    setBusy(true);
    setError("");
    try {
      await runPipeline(id);
      await refreshPipelines();
      await refreshCatalog();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onSyncWarehouse() {
    setBusy(true);
    setError("");
    try {
      const res = await syncWarehouseFromSilver();
      setIngestMsg(`warehouse sync: ${JSON.stringify(res)}`);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  const cols = preview?.rows?.length ? Object.keys(preview.rows[0]) : [];

  return (
    <AppShell>
      <div style={{ padding: "1.25rem 1.5rem", maxWidth: 1200 }}>
        <header style={{ marginBottom: "1rem" }}>
          <h1 style={{ margin: 0, fontSize: "1.4rem", letterSpacing: "0.04em" }}>DATA STUDIO</h1>
          <p style={{ margin: "0.35rem 0 0", opacity: 0.75, fontSize: "0.9rem" }}>
            Medallion lakehouse — bronze (swamp) → silver → gold. Mode:{" "}
            <strong>{status?.lakehouse_mode || "…"}</strong>
            {status?.home ? ` · ${status.home}` : ""}
          </p>
        </header>

        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem", flexWrap: "wrap" }}>
          {TABS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTab(t)}
              style={{
                padding: "0.4rem 0.85rem",
                border: tab === t ? "1px solid #6efbcb" : "1px solid rgba(255,255,255,0.15)",
                background: tab === t ? "rgba(110,251,203,0.12)" : "transparent",
                color: "inherit",
                cursor: "pointer",
                fontFamily: "inherit",
                letterSpacing: "0.06em",
                fontSize: "0.75rem",
              }}
            >
              {t}
            </button>
          ))}
          <button
            type="button"
            onClick={onSyncWarehouse}
            disabled={busy}
            style={{
              marginLeft: "auto",
              padding: "0.4rem 0.85rem",
              border: "1px solid rgba(255,255,255,0.2)",
              background: "transparent",
              color: "inherit",
              cursor: "pointer",
              fontSize: "0.75rem",
            }}
          >
            SYNC → Q2 WAREHOUSE
          </button>
        </div>

        {error ? (
          <p style={{ color: "#f87171", marginBottom: "0.75rem", fontSize: "0.85rem" }}>{error}</p>
        ) : null}
        {ingestMsg ? (
          <pre
            style={{
              fontSize: "0.7rem",
              opacity: 0.8,
              overflow: "auto",
              maxHeight: 120,
              marginBottom: "0.75rem",
            }}
          >
            {ingestMsg}
          </pre>
        ) : null}

        {tab === "CATALOG" && (
          <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: "1rem" }}>
            <div>
              <label style={{ fontSize: "0.7rem", opacity: 0.7 }}>SCHEMA</label>
              <select
                value={schema}
                onChange={(e) => {
                  setSchema(e.target.value);
                  setTable("");
                }}
                style={{ display: "block", width: "100%", marginBottom: "0.75rem" }}
              >
                {["bronze", "silver", "gold"].map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <div style={{ fontSize: "0.7rem", opacity: 0.7, marginBottom: "0.35rem" }}>TABLES</div>
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {schemaTables.map((name) => (
                  <li key={name}>
                    <button
                      type="button"
                      onClick={() => setTable(name)}
                      style={{
                        width: "100%",
                        textAlign: "left",
                        padding: "0.35rem 0.5rem",
                        border: "none",
                        background: table === name ? "rgba(110,251,203,0.15)" : "transparent",
                        color: "inherit",
                        cursor: "pointer",
                        fontFamily: "ui-monospace, monospace",
                        fontSize: "0.8rem",
                      }}
                    >
                      {name}
                    </button>
                  </li>
                ))}
                {!schemaTables.length ? (
                  <li style={{ opacity: 0.6, fontSize: "0.8rem" }}>Empty — run lakehouse_migrate</li>
                ) : null}
              </ul>
            </div>
            <div>
              <div style={{ fontSize: "0.8rem", marginBottom: "0.5rem" }}>
                Preview <code>{schema}.{table || "—"}</code>
                {busy ? " …" : ""}
              </div>
              {preview?.rows?.length ? (
                <div style={{ overflow: "auto", maxHeight: 480, border: "1px solid rgba(255,255,255,0.1)" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.75rem" }}>
                    <thead>
                      <tr>
                        {cols.map((c) => (
                          <th
                            key={c}
                            style={{
                              textAlign: "left",
                              padding: "0.35rem",
                              borderBottom: "1px solid rgba(255,255,255,0.15)",
                              position: "sticky",
                              top: 0,
                              background: "#12141a",
                            }}
                          >
                            {c}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {preview.rows.map((row, i) => (
                        <tr key={i}>
                          {cols.map((c) => (
                            <td
                              key={c}
                              style={{
                                padding: "0.3rem 0.35rem",
                                borderBottom: "1px solid rgba(255,255,255,0.06)",
                                fontFamily: "ui-monospace, monospace",
                              }}
                            >
                              {row[c] == null ? "" : String(row[c])}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p style={{ opacity: 0.6 }}>Select a table to preview.</p>
              )}
            </div>
          </div>
        )}

        {tab === "INGEST" && (
          <div>
            <p style={{ fontSize: "0.85rem", opacity: 0.8 }}>
              Drop messy Excel/CSV into bronze (exactly-once ledger). Steward role required.
            </p>
            <input
              type="file"
              accept=".xlsx,.xls,.csv,.tsv,.json,.jsonl"
              disabled={busy}
              onChange={(e) => onUpload(e.target.files?.[0])}
            />
            <h3 style={{ marginTop: "1.25rem", fontSize: "0.9rem" }}>Ingest ledger</h3>
            <pre style={{ fontSize: "0.7rem", overflow: "auto", maxHeight: 320 }}>
              {JSON.stringify(ledger, null, 2)}
            </pre>
          </div>
        )}

        {tab === "PIPELINES" && (
          <div>
            <p style={{ fontSize: "0.85rem", opacity: 0.8 }}>
              Promote bronze → silver. Then SYNC → Q2 WAREHOUSE so `/dms/query` sees silver.
            </p>
            <ul style={{ listStyle: "none", padding: 0 }}>
              {pipelines.map((id) => (
                <li key={id} style={{ marginBottom: "0.5rem" }}>
                  <code>{id}</code>{" "}
                  <button type="button" disabled={busy} onClick={() => onRunPipeline(id)}>
                    RUN
                  </button>
                </li>
              ))}
              {!pipelines.length ? <li style={{ opacity: 0.6 }}>No pipeline defs</li> : null}
            </ul>
            <h3 style={{ fontSize: "0.9rem" }}>Recent events</h3>
            <pre style={{ fontSize: "0.7rem", overflow: "auto", maxHeight: 280 }}>
              {JSON.stringify(events, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </AppShell>
  );
}
