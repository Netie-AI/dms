import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ResultView, type StudioAskEnvelope } from "./ResultView";
import {
  AdjustSqlEditorView,
  adjustFailureCode,
  fetchAdjustEnabled,
  postAdjustRun,
  postAdjustSave,
} from "./AdjustSqlPanel";

type AnyProps = Record<string, unknown> & { children?: ReactNode };

function find(node: ReactNode, testId: string): ReactElement<AnyProps> | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const hit = find(child, testId);
      if (hit) return hit;
    }
    return null;
  }
  if (!isValidElement(node)) return null;
  const el = node as ReactElement<AnyProps>;
  if (typeof el.type === "function") {
    return find((el.type as (props: AnyProps) => ReactNode)(el.props), testId);
  }
  if (el.props["data-testid"] === testId) return el;
  return find(el.props.children, testId);
}

function envelope(): StudioAskEnvelope {
  return {
    badge: "L2_VALIDATED",
    abstained: false,
    text: "Two widgets.",
    sql_used: "SELECT qty FROM widgets",
    rows: [{ qty: 2 }],
  };
}

describe("Adjust SQL panel", () => {
  it("flag off result view has no button and no textarea", () => {
    const plain = renderToStaticMarkup(createElement(ResultView, { envelope: envelope() }));
    const viewer = renderToStaticMarkup(
      createElement(ResultView, { envelope: envelope(), role: "viewer", question: "how many" }),
    );
    const steward = renderToStaticMarkup(
      createElement(ResultView, { envelope: envelope(), role: "steward", question: "how many" }),
    );
    for (const markup of [plain, viewer, steward]) {
      expect(markup).not.toContain("studio-adjust-open");
      expect(markup).not.toContain("<textarea");
    }
    expect(viewer).toBe(plain);
  });

  it("a steward with the flag on sees Adjust SQL, and a closed panel has no textarea", () => {
    const markup = renderToStaticMarkup(
      createElement(AdjustSqlEditorView, {
        question: "how many widgets",
        open: false,
        draft: "SELECT qty FROM widgets",
        running: false,
        saving: false,
        ranSql: null,
        rows: null,
        error: null,
        savedId: null,
        onOpen: () => undefined,
        onDraft: () => undefined,
        onRun: () => undefined,
        onSave: () => undefined,
      }),
    );
    expect(markup).toContain('data-testid="studio-adjust-open"');
    expect(markup).toContain("Adjust SQL");
    expect(markup).not.toContain("<textarea");
  });

  it("open panel runs the draft and saves only after that run", () => {
    const run = vi.fn();
    const save = vi.fn();
    const tree = AdjustSqlEditorView({
      question: "how many widgets",
      open: true,
      draft: "SELECT qty FROM widgets",
      running: false,
      saving: false,
      ranSql: "SELECT qty FROM widgets",
      rows: { rows: [{ qty: 2 }], rowCount: 1, truncated: false },
      error: null,
      savedId: null,
      onOpen: () => undefined,
      onDraft: () => undefined,
      onRun: run,
      onSave: save,
    });
    const html = renderToStaticMarkup(createElement(() => tree));
    expect(html).toContain('data-testid="studio-adjust-sql"');
    expect(html).toContain("Save as verified for this question");
    const runButton = find(tree, "studio-adjust-run");
    const saveButton = find(tree, "studio-adjust-save");
    expect(runButton).not.toBeNull();
    expect(saveButton).not.toBeNull();
    (runButton?.props.onClick as () => void)();
    (saveButton?.props.onClick as () => void)();
    expect(run).toHaveBeenCalledOnce();
    expect(save).toHaveBeenCalledOnce();
  });

  it("save stays disabled until the current draft has run", () => {
    const tree = AdjustSqlEditorView({
      question: "how many widgets",
      open: true,
      draft: "SELECT qty FROM widgets",
      running: false,
      saving: false,
      ranSql: null,
      rows: null,
      error: "multi_statement",
      savedId: null,
      onOpen: () => undefined,
      onDraft: () => undefined,
      onRun: () => undefined,
      onSave: () => undefined,
    });
    const saveButton = find(tree, "studio-adjust-save");
    expect(saveButton?.props.disabled).toBe(true);
    expect(renderToStaticMarkup(createElement(() => tree))).toContain("multi_statement");
  });

  it("a status 404 is not enabled, and a refusal code is shown as that code", async () => {
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn(async () => new Response("missing", { status: 404 })) as typeof fetch;
    expect(await fetchAdjustEnabled()).toBe(false);
    expect(adjustFailureCode({ detail: "ungranted" })).toBe("ungranted");
    expect(adjustFailureCode({ detail: "SELECT secret FROM widgets" })).toBe("adjust_failed");
    globalThis.fetch = original;
  });

  it("run and save post the sql and the question, and keep a bad body as a code", async () => {
    const calls: { url: string; body: string }[] = [];
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), body: String(init?.body ?? "") });
      if (String(input).endsWith("/run")) {
        return new Response(JSON.stringify({ rows: [{ qty: 2 }], row_count: 1, truncated: false }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ detail: "not a code with spaces and a value" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch;
    const ran = await postAdjustRun("space-adjust-01", "SELECT qty FROM widgets");
    const saved = await postAdjustSave("space-adjust-01", "how many widgets", "SELECT qty FROM widgets");
    expect(ran.ok).toBe(true);
    expect(saved).toEqual({ ok: false, reason: "adjust_failed" });
    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/studio/adjust/run",
      "/api/v1/studio/adjust/save",
    ]);
    expect(JSON.parse(calls[1].body)).toEqual({
      space_id: "space-adjust-01",
      question: "how many widgets",
      sql: "SELECT qty FROM widgets",
    });
  });
});
