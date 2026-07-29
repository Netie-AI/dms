"use client";

import { usePathname } from "next/navigation";
import RoleSwitcher from "./RoleSwitcher";

const PAGE_NAMES = {
  "/": "QUERY",
  "/warehouse": "WAREHOUSE",
  "/data": "DATA",
  "/audit": "AUDIT",
};

export default function TopBar({ indexedRows = "—", apiOnline = true }) {
  const pathname = usePathname();
  const pageName = PAGE_NAMES[pathname] || "QUERY";

  return (
    <header className="cx-topbar">
      <span className="cx-label">{pageName}</span>
      <div className="cx-topbar-status">
        {apiOnline && (
          <>
            <span className="cx-status-connected">
              <span className="cx-status-dot" />
              CONNECTED · dms_demo.duckdb
            </span>
            <span className="cx-status-sep">|</span>
            <span className="cx-status-meta">{indexedRows} ROWS INDEXED</span>
            <span className="cx-status-sep">|</span>
          </>
        )}
        <RoleSwitcher />
      </div>
    </header>
  );
}
