/** Studio copy for extract-link verify(). The API already measured; this is what a person sees. */

export type SqlLinkRow = {
  name: string;
  from: string;
  to: string;
  cardinality: string;
};

export type SqlLinkViolation = {
  check: string;
  subject: string;
  detail: string;
};

export type SqlLinkReport = {
  verified: boolean;
  measured: boolean;
  reason?: string;
  capped_tables?: string[];
  links?: SqlLinkRow[];
  violations?: SqlLinkViolation[];
};

export function sqlLinksLabel(report: SqlLinkReport | null | undefined): string {
  if (!report) return "link check missing from this receipt";
  if (!report.measured) {
    return report.reason || "links not measured";
  }
  const n = report.links?.length ?? 0;
  const v = report.violations?.length ?? 0;
  const capped = report.capped_tables ?? [];
  const capBit = capped.length ? ` Cap hit: ${capped.join(", ")}.` : "";
  if (report.verified) return `${n} link(s) verified.${capBit}`;
  if (v) {
    return `${n} link(s) measured, ${v} violation(s). Joins stay unverified.${capBit}`;
  }
  return `${n} link(s) measured, not verified.${capBit}`;
}