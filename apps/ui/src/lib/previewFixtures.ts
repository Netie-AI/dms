import type { ContributingSource } from "@/lib/types";

/** Fixture sheet for U0/U1 — contributing cells highlighted. */
export type PreviewCell = {
  row: number;
  col: string;
  value: string | number;
  contributing: boolean;
};

export type PreviewSheet = {
  ref_id: string;
  title: string;
  columns: string[];
  cells: PreviewCell[];
};

const COLS = ["A", "B", "C", "D", "E", "F"];

function buildSheet(
  ref_id: string,
  title: string,
  amountCol: string,
  contributingRows: number[],
): PreviewSheet {
  const cells: PreviewCell[] = [];
  for (let row = 1; row <= 40; row++) {
    for (const col of COLS) {
      const contributing =
        contributingRows.includes(row) && col === amountCol;
      const value =
        col === amountCol
          ? contributing
            ? 1000 + row * 17
            : row * 3
          : col === "A"
            ? `SKU-${row}`
            : col === "B"
              ? row % 2 === 0
                ? "North"
                : "South"
              : `${col}${row}`;
      cells.push({ row, col, value, contributing });
    }
  }
  return { ref_id, title, columns: COLS, cells };
}

export const PREVIEW_SHEETS: Record<string, PreviewSheet> = {
  ref_q3_sales: buildSheet("ref_q3_sales", "Q3_sales_final_v2.xlsx › Data", "F", [
    2, 5, 8, 12, 15, 19, 22, 27, 31, 36,
  ]),
  ref_kl_branch: buildSheet("ref_kl_branch", "KL_branch_sales.xlsx › Sheet1", "E", [
    3, 7, 11, 18, 24,
  ]),
  ref_erp_inv: buildSheet("ref_erp_inv", "erp.public.invoices", "D", [1, 4, 9, 14, 20, 28]),
};

export function sheetForSource(src: ContributingSource): PreviewSheet {
  return (
    PREVIEW_SHEETS[src.ref_id] ??
    buildSheet(src.ref_id, src.container, "F", [2, 3, 4])
  );
}
