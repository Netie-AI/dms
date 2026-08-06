/** True when origin_uri is an absolute filesystem path (not http/duckdb/…). */

export function isFilesystemOriginUri(uri: string | null | undefined): boolean {
  const p = (uri || "").trim();
  if (!p) return false;
  if (/^(https?:|duckdb:|s3:|azure:)/i.test(p)) return false;
  if (p.startsWith("\\\\") || p.startsWith("/")) return true;
  return /^[A-Za-z]:[\\/]/.test(p);
}
