"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

function BigNumber({ spec }) {
  return (
    <div className="cx-bignum">
      <div className="cx-bignum-value">{spec.value}</div>
      <div className="cx-bignum-label">{spec.label}</div>
      {spec.title && <div className="cx-bignum-title">{spec.title}</div>}
    </div>
  );
}

const tooltipStyle = {
  background: "#111111",
  border: "1px solid #333333",
  fontFamily: "JetBrains Mono, monospace",
  fontSize: 11,
  color: "#e5e5e5",
};

export default function ResultChart({ spec }) {
  if (!spec) return null;

  if (spec.type === "bignum") {
    return <BigNumber spec={spec} />;
  }

  const data = spec.data || [];
  if (!data.length) return null;

  const title = spec.title;
  const more = spec.more_count > 0 ? `+${spec.more_count} more` : null;

  if (spec.type === "line") {
    return (
      <div className="cx-chart-wrap">
        {title && <div className="cx-chart-title">{title}</div>}
        <div style={{ width: "100%", height: 200 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
              <CartesianGrid stroke="#222222" vertical={false} />
              <XAxis
                dataKey="name"
                tick={{ fill: "var(--netie-muted)", fontSize: 10, fontFamily: "JetBrains Mono" }}
                tickLine={false}
                axisLine={{ stroke: "#333333" }}
              />
              <YAxis hide />
              <Tooltip contentStyle={tooltipStyle} />
              <Line
                type="monotone"
                dataKey="value"
                stroke="rgba(0, 255, 135, 0.9)"
                strokeWidth={2}
                dot={{ fill: "#00ff87", r: 3 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
        {more && <div className="cx-chart-more">{more}</div>}
      </div>
    );
  }

  return (
    <div className="cx-chart-wrap">
      {title && <div className="cx-chart-title">{title}</div>}
      <div style={{ width: "100%", height: 200 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
            <CartesianGrid stroke="#222222" vertical={false} />
            <XAxis
              dataKey="name"
              tick={{ fill: "var(--netie-muted)", fontSize: 10, fontFamily: "JetBrains Mono" }}
              tickLine={false}
              axisLine={{ stroke: "#333333" }}
            />
            <YAxis hide />
            <Tooltip contentStyle={tooltipStyle} />
            <Bar dataKey="value" fill="rgba(0, 255, 135, 0.7)" radius={0} maxBarSize={48} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      {more && <div className="cx-chart-more">{more}</div>}
    </div>
  );
}
