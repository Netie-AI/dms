import { useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import {
  AppLoginContext,
  surfaceForSession,
  withoutSecret,
  type AppLoginValue,
  type SessionBody,
} from "@/lib/appLogin";
import { SignInPage, type AccessFields } from "@/pages/SignInPage";
import { StatusPage } from "@/pages/StatusPage";

async function readJson(res: Response): Promise<Record<string, unknown>> {
  try {
    return (await res.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

/**
 * Flag off (session route 404) renders the app immediately and stays there.
 * Flag on and signed out swaps in the sign-in page after the session call.
 */
export function LoginGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<"app" | "sign-in">("app");
  const [enabled, setEnabled] = useState(false);
  const [session, setSession] = useState<{ email: string; role: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filed, setFiled] = useState(false);
  const [denied, setDenied] = useState(false);
  const [csrf, setCsrf] = useState("");

  const loadCsrf = useCallback(async () => {
    const res = await fetch("/api/v1/auth/csrf", { credentials: "include" });
    const body = await readJson(res);
    const token = typeof body.token === "string" ? body.token : "";
    setCsrf(token);
    return token;
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch("/api/v1/auth/session", { credentials: "include" });
        const body = (await readJson(res)) as SessionBody;
        if (cancelled) return;
        if (surfaceForSession(res.status, body) === "sign-in") {
          setEnabled(true);
          setSession(null);
          setPhase("sign-in");
          return;
        }
        setEnabled(Boolean(body.enabled && body.authenticated));
        if (body.authenticated && body.email && body.role) {
          setSession({ email: body.email, role: body.role });
        }
        setPhase("app");
      } catch {
        if (!cancelled) setPhase("app");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (phase !== "sign-in") return;
    void loadCsrf().catch(() => setCsrf(""));
  }, [phase, loadCsrf]);

  const signOut = useCallback(async () => {
    try {
      const token = csrf || (await loadCsrf());
      await fetch("/api/v1/auth/logout", {
        method: "POST",
        credentials: "include",
        headers: { "x-csrf-token": token },
      });
    } catch {
      /* the cookie clear is best-effort; the page still returns to sign-in */
    }
    setSession(null);
    setEnabled(true);
    setPhase("sign-in");
  }, [csrf, loadCsrf]);

  if (phase === "sign-in") {
    if (denied) {
      return <StatusPage status={403} onBack={() => setDenied(false)} />;
    }
    return (
      <SignInPage
        error={error}
        filed={filed}
        onSubmit={(email, password) => {
          void (async () => {
            setError(null);
            const token = csrf || (await loadCsrf());
            const res = await fetch("/api/v1/auth/login", {
              method: "POST",
              credentials: "include",
              headers: {
                "content-type": "application/json",
                "x-csrf-token": token,
              },
              body: JSON.stringify({ email, password }),
            });
            const body = await readJson(res);
            if (res.status === 403) {
              setDenied(true);
              return;
            }
            if (!res.ok || body.role == null || body.email == null) {
              const message = typeof body.message === "string" ? body.message : "Sign in failed.";
              setError(withoutSecret(message, password));
              return;
            }
            setSession({ email: String(body.email), role: String(body.role) });
            setEnabled(true);
            setPhase("app");
          })();
        }}
        onRequest={(fields: AccessFields) => {
          void (async () => {
            const token = csrf || (await loadCsrf());
            const res = await fetch("/api/v1/auth/request-access", {
              method: "POST",
              credentials: "include",
              headers: {
                "content-type": "application/json",
                "x-csrf-token": token,
              },
              body: JSON.stringify(fields),
            });
            if (res.ok) setFiled(true);
          })();
        }}
      />
    );
  }

  return (
    <AppLoginContext.Provider value={{ enabled, session, signOut: () => void signOut() }}>
      {children}
    </AppLoginContext.Provider>
  );
}

export function useAppLogin(): AppLoginValue {
  return useContext(AppLoginContext);
}
