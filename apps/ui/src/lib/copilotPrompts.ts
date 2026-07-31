/** Excel 365 Copilot prompt pack — trial after importing DMS CSV (architecture: Excel = working surface, not SoT). */

export const COPILOT_PROMPTS = [
  "Check this table for blank headers, duplicate IDs, missing values, and inconsistent date or currency types. Report counts and affected columns.",
  "Summarize total revenue, order count, and average order value by any Region/category column present. Reconcile grand totals with the source table.",
  "Create a month-by-month trend if a date column exists. Identify the three largest changes and cite months and values.",
  "Find the top five rows by the primary money/quantity column, showing share of total.",
  "Flag rows where Quantity is negative, Unit Price is zero, or Revenue differs from Quantity × Unit Price. Do not change the data.",
  "Create a PivotTable with a useful row/column split and Revenue (or the main measure) as values. Add a clear title.",
  "Compare the latest complete period with the previous period. Show absolute and percentage changes.",
  "List three decision-useful findings. For every finding, provide exact supporting values and avoid causal claims not in the data.",
] as const;

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
