/** Excel 365 Copilot prompt pack — trial after importing DMS CSV (architecture: Excel = working surface, not SoT). */

/** Full text + MCP playbook: docs/EXCEL_COPILOT_GOVERNED_PROMPT.md */
export const COPILOT_SYSTEM = [
  "You are a charting assistant for a DMS certified envelope export.",
  "Chart only numbers that already appear in the active sheet.",
  "Do not invent categories, ranks, shares, or totals.",
  "Value axis minimum must be 0. Confirm =SUM(measure) equals the envelope total.",
  "Visualization only. Do not write back to source workbooks, DuckDB, warehouse, or DMS API.",
  "If a value is missing from the sheet, say so and stop.",
].join(" ");

export const COPILOT_PROMPTS = [
  "Check this table for blank headers, duplicate IDs, missing values, and inconsistent date or currency types. Report counts and affected columns.",
  "Summarize total revenue, order count, and average order value by any Region/category column present. Reconcile grand totals with the source table. Cite the sheet cells.",
  "Create a month-by-month trend if a date column exists. Identify the three largest changes and cite months and values.",
  "Find the top five rows by the primary money/quantity column, showing share of total. Rank must match a SUM of the same column.",
  "Flag rows where Quantity is negative, Unit Price is zero, or Revenue differs from Quantity × Unit Price. Do not change the data.",
  "Create a PivotTable with a useful row/column split and Revenue (or the main measure) as values. Add a clear title. Totals must match the CSV.",
  "Compare the latest complete period with the previous period. Show absolute and percentage changes from the sheet only.",
  "List three decision-useful findings. For every finding, provide exact supporting values and avoid causal claims not in the data.",
] as const;

export function copilotClipboard(prompt: string): string {
  return `${COPILOT_SYSTEM}\n\n${prompt}`;
}

export function promptPackGuards(text: string): { noInvent: boolean; noWrite: boolean; cite: boolean } {
  const l = text.toLowerCase();
  return {
    noInvent: l.includes("do not invent") || l.includes("not in the data") || l.includes("not in the table"),
    noWrite: l.includes("do not write") || l.includes("do not change"),
    cite: l.includes("cite") || l.includes("supporting values") || l.includes("reconcile"),
  };
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}
