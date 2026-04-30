import { describe, expect, it } from "vitest";

import {
  auditBlockSchema,
  auditCheckSchema,
  communitiesResponseSchema,
  evidenceGraphResponseSchema,
  compareResponseSchema,
  evalResponseSchema,
  knowledgeInsightsResponseSchema,
  trendScoutStatusResponseSchema,
  trendScoutDiscoveryLaneSchema,
  syncStatusResponseSchema,
  tentacleStatusResponseSchema,
  skillMetricsResponseSchema,
  operatorActionSchema,
  syncOperatorActionSchema,
  searchResponseSchema,
  sessionListResponseSchema,
  trendScoutOperatorActionSchema,
  timelineEventSchema,
  timelineEventsResponseSchema,
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

  // ── Shared OperatorAction contract tests ────────────────────────────────

  it("parses a minimal operator action (no route-specific fields)", () => {
    const action = operatorActionSchema.parse({
      id: "tentacle-list",
      title: "List all tentacles",
      description: "Read-only summary.",
      command: "python3 tentacle.py list",
      safe: true,
    });
    expect(action.id).toBe("tentacle-list");
    expect(action.safe).toBe(true);
    expect(action.requires_configured_gateway).toBeUndefined();
    expect(action.requires_configured_target).toBeUndefined();
  });

  it("parses a sync operator action with requires_configured_gateway", () => {
    const action = syncOperatorActionSchema.parse({
      id: "sync-status-json",
      title: "Local sync runtime snapshot",
      description: "Inspect queue + gateway health without mutating state.",
      command: "python3 sync-status.py --json",
      safe: true,
      requires_configured_gateway: false,
    });
    expect(action.requires_configured_gateway).toBe(false);
    expect(action.requires_configured_target).toBeUndefined();
  });

  it("parses a scout operator action with requires_configured_target", () => {
    const action = trendScoutOperatorActionSchema.parse({
      id: "trend-scout-dry-run",
      title: "Dry-run full pipeline preview",
      description: "Preview enrichment without creating/updating issues.",
      command: "python3 trend-scout.py --dry-run --limit 5",
      safe: true,
      requires_configured_target: true,
    });
    expect(action.requires_configured_target).toBe(true);
    expect(action.requires_configured_gateway).toBeUndefined();
  });

  it("rejects operator action with safe=false", () => {
    expect(() =>
      operatorActionSchema.parse({
        id: "bad-action",
        title: "Bad",
        description: "This should fail.",
        command: "rm -rf /",
        safe: false,
      })
    ).toThrow();
  });

  it("rejects operator action missing required fields", () => {
    // Missing command
    expect(() =>
      operatorActionSchema.parse({
        id: "incomplete",
        title: "Missing command",
        description: "No command field",
        safe: true,
      })
    ).toThrow();
  });

  it("rejects operator action with blank command text", () => {
    expect(() =>
      operatorActionSchema.parse({
        id: "blank-command",
        title: "Blank",
        description: "This should fail.",
        command: "   ",
        safe: true,
      })
    ).toThrow();
  });

  it("rejects sync operator action without requires_configured_gateway", () => {
    expect(() =>
      syncOperatorActionSchema.parse({
        id: "sync-status-json",
        title: "Local sync runtime snapshot",
        description: "Inspect queue + gateway health without mutating state.",
        command: "python3 sync-status.py --json",
        safe: true,
      })
    ).toThrow();
  });

  it("rejects scout operator action without requires_configured_target", () => {
    expect(() =>
      trendScoutOperatorActionSchema.parse({
        id: "trend-scout-dry-run",
        title: "Dry-run full pipeline preview",
        description: "Preview enrichment without creating/updating issues.",
        command: "python3 trend-scout.py --dry-run --limit 5",
        safe: true,
      })
    ).toThrow();
  });

  it("uses shared operatorActionSchema for tentacle status operator_actions", () => {
    const parsed = tentacleStatusResponseSchema.parse({
      status: "ready",
      configured: true,
      active_count: 0,
      total_count: 2,
      worktrees_prepared: 1,
      verification_covered: 1,
      marker: {
        active: false,
        path: "/home/.copilot/markers/dispatched",
        age_hours: null,
        stale: false,
      },
      tentacles: [],
      audit: {
        summary: { ok: true, total_checks: 3, warning_checks: 0 },
        checks: [],
      },
      operator_actions: [
        {
          id: "tentacle-list",
          title: "List all tentacles",
          description: "Read-only summary.",
          command: "python3 tentacle.py list",
          safe: true,
        },
      ],
      runtime: { generated_at: "2026-01-01T00:00:00Z" },
    });
    expect(parsed.operator_actions[0].safe).toBe(true);
    expect(parsed.operator_actions[0].requires_configured_gateway).toBeUndefined();
  });

  it("uses shared operatorActionSchema for skill metrics operator_actions", () => {
    const parsed = skillMetricsResponseSchema.parse({
      status: "unconfigured",
      configured: false,
      db_path: "/home/.copilot/session-state/skill-metrics.db",
      tables: {
        tentacle_outcomes: false,
        tentacle_outcome_skills: false,
        tentacle_verifications: false,
      },
      summary: {
        total_outcomes: 0,
        outcomes_with_skills: 0,
        outcomes_with_verification: 0,
        outcomes_with_worktree: 0,
        pass_rate: null,
      },
      recent_outcomes: [],
      skill_usage: [],
      audit: {
        summary: { ok: false, total_checks: 3, warning_checks: 3 },
        checks: [],
      },
      operator_actions: [
        {
          id: "skill-metrics-json",
          title: "Skill metrics in JSON",
          description: "Machine-readable skill outcome metrics.",
          command: "python3 skill-metrics.py --json",
          safe: true,
        },
      ],
      runtime: { generated_at: "2026-01-01T00:00:00Z" },
    });
    expect(parsed.operator_actions[0].safe).toBe(true);
  });

  // ── Shared audit block contract tests ───────────────────────────────

  it("parses a valid audit check", () => {
    const check = auditCheckSchema.parse({
      id: "db-connected",
      title: "Database connected",
      status: "ok",
      detail: "Connection established",
    });
    expect(check.id).toBe("db-connected");
    expect(check.status).toBe("ok");
  });

  it("parses a valid audit block with checks", () => {
    const block = auditBlockSchema.parse({
      summary: { ok: false, total_checks: 2, warning_checks: 1 },
      checks: [
        { id: "check-1", title: "Check 1", status: "ok", detail: "Fine" },
        { id: "check-2", title: "Check 2", status: "warning", detail: "Needs attention" },
      ],
    });
    expect(block.summary.total_checks).toBe(2);
    expect(block.checks).toHaveLength(2);
    expect(block.checks[1].status).toBe("warning");
  });

  it("parses audit block with empty checks array", () => {
    const block = auditBlockSchema.parse({
      summary: { ok: true, total_checks: 0, warning_checks: 0 },
      checks: [],
    });
    expect(block.checks).toHaveLength(0);
    expect(block.summary.ok).toBe(true);
  });

  it("rejects audit block missing summary", () => {
    expect(() =>
      auditBlockSchema.parse({
        checks: [],
      })
    ).toThrow();
  });

  // ── Timeline event file_mtime coercion tests ─────────────────────────────

  it("coerces numeric file_mtime to string (legacy numeric DB payload)", () => {
    const ev = timelineEventSchema.parse({
      event_id: 1,
      kind: "unknown",
      preview: "test",
      byte_offset: 0,
      file_mtime: 1777303726.969462,
      color: "#6b7280",
    });
    expect(typeof ev.file_mtime).toBe("string");
    expect(ev.file_mtime).toBe("1777303726.969462");
  });

  it("accepts string file_mtime unchanged", () => {
    const ev = timelineEventSchema.parse({
      event_id: 2,
      kind: "unknown",
      preview: "test",
      byte_offset: 0,
      file_mtime: "1777303726.969462",
      color: "#6b7280",
    });
    expect(ev.file_mtime).toBe("1777303726.969462");
  });

  it("accepts null file_mtime as null", () => {
    const ev = timelineEventSchema.parse({
      event_id: 3,
      kind: "unknown",
      preview: "test",
      byte_offset: null,
      file_mtime: null,
      color: "#6b7280",
    });
    expect(ev.file_mtime).toBeNull();
  });

  it("parses full timeline events response with numeric file_mtime", () => {
    const parsed = timelineEventsResponseSchema.parse({
      session_id: "de480029-0e37-4133-8ab1-61baa36be36f",
      total: 1,
      events: [
        {
          event_id: 0,
          kind: "unknown",
          preview: "some preview text",
          byte_offset: 0,
          file_mtime: 1777303726.969462,
          color: "#6b7280",
        },
      ],
    });
    expect(parsed.events[0].file_mtime).toBe("1777303726.969462");
    expect(typeof parsed.events[0].file_mtime).toBe("string");
  });
});

// ── Knowledge Insights schema tests ───────────────────────────────────────────

const _validInsights = {
  generated_at: "2026-01-01T00:00:00Z",
  summary: "All looks healthy.",
  overview: {
    health_score: 82.5,
    total_entries: 120,
    sessions: 15,
    high_confidence_pct: 70.0,
    low_confidence_pct: 8.0,
    stale_pct: 3.0,
    relation_density: 1.5,
    embedding_pct: 45.0,
  },
  quality_alerts: [
    {
      id: "low-conf",
      title: "Low confidence entries",
      severity: "warning" as const,
      detail: "8% of entries are low confidence.",
    },
  ],
  recommended_actions: [
    {
      id: "run-extract",
      title: "Re-extract knowledge",
      detail: "Run extraction to refresh.",
      command: "python3 extract-knowledge.py",
    },
  ],
  recurring_noise_titles: [
    { title: "Noisy title", category: "mistake", entry_count: 4, avg_confidence: 0.25 },
  ],
  hot_files: [{ path: "browse/api/__init__.py", references: 8 }],
  entries: {
    mistakes: [
      {
        id: 1,
        title: "Fix X",
        confidence: 0.9,
        occurrence_count: 2,
        last_seen: "2026-01-01",
        summary: "Fixed X by doing Y",
        session_id: "abc",
      },
    ],
    patterns: [],
    decisions: [],
    tools: [],
  },
};

describe("knowledgeInsightsResponseSchema", () => {
  it("parses a valid full insights response", () => {
    const parsed = knowledgeInsightsResponseSchema.parse(_validInsights);
    expect(parsed.generated_at).toBe("2026-01-01T00:00:00Z");
    expect(parsed.overview.health_score).toBe(82.5);
    expect(parsed.overview.total_entries).toBe(120);
    expect(parsed.quality_alerts).toHaveLength(1);
    expect(parsed.quality_alerts[0].severity).toBe("warning");
    expect(parsed.recommended_actions).toHaveLength(1);
    expect(parsed.hot_files[0].references).toBe(8);
    expect(parsed.entries.mistakes).toHaveLength(1);
    expect(parsed.entries.patterns).toHaveLength(0);
  });

  it("defaults empty arrays for list fields when absent", () => {
    const minimal = {
      generated_at: "2026-01-01T00:00:00Z",
      summary: "Ok",
      overview: {
        health_score: 50,
        total_entries: 0,
        sessions: 0,
        high_confidence_pct: 0,
        low_confidence_pct: 0,
        stale_pct: 0,
        relation_density: 0,
        embedding_pct: 0,
      },
      entries: { mistakes: [], patterns: [], decisions: [], tools: [] },
    };
    const parsed = knowledgeInsightsResponseSchema.parse(minimal);
    expect(parsed.quality_alerts).toEqual([]);
    expect(parsed.recommended_actions).toEqual([]);
    expect(parsed.recurring_noise_titles).toEqual([]);
    expect(parsed.hot_files).toEqual([]);
  });

  it("rejects unknown alert severity", () => {
    expect(() =>
      knowledgeInsightsResponseSchema.parse({
        ..._validInsights,
        quality_alerts: [{ id: "x", title: "Y", severity: "unknown", detail: "d" }],
      })
    ).toThrow();
  });

  it("rejects missing overview", () => {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { overview: _o, ...noOverview } = _validInsights;
    expect(() => knowledgeInsightsResponseSchema.parse(noOverview)).toThrow();
  });

  it("entry last_seen and summary accept null", () => {
    const withNull = {
      ..._validInsights,
      entries: {
        mistakes: [
          {
            id: 2,
            title: "T",
            confidence: 0.5,
            occurrence_count: 1,
            last_seen: null,
            summary: null,
            session_id: null,
          },
        ],
        patterns: [],
        decisions: [],
        tools: [],
      },
    };
    const parsed = knowledgeInsightsResponseSchema.parse(withNull);
    expect(parsed.entries.mistakes[0].last_seen).toBeNull();
    expect(parsed.entries.mistakes[0].summary).toBeNull();
  });
});
