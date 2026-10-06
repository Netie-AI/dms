import type { StudioSelectionItem } from "./api";

/** STUDIO-SELECT-01 (#364). Ticked tables in tick order; a table with no
 *  column ticked is still sent so the API refuses it by name (selection_no_columns). */
export function toSelectionPayload(picks: Map<string, string[]>): StudioSelectionItem[] {
  return [...picks].map(([table, columns]) => ({ table, columns: [...columns] }));
}

/** postAsk throws "ask <status>: <body>"; keep the body for describeApiError. */
export function askErrorBody(message: string): string {
  return message.replace(/^ask \d+: /, "");
}
