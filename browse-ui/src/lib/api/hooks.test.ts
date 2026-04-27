import { describe, expect, it } from "vitest";

import {
  createArrayQueryString,
  createQueryString,
  normalizeSessionsResponse,
  queryKeys,
} from "@/lib/api/hooks";

describe("api hooks helpers", () => {
  it("builds query strings while skipping empty values", () => {
    expect(createQueryString({ page: 1, q: "abc", source: null })).toBe(
      "?page=1&q=abc"
    );
    expect(createQueryString({ q: "", page: undefined })).toBe("");
  });

  it("serializes search filters with backend parameter names", () => {
    expect(
      createArrayQueryString({
        q: "abc",
        src: ["copilot", "claude"],
        kind: ["pattern"],
        in: ["title", "content"],
      })
    ).toBe("?q=abc&src=copilot%2Cclaude&kind=pattern&in=title%2Ccontent");
  });

  it("normalizes legacy array sessions response", () => {
    const normalized = normalizeSessionsResponse([
      {
        id: "abc",
        path: null,
        summary: null,
        source: "copilot",
        event_count_estimate: 1,
        fts_indexed_at: null,
      },
    ]);

    expect(normalized.items).toHaveLength(1);
    expect(normalized.total).toBe(1);
    expect(normalized.page).toBe(1);
  });

  it("preserves envelope sessions response", () => {
    const normalized = normalizeSessionsResponse({
      items: [],
      total: 0,
      page: 2,
      page_size: 50,
      has_more: false,
    });

    expect(normalized.page).toBe(2);
    expect(normalized.page_size).toBe(50);
  });

  it("builds stable query keys", () => {
    expect(queryKeys.sessionDetail("abc")).toEqual(["session-detail", "abc"]);
    expect(queryKeys.health()).toEqual(["health"]);
    expect(queryKeys.syncStatus()).toEqual(["sync-status"]);
    expect(queryKeys.graph({ wing: ["alpha"], limit: 10 })).toEqual([
      "graph",
      { wing: ["alpha"], limit: 10 },
    ]);
    expect(queryKeys.graphLegacy({ wing: ["alpha"], limit: 10 })).toEqual([
      "graph-legacy",
      { wing: ["alpha"], limit: 10 },
    ]);
    expect(queryKeys.graph({ wing: ["alpha"], limit: 10 })).not.toEqual(
      queryKeys.graphLegacy({ wing: ["alpha"], limit: 10 })
    );
  });
});
