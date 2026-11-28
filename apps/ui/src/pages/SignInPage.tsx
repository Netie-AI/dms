import { useState } from "react";

export type AccessFields = {
  name: string;
  email: string;
  company: string;
  note: string;
};

/**
 * Branded sign-in. Email and password, plus a request a steward can read.
 * No mail is sent.
 */
export function SignInPage({
  error,
  filed,
  onSubmit,
  onRequest,
}: {
  error: string | null;
  filed: boolean;
  onSubmit: (email: string, password: string) => void;
  onRequest: (fields: AccessFields) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [requestEmail, setRequestEmail] = useState("");
  const [company, setCompany] = useState("");
  const [note, setNote] = useState("");

  return (
    <main
      data-testid="sign-in-page"
      className="grid min-h-full place-items-center bg-[var(--color-paper)] px-6 py-16 text-[var(--color-ink)]"
    >
      <div className="w-full max-w-md">
        <p className="font-[family-name:var(--font-display)] text-3xl font-semibold tracking-tight">
          netie
        </p>
        <h1 className="mt-2 text-sm text-[var(--color-ink-muted)]">Sign in to your company data</h1>
        <form
          data-testid="sign-in-form"
          className="mt-8 border border-[var(--color-line)] bg-[var(--color-panel)] px-6 py-6"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit(email.trim(), password);
          }}
        >
          <label className="block text-sm" htmlFor="sign-in-email">
            Email
          </label>
          <input
            id="sign-in-email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="mt-1 h-10 w-full border border-[var(--color-line)] bg-[var(--color-surface)] px-3 text-sm"
            required
          />
          <label className="mt-4 block text-sm" htmlFor="sign-in-password">
            Password
          </label>
          <input
            id="sign-in-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="mt-1 h-10 w-full border border-[var(--color-line)] bg-[var(--color-surface)] px-3 text-sm"
            required
          />
          {error ? (
            <p className="mt-4 text-sm text-[var(--color-danger)]" data-testid="sign-in-error">
              {error}
            </p>
          ) : null}
          <button
            type="submit"
            className="mt-6 h-10 w-full bg-[var(--color-accent)] text-sm font-medium text-[var(--color-on-accent)]"
          >
            Sign in
          </button>
        </form>
        <form
          data-testid="request-access-form"
          className="mt-6 border border-[var(--color-line)] bg-[var(--color-panel)] px-6 py-6"
          onSubmit={(event) => {
            event.preventDefault();
            onRequest({
              name: name.trim(),
              email: requestEmail.trim(),
              company: company.trim(),
              note: note.trim(),
            });
          }}
        >
          <h2 className="text-sm font-semibold">Request access</h2>
          <p className="mt-2 text-sm leading-relaxed text-[var(--color-ink-muted)]">
            We file this for a steward. Nothing is emailed.
          </p>
          <label className="mt-4 block text-sm" htmlFor="request-name">
            Name
          </label>
          <input
            id="request-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="mt-1 h-10 w-full border border-[var(--color-line)] bg-[var(--color-surface)] px-3 text-sm"
            required
          />
          <label className="mt-4 block text-sm" htmlFor="request-email">
            Work email
          </label>
          <input
            id="request-email"
            type="email"
            value={requestEmail}
            onChange={(event) => setRequestEmail(event.target.value)}
            className="mt-1 h-10 w-full border border-[var(--color-line)] bg-[var(--color-surface)] px-3 text-sm"
            required
          />
          <label className="mt-4 block text-sm" htmlFor="request-company">
            Company
          </label>
          <input
            id="request-company"
            value={company}
            onChange={(event) => setCompany(event.target.value)}
            className="mt-1 h-10 w-full border border-[var(--color-line)] bg-[var(--color-surface)] px-3 text-sm"
          />
          <label className="mt-4 block text-sm" htmlFor="request-note">
            Note
          </label>
          <textarea
            id="request-note"
            value={note}
            onChange={(event) => setNote(event.target.value)}
            className="mt-1 h-20 w-full border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-sm"
          />
          {filed ? (
            <p className="mt-4 text-sm text-[var(--color-ink)]" data-testid="request-filed">
              Request filed. A steward will review it.
            </p>
          ) : null}
          <button
            type="submit"
            className="mt-4 h-10 border border-[var(--color-line)] px-4 text-sm font-medium"
          >
            Send request
          </button>
        </form>
      </div>
    </main>
  );
}
