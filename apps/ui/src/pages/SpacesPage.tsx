import { useState } from "react";
import { useNavigate } from "react-router-dom";
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
import { createSpace, fetchLibrarySources } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import type { LibrarySource } from "@/lib/types";

/**
 * Surface 4 of the ten. A Space is the answer to "what can this question see" —
 * until now it existed only as a dropdown in the top bar, which is where scope
 * goes to be forgotten. Selecting here sets the same active scope the composer
 * chip reports, so there is one notion of scope in the product, not two.
 */
export function SpacesPage() {
  const {
    spaces,
    activeSpaceId,
    setActiveSpaceId,
    spacesFromApi,
    databaseConfigured,
    spacesPersisted,
    spacesStorageHint,
  } = useApp();
  const navigate = useNavigate();
  const sources = useAsync<LibrarySource[]>((signal) => fetchLibrarySources(signal));
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const byId = new Map<string, LibrarySource[]>();
  for (const src of sources.data ?? []) {
    if (!src.space_id) continue;
    byId.set(src.space_id, [...(byId.get(src.space_id) ?? []), src]);
  }
  const companyScoped = (sources.data ?? []).filter((s) => !s.space_id);

  async function submit() {
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    setFailure(null);
    setNotice(null);
    try {
      const body = await createSpace(trimmed);
      setName("");
      setNotice(
        body.persisted
          ? `Space created — ${body.space.name}. Reload to see it in the switcher.`
          : `Space created in memory — ${body.space.name}. ${body.hint ?? ""}`,
      );
    } catch (err) {
      setFailure(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page>
      <PageHeader
        phase="T3 · scope"
        title="Spaces"
        blurb="A Space is a sandbox of sources. Asking inside one is the difference between “the AI can see everything” and a scope a steward can point at. Attach sources in Studio; the scope chip above the composer always reports the Space in force."
        actions={
          <button
            type="button"
            onClick={() => navigate("/studio")}
            className="h-9 border border-[var(--color-line)] px-3 text-sm hover:border-[var(--color-accent)]"
          >
            Attach a source
          </button>
        }
      />

      {!spacesFromApi && (
        <DependencyNotice
          title="Showing offline Space fixtures"
          hint="The DMS API did not return a Space list, so the switcher is running on built-in examples."
        />
      )}
      {spacesPersisted === false && (
        <DependencyNotice
          tone="danger"
          title="Spaces are not persisted — they die on restart"
          hint={
            spacesStorageHint ??
            "This catalog is in-memory only. Nothing you create here survives an API restart. Set DATABASE_URL and apply migrations only when you need durable Spaces."
          }
          data-testid="spaces-memory-banner"
        />
      )}
      {databaseConfigured === false && spacesPersisted !== false && (
        <DependencyNotice
          title="No Postgres control plane"
          hint="Spaces created here live in the API process and disappear on restart. Set DATABASE_URL and apply migrations for durable Spaces, members, and ACL grants."
        />
      )}

      <div className="mt-6 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatTile label="Spaces" value={spaces.length} />
        <StatTile
          label="Scoped sources"
          value={sources.data ? (sources.data.length - companyScoped.length) : "—"}
        />
        {!spacesFromApi && (
          <StatTile label="Company-wide sources" value={sources.data ? companyScoped.length : "—"} />
        )}
        <StatTile
          label="Active scope"
          value={spaces.find((s) => s.id === activeSpaceId)?.name ?? "Company"}
        />
      </div>

      <Section title="Create a Space">
        <div className="flex max-w-xl flex-wrap gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void submit();
            }}
            placeholder="e.g. Q4 close, Supplier audit, Procurement"
            className="h-10 min-w-[16rem] flex-1 border border-[var(--color-line)] bg-[var(--color-surface)] px-3 text-sm"
          />
          <button
            type="button"
            disabled={!name.trim() || busy}
            onClick={() => void submit()}
            className="h-10 bg-[var(--color-accent)] px-4 text-sm font-medium text-[var(--color-on-accent)] disabled:opacity-50"
          >
            {busy ? "Creating…" : "Create Space"}
          </button>
        </div>
        {notice && <p className="mt-2 text-sm text-[var(--color-ink-muted)]">{notice}</p>}
        {failure && <p className="mt-2 text-sm text-[var(--color-danger)]">{failure}</p>}
      </Section>

      <Section title="Spaces" count={spaces.length}>
        <AsyncBoundary
          loading={sources.loading}
          error={sources.error}
          onRetry={sources.reload}
          empty={spaces.length === 0}
          emptyMessage="No Spaces yet — create one above, then attach sources in Studio."
        >
          <ul className="flex flex-col gap-2">
            {spaces.map((space) => {
              const attached = byId.get(space.id) ?? [];
              const active = space.id === activeSpaceId;
              return (
                <li
                  key={space.id}
                  className={`border bg-[var(--color-surface)]/60 px-3.5 py-3 ${
                    active
                      ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)]/30"
                      : "border-[var(--color-line)]"
                  }`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="font-medium">
                        {space.name}
                        {active && (
                          <span className="ml-2 text-xs font-normal text-[var(--color-accent)]">
                            active scope
                          </span>
                        )}
                      </p>
                      <p className="mt-0.5 font-mono text-[11px] text-[var(--color-ink-muted)]">
                        {space.id}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Pill>{attached.length || space.source_count} sources</Pill>
                      <Pill>{space.member_count} members</Pill>
                      <button
                        type="button"
                        onClick={() => setActiveSpaceId(space.id)}
                        disabled={active}
                        className="border border-[var(--color-line)] px-2.5 py-1 text-xs hover:border-[var(--color-accent)] disabled:opacity-40"
                      >
                        {active ? "In scope" : "Ask in this Space"}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setActiveSpaceId(space.id);
                          navigate("/");
                        }}
                        className="border border-[var(--color-accent)] bg-[var(--color-accent)] px-2.5 py-1 text-xs text-[var(--color-on-accent)]"
                      >
                        Open chat
                      </button>
                    </div>
                  </div>
                  {attached.length > 0 && (
                    <ul className="mt-2.5 divide-y divide-[var(--color-line)]/60 border-t border-[var(--color-line)]/60 pt-1.5">
                      {attached.map((src) => (
                        <li
                          key={src.id}
                          className="flex flex-wrap justify-between gap-2 py-1 text-xs"
                        >
                          <span className="font-mono">{src.ref}</span>
                          <span className="text-[var(--color-ink-muted)]">
                            {src.kind} · {src.scope}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
        </AsyncBoundary>
      </Section>

      {!spacesFromApi && (
      <Section
        title="Company scope"
        count={companyScoped.length}
        description="Visible to anyone with the default ACL — what a question sees when no Space is selected."
      >
        <ul className="divide-y divide-[var(--color-line)] border border-[var(--color-line)] bg-[var(--color-surface)]/60 text-sm">
          {companyScoped.map((src) => (
            <li key={src.id} className="flex flex-wrap justify-between gap-2 px-3 py-2">
              <span className="font-mono text-xs">{src.ref}</span>
              <span className="text-xs text-[var(--color-ink-muted)]">{src.kind}</span>
            </li>
          ))}
          {!companyScoped.length && (
            <li className="px-3 py-4 text-sm text-[var(--color-ink-muted)]">
              No company-wide sources.
            </li>
          )}
        </ul>
      </Section>
      )}
    </Page>
  );
}
