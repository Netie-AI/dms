/** Studio -> Chat handoff. Objects are tools-only; free-text on the object abstains. */

export type ObjectAskHandoff = {
  groundedTables: string[];
  groundedLabels: string[];
  askTool?: string;
  askNonce?: string;
};

let lastAskNonce = "";

export function consumeObjectAskHandoff(state: unknown): ObjectAskHandoff | null {
  if (!state || typeof state !== "object") return null;
  const s = state as Record<string, unknown>;
  const tables = s.groundedTables;
  if (!Array.isArray(tables) || tables.length === 0) return null;
  const groundedTables = tables.map((t) => String(t)).filter(Boolean);
  if (!groundedTables.length) return null;
  const labels = Array.isArray(s.groundedLabels)
    ? s.groundedLabels.map((t) => String(t))
    : [];
  const raw = typeof s.askTool === "string" ? s.askTool.trim() : "";
  const nonce = typeof s.askNonce === "string" ? s.askNonce.trim() : "";
  return {
    groundedTables,
    groundedLabels: labels,
    askTool: raw || undefined,
    askNonce: nonce || undefined,
  };
}

export function objectAskNavigateState(
  objName: string,
  toolName: string,
): ObjectAskHandoff {
  const name = objName.trim();
  const tool = toolName.trim();
  return {
    groundedTables: [`objects.${name}`],
    groundedLabels: [name],
    askTool: tool || undefined,
    askNonce: `${Date.now()}`,
  };
}

/** Strict Mode remounts Chat with the same router state; ask the tool once per click. */
export function takeAskTool(handoff: ObjectAskHandoff | null): string | undefined {
  const tool = handoff?.askTool;
  if (!tool) return undefined;
  const nonce = handoff.askNonce || `${handoff.groundedTables.join(",")}|${tool}`;
  if (nonce === lastAskNonce) return undefined;
  lastAskNonce = nonce;
  return tool;
}