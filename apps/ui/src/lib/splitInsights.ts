/** Cortex INS-01 appends ``Insights:\\n- …``; split so bullets render as a list. */
export function splitInsights(text: string): { prose: string; insights: string[] } {
  const m = text.match(/\n\nInsights:\n/);
  if (!m || m.index == null) return { prose: text, insights: [] };
  const prose = text.slice(0, m.index).trimEnd();
  const block = text.slice(m.index + m[0].length);
  const insights = block
    .split("\n")
    .map((line) => line.replace(/^\s*[-*]\s+/, "").trim())
    .filter(Boolean);
  return { prose, insights };
}
