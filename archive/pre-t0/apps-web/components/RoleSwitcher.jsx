"use client";

import { useEffect, useRef, useState } from "react";
import { ROLES, useRole } from "../context/RoleContext";

export default function RoleSwitcher() {
  const { role, setRole } = useRole();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function onDoc(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const display =
    role.id === "ANALYST"
      ? "DEMO ANALYST"
      : role.id === "STEWARD"
        ? "DATA STEWARD"
        : "ADMIN";

  return (
    <div className="cx-role-switcher" ref={ref}>
      <button
        type="button"
        className="cx-role-trigger"
        onClick={() => setOpen((o) => !o)}
      >
        [{display} ▾]
      </button>
      {open && (
        <div className="cx-role-menu">
          {Object.values(ROLES).map((r) => (
            <button
              key={r.id}
              type="button"
              className={`cx-role-item${role.id === r.id ? " active" : ""}`}
              onClick={() => {
                setRole(r);
                setOpen(false);
              }}
            >
              <span>{r.label}</span>
              {role.id === r.id && <span className="cx-role-check">✓</span>}
            </button>
          ))}
          {role.isAdmin && (
            <div className="cx-role-admin-hint">
              contact Jian Hong for custom verticals
            </div>
          )}
        </div>
      )}
    </div>
  );
}
