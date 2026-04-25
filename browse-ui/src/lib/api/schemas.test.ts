import { describe, expect, it } from "vitest";

import {
  compareResponseSchema,
  evalResponseSchema,
  searchResponseSchema,
  sessionListResponseSchema,
} from "@/lib/api/schemas";

describe("api schemas", () => {
  it("parses a valid session list response", () => {
    const parsed = sessionListResponseSchema.parse({
      items: [
        {
          id: "abc123",
          path: null,
          summary: "test",
          source: "copilot",
          event_count_estimate: 10,
          fts_indexed_at: "2025-01-01T00:00:00Z",
          indexed_at_r: null,
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
      has_more: false,
    });

    expect(parsed.total).toBe(1);
    expect(parsed.items[0].id).toBe("abc123");
  });

  it("validates search response shape", () => {
    const parsed = searchResponseSchema.parse({
      query: "test",
      total: 1,
      took_ms: 3,
      results: [
        {
          type: "session",
          id: "abc123",
          title: "Result title",
          score: 1,
          snippet: "Matched text",
        },
      ],
    });
    expect(parsed.results).toHaveLength(1);
  });

  it("rejects invalid eval verdict values", () => {
    expect(() =>
      evalResponseSchema.parse({
        aggregation: [],
        recent_comments: [
          {
            query: "q",
            result_id: "1",
            verdict: 2,
            comment: "bad",
            created_at: "2025-01-01T00:00:00Z",
          },
        ],
      })
    ).toThrow();
  });

  it("parses compare response", () => {
    const parsed = compareResponseSchema.parse({
      a: {
        session: null,
        timeline: [],
      },
      b: {
        session: null,
        timeline: [],
      },
    });
    expect(parsed.a.timeline).toEqual([]);
  });
});
