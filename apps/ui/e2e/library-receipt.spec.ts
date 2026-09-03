import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
  type Page,
} from "@playwright/test";

test.describe.configure({ mode: "serial", timeout: 120_000 });

async function openCompanyLibrary(page: Page): Promise<void> {
  const spaces = page.waitForResponse(
    (r) => r.url().includes("/v1/spaces") && r.ok(),
  );
  await page.goto("/library");
  await spaces;
  await expect(page.getByRole("heading", { name: "Library", level: 1 })).toBeVisible();
  await page.getByLabel("Space").selectOption({ label: "Company (default ACL)" });
  await expect(page.getByLabel("Space")).toHaveValue("");
  await expect(page.locator("strong").filter({ hasText: "Company (default ACL)" })).toBeVisible();
}

type Receipt = {
  source_rows: number | null;
  passed: number;
  quarantined: number;
  unmatched: number;
  reconciled: boolean;
};

const HEALTHY_YAML = `
target: silver.e2e_sales
sources: [bronze.e2e_sales_raw]
lineage: propagate
contract:
  columns:
    invoice_date: {type: date, required: true}
    amount: {type: "decimal(18,2)", required: true, min: 0}
    region: {type: text, required: true}
    invoice_no: {type: text, required: true}
    line_no: {type: integer, required: true}
  dedup_key: [invoice_no, line_no]
  expectations:
    - amount_not_null_rate: ">= 0.99"
`;

const FANOUT_YAML = `
target: silver.e2e_fanout
sources: [bronze.e2e_fanout_a, bronze.e2e_fanout_b]
lineage: propagate
join_on: [invoice_no, line_no]
contract:
  columns:
    invoice_date: {type: date, required: true}
    amount: {type: "decimal(18,2)", required: true, min: 0}
    region: {type: text, required: true}
    invoice_no: {type: text, required: true}
    line_no: {type: integer, required: true}
    sku: {type: text, required: true}
  dedup_key: [invoice_no, line_no, sku]
`;

let healthy: Receipt;
let fanout: Receipt;

async function readOk(res: APIResponse, step: string): Promise<string> {
  const text = await res.text();
  if (res.status() === 403) {
    throw new Error(`${step} 403 ${text}`);
  }
  if (!res.ok()) {
    throw new Error(`${step} ${res.status()} ${text}`);
  }
  return text;
}

function salesCsv(n = 100, badFrac = 0.1): string {
  const header = "invoice_no,line_no,invoice_date,amount,region";
  const badN = Math.floor(n * badFrac);
  const rows: string[] = [];
  for (let i = 0; i < n; i++) {
    const inv = `INV-${String(Math.floor(i / 2)).padStart(4, "0")}`;
    const line = String((i % 2) + 1);
    const amount = i < badN ? "-5.00" : "10.00";
    rows.push(`${inv},${line},2026-07-01,${amount},North`);
  }
  return [header, ...rows].join("\n");
}

async function ingestCsv(
  request: APIRequestContext,
  filename: string,
  csv: string,
): Promise<void> {
  const res = await request.post("/api/v1/studio/ingest", {
    multipart: {
      file: {
        name: filename,
        mimeType: "text/csv",
        buffer: Buffer.from(csv, "utf8"),
      },
    },
  });
  const text = await readOk(res, `ingest ${filename}`);
  const body = JSON.parse(text) as { ingested?: number; files?: Array<{ reason?: string }> };
  if (!body.ingested) {
    const why = body.files?.map((f) => f.reason).join("; ") || "unknown";
    if (/being used by another process/i.test(why)) {
      throw new Error(
        `ingest ${filename} ingested 0: DMS lake locked (Cortex holding DMS_WAREHOUSE_DB). Restart Cortex on the Cortex duckdb, not the DMS file. ${why}`,
      );
    }
    throw new Error(`ingest ${filename} ingested 0: ${why}`);
  }
}

async function runYaml(request: APIRequestContext, yaml_text: string): Promise<Receipt> {
  const res = await request.post("/api/v1/pipelines/run", {
    data: { yaml_text },
  });
  const text = await readOk(res, "pipelines/run");
  return JSON.parse(text) as Receipt;
}

test.describe("Library promote receipt", () => {
  test.beforeAll(async ({ request }, testInfo) => {
    testInfo.setTimeout(120_000);
    await ingestCsv(request, "e2e_sales_raw.csv", salesCsv());
    healthy = await runYaml(request, HEALTHY_YAML);

    await ingestCsv(
      request,
      "e2e_fanout_a.csv",
      [
        "invoice_no,line_no,invoice_date,amount,region",
        "INV-1,1,2026-07-01,10.00,North",
        "INV-2,1,2026-07-02,20.00,South",
      ].join("\n"),
    );
    await ingestCsv(
      request,
      "e2e_fanout_b.csv",
      ["invoice_no,line_no,sku", "INV-1,1,SKU-A", "INV-1,1,SKU-B", "INV-2,1,SKU-C"].join(
        "\n",
      ),
    );
    fanout = await runYaml(request, FANOUT_YAML);
  });

  test("healthy node numbers match the /run receipt", async ({ page }) => {
    await openCompanyLibrary(page);
    await expect(page.getByTestId("promote-node-silver.e2e_sales")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByTestId("promote-node-silver.e2e_sales").click();
    await expect(page.getByTestId("receipt-healthy")).toBeVisible();
    await expect(page.getByTestId("receipt-defect")).toHaveCount(0);
    await expect(page.getByTestId("promote-source-rows")).toHaveText(
      String(healthy.source_rows),
    );
    await expect(page.getByTestId("promote-passed")).toHaveText(String(healthy.passed));
    await expect(page.getByTestId("promote-quarantined")).toHaveText(
      String(healthy.quarantined),
    );
    await expect(page.getByTestId("promote-unmatched")).toHaveText(
      String(healthy.unmatched),
    );
    await expect(page.getByTestId("promote-reconciled")).toHaveText(
      String(healthy.reconciled),
    );
  });

  test("fan-out node is a defect with the run's negative unmatched", async ({ page }) => {
    await openCompanyLibrary(page);
    await expect(page.getByTestId("promote-node-silver.e2e_fanout")).toBeVisible({
      timeout: 30_000,
    });
    await page.getByTestId("promote-node-silver.e2e_fanout").click();
    await expect(page.getByTestId("receipt-defect")).toBeVisible();
    await expect(page.getByTestId("receipt-healthy")).toHaveCount(0);
    await expect(page.getByTestId("receipt-defect-reason")).toHaveText(/^Join fan-out/);
    await expect(page.getByTestId("promote-unmatched")).toHaveText(
      String(fanout.unmatched),
    );
  });
});
