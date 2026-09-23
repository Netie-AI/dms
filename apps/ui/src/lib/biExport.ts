/** INSIGHTS-EXPORT-02 — Power BI / Superset stubs from a real ask envelope. */

export type BiTarget = "powerbi" | "superset";

export type BiTargetStub = {
  status: string;
  live_connector: boolean;
  blocked: string[];
  needs_you: string[];
  filename: string;
  power_query_m?: string;
  dataset?: Record<string, unknown>;
};

export type BiExportResponse = {
  ok: boolean;
  complete: boolean;
  live_connector: boolean;
  answer_id: string;
  badge: string;
  abstained?: boolean;
  row_count: number;
  columns: string[];
  source_table: string;
  table: Record<string, unknown>[];
  targets: Partial<Record<BiTarget, BiTargetStub>>;
};

export function biStub(payload: BiExportResponse, target: BiTarget): BiTargetStub | null {
  return payload.targets[target] ?? null;
}

export function biCopyText(stub: BiTargetStub): string {
  if (stub.power_query_m) return stub.power_query_m;
  if (stub.dataset) return JSON.stringify(stub.dataset, null, 2);
  return stub.needs_you.join("\n");
}

export function biPanelHeadline(payload: BiExportResponse, target: BiTarget): string {
  const stub = biStub(payload, target);
  const blocked = stub && stub.live_connector === false;
  if (!blocked) {
    return "Envelope stub -- live connector is not shipped.";
  }
  if (target === "powerbi") {
    return "Power BI from this ask envelope (stub -- NEEDS-YOU for Desktop / live ODBC).";
  }
  return "Superset from this ask envelope (stub -- not DMS chrome; URI omitted).";
}
