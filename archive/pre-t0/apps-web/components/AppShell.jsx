"use client";

import { useEffect, useState } from "react";
import Sidebar from "./Sidebar";
import TopBar from "./TopBar";
import ApiOfflineBanner from "./ApiOfflineBanner";
import ProgressBar from "./ProgressBar";
import { useApiHealth } from "../hooks/useApiHealth";
import { fetchTables } from "../lib/api";

function ShellInner({ children, loading = false }) {
  const { online, checked } = useApiHealth();
  const [indexedRows, setIndexedRows] = useState("—");

  useEffect(() => {
    if (!online) {
      setIndexedRows("—");
      return;
    }
    fetchTables()
      .then((d) => {
        const total = d.total_rows ?? d.tables?.reduce((s, t) => s + (t.row_count || 0), 0);
        setIndexedRows(total != null ? String(total) : "—");
      })
      .catch(() => setIndexedRows("—"));
  }, [online]);

  return (
    <div className="cx-shell">
      <Sidebar />
      <div className="cx-main-wrap">
        <ProgressBar active={loading} />
        {checked && !online && <ApiOfflineBanner show />}
        <TopBar indexedRows={indexedRows} apiOnline={online} />
        <main className="cx-content">{children}</main>
      </div>
    </div>
  );
}

export default function AppShell({ children, loading = false }) {
  return <ShellInner loading={loading}>{children}</ShellInner>;
}
