import { useRef, useState } from "react";
import { SimpleChart } from "@/components/SimpleChart";
import {
  activeChart,
  chartRows,
  chartShape,
  formatFigure,
  kindLabel,
  kpiValue,
  switcherKinds,
  type ChartKind,
  type FigureValue,
} from "@/lib/answerChart";

type Props = {
  rows: Record<string, unknown>[];
  values: FigureValue[];
  onValue: (id: string) => void;
  answerId?: string;
};

/**
 * Chart of the served rows. The switcher changes the drawing, not the cells.
 * ponytail: PNG is one canvas of the svg (or the KPI text). Upgrade: a
 * shared exporter if a second surface needs the same file.
 */
export function AnswerChart({ rows, values, onValue, answerId }: Props) {
  const shape = chartShape(rows);
  const [pick, setPick] = useState<ChartKind | null>(null);
  const [pngErr, setPngErr] = useState<string | null>(null);
  const frame = useRef<HTMLDivElement>(null);
  const view = activeChart(rows, pick);
  const kinds = switcherKinds(shape);
  const plotted = chartRows(rows, view);
  const canPng = view.kind === "bar" || view.kind === "line" || view.kind === "scatter" || view.kind === "kpi";

  async function downloadPng() {
    const node = frame.current;
    if (!node) return;
    setPngErr(null);
    try {
      await savePng(node, answerId ? `${answerId}.png` : "chart.png");
    } catch {
      setPngErr("Could not download the chart.");
    }
  }

  return (
    <div className="mt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="group" aria-label="Chart type" className="flex flex-wrap gap-1">
          {kinds.map((kind) => (
            <button
              key={kind}
              type="button"
              data-testid={`ux-chart-kind-${kind}`}
              aria-pressed={view.kind === kind}
              onClick={() => setPick(kind)}
              className={
                view.kind === kind
                  ? "border border-[var(--color-accent)] bg-[var(--color-panel)] px-2 py-1 text-xs text-[var(--color-ink)]"
                  : "border border-[var(--color-line)] bg-[var(--color-panel)] px-2 py-1 text-xs text-[var(--color-ink-muted)] hover:border-[var(--color-accent)]"
              }
            >
              {kindLabel(kind)}
            </button>
          ))}
        </div>
        <button
          type="button"
          data-testid="ux-chart-png"
          disabled={!canPng}
          onClick={() => void downloadPng()}
          className="text-xs text-[var(--color-ink-muted)] underline-offset-2 hover:underline disabled:opacity-40"
        >
          Download PNG
        </button>
      </div>
      {pngErr ? <p className="mt-2 text-xs text-[var(--color-danger)]">{pngErr}</p> : null}
      <div
        ref={frame}
        data-testid="ux-chart"
        data-chart-kind={view.kind}
        data-point-count={rows.length}
      >
        {view.kind === "kpi" ? (
          <KpiCard rows={rows} y={view.y} x={view.x} values={values} onValue={onValue} />
        ) : null}
        {view.kind === "scatter" && view.y ? (
          <SimpleChart
            chart={{ kind: "scatter", x: view.x, y: [view.y], series: null, title: view.y }}
            rows={plotted.rows}
          />
        ) : null}
        {(view.kind === "bar" || view.kind === "line") && view.y ? (
          <SimpleChart
            chart={{ kind: view.kind, x: plotted.x, y: view.y, title: view.y }}
            rows={plotted.rows}
          />
        ) : null}
      </div>
    </div>
  );
}

function KpiCard({
  rows,
  y,
  x,
  values,
  onValue,
}: {
  rows: Record<string, unknown>[];
  y: string;
  x: string;
  values: FigureValue[];
  onValue: (id: string) => void;
}) {
  const raw = kpiValue(rows, y);
  if (raw == null) return null;
  const text = formatFigure(raw, y, values);
  const id = values.find((item) => item.label === y && Math.abs(item.value - raw) < 0.011)?.id;
  const caption = x && rows[0] ? String(rows[0][x] ?? "") : "";
  const className =
    "mt-2 text-4xl font-semibold tabular-nums tracking-tight text-[var(--color-ink)]";
  return (
    <div
      data-testid="ux-kpi"
      className="mt-3 border border-[var(--color-line)] bg-[var(--color-panel)] px-4 py-6 text-center"
    >
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-ink-muted)]">
        {y}
      </p>
      {id ? (
        <button type="button" data-kpi="1" data-value-id={id} onClick={() => onValue(id)} className={className}>
          {text}
        </button>
      ) : (
        <p data-kpi="1" className={className}>
          {text}
        </p>
      )}
      {caption ? <p className="mt-2 text-sm text-[var(--color-ink-muted)]">{caption}</p> : null}
    </div>
  );
}

async function savePng(node: HTMLElement, filename: string): Promise<void> {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas");
  const svg = node.querySelector("svg");
  if (svg) {
    const xml = new XMLSerializer().serializeToString(svg);
    const blob = new Blob([xml], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    try {
      const img = new Image();
      await new Promise<void>((resolve, reject) => {
        img.onload = () => resolve();
        img.onerror = () => reject(new Error("chart"));
        img.src = url;
      });
      const w = Math.max(img.naturalWidth, 640);
      const h = Math.max(img.naturalHeight, 360);
      canvas.width = w;
      canvas.height = h;
      ctx.fillStyle = fillOf(node);
      ctx.fillRect(0, 0, w, h);
      ctx.drawImage(img, 0, 0, w, h);
    } finally {
      URL.revokeObjectURL(url);
    }
  } else {
    canvas.width = 640;
    canvas.height = 280;
    ctx.fillStyle = fillOf(node);
    ctx.fillRect(0, 0, 640, 280);
    ctx.fillStyle = "#1c1917";
    ctx.font = "48px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(node.querySelector("[data-kpi]")?.textContent ?? "", 320, 150);
  }
  const png = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  if (!png) throw new Error("png");
  const href = URL.createObjectURL(png);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(href);
}

function fillOf(node: HTMLElement): string {
  const color = getComputedStyle(node).backgroundColor;
  if (!color || color === "rgba(0, 0, 0, 0)" || color === "transparent") return "#ffffff";
  return color;
}
