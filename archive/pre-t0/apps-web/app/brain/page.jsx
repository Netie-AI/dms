"use client";
import { useState, useRef } from "react";
import AppShell from "../../components/AppShell";
import {
  BarChart, Bar, LineChart, Line, AreaChart, Area, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const COLORS = ["#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#06B6D4", "#84CC16"];

// ── Quick-access command buttons ──────────────────────────────────────────────
const QUICK_COMMANDS = [
  { label: "📊 Chart: Inventory", intent: "generate_chart", query: "Show inventory levels by category" },
  { label: "📈 Chart: Movements", intent: "generate_chart", query: "Show item movements over time" },
  { label: "📥 Export CSV", intent: "export_csv", query: "Export all inventory items" },
  { label: "📧 Email CEO", intent: "draft_email", request: "Draft weekly operations summary for the CEO" },
  { label: "💬 WhatsApp Staff", intent: "draft_whatsapp", request: "Send daily movement summary to warehouse team" },
  { label: "📋 Weekly Analysis", intent: "analyze_sales", period: "last_7_days" },
  { label: "🏢 CEO Report", intent: "auto_analysis" },
  { label: "📄 Organize Report", intent: "organize_report", query: "Organize last week's sales and warehouse activity into a report" },
];

// ── Chart renderer ────────────────────────────────────────────────────────────
function ChartRenderer({ config }) {
  if (!config || !config.data || !Array.isArray(config.data)) {
    return <p className="text-gray-400 text-sm">No chart data available.</p>;
  }
  const { chart_type = "bar", data, y_keys = ["value"], x_key = "name", colors = COLORS, title, x_label, y_label } = config;

  const commonProps = {
    data,
    margin: { top: 10, right: 20, left: 0, bottom: 30 },
  };

  const renderChart = () => {
    switch (chart_type) {
      case "line":
        return (
          <LineChart {...commonProps}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey={x_key} tick={{ fill: "#9CA3AF", fontSize: 11 }} label={x_label ? { value: x_label, position: "insideBottom", fill: "#6B7280", offset: -10 } : undefined} />
            <YAxis tick={{ fill: "#9CA3AF", fontSize: 11 }} label={y_label ? { value: y_label, angle: -90, position: "insideLeft", fill: "#6B7280" } : undefined} />
            <Tooltip contentStyle={{ background: "#1F2937", border: "1px solid #374151", color: "#F9FAFB" }} />
            <Legend wrapperStyle={{ color: "#9CA3AF" }} />
            {y_keys.map((k, i) => <Line key={k} type="monotone" dataKey={k} stroke={colors[i % colors.length]} strokeWidth={2} dot={false} />)}
          </LineChart>
        );
      case "area":
        return (
          <AreaChart {...commonProps}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey={x_key} tick={{ fill: "#9CA3AF", fontSize: 11 }} />
            <YAxis tick={{ fill: "#9CA3AF", fontSize: 11 }} />
            <Tooltip contentStyle={{ background: "#1F2937", border: "1px solid #374151", color: "#F9FAFB" }} />
            {y_keys.map((k, i) => (
              <Area key={k} type="monotone" dataKey={k} stroke={colors[i % colors.length]} fill={colors[i % colors.length] + "33"} />
            ))}
          </AreaChart>
        );
      case "pie":
        return (
          <PieChart>
            <Pie data={data} dataKey={y_keys[0] || "value"} nameKey={x_key} cx="50%" cy="50%" outerRadius={120} label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}>
              {data.map((_, i) => <Cell key={i} fill={colors[i % colors.length]} />)}
            </Pie>
            <Tooltip contentStyle={{ background: "#1F2937", border: "1px solid #374151", color: "#F9FAFB" }} />
            <Legend wrapperStyle={{ color: "#9CA3AF" }} />
          </PieChart>
        );
      default: // bar
        return (
          <BarChart {...commonProps}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey={x_key} tick={{ fill: "#9CA3AF", fontSize: 11 }} />
            <YAxis tick={{ fill: "#9CA3AF", fontSize: 11 }} />
            <Tooltip contentStyle={{ background: "#1F2937", border: "1px solid #374151", color: "#F9FAFB" }} />
            <Legend wrapperStyle={{ color: "#9CA3AF" }} />
            {y_keys.map((k, i) => <Bar key={k} dataKey={k} fill={colors[i % colors.length]} radius={[3, 3, 0, 0]} />)}
          </BarChart>
        );
    }
  };

  return (
    <div className="bg-gray-800 rounded-lg p-4 mt-3">
      {title && <h3 className="text-white font-semibold mb-3">{title}</h3>}
      <ResponsiveContainer width="100%" height={300}>
        {renderChart()}
      </ResponsiveContainer>
      {config.insights && config.insights.length > 0 && (
        <ul className="mt-3 space-y-1">
          {config.insights.map((insight, i) => (
            <li key={i} className="text-sm text-emerald-400 flex items-start gap-2">
              <span className="mt-0.5">💡</span><span>{insight}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Email/WhatsApp card ───────────────────────────────────────────────────────
function DraftCard({ data, type }) {
  const [copied, setCopied] = useState(false);
  const content = type === "email" ? data.body : data.message;
  const copyText = () => {
    navigator.clipboard?.writeText(content || "");
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <div className="bg-gray-800 rounded-lg p-4 mt-3 border border-yellow-500/30">
      <div className="flex items-center justify-between mb-2">
        <span className="text-yellow-400 text-xs font-semibold uppercase tracking-wider">
          ⚠ Requires Review Before {type === "email" ? "Sending" : "Messaging"}
        </span>
        <button onClick={copyText} className="text-xs px-2 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded transition">
          {copied ? "✓ Copied" : "Copy"}
        </button>
      </div>
      {type === "email" && (
        <>
          <p className="text-gray-400 text-xs mb-1">To: <span className="text-gray-200">{data.to_suggestion || "—"}</span></p>
          <p className="text-gray-400 text-xs mb-3">Subject: <span className="text-white font-medium">{data.subject}</span></p>
        </>
      )}
      <pre className="text-gray-200 text-sm whitespace-pre-wrap font-sans leading-relaxed">{content}</pre>
      {data.key_points && (
        <ul className="mt-3 space-y-1">
          {data.key_points.map((p, i) => (
            <li key={i} className="text-xs text-blue-400">• {p}</li>
          ))}
        </ul>
      )}
      {data.review_note && (
        <p className="text-yellow-500 text-xs mt-3 italic">{data.review_note}</p>
      )}
    </div>
  );
}

// ── Analysis card ─────────────────────────────────────────────────────────────
function AnalysisCard({ data }) {
  const score = data.performance_score;
  const scoreColor = score >= 75 ? "text-emerald-400" : score >= 50 ? "text-yellow-400" : "text-red-400";
  return (
    <div className="bg-gray-800 rounded-lg p-4 mt-3 space-y-4">
      {score !== undefined && (
        <div className="flex items-center gap-3">
          <div className={`text-4xl font-bold ${scoreColor}`}>{score}</div>
          <div>
            <p className="text-gray-400 text-xs">Performance Score</p>
            <div className="w-48 bg-gray-700 rounded-full h-2 mt-1">
              <div className={`h-2 rounded-full ${score >= 75 ? "bg-emerald-500" : score >= 50 ? "bg-yellow-500" : "bg-red-500"}`} style={{ width: `${score}%` }} />
            </div>
          </div>
        </div>
      )}
      {(data.executive_summary || data.narrative) && (
        <p className="text-gray-200 text-sm leading-relaxed">{data.executive_summary || data.narrative}</p>
      )}
      {data.sections && data.sections.map((s, i) => (
        <div key={i}>
          <h4 className="text-indigo-400 font-semibold text-sm mb-1">{s.title}</h4>
          <p className="text-gray-300 text-sm">{s.content}</p>
          {s.metrics && Object.keys(s.metrics).length > 0 && (
            <div className="flex flex-wrap gap-3 mt-2">
              {Object.entries(s.metrics).map(([k, v]) => (
                <span key={k} className="bg-gray-700 px-2 py-1 rounded text-xs text-gray-300">
                  <span className="text-gray-500">{k}:</span> {String(v)}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
      {data.risk_flags && data.risk_flags.length > 0 && (
        <div>
          <h4 className="text-red-400 font-semibold text-sm mb-1">⚠ Risk Flags</h4>
          {data.risk_flags.map((f, i) => (
            <div key={i} className={`text-xs px-2 py-1 rounded mb-1 ${f.severity === "critical" ? "bg-red-900/40 text-red-300" : f.severity === "warning" ? "bg-yellow-900/40 text-yellow-300" : "bg-gray-700 text-gray-400"}`}>
              {f.flag}
            </div>
          ))}
        </div>
      )}
      {data.top_actions && data.top_actions.length > 0 && (
        <div>
          <h4 className="text-emerald-400 font-semibold text-sm mb-2">Top Actions</h4>
          <div className="space-y-1">
            {data.top_actions.map((a, i) => (
              <div key={i} className="flex items-start gap-2 text-sm">
                <span className="text-gray-500 text-xs mt-0.5">{i + 1}.</span>
                <span className="text-gray-200">{a.action}</span>
                {a.priority && <span className={`text-xs px-1.5 py-0.5 rounded ${a.priority === "high" ? "bg-red-900/40 text-red-300" : "bg-gray-700 text-gray-400"}`}>{a.priority}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Suggestions card ──────────────────────────────────────────────────────────
function SuggestionsCard({ suggestions, onAccept, onDismiss, gateResults = {} }) {
  return (
    <div className="mt-3 space-y-2">
      {suggestions.map((s) => {
        const gate = gateResults[s.task_id];
        return (
        <div key={s.task_id} className="bg-gray-800 border border-gray-700 rounded-lg p-3">
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${s.priority === "critical" ? "bg-red-900/60 text-red-300" : s.priority === "high" ? "bg-orange-900/60 text-orange-300" : "bg-gray-700 text-gray-400"}`}>{s.priority}</span>
                <span className="text-white text-sm font-medium">{s.title}</span>
              </div>
              <p className="text-gray-400 text-xs mt-1">{s.description}</p>
              {gate && (
                <p className={`text-xs mt-2 font-medium ${gate.status === "pass" ? "text-emerald-400" : gate.status === "warn" ? "text-yellow-400" : "text-red-400"}`}>
                  Gate: {gate.status.toUpperCase()}{gate.executable ? " (executable)" : " (blocked)"}
                </p>
              )}
              <div className="flex items-center gap-3 mt-2">
                <span className="text-gray-500 text-xs">Confidence: {Math.round((s.confidence || 0) * 100)}%</span>
              </div>
            </div>
            <div className="flex gap-1 shrink-0">
              <button onClick={() => onAccept(s)} className="text-xs px-2 py-1 bg-emerald-700 hover:bg-emerald-600 text-white rounded transition">Accept</button>
              <button onClick={() => onDismiss(s.task_id)} className="text-xs px-2 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded transition">Dismiss</button>
            </div>
          </div>
        </div>
      );})}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function BrainPage() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([
    { role: "system", text: "DMS Brain ready. Select a quick command or type a request." }
  ]);
  const [loading, setLoading] = useState(false);
  const [gateResults, setGateResults] = useState({});
  const bottomRef = useRef(null);

  const addMessage = (role, text, data = null, dataType = null) => {
    setMessages(prev => [...prev, { role, text, data, dataType, id: Date.now() }]);
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
  };

  const downloadCSV = (filename, content) => {
    const blob = new Blob([content], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  };

  const downloadMarkdown = (filename, content) => {
    const blob = new Blob([content], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  };

  const callBrain = async (endpoint, body) => {
    const res = await fetch(`${API}/dms/brain/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.text();
      throw new Error(err);
    }
    return res.json();
  };

  const handleQuick = async (cmd) => {
    setLoading(true);
    addMessage("user", cmd.label);
    try {
      let result, dataType;
      switch (cmd.intent) {
        case "generate_chart":
          result = await callBrain("chart", { query: cmd.query, data: {} });
          dataType = "chart";
          addMessage("assistant", `Chart generated: "${result.title || cmd.query}"`, result, "chart");
          break;
        case "export_csv":
          result = await callBrain("export", { query: cmd.query, table: "dms_inventory", limit: 5000 });
          dataType = "csv";
          addMessage("assistant", `CSV ready: ${result.filename} (${result.row_count} rows). ${result.summary}`, result, "csv");
          break;
        case "draft_email":
          result = await callBrain("email", { request: cmd.request, context: {} });
          addMessage("assistant", "Email drafted — review before sending.", result, "email");
          break;
        case "draft_whatsapp":
          result = await callBrain("whatsapp", { request: cmd.request, context: {} });
          addMessage("assistant", "WhatsApp message drafted — review before sending.", result, "whatsapp");
          break;
        case "analyze_sales":
          result = await callBrain("analyze", { period: cmd.period });
          addMessage("assistant", `Analysis complete for ${cmd.period}.`, result, "analysis");
          break;
        case "auto_analysis":
          result = await callBrain("auto-analysis", {});
          addMessage("assistant", "Executive summary generated.", result, "analysis");
          break;
        case "organize_report":
          result = await callBrain("report", { query: cmd.query });
          addMessage("assistant", "Report organized.", result, "report");
          break;
        default:
          addMessage("assistant", "Unknown command.");
      }
    } catch (e) {
      addMessage("assistant", `Error: ${e.message}`);
    }
    setLoading(false);
  };

  const handleCustom = async () => {
    if (!input.trim()) return;
    const text = input.trim();
    setInput("");
    setLoading(true);
    addMessage("user", text);
    try {
      // Route custom input to best intent
      const lower = text.toLowerCase();
      let result, type;
      if (lower.includes("chart") || lower.includes("graph") || lower.includes("show me")) {
        result = await callBrain("chart", { query: text, data: {} });
        type = "chart";
        addMessage("assistant", result.title || "Chart ready", result, "chart");
      } else if (lower.includes("export") || lower.includes("csv") || lower.includes("download")) {
        result = await callBrain("export", { query: text, table: "dms_inventory", limit: 5000 });
        type = "csv";
        addMessage("assistant", `CSV: ${result.filename} (${result.row_count} rows)`, result, "csv");
      } else if (lower.includes("email") || lower.includes("send to") || lower.includes("draft")) {
        result = await callBrain("email", { request: text, context: {} });
        addMessage("assistant", "Email drafted — review before sending.", result, "email");
      } else if (lower.includes("whatsapp") || lower.includes("message") || lower.includes("text")) {
        result = await callBrain("whatsapp", { request: text, context: {} });
        addMessage("assistant", "Message drafted.", result, "whatsapp");
      } else if (lower.includes("summary") || lower.includes("ceo") || lower.includes("executive")) {
        result = await callBrain("auto-analysis", {});
        addMessage("assistant", "Executive summary ready.", result, "analysis");
      } else if (lower.includes("suggest") || lower.includes("task") || lower.includes("recommend")) {
        result = await callBrain("suggest", { use_llm: false });
        addMessage("assistant", `${result.suggestions?.length || 0} task suggestions.`, result.suggestions, "suggestions");
      } else {
        result = await callBrain("analyze", { period: "last_7_days" });
        addMessage("assistant", "Analysis complete.", result, "analysis");
      }
    } catch (e) {
      addMessage("assistant", `Error: ${e.message}`);
    }
    setLoading(false);
  };

  const handleAccept = async (suggestion) => {
    const taskId = suggestion.task_id;
    const template = {
      task_action: "send_quote",
      quote_total_myr: 1200,
      value_myr: 1200,
      human_acknowledged: true,
    };
    const result = await callBrain("suggest/choice", {
      task_id: taskId,
      accepted: true,
      actor: "user",
      filled_template: template,
    });
    if (result.verdict) {
      setGateResults((prev) => ({ ...prev, [taskId]: result.verdict }));
      addMessage("assistant", `Gate ${result.verdict.status}: task "${taskId}" ${result.verdict.executable ? "executable" : "blocked"}.`);
    } else {
      addMessage("assistant", `✓ Task "${taskId}" accepted and logged.`);
    }
  };

  const handleDismiss = async (taskId) => {
    await callBrain("suggest/choice", { task_id: taskId, accepted: false, actor: "user" });
  };

  return (
    <AppShell loading={loading}>
    <div className="flex flex-col min-h-[calc(100vh-4rem)] bg-gray-900 text-white rounded-lg overflow-hidden">
      {/* Header */}
      <div className="border-b border-gray-800 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">DMS Brain</h1>
          <p className="text-gray-500 text-xs mt-0.5">Governed AI · All actions audited · PII protected</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-emerald-400 text-xs">Live</span>
        </div>
      </div>

      {/* Quick commands */}
      <div className="px-6 pt-4 flex flex-wrap gap-2">
        {QUICK_COMMANDS.map((cmd) => (
          <button
            key={cmd.label}
            onClick={() => handleQuick(cmd)}
            disabled={loading}
            className="text-xs px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 hover:border-indigo-500 text-gray-300 hover:text-white rounded-full transition disabled:opacity-50"
          >
            {cmd.label}
          </button>
        ))}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.map((msg, i) => (
          <div key={msg.id || i} className={`${msg.role === "user" ? "flex justify-end" : ""}`}>
            {msg.role === "user" ? (
              <div className="bg-indigo-600 text-white px-4 py-2 rounded-2xl rounded-tr-sm max-w-md text-sm">{msg.text}</div>
            ) : msg.role === "system" ? (
              <p className="text-gray-600 text-xs text-center">{msg.text}</p>
            ) : (
              <div className="max-w-2xl w-full">
                <p className="text-gray-300 text-sm mb-1">{msg.text}</p>

                {/* Chart */}
                {msg.dataType === "chart" && msg.data && <ChartRenderer config={msg.data} />}

                {/* CSV */}
                {msg.dataType === "csv" && msg.data && (
                  <div className="bg-gray-800 rounded-lg p-3 mt-2">
                    <p className="text-gray-400 text-xs mb-2">{msg.data.summary}</p>
                    <p className="text-gray-500 text-xs mb-3">Columns: {msg.data.columns?.join(", ")}</p>
                    <button
                      onClick={() => downloadCSV(msg.data.filename, msg.data.csv_content)}
                      className="text-sm px-4 py-2 bg-emerald-700 hover:bg-emerald-600 text-white rounded-lg transition flex items-center gap-2"
                    >
                      ⬇ Download {msg.data.filename}
                    </button>
                  </div>
                )}

                {/* Email / WhatsApp */}
                {(msg.dataType === "email" || msg.dataType === "whatsapp") && msg.data && (
                  <DraftCard data={msg.data} type={msg.dataType} />
                )}

                {/* Analysis / CEO report */}
                {msg.dataType === "analysis" && msg.data && <AnalysisCard data={msg.data} />}

                {/* Markdown report */}
                {msg.dataType === "report" && msg.data && (
                  <div className="bg-gray-800 rounded-lg p-4 mt-2">
                    <p className="text-gray-300 text-sm mb-3">{msg.data.summary}</p>
                    <pre className="text-gray-400 text-xs whitespace-pre-wrap font-mono max-h-64 overflow-y-auto">
                      {msg.data.markdown?.slice(0, 1000)}{msg.data.markdown?.length > 1000 ? "\n…(truncated)" : ""}
                    </pre>
                    <button
                      onClick={() => downloadMarkdown(msg.data.suggested_filename || "report.md", msg.data.markdown || "")}
                      className="mt-3 text-sm px-4 py-2 bg-indigo-700 hover:bg-indigo-600 text-white rounded-lg transition"
                    >
                      ⬇ Download Report
                    </button>
                  </div>
                )}

                {/* Task suggestions */}
                {msg.dataType === "suggestions" && Array.isArray(msg.data) && (
                  <SuggestionsCard suggestions={msg.data} onAccept={handleAccept} onDismiss={handleDismiss} gateResults={gateResults} />
                )}
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="flex items-center gap-2 text-gray-500">
            <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce" style={{ animationDelay: "0ms" }} />
            <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce" style={{ animationDelay: "150ms" }} />
            <div className="w-1.5 h-1.5 rounded-full bg-indigo-500 animate-bounce" style={{ animationDelay: "300ms" }} />
            <span className="text-xs">Brain processing…</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="border-t border-gray-800 px-6 py-4">
        <div className="flex gap-3">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleCustom()}
            placeholder="Ask anything — 'chart slow movers', 'email CEO summary', 'export low stock'…"
            className="flex-1 bg-gray-800 border border-gray-700 text-white placeholder-gray-600 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-indigo-500 transition"
            disabled={loading}
          />
          <button
            onClick={handleCustom}
            disabled={loading || !input.trim()}
            className="px-5 py-3 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white rounded-xl text-sm font-medium transition"
          >
            Run
          </button>
        </div>
        <p className="text-gray-600 text-xs mt-2 text-center">
          All requests are PII-filtered · Every action is ledger-audited · Drafts require human approval
        </p>
      </div>
    </div>
    </AppShell>
  );
}
