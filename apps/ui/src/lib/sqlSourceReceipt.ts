/** Steward-readable copy for a SQL extract receipt (SQLSRC-09).
 *
 * Wording lives here so vitest can pin it. The panel must not invent a
 * second status: cardinality and violations are what POST /v1/studio/sources/sql
 * already measured (#156 / #157).
 */

import type {
  SqlSourceKind,
  SqlSourceLink,
  SqlSourceLinks,
  SqlSourcePull,
  SqlSourceReceipt,
  SqlSourceRequest,
  SqlSourceViolation,
} from "./api";

export function parseTableList(raw: string): string[] | undefined {
  const names = raw
    .split(/[,;\n]+/)
    .map((s) => s.trim())
    .filter(Boolean);
  return names.length ? names : undefined;
}

export function parseOptionalPositiveInt(raw: string, label: string): number | undefined {
  const t = raw.trim();
  if (!t) return undefined;
  const n = Number(t);
  if (!Number.isInteger(n) || n < 1) {
    throw new Error(`${label} must be a positive integer`);
  }
  return n;
}

export function defaultPortHint(kind: SqlSourceKind): string {
  return kind === "mysql" ? "3306" : "1433";
}

export function truncatedLabel(pull: SqlSourcePull): string {
  if (!pull.truncated) return `${pull.row_count} rows landed`;
  return `${pull.row_count} rows landed, capped at max_rows (incomplete)`;
}

export function extractHeadline(receipt: SqlSourceReceipt): string {
  const stamp = receipt.tables[0]?.extracted_at;
  const when = stamp ? ` Extracted at ${stamp}.` : "";
  const failed = unusableLinks(receipt.links);
  if (failed.length === 0) {
    return `Extracted ${receipt.tables.length} table${receipt.tables.length === 1 ? "" : "s"} from ${receipt.source}.${when}`;
  }
  const names = failed.map((l) => l.name).join(", ");
  return (
    `Extracted ${receipt.tables.length} table${receipt.tables.length === 1 ? "" : "s"} from ${receipt.source}.${when}` +
    ` Link${failed.length === 1 ? "" : "s"} unusable: ${names}. Rows still landed.`
  );
}

export function unusableLinks(links: SqlSourceLinks): SqlSourceLink[] {
  return links.links.filter(
    (l) => l.cardinality !== "many_to_one",
  );
}

export function cardinalitySentence(link: SqlSourceLink): string {
  const hop = `${link.from} (${link.from_columns.join(", ")}) -> ${link.to} (${link.to_columns.join(", ")})`;
  if (link.cardinality === "many_to_one") {
    return `${link.name}: many-to-one ${hop}. Safe to group through; a child row matches one parent.`;
  }
  if (link.cardinality === "many_to_many") {
    const fan =
      link.max_fanout != null && link.max_fanout > 0 ? ` up to ${link.max_fanout}x` : "";
    return `${link.name}: fan-out${fan} ${hop}. Joining inflates measures. Unusable for grouping.`;
  }
  return `${link.name}: unverified ${hop}. The declared join did not survive measurement and is unusable.`;
}

export function violationSentence(v: SqlSourceViolation): string {
  return `${v.check} on ${v.subject}: ${v.detail}`;
}

export function linksHeadline(links: SqlSourceLinks): string {
  if (!links.measured) {
    return links.reason ?? "The source declares no foreign keys, so there is nothing to measure.";
  }
  if (links.verified) {
    return "Every declared link measured as many-to-one.";
  }
  const n = links.violations.length;
  const failed = unusableLinks(links);
  if (n > 0) {
    return `${n} violation${n === 1 ? "" : "s"} named below. Failed link${failed.length === 1 ? "" : "s"}: ${failed.map((l) => l.name).join(", ") || "see detail"}.`;
  }
  if (failed.length > 0) {
    return `Declared links measured; ${failed.map((l) => l.name).join(", ")} ${failed.length === 1 ? "is" : "are"} unusable.`;
  }
  return "Declared links measured.";
}

export function fieldsFromControls(input: {
  kind: SqlSourceKind;
  host: string;
  database: string;
  user: string;
  password: string;
  port: string;
  tables: string;
  maxRows: string;
  spaceId: string | null;
  encrypt: boolean;
  trustServerCertificate: boolean;
}): SqlSourceRequest {
  const host = input.host.trim();
  const database = input.database.trim();
  const user = input.user.trim();
  if (!host || !database || !user) {
    throw new Error("host, database, and user are required");
  }
  return {
    kind: input.kind,
    host,
    database,
    user,
    password: input.password,
    port: parseOptionalPositiveInt(input.port, "port"),
    tables: parseTableList(input.tables),
    max_rows: parseOptionalPositiveInt(input.maxRows, "max_rows"),
    space_id: input.spaceId,
    encrypt: input.encrypt,
    trust_server_certificate: input.trustServerCertificate,
  };
}
