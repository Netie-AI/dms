import { createContext } from "react";

/** What the shell should show after GET /v1/auth/session. Flag off is 404, which stays the app. */
export type SessionBody = {
  enabled?: boolean;
  authenticated?: boolean;
  email?: string | null;
  role?: string | null;
};

export function surfaceForSession(
  status: number,
  body: SessionBody | null,
): "app" | "sign-in" {
  if (status === 404 || status === 0) return "app";
  if (status === 401) return "sign-in";
  if (status >= 200 && status < 300 && body?.enabled && !body.authenticated) return "sign-in";
  return "app";
}

export type AppLoginValue = {
  enabled: boolean;
  session: { email: string; role: string } | null;
  signOut: () => void;
};

export const AppLoginContext = createContext<AppLoginValue>({
  enabled: false,
  session: null,
  signOut: () => {},
});

/** Drop a password if a response ever echoes it. */
export function withoutSecret(text: string, secret: string): string {
  if (!secret) return text;
  return text.split(secret).join("");
}
