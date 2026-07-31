import { useMemo, useState } from "react";
import {
  AsyncBoundary,
  DependencyNotice,
  Page,
  PageHeader,
  Pill,
  Section,
  StatTile,
} from "@/components/PageShell";
import { fetchOntology } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import type {
  OntologyBundle,
  OntologyGraphEdge,
  OntologyGraphNode,
  OntologyMetric,
  OntologyObject,
} from "@/lib/types";

type Tab = "objects" | "map" | "metrics" | "actions" | "functions";

const TABS: { id: Tab; label: string }[] = [
  { id: "objects", label: "Objects" },
  { id: "map", label: "Map" },
  { id: "metrics", label: "Metrics" },
  { id: "actions", label: "Actions" },
  { id: "functions", label: "Functions" },
];

/* ── Map ───────────────────────────────────────────────────────────────────
 * A ring layout, not a force simulation: the ontology is a dozen objects and a
 * deterministic picture is one a steward can point at twice and get the same
 * shape. Node size carries property count; the ring keeps every edge visible.
 */
function OntologyMap({
  nodes,
  edges,
  selected,
  onSelect,
}: {
  nodes: OntologyGraphNode[];
  edges: OntologyGraphEdge[];
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const W = 860;
  const H = 460;
  const positions = useMemo(() => {
    const cx = W / 2;
    const cy = H / 2;
    const r = Math.min(W, H) * 0.36;
    const map = new Map<string, { x: number; y: number }>();
    nodes.forEach((n, i) => {
      const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2 - Math.PI / 2;
      map.set(n.id, { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) });
    });
    return map;
  }, [nodes]);

  if (!nodes.length) return null;

  return (
    <div className="overflow-x-auto border border-[var(--color-line)] bg-[var(--color-surface)]/60">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full min-w-[42rem]"
        role="img"
        aria-label="Object types and the links between them"
      >
        {edges.map((e) => {
          const a = positions.get(e.source);
          const b = positions.get(e.target);
          if (!a || !b) return null;
          const active = selected === e.source || selected === e.target;
          const mx = (a.x + b.x) / 2;
          const my = (a.y + b.y) / 2;
          return (
            <g key={e.id}>
              <line
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={active ? "var(--color-accent)" : "var(--color-line)"}
                strokeWidth={active ? 2 : 1}
              />
              {active && (
                <text
                  x={mx}
                  y={my - 4}
                  textAnchor="middle"
                  className="fill-[var(--color-ink-muted)] text-[10px]"
                >
                  {e.source_property} → {e.target_property} · {e.cardinality}
                </text>
              )}
            </g>
          );
        })}
        {nodes.map((n) => {
          const p = positions.get(n.id);
          if (!p) return null;
          const active = selected === n.id;
          const w = Math.min(180, 74 + n.label.length * 7);
          return (
            <g
              key={n.id}
              transform={`translate(${p.x - w / 2}, ${p.y - 21})`}
              onClick={() => onSelect(active ? null : n.id)}
              className="cursor-pointer"
            >
              <rect
                width={w}
                height={42}
                fill={active ? "var(--color-accent-soft)" : "var(--color-panel)"}
                stroke={active ? "var(--color-accent)" : "var(--color-line)"}
                strokeWidth={active ? 2 : 1}
              />
              <text
                x={w / 2}
                y={18}
                textAnchor="middle"
                className="fill-[var(--color-ink)] text-[12px] font-semibold"
              >
                {n.label}
              </text>
              <text
                x={w / 2}
                y={32}
                textAnchor="middle"
                className="fill-[var(--color-ink-muted)] text-[10px]"
              >
                {n.property_count} props · {n.metric_count} metrics
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* ── Objects ─────────────────────────────────────────────────────────────── */

function ObjectCard({
  object,
  open,
  onToggle,
  onMetric,
}: {
  object: OntologyObject;
  open: boolean;
  onToggle: () => void;
  onMetric: (id: string) => void;
}) {
  return (
    <li className="border border-[var(--color-line)] bg-[var(--color-surface)]/60">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-start justify-between gap-3 px-3.5 py-3 text-left hover:bg-[var(--color-paper-2)]/40"
      >
        <div className="min-w-0">
          <p className="font-medium text-[var(--color-ink)]">
            {object.id}
            <span className="ml-2 font-mono text-[11px] text-[var(--color-ink-muted)]">
              pk {object.primary_key}
            </span>
          </p>
          <p className="mt-1 line-clamp-2 text-sm text-[var(--color-ink-muted)]">
            {object.description}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          <Pill>{object.property_count} props</Pill>
          {object.sensitive_count > 0 && (
            <Pill tone="warn" title="Withheld from the agent — property-level flag">
              {object.sensitive_count} withheld
            </Pill>
          )}
          {object.metric_ids.length ? (
            <Pill tone="accent">{object.metric_ids.length} metrics</Pill>
          ) : (
            <Pill tone="danger" title="No governed metric reads this object type">
              no metric
            </Pill>
          )}
        </div>
      </button>
      {open && (
        <div className="border-t border-[var(--color-line)] px-3.5 py-3">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-[10px] uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
                <th className="pb-1.5 pr-3 font-semibold">Property</th>
                <th className="pb-1.5 pr-3 font-semibold">Type</th>
                <th className="pb-1.5 font-semibold">Agent access</th>
              </tr>
            </thead>
            <tbody>
              {object.properties.map((p) => (
                <tr key={p.name} className="border-t border-[var(--color-line)]/60">
                  <td className="py-1.5 pr-3 font-mono text-xs">
                    {p.name}
                    {p.name === object.primary_key && (
                      <span className="ml-1.5 text-[10px] text-[var(--color-accent)]">pk</span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3 text-xs text-[var(--color-ink-muted)]">{p.type}</td>
                  <td className="py-1.5 text-xs">
                    {p.agent_visible ? (
                      <span className="text-[var(--color-ink-muted)]">visible</span>
                    ) : (
                      <span className="text-[var(--color-warn)]">withheld</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {object.metric_ids.length > 0 && (
            <div className="mt-3">
              <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
                Governed ways to ask
              </p>
              <ul className="mt-1.5 flex flex-wrap gap-1.5">
                {object.metric_ids.map((m) => (
                  <li key={m}>
                    <button
                      type="button"
                      onClick={() => onMetric(m)}
                      className="border border-[var(--color-line)] px-2 py-0.5 font-mono text-[11px] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]"
                    >
                      {m}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/* ── Metrics ─────────────────────────────────────────────────────────────── */

function MetricRow({ metric }: { metric: OntologyMetric }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="border border-[var(--color-line)] bg-[var(--color-surface)]/60">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 px-3.5 py-2.5 text-left hover:bg-[var(--color-paper-2)]/40"
      >
        <span className="font-mono text-sm">{metric.id}</span>
        <span className="flex flex-wrap items-center gap-1.5">
          <Pill>{metric.kind}</Pill>
          {metric.object_types.map((o) => (
            <Pill key={o} tone="accent">
              {o}
            </Pill>
          ))}
          {metric.params.length > 0 && <Pill>{metric.params.length} params</Pill>}
        </span>
      </button>
      {open && (
        <div className="border-t border-[var(--color-line)] px-3.5 py-3">
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            Recognised phrasings
          </p>
          <p className="mt-1 text-xs text-[var(--color-ink-muted)]">
            {metric.synonyms.length ? metric.synonyms.join(" · ") : "— none registered —"}
          </p>
          <p className="mt-3 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--color-ink-muted)]">
            Compiled SQL
          </p>
          <pre className="mt-1 overflow-x-auto border border-[var(--color-line)] bg-[var(--color-paper)] p-2.5 text-[11px] leading-relaxed">
            {metric.sql}
          </pre>
        </div>
      )}
    </li>
  );
}

/* ── Page ────────────────────────────────────────────────────────────────── */

export function OntologyPage() {
  const { data, error, loading, reload } = useAsync<OntologyBundle>((signal) =>
    fetchOntology(signal),
  );
  const [tab, setTab] = useState<Tab>("objects");
  const [query, setQuery] = useState("");
  const [openObject, setOpenObject] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  const counts = data?.summary.counts;
  const degraded = data ? data.summary.ok === false : false;

  const q = query.trim().toLowerCase();
  const objects = (data?.objects ?? []).filter(
    (o) =>
      !q ||
      o.id.includes(q) ||
      o.description.toLowerCase().includes(q) ||
      o.properties.some((p) => p.name.includes(q)),
  );
  const metrics = (data?.metrics ?? []).filter(
    (m) =>
      !q ||
      m.id.includes(q) ||
      m.object_types.some((o) => o.includes(q)) ||
      m.synonyms.some((s) => s.toLowerCase().includes(q)),
  );
  const actions = data?.actions ?? [];
  const tools = actions.filter((a) => a.kind === "tool");
  const events = actions.filter((a) => a.kind !== "tool");

  return (
    <Page>
      <PageHeader
        phase="Ontology · Cortex registry"
        title="Ontology"
        blurb="The shared vocabulary underneath every answer: what objects exist, how they join, which properties an agent may see, what may be written back, and the governed ways to ask. Authored as YAML in the engine pack — this page reads it, never edits it."
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

      {degraded && (
        <DependencyNotice
          tone="danger"
          title="Ontology unavailable — Cortex did not answer"
          hint={data?.summary.hint ?? "Start the engine on :8010, then Refresh."}
        />
      )}

      <div className="mt-6 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <StatTile label="Object types" value={counts?.object_types ?? "—"} />
        <StatTile label="Links" value={counts?.link_types ?? "—"} />
        <StatTile
          label="Governed actions"
          value={counts?.action_tools ?? "—"}
          hint="invocable writes"
        />
        <StatTile label="Ledger events" value={counts?.action_events ?? "—"} />
        <StatTile label="Functions" value={counts?.functions ?? "—"} />
        <StatTile
          label="Withheld props"
          value={counts?.sensitive_properties ?? "—"}
          tone={counts?.sensitive_properties ? "warn" : "neutral"}
          hint="never sent to a model"
        />
      </div>

      {data?.summary.objects_without_metrics?.length ? (
        <DependencyNotice
          title={`${data.summary.objects_without_metrics.length} object type(s) have no governed metric`}
          hint={`${data.summary.objects_without_metrics.join(", ")} — questions about these fall through L1 to generation or abstain. Add a metric in the pack's semantic layer to make them answerable under a governed badge.`}
        />
      ) : null}

      <div className="mt-7 flex flex-wrap items-center gap-2 border-b border-[var(--color-line)]">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
              tab === t.id
                ? "border-[var(--color-accent)] text-[var(--color-ink)]"
                : "border-transparent text-[var(--color-ink-muted)] hover:text-[var(--color-ink)]"
            }`}
          >
            {t.label}
          </button>
        ))}
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter objects, properties, metrics…"
          className="ml-auto mb-1.5 h-8 w-full max-w-xs border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-2.5 text-sm"
        />
      </div>

      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        {tab === "objects" && (
          <Section title="Object types" count={objects.length}>
            <ul className="flex flex-col gap-2">
              {objects.map((o) => (
                <ObjectCard
                  key={o.id}
                  object={o}
                  open={openObject === o.id}
                  onToggle={() => setOpenObject(openObject === o.id ? null : o.id)}
                  onMetric={(id) => {
                    setQuery(id);
                    setTab("metrics");
                  }}
                />
              ))}
              {!objects.length && (
                <li className="border border-[var(--color-line)] px-3 py-6 text-sm text-[var(--color-ink-muted)]">
                  No object type matches “{query}”.
                </li>
              )}
            </ul>
          </Section>
        )}

        {tab === "map" && (
          <Section
            title="Object map"
            description="Click an object to reveal the join keys and cardinality on its links."
          >
            <OntologyMap
              nodes={data?.graph.nodes ?? []}
              edges={data?.graph.edges ?? []}
              selected={selectedNode}
              onSelect={setSelectedNode}
            />
            <ul className="mt-3 divide-y divide-[var(--color-line)] border border-[var(--color-line)] bg-[var(--color-surface)]/60 text-sm">
              {(data?.links ?? []).map((l) => (
                <li key={l.id} className="flex flex-wrap justify-between gap-2 px-3 py-2">
                  <span className="font-mono text-xs">
                    {l.from_object}.{l.from_property} → {l.to_object}.{l.to_property}
                  </span>
                  <Pill>{l.cardinality}</Pill>
                </li>
              ))}
            </ul>
          </Section>
        )}

        {tab === "metrics" && (
          <Section
            title="Governed metrics"
            count={metrics.length}
            description="What the answer engine compiles deterministically at L1. If a question does not reach one of these, it is generated and checked, or it abstains."
          >
            <ul className="flex flex-col gap-1.5">
              {metrics.map((m) => (
                <MetricRow key={m.id} metric={m} />
              ))}
              {!metrics.length && (
                <li className="border border-[var(--color-line)] px-3 py-6 text-sm text-[var(--color-ink-muted)]">
                  No metric matches “{query}”.
                </li>
              )}
            </ul>
          </Section>
        )}

        {tab === "actions" && (
          <>
            <Section
              title="Governed actions"
              count={tools.length}
              description="The only write paths that exist. Each one names the role it needs and whether a human has to confirm before it runs."
            >
              <ul className="flex flex-col gap-1.5">
                {tools.map((a) => (
                  <li
                    key={a.id}
                    className="border border-[var(--color-line)] bg-[var(--color-surface)]/60 px-3.5 py-2.5"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-mono text-sm">{a.id}</span>
                      <span className="flex flex-wrap gap-1.5">
                        {a.required_role && <Pill tone="accent">{a.required_role}+</Pill>}
                        {a.requires_confirm && <Pill tone="warn">confirm required</Pill>}
                        {a.object_type && <Pill>{a.object_type}</Pill>}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-[var(--color-ink-muted)]">{a.description}</p>
                    <p className="mt-1 font-mono text-[11px] text-[var(--color-ink-muted)]">
                      ledger: {a.ledger_event_type}
                      {a.params.length ? ` · params: ${a.params.join(", ")}` : ""}
                    </p>
                  </li>
                ))}
                {!tools.length && (
                  <li className="border border-[var(--color-line)] px-3 py-6 text-sm text-[var(--color-ink-muted)]">
                    No invocable action registered.
                  </li>
                )}
              </ul>
            </Section>
            <Section
              title="Registered ledger events"
              count={events.length}
              description="Event types the ledger already writes, named here so the audit namespace is one describable list rather than string literals scattered through the pack."
            >
              <ul className="divide-y divide-[var(--color-line)] border border-[var(--color-line)] bg-[var(--color-surface)]/60 text-sm">
                {events.map((a) => (
                  <li key={a.id} className="flex flex-wrap justify-between gap-2 px-3 py-2">
                    <span className="font-mono text-xs">{a.id}</span>
                    <span className="max-w-md text-right text-xs text-[var(--color-ink-muted)]">
                      {a.description}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          </>
        )}

        {tab === "functions" && (
          <Section
            title="Functions"
            count={data?.functions.length ?? 0}
            description="Business logic an agent or pipeline may call. Registered so capability is discoverable without reading the pack source."
          >
            <ul className="divide-y divide-[var(--color-line)] border border-[var(--color-line)] bg-[var(--color-surface)]/60">
              {(data?.functions ?? []).map((f) => (
                <li key={f.id} className="px-3.5 py-2.5">
                  <p className="font-mono text-sm">{f.id}</p>
                  <p className="mt-0.5 text-sm text-[var(--color-ink-muted)]">{f.description}</p>
                  <p className="mt-0.5 font-mono text-[11px] text-[var(--color-ink-muted)]">
                    {f.module}:{f.callable}
                  </p>
                </li>
              ))}
            </ul>
          </Section>
        )}
      </AsyncBoundary>
    </Page>
  );
}
