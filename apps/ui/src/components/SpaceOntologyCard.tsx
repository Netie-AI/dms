import { AsyncBoundary } from "@/components/PageShell";
import { fetchSpaceOntology } from "@/lib/api";
import { linkSentence, violationText } from "@/lib/spaceOntology";
import type { SpaceOntologyView, SpaceOntologyViolation } from "@/lib/types";
import { useAsync } from "@/lib/useAsync";

/** Local tag: the shared Pill is 10px, which is under the 12px floor on a phone. */
function Tag({ children, warn }: { children: string; warn?: boolean }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center border px-1.5 py-0.5 text-xs font-medium ${
        warn
          ? "border-[var(--color-warn)]/50 bg-[var(--color-warn-soft)] text-[var(--color-warn)]"
          : "border-[var(--color-line)] text-[var(--color-ink-muted)]"
      }`}
    >
      {children}
    </span>
  );
}

function Violations({ items }: { items: SpaceOntologyViolation[] }) {
  if (!items.length) return null;
  return (
    <ul className="mt-1 flex flex-col gap-1">
      {items.map((v, i) => (
        <li key={`${v.check}-${i}`} className="text-xs leading-snug text-[var(--color-warn)]">
          {violationText(v)}
        </li>
      ))}
    </ul>
  );
}

const ROW = "min-h-11 border-t border-[var(--color-line)]/60 py-2";

function SourceBlock({ view }: { view: SpaceOntologyView }) {
  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="min-w-0 break-words text-sm font-medium">
          {view.source.kind} · {view.source.database}
        </p>
        {!view.answers_asks && <Tag warn>proposed, not answering yet</Tag>}
        {!view.verified && <Tag warn>has unverified parts</Tag>}
      </div>
      <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">
        {view.objects.length} objects · {view.links.length} links · measured{" "}
        {new Date(view.measured_at).toLocaleDateString()}
      </p>

      <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-[var(--color-ink-muted)]">
        Objects
      </p>
      <ul className="mt-1">
        {view.objects.map((o) => (
          <li key={o.name} className={ROW}>
            <div className="flex items-center justify-between gap-2">
              <span className="min-w-0 break-all font-mono text-sm">{o.name}</span>
              {o.status === "unverified" && <Tag warn>unverified</Tag>}
            </div>
            <p className="text-xs text-[var(--color-ink-muted)]">
              {o.key.length ? `key ${o.key.join(", ")}` : "no key"} · {o.attributes.length}{" "}
              attributes
            </p>
            <Violations items={o.violations} />
          </li>
        ))}
      </ul>

      <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-[var(--color-ink-muted)]">
        Links
      </p>
      <ul className="mt-1">
        {view.links.map((l) => (
          <li key={l.name} className={ROW}>
            <div className="flex items-center justify-between gap-2">
              <span className="min-w-0 break-words font-mono text-sm">{linkSentence(l)}</span>
              {l.status === "unverified" && <Tag warn>unverified</Tag>}
            </div>
            {l.cardinality && (
              <p className="text-xs text-[var(--color-ink-muted)]">
                {l.cardinality.replace(/_/g, " ")}
              </p>
            )}
            <Violations items={l.violations} />
          </li>
        ))}
        {!view.links.length && (
          <li className={`${ROW} text-sm text-[var(--color-ink-muted)]`}>No links declared.</li>
        )}
      </ul>
    </div>
  );
}

/** Read-only view of what ingest derived for one Space. Never calls the derive route. */
export function SpaceOntologyCard({ spaceId }: { spaceId: string }) {
  const { data, error, loading, reload } = useAsync(
    (signal) => fetchSpaceOntology(spaceId, signal),
    [spaceId],
  );
  return (
    <div
      data-testid="space-ontology-card"
      className="mt-2.5 border-t border-[var(--color-line)]/60 pt-2.5"
    >
      <p className="text-sm font-semibold">Derived ontology</p>
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        {data && !data.derived && (
          <p className="mt-2 border border-[var(--color-line)] px-3 py-3 text-sm text-[var(--color-ink-muted)]">
            {data.reason ?? "No ontology has been derived for this Space."}
          </p>
        )}
        {data?.derived && (data.ontologies ?? []).map((v) => <SourceBlock key={v.version_id} view={v} />)}
        {data?.derived && (
          <p className="mt-3 text-xs text-[var(--color-ink-muted)]">
            Measures are declared by a steward, never derived.
          </p>
        )}
      </AsyncBoundary>
    </div>
  );
}
