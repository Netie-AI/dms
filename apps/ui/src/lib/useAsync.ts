import { useCallback, useEffect, useState } from "react";

export type AsyncState<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
};

/**
 * One fetch, three states, and a reload button — the shape every reference page
 * on this product needs. Deliberately not TanStack Query: these pages read small
 * documents on mount, and a cache layer would let a stale ontology sit on screen
 * after a pack reload with no way for the user to tell.
 */
export function useAsync<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  deps: unknown[] = [],
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(fn, deps);

  useEffect(() => {
    const ctrl = new AbortController();
    // Drop the prior payload immediately so a Space switch cannot leave the
    // previous Space's rows on screen while the next fetch is in flight.
    setData(null);
    setLoading(true);
    setError(null);
    run(ctrl.signal)
      .then((value) => {
        if (!ctrl.signal.aborted) setData(value);
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [run, nonce]);

  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}
