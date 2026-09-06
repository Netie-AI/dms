import { useEffect, useRef, useState, type FormEvent } from "react";
import { useApp } from "@/context/AppContext";
import {
  postSqlSource,
  type SqlSourceKind,
  type SqlSourceReceipt,
} from "@/lib/api";
import {
  cardinalitySentence,
  defaultPortHint,
  extractHeadline,
  fieldsFromControls,
  linksHeadline,
  truncatedLabel,
  violationSentence,
} from "@/lib/sqlSourceReceipt";

const FIELD =
  "mt-1 w-full border border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-2 text-sm text-[var(--color-ink)]";

export function SqlSourcePanel({
  spaceId,
  onExtracted,
}: {
  spaceId: string | null;
  onExtracted?: () => void | Promise<void>;
}) {
  const { setActivity } = useApp();
  const passwordRef = useRef<HTMLInputElement | null>(null);
  const [kind, setKind] = useState<SqlSourceKind>("sqlserver");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [database, setDatabase] = useState("");
  const [user, setUser] = useState("");
  const [tables, setTables] = useState("");
  const [maxRows, setMaxRows] = useState("");
  const [encrypt, setEncrypt] = useState(true);
  const [trustCert, setTrustCert] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<SqlSourceReceipt | null>(null);

  useEffect(() => {
    setErr(null);
    setReceipt(null);
    if (passwordRef.current) passwordRef.current.value = "";
  }, [spaceId]);

  const clearPassword = () => {
    if (passwordRef.current) passwordRef.current.value = "";
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const password = passwordRef.current?.value ?? "";
    setBusy(true);
    setErr(null);
    setActivity({ label: "Extracting SQL source…", progress: 30 });
    try {
      const body = fieldsFromControls({
        kind,
        host,
        database,
        user,
        password,
        port,
        tables,
        maxRows,
        spaceId,
        encrypt,
        trustServerCertificate: trustCert,
      });
      const landed = await postSqlSource(body);
      setReceipt(landed);
      setActivity({ label: "SQL extract receipt ready", progress: 100 });
      try {
        await onExtracted?.();
      } catch {
        /* extract already landed; tree refresh is best-effort */
      }
    } catch (ex) {
      const message = ex instanceof Error ? ex.message : "extract failed";
      setErr(message);
      setActivity(null);
    } finally {
      clearPassword();
      setBusy(false);
      window.setTimeout(() => setActivity(null), 600);
    }
  };

  return (
    <div
      data-testid="sql-source-panel"
      className="mt-6 border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-4 py-4"
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-[var(--color-ink-muted)]">
        SQL Server / MySQL
      </p>
      <p className="mt-2 max-w-2xl text-sm text-[var(--color-ink-muted)]">
        Point this Space at a database. Rows land in bronze with the source named.
        The password is sent once with the request and is not stored. Extract-only
        -- DMS does not query the source live.
      </p>
      <form data-testid="sql-source-form" className="mt-4" onSubmit={(e) => void onSubmit(e)}>
        <fieldset className="flex flex-wrap gap-4 text-sm text-[var(--color-ink)]">
          <legend className="sr-only">Engine</legend>
          <label className="inline-flex items-center gap-2">
            <input
              type="radio"
              name="sql-kind"
              value="sqlserver"
              checked={kind === "sqlserver"}
              onChange={() => setKind("sqlserver")}
              data-testid="sql-source-kind-sqlserver"
            />
            SQL Server
          </label>
          <label className="inline-flex items-center gap-2">
            <input
              type="radio"
              name="sql-kind"
              value="mysql"
              checked={kind === "mysql"}
              onChange={() => setKind("mysql")}
              data-testid="sql-source-kind-mysql"
            />
            MySQL
          </label>
        </fieldset>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <label className="block text-xs text-[var(--color-ink-muted)]">
            Host
            <input
              required
              value={host}
              onChange={(e) => setHost(e.target.value)}
              className={FIELD}
              autoComplete="off"
              data-testid="sql-source-host"
            />
          </label>
          <label className="block text-xs text-[var(--color-ink-muted)]">
            Port
            <input
              value={port}
              onChange={(e) => setPort(e.target.value)}
              className={FIELD}
              inputMode="numeric"
              placeholder={defaultPortHint(kind)}
              data-testid="sql-source-port"
            />
          </label>
          <label className="block text-xs text-[var(--color-ink-muted)]">
            Database
            <input
              required
              value={database}
              onChange={(e) => setDatabase(e.target.value)}
              className={FIELD}
              autoComplete="off"
              data-testid="sql-source-database"
            />
          </label>
          <label className="block text-xs text-[var(--color-ink-muted)]">
            User
            <input
              required
              value={user}
              onChange={(e) => setUser(e.target.value)}
              className={FIELD}
              autoComplete="off"
              data-testid="sql-source-user"
            />
          </label>
          <label className="block text-xs text-[var(--color-ink-muted)]">
            Password
            <input
              ref={passwordRef}
              type="password"
              className={FIELD}
              autoComplete="off"
              data-testid="sql-source-password"
            />
          </label>
          <label className="block text-xs text-[var(--color-ink-muted)]">
            Tables (optional)
            <input
              value={tables}
              onChange={(e) => setTables(e.target.value)}
              className={FIELD}
              placeholder="orders, customers — blank = all visible"
              data-testid="sql-source-tables"
            />
          </label>
          <label className="block text-xs text-[var(--color-ink-muted)]">
            Max rows per table
            <input
              value={maxRows}
              onChange={(e) => setMaxRows(e.target.value)}
              className={FIELD}
              inputMode="numeric"
              placeholder="500000"
              data-testid="sql-source-max-rows"
            />
          </label>
        </div>
        <div className="mt-3 flex flex-wrap gap-4 text-xs text-[var(--color-ink)]">
          <label className="inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={encrypt}
              onChange={(e) => setEncrypt(e.target.checked)}
              data-testid="sql-source-encrypt"
            />
            Encrypt
          </label>
          <label className="inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={trustCert}
              onChange={(e) => setTrustCert(e.target.checked)}
              data-testid="sql-source-trust-cert"
            />
            Trust server certificate
          </label>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={busy || !host.trim() || !database.trim() || !user.trim()}
            data-testid="sql-source-submit"
            className="border border-[var(--color-accent)] bg-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "Extracting…" : "Extract into bronze"}
          </button>
        </div>
      </form>
      {err && (
        <p data-testid="sql-source-error" className="mt-3 text-sm text-[var(--color-danger)]">
          {err}
        </p>
      )}
      {receipt && <SqlSourceReceiptView receipt={receipt} />}
    </div>
  );
}

export function SqlSourceReceiptView({ receipt }: { receipt: SqlSourceReceipt }) {
  const failed = receipt.links.links.filter((l) => l.cardinality !== "many_to_one");
  return (
    <div data-testid="sql-source-receipt" className="mt-4 text-sm text-[var(--color-ink)]">
      <p data-testid="sql-source-receipt-headline" className="font-medium">
        {extractHeadline(receipt)}
      </p>
      {receipt.tables[0]?.extracted_at ? (
        <p data-testid="sql-source-extracted-at" className="mt-1 text-xs text-[var(--color-ink-muted)]">
          extracted_at {receipt.tables[0].extracted_at}
        </p>
      ) : null}
      <ul data-testid="sql-source-tables-landed" className="mt-3 space-y-1 text-xs">
        {receipt.tables.map((t) => (
          <li key={t.bronze_table} data-testid="sql-source-table-row">
            <span className="font-mono">{t.bronze_table}</span>
            {" · "}
            {truncatedLabel(t)}
            {t.truncated ? (
              <span data-testid="sql-source-truncated" className="ml-1 text-[var(--color-danger)]">
                truncated
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      {receipt.skipped.length > 0 && (
        <p data-testid="sql-source-skipped" className="mt-2 text-xs text-[var(--color-warn)]">
          Skipped (login cannot see): {receipt.skipped.join(", ")}
        </p>
      )}
      <p data-testid="sql-source-links-headline" className="mt-3 text-xs text-[var(--color-ink-muted)]">
        {linksHeadline(receipt.links)}
      </p>
      {receipt.links.links.length > 0 && (
        <ul data-testid="sql-source-links" className="mt-2 space-y-2 text-xs">
          {receipt.links.links.map((link) => (
            <li
              key={link.name}
              data-testid="sql-source-link-row"
              data-cardinality={link.cardinality}
              className={link.cardinality === "many_to_one" ? "" : "text-[var(--color-danger)]"}
            >
              {cardinalitySentence(link)}
            </li>
          ))}
        </ul>
      )}
      {receipt.links.violations.length > 0 && (
        <ul data-testid="sql-source-violations" className="mt-2 space-y-1 text-xs text-[var(--color-danger)]">
          {receipt.links.violations.map((v, i) => (
            <li key={`${v.check}-${v.subject}-${i}`}>{violationSentence(v)}</li>
          ))}
        </ul>
      )}
      {failed.length > 0 && (
        <p data-testid="sql-source-failed-links" className="mt-2 text-xs text-[var(--color-danger)]">
          Failed link{failed.length === 1 ? "" : "s"}: {failed.map((l) => l.name).join(", ")}. Extract
          still succeeded.
        </p>
      )}
    </div>
  );
}
