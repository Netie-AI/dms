function StubPage({
  title,
  blurb,
  phase,
}: {
  title: string;
  blurb: string;
  phase: string;
}) {
  return (
    <div className="h-full overflow-y-auto px-8 py-8">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
        {phase}
      </p>
      <h1 className="mt-2 font-[family-name:var(--font-display)] text-3xl font-semibold tracking-tight">
        {title}
      </h1>
      <p className="mt-3 max-w-xl text-[var(--color-ink-muted)]">{blurb}</p>
    </div>
  );
}

export function LibraryPage() {
  return (
    <StubPage
      title="Library"
      phase="U2"
      blurb="Every source with ownership tags and the Data Map — where data physically lives. Fixture list lands with Spaces API."
    />
  );
}

export function StudioPage() {
  return (
    <StubPage
      title="Studio"
      phase="U3"
      blurb="Drop zone, connectors, promote bronze→silver, quarantine review. Ingest returns a receipt — never a silent partial success."
    />
  );
}

export function AmendPage() {
  return (
    <StubPage
      title="Amend"
      phase="U4"
      blurb="Plain-language diff first; revise creates a new version and kills the old token. Confirm change — steward + Cortex F5."
    />
  );
}

export function AuditPage() {
  return (
    <StubPage
      title="Audit"
      phase="U4"
      blurb="Ledger view of answers and applies. DMS stores Cortex entry pointers only — verify chain via DMS API."
    />
  );
}

export function RunsPage() {
  return (
    <StubPage
      title="Runs"
      phase="U3"
      blurb="Ingest, promote, sync, and apply progress. Durable Postgres state machine — retry with the file and the fix named."
    />
  );
}

export function AdminPage() {
  return (
    <StubPage
      title="Admin"
      phase="U5"
      blurb="Users, roles, departments, who can see this (ACL), and compute pools — never Spark clusters."
    />
  );
}
