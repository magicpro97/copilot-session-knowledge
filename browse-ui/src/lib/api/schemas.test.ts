import { describe, expect, it } from "vitest";

import {
  communitiesResponseSchema,
  evidenceGraphResponseSchema,
  compareResponseSchema,
  evalResponseSchema,
  trendScoutStatusResponseSchema,
  trendScoutDiscoveryLaneSchema,
  syncStatusResponseSchema,
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

  it("parses sync diagnostics status response", () => {
    const parsed = syncStatusResponseSchema.parse({
      status: "pending",
      configured: true,
      connection: {
        configured: true,
        endpoint: "https://sync.local",
        config_path: "/home/user/.copilot/tools/sync-config.json",
      },
      runtime: {
        generated_at: "2026-01-01T00:00:00Z",
        db_path: "/home/user/.copilot/session-state/knowledge.db",
        db_mode: "file",
        sync_tables: {
          sync_state: true,
          sync_txns: true,
        },
        sync_tables_ready: false,
        available_sync_tables: 2,
        total_sync_tables: 5,
        failed_txns: 0,
      },
      operator_actions: [
        {
          id: "sync-status-json",
          title: "Local sync runtime snapshot",
          description: "Inspect queue + gateway health without mutating state.",
          command: "python3 sync-status.py --json",
          safe: true,
          requires_configured_gateway: false,
        },
      ],
      local_replica_id: "local",
      pending_txns: 2,
      pending_ops: 4,
      committed_txns: 10,
      failed_txns: 0,
      failed_ops: 1,
      cursor_count: 1,
      last_committed_at: "2026-01-01T00:00:00Z",
      last_failure: {
        failed_at: "2026-01-01T00:10:00Z",
        error_message: "timeout",
        retry_count: 2,
      },
    });

    expect(parsed.connection.endpoint).toBe("https://sync.local");
    expect(parsed.runtime.db_mode).toBe("file");
    expect(parsed.operator_actions[0].safe).toBe(true);
    expect(parsed.failed_ops).toBe(1);
  });

  it("parses trend scout diagnostics status response", () => {
    const parsed = trendScoutStatusResponseSchema.parse({
      status: "grace-window",
      configured: true,
      config: {
        configured: true,
        config_path: "/home/user/repo/trend-scout-config.json",
        script_path: "/home/user/repo/trend-scout.py",
        target_repo: "magicpro97/copilot-session-knowledge",
      },
      analysis: {
        enabled: false,
        model: "openai/gpt-4o-mini",
        token_env: "GITHUB_MODELS_TOKEN",
        token_present: false,
      },
      grace_window: {
        enabled: true,
        grace_window_hours: 20,
        state_file: "/home/user/repo/.trend-scout-state.json",
        state_file_exists: true,
        last_run_utc: "2026-01-01T00:00:00+00:00",
        elapsed_hours: 3.5,
        remaining_hours: 16.5,
        would_skip_without_force: true,
        reason: "last run 3.5h ago, grace window 20h (16.5h remaining)",
      },
      audit: {
        summary: {
          ok: false,
          total_checks: 5,
          warning_checks: 1,
        },
        checks: [
          {
            id: "analysis-token",
            title: "Analysis token availability",
            status: "warning",
            detail: "analysis enabled but env GITHUB_MODELS_TOKEN is not set",
          },
          {
            id: "lanes-configured",
            title: "Discovery lanes configured",
            status: "ok",
            detail: "2 lane(s): primary, adjacent-ai-dev",
          },
        ],
      },
      operator_actions: [
        {
          id: "trend-scout-dry-run",
          title: "Dry-run full pipeline preview",
          description: "Preview enrichment + rendering outcomes without creating/updating issues.",
          command: "python3 trend-scout.py --dry-run --limit 5",
          safe: true,
          requires_configured_target: true,
        },
      ],
      discovery_lanes: [
        {
          name: "primary",
          keyword_count: 6,
          topic_count: 6,
          language: "python",
          min_stars: 5,
        },
        {
          name: "adjacent-ai-dev",
          keyword_count: 4,
          topic_count: 6,
          language: null,
          min_stars: 2,
        },
      ],
      runtime: {
        generated_at: "2026-01-01T01:00:00Z",
      },
    });

    expect(parsed.config.target_repo).toBe("magicpro97/copilot-session-knowledge");
    expect(parsed.grace_window.would_skip_without_force).toBe(true);
    expect(parsed.operator_actions[0].safe).toBe(true);
    expect(parsed.discovery_lanes).toHaveLength(2);
    expect(parsed.discovery_lanes?.[0].name).toBe("primary");
    expect(parsed.discovery_lanes?.[1].language).toBeNull();
  });

  it("parses trend scout status response without discovery_lanes (pre-multi-lane compat)", () => {
    const parsed = trendScoutStatusResponseSchema.parse({
      status: "ready",
      configured: true,
      config: {
        configured: true,
        config_path: "/home/user/repo/trend-scout-config.json",
        script_path: "/home/user/repo/trend-scout.py",
        target_repo: "magicpro97/copilot-session-knowledge",
      },
      analysis: {
        enabled: false,
        model: "openai/gpt-4o-mini",
        token_env: "GITHUB_MODELS_TOKEN",
        token_present: false,
      },
      grace_window: {
        enabled: false,
        grace_window_hours: 0,
        state_file: "/home/user/repo/.trend-scout-state.json",
        state_file_exists: false,
        last_run_utc: null,
        elapsed_hours: null,
        remaining_hours: null,
        would_skip_without_force: false,
        reason: null,
      },
      audit: {
        summary: { ok: true, total_checks: 4, warning_checks: 0 },
        checks: [],
      },
      operator_actions: [],
      runtime: { generated_at: "2026-01-01T01:00:00Z" },
    });

    expect(parsed.discovery_lanes).toBeUndefined();
    expect(parsed.status).toBe("ready");
  });

  it("parses a single discovery lane", () => {
    const lane = trendScoutDiscoveryLaneSchema.parse({
      name: "adjacent-ai-dev",
      keyword_count: 4,
      topic_count: 6,
      language: null,
      min_stars: 2,
    });
    expect(lane.name).toBe("adjacent-ai-dev");
    expect(lane.language).toBeNull();
    expect(lane.keyword_count).toBe(4);
  });
});
