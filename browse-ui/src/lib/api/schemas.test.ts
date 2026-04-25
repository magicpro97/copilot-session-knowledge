import { describe, expect, it } from "vitest";

import {
  communitiesResponseSchema,
  evidenceGraphResponseSchema,
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

  it("accepts unknown evidence relation types from runtime data", () => {
    const parsed = evidenceGraphResponseSchema.parse({
      nodes: [
        {
          id: "n1",
          kind: "entry",
          label: "Entry 1",
          color: "#111111",
        },
      ],
      edges: [
        {
          source: "n1",
          target: "n1",
          relation_type: "CITED_WITH",
          confidence: 0.6,
        },
      ],
      truncated: false,
      meta: {
        edge_source: "knowledge_relations",
        relation_types: ["RESOLVED_BY", "CITED_WITH"],
      },
    });

    expect(parsed.edges[0].relation_type).toBe("CITED_WITH");
    expect(parsed.meta?.relation_types).toContain("CITED_WITH");
  });

  it("accepts unknown community top relation types", () => {
    const parsed = communitiesResponseSchema.parse({
      communities: [
        {
          id: "c-1",
          entry_count: 2,
          top_categories: [{ name: "pattern", count: 2 }],
          top_relation_types: [{ type: "CITED_WITH", count: 2 }],
          representative_entries: [{ id: 1, title: "x", category: "pattern" }],
        },
      ],
    });

    expect(parsed.communities[0].top_relation_types?.[0]?.type).toBe("CITED_WITH");
  });
});
