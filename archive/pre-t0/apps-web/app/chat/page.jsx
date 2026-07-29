"use client";

import { useCallback, useEffect, useState } from "react";
import useSWR from "swr";
import AppShell from "../../components/AppShell";
import {
  ApiOfflineError,
  acknowledgeGate,
  chooseTask,
  createChatThread,
  fetchTaskSuggestions,
  fetchThreadMessages,
  sendThreadMessage,
} from "../../lib/api";

function threadLabel(thread) {
  return thread.customer_label || thread.external_ref || thread.id.slice(0, 8);
}

function taskTemplateFromSuggestion(s) {
  const action = s.action || "audit";
  const base = { task_action: action, value_myr: 500 };
  if (action === "send_quote" || s.task_id?.includes("quote")) {
    return { task_action: "send_quote", quote_total_myr: 1200, value_myr: 1200 };
  }
  if (action === "move" || s.task_id?.includes("rebalance")) {
    return { task_action: "schedule_pickup", pickup_address: "Warehouse dock A" };
  }
  return base;
}

function VerdictBanner({ verdict, onAcknowledge, busy }) {
  if (!verdict) return null;
  const colors = {
    pass: { bg: "#064e3b", border: "#10b981", label: "PASS — ready for steward execution" },
    warn: { bg: "#78350f", border: "#f59e0b", label: "WARN — acknowledgement required" },
    fail: { bg: "#7f1d1d", border: "#ef4444", label: "FAIL — blocked" },
  };
  const c = colors[verdict.status] || colors.fail;
  return (
    <div className="cx-gate-verdict" style={{ background: c.bg, borderColor: c.border }}>
      <div className="cx-mono" style={{ color: c.border, marginBottom: 6 }}>{c.label}</div>
      {verdict.violations?.length > 0 && (
        <ul className="cx-muted" style={{ fontSize: 12, margin: "4px 0" }}>
          {verdict.violations.map((v, i) => (
            <li key={i}>{v.rule_id}: {v.message}</li>
          ))}
        </ul>
      )}
      {verdict.status === "warn" && (
        <button
          type="button"
          className="cx-btn cx-btn-primary"
          style={{ marginTop: 8 }}
          disabled={busy}
          onClick={onAcknowledge}
        >
          ACKNOWLEDGE &amp; PROCEED
        </button>
      )}
    </div>
  );
}

export default function ChatPage() {
  const [threads, setThreads] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [newLabel, setNewLabel] = useState("");
  const [newRef, setNewRef] = useState("");
  const [sender, setSender] = useState("customer");
  const [body, setBody] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [suggestions, setSuggestions] = useState([]);
  const [lastMessageId, setLastMessageId] = useState(null);
  const [verdict, setVerdict] = useState(null);
  const [pendingEventId, setPendingEventId] = useState(null);

  const { data, mutate, isLoading } = useSWR(
    selectedId ? `chat-messages-${selectedId}` : null,
    () => fetchThreadMessages(selectedId),
    { refreshInterval: 5000 }
  );

  const messages = data?.messages || [];
  const selected = threads.find((t) => t.id === selectedId) || null;
  const lastInbound = [...messages].reverse().find((m) => m.direction === "inbound");

  useEffect(() => {
    if (!lastInbound) {
      setSuggestions([]);
      return;
    }
    fetchTaskSuggestions({ useLlm: false })
      .then((r) => setSuggestions(r.suggestions || []))
      .catch(() => setSuggestions([]));
  }, [lastInbound?.id]);

  const handleCreateThread = useCallback(async () => {
    setBusy(true);
    setStatus("");
    try {
      const result = await createChatThread({
        customer_label: newLabel || undefined,
        external_ref: newRef || undefined,
      });
      const thread = result.thread;
      setThreads((prev) => [thread, ...prev.filter((t) => t.id !== thread.id)]);
      setSelectedId(thread.id);
      setNewLabel("");
      setNewRef("");
      setStatus("Thread created.");
    } catch (err) {
      setStatus(err instanceof ApiOfflineError ? "API offline." : err.message);
    } finally {
      setBusy(false);
    }
  }, [newLabel, newRef]);

  const handleSend = useCallback(async () => {
    if (!selectedId || !body.trim()) return;
    setBusy(true);
    setStatus("");
    setVerdict(null);
    try {
      const result = await sendThreadMessage(selectedId, { sender, body: body.trim() });
      setBody("");
      if (result.message?.id) setLastMessageId(result.message.id);
      await mutate();
      setStatus("Message sent.");
    } catch (err) {
      setStatus(err instanceof ApiOfflineError ? "API offline." : err.message);
    } finally {
      setBusy(false);
    }
  }, [selectedId, sender, body, mutate]);

  const handleChooseTask = useCallback(async (suggestion) => {
    if (!selectedId) return;
    setBusy(true);
    setVerdict(null);
    const template = taskTemplateFromSuggestion(suggestion);
    try {
      const result = await chooseTask({
        messageId: lastMessageId || lastInbound?.id,
        threadId: selectedId,
        taskId: suggestion.task_id,
        filledTemplate: template,
        intent: suggestion.source,
        actor: "steward",
      });
      setVerdict(result.verdict);
      setPendingEventId(result.event_id);
      setStatus(`Gate: ${result.verdict?.status}`);
    } catch (err) {
      setStatus(err instanceof ApiOfflineError ? "API offline." : err.message);
    } finally {
      setBusy(false);
    }
  }, [selectedId, lastMessageId, lastInbound?.id]);

  const handleAcknowledge = useCallback(async () => {
    if (!pendingEventId) return;
    setBusy(true);
    try {
      const result = await acknowledgeGate({ eventId: pendingEventId, actor: "steward" });
      setVerdict(result.verdict);
      setStatus(`Gate: ${result.verdict?.status}`);
    } catch (err) {
      setStatus(err.message);
    } finally {
      setBusy(false);
    }
  }, [pendingEventId]);

  return (
    <AppShell loading={false}>
      <div className="cx-chat-layout">
        <section className="cx-chat-threads">
          <div className="cx-label" style={{ marginBottom: 12 }}>THREADS</div>
          <div className="cx-chat-new">
            <input className="cx-input" placeholder="Customer label" value={newLabel} onChange={(e) => setNewLabel(e.target.value)} />
            <input className="cx-input" placeholder="External ref (optional)" value={newRef} onChange={(e) => setNewRef(e.target.value)} />
            <button type="button" className="cx-btn cx-btn-primary" disabled={busy} onClick={handleCreateThread}>NEW THREAD</button>
          </div>
          <div className="cx-chat-thread-list">
            {threads.length === 0 ? (
              <p className="cx-muted">No threads yet. Create one to start.</p>
            ) : (
              threads.map((thread) => (
                <button key={thread.id} type="button" className={`cx-chat-thread-item ${selectedId === thread.id ? "active" : ""}`} onClick={() => setSelectedId(thread.id)}>
                  <span className="cx-mono">{threadLabel(thread)}</span>
                  <span className="cx-muted">{thread.status}</span>
                </button>
              ))
            )}
          </div>
        </section>

        <section className="cx-chat-pane">
          {!selected ? (
            <p className="cx-muted">Select a thread to view messages.</p>
          ) : (
            <>
              <div className="cx-label" style={{ marginBottom: 8 }}>{threadLabel(selected).toUpperCase()}</div>
              <div className="cx-chat-messages">
                {isLoading && messages.length === 0 ? (
                  <p className="cx-muted">Loading messages…</p>
                ) : messages.length === 0 ? (
                  <p className="cx-muted">No messages yet.</p>
                ) : (
                  messages.map((msg) => (
                    <div key={msg.id} className="cx-chat-message">
                      <div className="cx-chat-message-meta">
                        <span className="cx-mono">{msg.direction.toUpperCase()}</span>
                        <span className="cx-mono">{msg.sender}</span>
                        <span className="cx-muted">{msg.created_at}</span>
                      </div>
                      <pre className="cx-chat-message-body">{msg.body}</pre>
                    </div>
                  ))
                )}
              </div>

              {suggestions.length > 0 && (
                <div style={{ marginTop: 16 }}>
                  <div className="cx-label" style={{ marginBottom: 8 }}>SUGGESTED TASKS (F4)</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {suggestions.map((s) => (
                      <button key={s.task_id} type="button" className="cx-btn" disabled={busy} onClick={() => handleChooseTask(s)}>
                        {s.title}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <VerdictBanner verdict={verdict} onAcknowledge={handleAcknowledge} busy={busy} />

              <div className="cx-chat-compose">
                <input className="cx-input" placeholder="Sender" value={sender} onChange={(e) => setSender(e.target.value)} />
                <textarea className="cx-input cx-chat-textarea" placeholder="Inbound message body" value={body} onChange={(e) => setBody(e.target.value)} rows={3} />
                <button type="button" className="cx-btn cx-btn-primary" disabled={busy || !body.trim()} onClick={handleSend}>SEND INBOUND</button>
              </div>
            </>
          )}
          {status ? <p className="cx-muted" style={{ marginTop: 12 }}>{status}</p> : null}
        </section>
      </div>
    </AppShell>
  );
}
