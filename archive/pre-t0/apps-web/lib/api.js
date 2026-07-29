const API_BASE =
  process.env.NEXT_PUBLIC_DMS_API_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8090";

const DEMO_API_KEYS = {
  ANALYST: "dms-demo-viewer-key",
  STEWARD: "dms-demo-steward-key",
  ADMIN: "dms-demo-admin-key",
};

let _roleId = "ANALYST";
let _jwt = null;

export function setApiRoleKey(roleId) {
  _roleId = roleId || "ANALYST";
}

export function setAccessToken(token) {
  _jwt = token || null;
  if (typeof window !== "undefined") {
    if (token) window.localStorage.setItem("dms_access_token", token);
    else window.localStorage.removeItem("dms_access_token");
  }
}

export function loadStoredToken() {
  if (typeof window === "undefined") return null;
  _jwt = window.localStorage.getItem("dms_access_token");
  return _jwt;
}

function authHeaders(extra = {}) {
  if (!_jwt && typeof window !== "undefined") {
    _jwt = window.localStorage.getItem("dms_access_token");
  }
  if (_jwt) {
    return { Authorization: `Bearer ${_jwt}`, ...extra };
  }
  const key = DEMO_API_KEYS[_roleId] || DEMO_API_KEYS.ANALYST;
  return { "X-API-Key": key, ...extra };
}

export class ApiOfflineError extends Error {
  constructor(message = "API offline") {
    super(message);
    this.name = "ApiOfflineError";
  }
}

async function request(path, options = {}) {
  const url = `${API_BASE}${path}`;
  let res;
  try {
    res = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new ApiOfflineError();
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    return res.json();
  }
  return res;
}

export async function checkHealth() {
  return request("/health");
}

function getQuerySessionId() {
  if (typeof window === "undefined") return "demo";
  try {
    const key = "dms_query_session_id";
    let id = window.localStorage.getItem(key);
    if (!id) {
      id =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `sess-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
      window.localStorage.setItem(key, id);
    }
    return id;
  } catch {
    return "demo";
  }
}

export async function postQuery(question, { spaceId } = {}) {
  if (spaceId) {
    return request("/dms/query-local", {
      method: "POST",
      body: JSON.stringify({ question, session_id: getQuerySessionId(), space_id: spaceId }),
      headers: { "X-Space-Id": spaceId },
    });
  }
  return request("/dms/query", {
    method: "POST",
    body: JSON.stringify({ question, session_id: getQuerySessionId() }),
  });
}

export async function loginDms({ email, password, org_slug = "default" }) {
  const res = await request("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password, org_slug }),
  });
  setAccessToken(res.access_token);
  return res;
}

export async function fetchMe() {
  return request("/auth/me");
}

export async function fetchSpaces() {
  return request("/spaces");
}

export async function createSpace(payload) {
  return request("/spaces", { method: "POST", body: JSON.stringify(payload) });
}

export async function fetchSpace(id) {
  return request(`/spaces/${encodeURIComponent(id)}`);
}

export async function checkReady() {
  return request("/ready");
}

export async function fetchTables() {
  return request("/dms/tables");
}

export async function fetchTablePreview(table, from = 0, to = 100, cols = "") {
  const params = new URLSearchParams({ table, from: String(from), to: String(to) });
  if (cols) params.set("cols", cols);
  return request(`/dms/table-preview?${params}`);
}

export async function fetchAudit() {
  return request("/dms/audit");
}

export async function proposeEdits(changes, approvedBy = "demo_steward") {
  return request("/dms/propose-edit", {
    method: "POST",
    body: JSON.stringify({ changes, approved_by: approvedBy }),
  });
}

export async function fetchData(variant, limit = 50) {
  return request(`/dms/data/${variant}?limit=${limit}`);
}

export async function analyseEntry(rawText) {
  return request("/dms/analyse-entry", {
    method: "POST",
    body: JSON.stringify({ raw_text: rawText }),
  });
}

export async function addEntry(proposed, approvedBy = "demo_steward") {
  return request("/dms/add-entry", {
    method: "POST",
    body: JSON.stringify({ proposed, approved_by: approvedBy }),
  });
}

export async function createChatThread({ customer_label, external_ref, actor = "demo_operator" } = {}) {
  return request("/dms/threads", {
    method: "POST",
    body: JSON.stringify({
      customer_label,
      external_ref,
      actor,
    }),
  });
}

export async function fetchThreadMessages(threadId) {
  return request(`/dms/threads/${threadId}/messages`);
}

export async function sendThreadMessage(threadId, { sender, body, direction = "inbound", actor = "demo_operator" }) {
  return request(`/dms/threads/${threadId}/messages`, {
    method: "POST",
    body: JSON.stringify({ sender, body, direction, actor }),
  });
}

export async function fetchWarehouseTree(tenantId = "default") {
  return request(`/dms/warehouse/locations/tree?tenant_id=${tenantId}`);
}

export async function fetchLocationSpace(locationId, tenantId = "default") {
  return request(`/dms/locations/${locationId}/space?tenant_id=${tenantId}`);
}

export function warehouseQrLabelUrl(locationId, tenantId = "default") {
  return `${API_BASE}/dms/warehouse/locations/${locationId}/qr-label?tenant_id=${tenantId}`;
}

export async function intakeItem(payload) {
  return request("/dms/items/intake", { method: "POST", body: JSON.stringify(payload) });
}

export async function scanMove(payload) {
  return request("/dms/movements/scan", { method: "POST", body: JSON.stringify(payload) });
}

export async function confirmItemDims(itemId, payload) {
  return request(`/dms/items/${itemId}/confirm-dims`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function checkTaskGate({ eventId, taskId, filledTemplate, actor = "user" }) {
  return request("/dms/tasks/gate/check", {
    method: "POST",
    body: JSON.stringify({
      event_id: eventId,
      task_id: taskId,
      filled_template: filledTemplate,
      actor,
    }),
  });
}

export async function chooseTask({ messageId, threadId, taskId, filledTemplate, intent, actor = "user", accepted = true }) {
  return request("/dms/tasks/choose", {
    method: "POST",
    body: JSON.stringify({
      message_id: messageId,
      thread_id: threadId,
      task_id: taskId,
      filled_template: filledTemplate,
      intent,
      actor,
      accepted,
    }),
  });
}

export async function acknowledgeGate({ eventId, actor = "steward" }) {
  return request("/dms/tasks/gate/acknowledge", {
    method: "POST",
    body: JSON.stringify({ event_id: eventId, actor }),
  });
}

export async function fetchTaskSuggestions({ useLlm = false, triggerText = null } = {}) {
  return request("/dms/brain/suggest", {
    method: "POST",
    body: JSON.stringify({ use_llm: useLlm, trigger_text: triggerText }),
  });
}

export async function fetchSkills(activeOnly = false) {
  return request(`/dms/skills?active_only=${activeOnly}`);
}

export async function fetchSkillCaptureConfig() {
  return request("/dms/skills/config");
}

export async function setSkillCaptureConfig(enabled) {
  return request("/dms/skills/config", {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

export async function deactivateSkill(skillId) {
  return request(`/dms/skills/${skillId}/deactivate`, {
    method: "POST",
  });
}

export async function completeTaskEvent({ eventId, outcome, triggerText }) {
  return request("/dms/skills/complete", {
    method: "POST",
    body: JSON.stringify({
      event_id: eventId,
      outcome,
      trigger_text: triggerText,
    }),
  });
}

/** Find Skills — skills-first discovery over curated GitHub refs + local cards. */
export async function findSkills(goal, { topK = 8, evolve = false } = {}) {
  return request("/api/discovery/find-skills", {
    method: "POST",
    body: JSON.stringify({ goal, top_k: topK, evolve }),
  });
}

export async function findMcp(goal, { topK = 8 } = {}) {
  return request("/api/discovery/find-mcp", {
    method: "POST",
    body: JSON.stringify({ goal, top_k: topK }),
  });
}

export async function findSubagents(goal, { topK = 8 } = {}) {
  return request("/api/discovery/find-subagents", {
    method: "POST",
    body: JSON.stringify({ goal, top_k: topK }),
  });
}

export async function fetchDiscoverySources() {
  return request("/api/discovery/sources");
}

// ── Lakehouse / Studio (medallion) ──────────────────────────────────────────

export async function fetchLakehouseStatus() {
  return request("/dms/lakehouse/status");
}

export async function fetchLakehouseTables() {
  return request("/dms/lakehouse/tables");
}

export async function previewLakeTable(schema, name, limit = 50) {
  return request(`/dms/lakehouse/tables/${encodeURIComponent(schema)}/${encodeURIComponent(name)}?limit=${limit}`);
}

export async function fetchLakeSnapshots(schema, name) {
  return request(
    `/dms/lakehouse/tables/${encodeURIComponent(schema)}/${encodeURIComponent(name)}/snapshots`
  );
}

export async function lakehouseQuery(sql) {
  return request("/dms/lakehouse/query", {
    method: "POST",
    body: JSON.stringify({ sql }),
  });
}

export async function ingestFileBase64(filename, contentB64) {
  return request("/dms/ingest/file", {
    method: "POST",
    body: JSON.stringify({ filename, content_b64: contentB64 }),
  });
}

export async function fetchIngestLedger() {
  return request("/dms/ingest/ledger");
}

export async function fetchPipelines() {
  return request("/dms/pipelines");
}

export async function runPipeline(pipelineId) {
  return request(`/dms/pipelines/${encodeURIComponent(pipelineId)}/run`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function fetchPipelineEvents(limit = 50) {
  return request(`/dms/pipelines/events?limit=${limit}`);
}

export async function fetchPipelineProposals() {
  return request("/dms/pipelines/proposals");
}

export async function syncWarehouseFromSilver() {
  return request("/dms/lakehouse/sync-warehouse", { method: "POST", body: JSON.stringify({}) });
}
