import {
  AsyncBoundary,
  DependencyNotice,
  Page,
  PageHeader,
  Pill,
  Section,
  StatTile,
} from "@/components/PageShell";
import { useApp } from "@/context/AppContext";
import { fetchAdminOverview } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import type { AdminOverview } from "@/lib/types";

/**
 * Usability rule 11: name things by what people control. The heading over the
 * ACL table says "Who can see this"; the underlying column names stay
 * `acl_grants` / `resource_kind` because that is what the ledger records, and a
 * vocabulary that changes between the screen and the audit trail is how a
 * steward loses the thread.
 */
export function AdminPage() {
  const { role } = useApp();
  const { data, error, loading, reload } = useAsync<AdminOverview>((signal) =>
    fetchAdminOverview(signal),
  );

  return (
    <Page>
      <PageHeader
        phase="U5 · control plane"
        title="Admin"
        blurb="People, the role each one holds, the departments they belong to, who can see what, and the compute pools queries run in. Read-only: granting access is a write with an ACL blast radius, so it goes through the amend loop when it lands, not a button added because a page looked empty."
        actions={
          <button
            type="button"
            onClick={reload}
            className="h-9 border border-[var(--color-line)] px-3 text-sm hover:border-[var(--color-accent)]"
          >
            Refresh
          </button>
        }
      />

      {role !== "admin" && (
        <DependencyNotice
          title={`Viewing as ${role}`}
          hint="Switch the role selector in the top bar to admin to see this the way an administrator does."
        />
      )}
      {data?.configured === false && (
        <DependencyNotice title="No control plane configured" hint={data.hint} />
      )}

      <div className="mt-6 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatTile label="People" value={data?.users.length ?? "—"} />
        <StatTile label="Departments" value={data?.departments.length ?? "—"} />
        <StatTile label="Access grants" value={data?.grants.length ?? "—"} />
        <StatTile label="Compute pools" value={data?.pools.length ?? "—"} />
      </div>

      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        <Section title="People" count={data?.users.length}>
          {data?.users.length ? (
            <div className="overflow-x-auto border border-[var(--color-line)] bg-[var(--color-surface)]/60">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
                    <th className="px-3 py-2 font-semibold">Email</th>
                    <th className="px-3 py-2 font-semibold">Name</th>
                    <th className="px-3 py-2 font-semibold">Role</th>
                    <th className="px-3 py-2 font-semibold">Department</th>
                  </tr>
                </thead>
                <tbody>
                  {data.users.map((u) => (
                    <tr key={u.id} className="border-t border-[var(--color-line)]/60">
                      <td className="px-3 py-1.5 font-mono text-xs">{u.email}</td>
                      <td className="px-3 py-1.5 text-xs">{u.display_name ?? "—"}</td>
                      <td className="px-3 py-1.5 text-xs">
                        <Pill tone={u.role === "admin" ? "accent" : "neutral"}>{u.role}</Pill>
                      </td>
                      <td className="px-3 py-1.5 text-xs">{u.department ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="border border-[var(--color-line)] px-3 py-5 text-sm text-[var(--color-ink-muted)]">
              No memberships for tenant {data?.tenant_id ?? "—"}.
            </p>
          )}
        </Section>

        <Section
          title="Roles"
          count={data?.roles.length}
          description="Three, deliberately. A viewer reads, a steward proposes and confirms, an admin administers the tenant."
        >
          <ul className="divide-y divide-[var(--color-line)] border border-[var(--color-line)] bg-[var(--color-surface)]/60 text-sm">
            {(data?.roles.length
              ? data.roles
              : [
                  { name: "viewer", description: "Read-only within tenant" },
                  { name: "steward", description: "Propose and confirm amends" },
                  { name: "admin", description: "Tenant administration" },
                ]
            ).map((r) => (
              <li key={r.name} className="flex flex-wrap justify-between gap-2 px-3 py-2">
                <span className="font-mono text-xs">{r.name}</span>
                <span className="text-xs text-[var(--color-ink-muted)]">{r.description}</span>
              </li>
            ))}
          </ul>
        </Section>

        <Section
          title="Who can see this"
          count={data?.grants.length}
          description="Every grant is a principal, a resource, and one of read / write / admin. The manifest each question runs under is derived from exactly this."
        >
          {data?.grants.length ? (
            <div className="overflow-x-auto border border-[var(--color-line)] bg-[var(--color-surface)]/60">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
                    <th className="px-3 py-2 font-semibold">Principal</th>
                    <th className="px-3 py-2 font-semibold">Kind</th>
                    <th className="px-3 py-2 font-semibold">Resource</th>
                    <th className="px-3 py-2 font-semibold">Permission</th>
                  </tr>
                </thead>
                <tbody>
                  {data.grants.map((g) => (
                    <tr key={g.id} className="border-t border-[var(--color-line)]/60">
                      <td className="px-3 py-1.5 text-xs">{g.principal}</td>
                      <td className="px-3 py-1.5 text-xs text-[var(--color-ink-muted)]">
                        {g.principal_kind}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-[11px]">
                        {g.resource_kind}:{g.resource_id.slice(0, 8)}…
                      </td>
                      <td className="px-3 py-1.5 text-xs">
                        <Pill tone={g.permission === "read" ? "neutral" : "warn"}>
                          {g.permission}
                        </Pill>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="border border-[var(--color-line)] px-3 py-5 text-sm text-[var(--color-ink-muted)]">
              No explicit grants — access falls back to tenant default ACL.
            </p>
          )}
        </Section>

        <Section
          title="Compute pools"
          count={data?.pools.length}
          description="Where a query is allowed to run. Pools, never clusters — the customer controls a budget, not a Spark topology."
        >
          <ul className="divide-y divide-[var(--color-line)] border border-[var(--color-line)] bg-[var(--color-surface)]/60 text-sm">
            {(data?.pools ?? []).map((p) => (
              <li key={p.id} className="flex flex-wrap justify-between gap-2 px-3 py-2">
                <span className="font-mono text-xs">{p.name}</span>
                <span className="flex items-center gap-2 text-xs text-[var(--color-ink-muted)]">
                  <Pill>{p.kind}</Pill>
                  {Object.keys(p.config ?? {}).length > 0 && (
                    <span className="font-mono">{JSON.stringify(p.config)}</span>
                  )}
                </span>
              </li>
            ))}
            {!data?.pools.length && (
              <li className="px-3 py-4 text-sm text-[var(--color-ink-muted)]">
                No pools defined — queries run on the default read pool.
              </li>
            )}
          </ul>
        </Section>
      </AsyncBoundary>
    </Page>
  );
}
