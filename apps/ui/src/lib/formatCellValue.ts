/** Laptop-ASCII cell text — never ``[object Object]`` for structs / arrays. */

function formatSrcList(items: unknown[]): string {
  return items
    .map((item) => {
      if (item && typeof item === "object" && !Array.isArray(item)) {
        const o = item as Record<string, unknown>;
        const ref = o.ref_id ?? o.ref ?? "";
        const row = o.row ?? o.line ?? "";
        if (ref !== "" && row !== "") return `${ref}:${row}`;
      }
      return formatCellValue(item);
    })
    .join(", ");
}

export function formatCellValue(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "number") {
    return Number.isFinite(v)
      ? v.toLocaleString("en-MY", { maximumFractionDigits: 4 })
      : String(v);
  }
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "string") return v;
  if (Array.isArray(v)) {
    if (!v.length) return "[]";
    if (
      v.every(
        (item) =>
          item &&
          typeof item === "object" &&
          !Array.isArray(item) &&
          ("ref_id" in item || "ref" in item),
      )
    ) {
      return formatSrcList(v);
    }
    return v.map((item) => formatCellValue(item)).join(", ");
  }
  if (typeof v === "object") {
    try {
      return JSON.stringify(v);
    } catch {
      return "[unserializable]";
    }
  }
  return String(v);
}
