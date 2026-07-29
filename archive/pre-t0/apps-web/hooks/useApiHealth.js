"use client";

import { useEffect, useState } from "react";
import { checkHealth } from "../lib/api";

export function useApiHealth(intervalMs = 10000) {
  const [online, setOnline] = useState(true);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function probe() {
      try {
        const ok = await checkHealth();
        if (!cancelled) {
          setOnline(ok);
          setChecked(true);
        }
      } catch {
        if (!cancelled) {
          setOnline(false);
          setChecked(true);
        }
      }
    }

    probe();
    const t = setInterval(probe, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [intervalMs]);

  return { online, checked };
}
