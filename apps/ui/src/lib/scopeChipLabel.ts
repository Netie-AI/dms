/** Scope chip copy — shared by ScopeChip and vitest (SPACE-UI / RAG-04). */

export function scopeChipLabel(
  spaceName: string | null | undefined,
  sourceCount: number | null | undefined,
): string {
  if (!spaceName) {
    return "Asking: Company (default ACL)";
  }
  const n = sourceCount ?? 0;
  return `Asking: ${spaceName} · ${n} source${n === 1 ? "" : "s"}`;
}
