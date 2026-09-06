import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describeApiError, postSqlSource, SQL_SOURCE_PATH } from "./api";
import {
  cardinalitySentence,
  extractHeadline,
  fieldsFromControls,
  linksHeadline,
  parseTableList,
  truncatedLabel,
  violationSentence,
} from "./sqlSourceReceipt";
import type { SqlSourceReceipt } from "./api";

const SECRET = "p;w}d";
const here = dirname(fileURLToPath(import.meta.url));

afterEach(() => {
  vi.unstubAllGlobals();
});

function mockFetch(status: number, body: unknown): ReturnType<typeof vi.fn> {
  const ok = status >= 200 && status < 300;
  const fn = vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const fanoutReceipt: SqlSourceReceipt = {
  source: "sqlserver://db.example.net:1433/sales",
  tables: [
    {
      bronze_table: "bronze.dbo_orders",
      source: "sqlserver://db.example.net:1433/sales#dbo.orders",
      row_count: 2,
      truncated: false,
      extracted_at: "2026-09-06T12:00:00.000000Z",
    },
    {
      bronze_table: "bronze.dbo_customers",
      source: "sqlserver://db.example.net:1433/sales#dbo.customers",
      row_count: 2,
      truncated: true,
      extracted_at: "2026-09-06T12:00:00.000000Z",
    },
  ],
  skipped: ["secret"],
  declared_primary_keys: 2,
  declared_foreign_keys: 1,
  links: {
    verified: false,
    measured: true,
    links: [
      {
        name: "FK_Orders_Customers",
        from: "dbo.orders",
        from_columns: ["cust_ref"],
        to: "dbo.customers",
        to_columns: ["cust_ref"],
        cardinality: "many_to_many",
        max_fanout: 2,
      },
    ],
    violations: [],
  },
};

describe("postSqlSource", () => {
  it("POSTs /v1/studio/sources/sql with the route body (R-0007)", async () => {
    const fetchMock = mockFetch(200, fanoutReceipt);
    const got = await postSqlSource({
      kind: "sqlserver",
      host: "db.example.net",
      database: "sales",
      user: "reader",
      password: SECRET,
      tables: ["orders"],
      max_rows: 1,
      space_id: "sp_finance",
      encrypt: true,
      trust_server_certificate: false,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/api${SQL_SOURCE_PATH}`);
    expect(url).toBe("/api/v1/studio/sources/sql");
    expect(init.method).toBe("POST");
    const sent = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(sent).toEqual({
      kind: "sqlserver",
      host: "db.example.net",
      database: "sales",
      user: "reader",
      password: SECRET,
      tables: ["orders"],
      max_rows: 1,
      space_id: "sp_finance",
      encrypt: true,
      trust_server_certificate: false,
    });
    expect(got.tables[0].truncated).toBe(false);
    expect(got.tables[1].truncated).toBe(true);
  });

  it("fail-closed gate shows the steward sentence and never the password", async () => {
    mockFetch(403, { detail: "gate_unavailable" });
    await expect(
      postSqlSource({
        kind: "mysql",
        host: "db.example.net",
        database: "sales",
        user: "reader",
        password: SECRET,
      }),
    ).rejects.toThrow(/Start Cortex before writing/);
    try {
      await postSqlSource({
        kind: "mysql",
        host: "db.example.net",
        database: "sales",
        user: "reader",
        password: SECRET,
      });
    } catch (e) {
      expect(String(e)).not.toContain(SECRET);
    }
  });

  it("structured driver refusal does not echo the password even if the body did", async () => {
    mockFetch(502, {
      detail: {
        code: "source_unreachable",
        message: `could not connect using ${SECRET}`,
      },
    });
    await expect(
      postSqlSource({
        kind: "sqlserver",
        host: "db.example.net",
        database: "sales",
        user: "reader",
        password: SECRET,
      }),
    ).rejects.toThrow(/source_unreachable/);
    try {
      await postSqlSource({
        kind: "sqlserver",
        host: "db.example.net",
        database: "sales",
        user: "reader",
        password: SECRET,
      });
    } catch (e) {
      const text = String(e);
      expect(text).not.toContain(SECRET);
      expect(text).toContain("[redacted]");
    }
  });
});

describe("Studio SQL submit wiring (R-0007)", () => {
  it("the panel submit handler calls postSqlSource, not a second path", () => {
    const panel = readFileSync(join(here, "../components/SqlSourcePanel.tsx"), "utf8");
    expect(panel).toMatch(/await postSqlSource\(/);
    expect(panel).not.toMatch(/fetch\(/);
    const page = readFileSync(join(here, "../pages/StudioPage.tsx"), "utf8");
    expect(page).toMatch(/<SqlSourcePanel /);
  });
});

describe("sql source receipt copy", () => {
  it("names truncated tables and failed links while extract still succeeded", () => {
    expect(truncatedLabel(fanoutReceipt.tables[1])).toMatch(/capped at max_rows/);
    expect(extractHeadline(fanoutReceipt)).toMatch(/FK_Orders_Customers/);
    expect(extractHeadline(fanoutReceipt)).toMatch(/Rows still landed/);
    expect(cardinalitySentence(fanoutReceipt.links.links[0])).toMatch(/fan-out up to 2x/);
    expect(cardinalitySentence(fanoutReceipt.links.links[0])).toMatch(/Unusable for grouping/);
  });

  it("renders a cap-invented orphan in steward words", () => {
    const v = {
      check: "fk_intact",
      subject: "FK_Orders_Customers",
      detail:
        "2 orphan rows (2 distinct keys) on dbo.orders; parent dbo.customers was capped by max_rows",
    };
    expect(violationSentence(v)).toContain("max_rows");
    expect(violationSentence(v)).toContain("FK_Orders_Customers");
  });

  it("does not paint verified over an empty FK set", () => {
    expect(
      linksHeadline({
        verified: false,
        measured: false,
        reason: "the source declares no foreign keys, so there is nothing to measure",
        links: [],
        violations: [],
      }),
    ).toMatch(/no foreign keys/);
  });

  it("parses optional tables and rejects a non-integer max_rows", () => {
    expect(parseTableList(" orders, customers ")).toEqual(["orders", "customers"]);
    expect(parseTableList("")).toBeUndefined();
    expect(() =>
      fieldsFromControls({
        kind: "sqlserver",
        host: "h",
        database: "d",
        user: "u",
        password: "",
        port: "",
        tables: "",
        maxRows: "nope",
        spaceId: null,
        encrypt: true,
        trustServerCertificate: false,
      }),
    ).toThrow(/max_rows/);
  });
});

describe("describeApiError object detail", () => {
  it("uses message+code and redacts a supplied secret", () => {
    expect(
      describeApiError(
        JSON.stringify({ detail: { code: "bad_request", message: "max_rows must be >= 1" } }),
      ),
    ).toBe("bad_request: max_rows must be >= 1");
    expect(
      describeApiError(JSON.stringify({ detail: `boom ${SECRET}` }), SECRET),
    ).toBe("boom [redacted]");
  });
});
