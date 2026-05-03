import { describe, expect, it } from "vitest";

import {
  auditBlockSchema,
  auditCheckSchema,
  chatSettingsSchema,
  cliKindSchema,
  communitiesResponseSchema,
  copilotEventFrameSchema,
  copilotRawFrameSchema,
  copilotStatusFrameSchema,
  copilotStreamFrameSchema,
  evidenceGraphResponseSchema,
  compareResponseSchema,
  createOperatorSessionRequestSchema,
  evalResponseSchema,
  fileDiffResponseSchema,
  filePreviewResponseSchema,
  hostCapabilitiesSchema,
  hostProfileSchema,
  knowledgeInsightsResponseSchema,
  operatorRunsResponseSchema,
  operatorRunStatusSchema,
  operatorSessionListResponseSchema,
  operatorSessionSchema,
  researchPackReloadResponseSchema,
  retroResponseSchema,
  pathSuggestResponseSchema,
  promptRequestSchema,
  promptSubmitResponseSchema,
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
  operatorModelEntrySchema,
  operatorModelCatalogResponseSchema,
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

  it("parses a research pack reload response", () => {
    const parsed = researchPackReloadResponseSchema.parse({
      ok: true,
      command: "python3 trend-scout.py --research-pack",
      exit_code: 0,
      artifact_available: true,
      generated_at: "2026-01-01T01:00:00Z",
      repo_count: 3,
      run_skipped: false,
      skip_reason: null,
      error: null,
    });

    expect(parsed.ok).toBe(true);
    expect(parsed.command).toContain("--research-pack");
    expect(parsed.repo_count).toBe(3);
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

  it("accepts tentacle entries with optional has_handoff and terminal_status fields", () => {
    const result = tentacleStatusResponseSchema.parse({
      status: "active",
      configured: true,
      active_count: 1,
      total_count: 1,
      worktrees_prepared: 0,
      verification_covered: 0,
      marker: {
        active: true,
        path: "/home/.copilot/markers/dispatched",
        age_hours: 1.2,
        stale: false,
      },
      tentacles: [
        {
          name: "my-tentacle",
          tentacle_id: "abc-123",
          status: "active",
          created_at: "2026-01-01T00:00:00Z",
          description: "Test tentacle",
          scope: ["src/**"],
          skills: [],
          worktree: { prepared: false, path: "", stale: false },
          verification: { coverage_exists: false, total: 0, passed: 0, failed: 0 },
          has_handoff: true,
          terminal_status: "DONE",
        },
      ],
      audit: { summary: { ok: true, total_checks: 4, warning_checks: 0 }, checks: [] },
      operator_actions: [
        {
          id: "tentacle-marker-cleanup",
          title: "Inspect stale dispatch markers",
          description: "Dry-run inspection of stale dispatched-subagent marker entries.",
          command: "python3 tentacle.py marker-cleanup",
          safe: true,
        },
      ],
      runtime: { generated_at: "2026-01-01T00:00:00Z" },
    });
    expect(result.tentacles[0].terminal_status).toBe("DONE");
    expect(result.tentacles[0].has_handoff).toBe(true);
    expect(result.operator_actions[0].id).toBe("tentacle-marker-cleanup");
  });

  it("accepts tentacle entries without optional handoff fields (backward compat)", () => {
    const result = tentacleStatusResponseSchema.parse({
      status: "idle",
      configured: true,
      active_count: 0,
      total_count: 1,
      worktrees_prepared: 0,
      verification_covered: 0,
      marker: {
        active: false,
        path: "/home/.copilot/markers/dispatched",
        age_hours: null,
        stale: false,
      },
      tentacles: [
        {
          name: "old-tentacle",
          tentacle_id: "xyz-456",
          status: "idle",
          created_at: "2025-01-01T00:00:00Z",
          description: "",
          scope: [],
          skills: [],
          worktree: { prepared: false, path: "", stale: false },
          verification: { coverage_exists: false, total: 0, passed: 0, failed: 0 },
        },
      ],
      audit: { summary: { ok: true, total_checks: 4, warning_checks: 0 }, checks: [] },
      operator_actions: [],
      runtime: { generated_at: "2025-01-01T00:00:00Z" },
    });
    expect(result.tentacles[0].terminal_status).toBeUndefined();
    expect(result.tentacles[0].has_handoff).toBeUndefined();
  });

  it("accepts tentacle entries with goal-aware optional fields", () => {
    const result = tentacleStatusResponseSchema.parse({
      status: "active",
      configured: true,
      active_count: 1,
      total_count: 1,
      worktrees_prepared: 1,
      verification_covered: 1,
      goal_aware_count: 1,
      marker: {
        active: true,
        path: "/home/.copilot/markers/dispatched",
        age_hours: 0.5,
        stale: false,
      },
      tentacles: [
        {
          name: "goal-linked-tentacle",
          tentacle_id: "glt-001",
          status: "active",
          created_at: "2026-05-01T00:00:00Z",
          description: "Tentacle linked to a goal",
          scope: ["src/**"],
          skills: [],
          worktree: { prepared: true, path: "/worktrees/glt-001", stale: false },
          verification: { coverage_exists: true, total: 5, passed: 5, failed: 0 },
          goal_id: "goal-abc-123",
          goal_name: "Improve search performance",
          goal_iteration: 2,
        },
      ],
      audit: { summary: { ok: true, total_checks: 4, warning_checks: 0 }, checks: [] },
      operator_actions: [],
      runtime: { generated_at: "2026-05-01T00:00:00Z" },
    });
    expect(result.goal_aware_count).toBe(1);
    expect(result.tentacles[0].goal_id).toBe("goal-abc-123");
    expect(result.tentacles[0].goal_name).toBe("Improve search performance");
    expect(result.tentacles[0].goal_iteration).toBe(2);
  });

  it("accepts tentacle entries without goal fields (backward compat — goal-core not yet shipped)", () => {
    const result = tentacleStatusResponseSchema.parse({
      status: "idle",
      configured: true,
      active_count: 0,
      total_count: 1,
      worktrees_prepared: 0,
      verification_covered: 0,
      marker: {
        active: false,
        path: "/home/.copilot/markers/dispatched",
        age_hours: null,
        stale: false,
      },
      tentacles: [
        {
          name: "plain-tentacle",
          tentacle_id: "pt-001",
          status: "idle",
          created_at: "2025-01-01T00:00:00Z",
          description: "",
          scope: [],
          skills: [],
          worktree: { prepared: false, path: "", stale: false },
          verification: { coverage_exists: false, total: 0, passed: 0, failed: 0 },
        },
      ],
      audit: { summary: { ok: true, total_checks: 4, warning_checks: 0 }, checks: [] },
      operator_actions: [],
      runtime: { generated_at: "2025-01-01T00:00:00Z" },
    });
    expect(result.goal_aware_count).toBeUndefined();
    expect(result.tentacles[0].goal_id).toBeUndefined();
    expect(result.tentacles[0].goal_name).toBeUndefined();
    expect(result.tentacles[0].goal_iteration).toBeUndefined();
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

  it("parses toward_100 dict when present", () => {
    const parsed = knowledgeInsightsResponseSchema.parse({
      ..._validInsights,
      toward_100: {
        total_gap: 33.5,
        dimensions: [
          {
            dimension: "confidence_quality",
            current: 0.2,
            max: 15.0,
            gap: 14.8,
            gap_pct: 98.7,
            pct_of_total_gap: 44.3,
          },
        ],
        top_gaps: [
          {
            dimension: "confidence_quality",
            current: 0.2,
            max: 15.0,
            gap: 14.8,
            gap_pct: 98.7,
            pct_of_total_gap: 44.2,
          },
          {
            dimension: "stale_ratio",
            current: 0.7,
            max: 10.0,
            gap: 8.5,
            gap_pct: 85.0,
            pct_of_total_gap: 25.4,
          },
        ],
      },
    });
    expect(parsed.toward_100).toBeDefined();
    expect(parsed.toward_100?.total_gap).toBe(33.5);
    expect(parsed.toward_100?.top_gaps).toHaveLength(2);
    expect(parsed.toward_100?.top_gaps[0].dimension).toBe("confidence_quality");
    expect(parsed.toward_100?.top_gaps[0].pct_of_total_gap).toBe(44.2);
    expect(parsed.toward_100?.dimensions).toHaveLength(1);
    expect(parsed.toward_100?.dimensions[0].gap_pct).toBe(98.7);
  });

  it("accepts toward_100 as absent (backward compat — older payloads)", () => {
    const parsed = knowledgeInsightsResponseSchema.parse(_validInsights);
    expect(parsed.toward_100).toBeUndefined();
  });

  it("accepts toward_100 as null (graceful degradation)", () => {
    const parsed = knowledgeInsightsResponseSchema.parse({ ..._validInsights, toward_100: null });
    expect(parsed.toward_100).toBeNull();
  });
});

// ── Retro toward_100 schema tests ──────────────────────────────────────────

const _baseRetro = {
  retro_score: 70,
  grade: "Good",
  grade_emoji: "✅",
  mode: "repo" as const,
  generated_at: "2026-01-01T00:00:00Z",
  available_sections: ["git"],
  weights: { git: 1.0 },
  subscores: { git: 70 },
  knowledge: null,
  skills: null,
  hooks: null,
  git: { available: true },
};

describe("retroResponseSchema toward_100", () => {
  it("parses toward_100 as a list of section gap items", () => {
    const parsed = retroResponseSchema.parse({
      ..._baseRetro,
      toward_100: [
        { section: "skills", score: 30.0, gap: 70.0, barriers: ["no_verification_evidence"] },
        { section: "behavior", score: 55.0, gap: 45.0, barriers: [] },
      ],
    });
    expect(parsed.toward_100).toHaveLength(2);
    expect(parsed.toward_100?.[0].section).toBe("skills");
    expect(parsed.toward_100?.[0].gap).toBe(70.0);
    expect(parsed.toward_100?.[0].barriers).toContain("no_verification_evidence");
    expect(parsed.toward_100?.[1].barriers).toHaveLength(0);
  });

  it("accepts toward_100 as absent (backward compat)", () => {
    const parsed = retroResponseSchema.parse(_baseRetro);
    expect(parsed.toward_100).toBeUndefined();
  });

  it("accepts toward_100 as null", () => {
    const parsed = retroResponseSchema.parse({ ..._baseRetro, toward_100: null });
    expect(parsed.toward_100).toBeNull();
  });

  it("rejects toward_100 items missing required fields", () => {
    expect(() =>
      retroResponseSchema.parse({
        ..._baseRetro,
        toward_100: [{ section: "skills", score: 30 }], // missing gap and barriers
      })
    ).toThrow();
  });
});

// ── Operator/Chat schema tests ─────────────────────────────────────────────

const _validOperatorSession = {
  id: "sess-001",
  name: "My session",
  model: "claude-sonnet-4.6",
  mode: "default",
  workspace: "/Users/user/projects/myapp",
  add_dirs: [],
  created_at: "2026-05-01T10:00:00Z",
  updated_at: "2026-05-01T10:05:00Z",
  run_count: 2,
  last_run_id: "run-abc",
  resume_ready: true,
};

describe("operator session schemas", () => {
  it("parses a valid operator session", () => {
    const parsed = operatorSessionSchema.parse(_validOperatorSession);
    expect(parsed.id).toBe("sess-001");
    expect(parsed.model).toBe("claude-sonnet-4.6");
    expect(parsed.resume_ready).toBe(true);
    expect(parsed.run_count).toBe(2);
    expect(parsed.last_run_id).toBe("run-abc");
  });

  it("accepts operator session with null last_run_id", () => {
    const parsed = operatorSessionSchema.parse({ ..._validOperatorSession, last_run_id: null });
    expect(parsed.last_run_id).toBeNull();
  });

  it("accepts add_dirs as empty array", () => {
    const parsed = operatorSessionSchema.parse({ ..._validOperatorSession, add_dirs: [] });
    expect(parsed.add_dirs).toEqual([]);
  });

  it("accepts add_dirs with paths", () => {
    const parsed = operatorSessionSchema.parse({
      ..._validOperatorSession,
      add_dirs: ["/extra/dir", "/another/dir"],
    });
    expect(parsed.add_dirs).toHaveLength(2);
  });

  it("parses a list of operator sessions", () => {
    const parsed = operatorSessionListResponseSchema.parse({
      sessions: [_validOperatorSession],
      count: 1,
    });
    expect(parsed.sessions).toHaveLength(1);
    expect(parsed.sessions[0].id).toBe("sess-001");
    expect(parsed.count).toBe(1);
  });

  it("parses an empty session list", () => {
    const parsed = operatorSessionListResponseSchema.parse({ sessions: [], count: 0 });
    expect(parsed.sessions).toEqual([]);
    expect(parsed.count).toBe(0);
  });

  it("rejects operator session missing required fields", () => {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { id: _id, ...noId } = _validOperatorSession;
    expect(() => operatorSessionSchema.parse(noId)).toThrow();
  });
});

describe("createOperatorSessionRequestSchema", () => {
  it("parses a valid create request", () => {
    const parsed = createOperatorSessionRequestSchema.parse({
      name: "New session",
      model: "claude-sonnet-4.6",
      mode: "default",
      workspace: "/Users/user/projects",
    });
    expect(parsed.name).toBe("New session");
    expect(parsed.add_dirs).toBeUndefined();
  });

  it("accepts create request with add_dirs", () => {
    const parsed = createOperatorSessionRequestSchema.parse({
      name: "Session with dirs",
      model: "claude-sonnet-4.6",
      mode: "default",
      workspace: "/Users/user/projects",
      add_dirs: ["/extra"],
    });
    expect(parsed.add_dirs).toEqual(["/extra"]);
  });

  it("accepts empty strings for backend parity", () => {
    const parsed = createOperatorSessionRequestSchema.parse({
      name: "",
      model: "",
      mode: "",
      workspace: "",
    });
    expect(parsed.name).toBe("");
    expect(parsed.workspace).toBe("");
  });

  it("accepts omitted model for default model flow", () => {
    const parsed = createOperatorSessionRequestSchema.parse({
      name: "Session without explicit model",
      mode: "default",
      workspace: "/Users/user/projects",
    });
    expect(parsed.model).toBeUndefined();
  });
});

describe("promptRequestSchema", () => {
  it("parses a valid prompt request", () => {
    const parsed = promptRequestSchema.parse({ prompt: "What files changed?" });
    expect(parsed.prompt).toBe("What files changed?");
  });

  it("rejects prompt request with empty prompt", () => {
    expect(() => promptRequestSchema.parse({ prompt: "" })).toThrow();
  });
});

describe("promptSubmitResponseSchema", () => {
  it("parses a valid prompt submit response", () => {
    const parsed = promptSubmitResponseSchema.parse({
      run_id: "run-001",
      session_id: "sess-001",
      status: "running",
    });
    expect(parsed.run_id).toBe("run-001");
    expect(parsed.session_id).toBe("sess-001");
    expect(parsed.status).toBe("running");
  });
});

describe("operatorRunStatusSchema", () => {
  it("parses a valid run status response", () => {
    const parsed = operatorRunStatusSchema.parse({
      session: _validOperatorSession,
      run: {
        id: "run-001",
        session_id: "sess-001",
        prompt: "hello world",
        status: "running",
        exit_code: null,
        started_at: "2026-05-01T10:01:00Z",
        finished_at: null,
        events: [],
      },
    });
    expect(parsed.session.id).toBe("sess-001");
    expect(parsed.run?.id).toBe("run-001");
    expect(parsed.run?.status).toBe("running");
  });

  it("accepts run with exit_code and timestamps", () => {
    const parsed = operatorRunStatusSchema.parse({
      session: _validOperatorSession,
      run: {
        id: "run-002",
        session_id: "sess-001",
        prompt: "done",
        status: "done",
        exit_code: 0,
        started_at: "2026-05-01T10:01:00Z",
        finished_at: "2026-05-01T10:02:00Z",
        events: [
          {
            type: "assistant.message",
            idx: 0,
            event: {
              type: "assistant.message",
              data: { content: "OK" },
            },
            data: { content: "OK" },
          },
        ],
      },
    });
    expect(parsed.run?.exit_code).toBe(0);
    expect(parsed.run?.finished_at).toBe("2026-05-01T10:02:00Z");
    expect(parsed.run?.events).toHaveLength(1);
  });

  it("accepts null run when no run id was requested", () => {
    const parsed = operatorRunStatusSchema.parse({
      session: _validOperatorSession,
      run: null,
    });
    expect(parsed.run).toBeNull();
  });
});

describe("operatorRunsResponseSchema", () => {
  const _historyRun = {
    id: "run-010",
    session_id: "sess-001",
    prompt: "ship it",
    status: "failed",
    exit_code: 1,
    started_at: "2026-05-01T10:01:00Z",
    finished_at: "2026-05-01T10:02:00Z",
    events: [],
  };

  it("parses a valid persisted run history envelope", () => {
    const parsed = operatorRunsResponseSchema.parse({ runs: [_historyRun], count: 1 });
    expect(parsed.runs).toHaveLength(1);
    expect(parsed.runs[0].id).toBe("run-010");
    expect(parsed.runs[0].status).toBe("failed");
    expect(parsed.count).toBe(1);
  });

  it("accepts an empty persisted run history envelope", () => {
    const parsed = operatorRunsResponseSchema.parse({ runs: [], count: 0 });
    expect(parsed.runs).toEqual([]);
    expect(parsed.count).toBe(0);
  });

  it("rejects malformed run entries in history envelopes", () => {
    expect(() =>
      operatorRunsResponseSchema.parse({
        runs: [{ id: "broken-run", status: "done" }],
        count: 1,
      })
    ).toThrow();
  });

  it("rejects negative history counts", () => {
    expect(() => operatorRunsResponseSchema.parse({ runs: [], count: -1 })).toThrow();
  });
});

describe("pathSuggestResponseSchema", () => {
  it("parses a valid suggest response", () => {
    const parsed = pathSuggestResponseSchema.parse({
      suggestions: ["/Users/user/projects/app", "/Users/user/projects/lib"],
      count: 2,
    });
    expect(parsed.suggestions).toHaveLength(2);
    expect(parsed.count).toBe(2);
  });

  it("parses an empty suggest response", () => {
    const parsed = pathSuggestResponseSchema.parse({ suggestions: [], count: 0 });
    expect(parsed.suggestions).toEqual([]);
    expect(parsed.count).toBe(0);
  });
});

describe("filePreviewResponseSchema", () => {
  it("parses a valid preview response", () => {
    const parsed = filePreviewResponseSchema.parse({
      path: "/Users/user/projects/app/main.ts",
      content: "export default function main() {}",
      mime: "text/plain",
      size: 33,
    });
    expect(parsed.path).toBe("/Users/user/projects/app/main.ts");
    expect(parsed.mime).toBe("text/plain");
    expect(parsed.size).toBe(33);
  });
});

describe("fileDiffResponseSchema", () => {
  it("parses a valid diff response", () => {
    const parsed = fileDiffResponseSchema.parse({
      path_a: "/Users/user/projects/app/main.ts",
      path_b: "/Users/user/projects/app/main.ts",
      unified_diff: "@@ -1,1 +1,2 @@\n-old\n+new\n+added",
      stats: { added: 2, removed: 1 },
    });
    expect(parsed.path_a).toBe("/Users/user/projects/app/main.ts");
    expect(parsed.stats.added).toBe(2);
    expect(parsed.stats.removed).toBe(1);
  });

  it("accepts empty unified_diff (no changes)", () => {
    const parsed = fileDiffResponseSchema.parse({
      path_a: "a.ts",
      path_b: "b.ts",
      unified_diff: "",
      stats: { added: 0, removed: 0 },
    });
    expect(parsed.unified_diff).toBe("");
    expect(parsed.stats.added).toBe(0);
  });
});

describe("SSE frame schemas", () => {
  it("parses a typed copilot event frame", () => {
    const parsed = copilotEventFrameSchema.parse({
      type: "assistant.message_delta",
      idx: 0,
      event: {
        type: "assistant.message_delta",
        data: { deltaContent: "Hello" },
      },
      data: { deltaContent: "Hello" },
    });
    expect(parsed.type).toBe("assistant.message_delta");
    expect(parsed.event.type).toBe("assistant.message_delta");
    expect(parsed.data).toEqual({ deltaContent: "Hello" });
  });

  it("parses typed event frame without data (optional)", () => {
    const parsed = copilotEventFrameSchema.parse({
      type: "assistant.turn_start",
      idx: 1,
      event: {
        type: "assistant.turn_start",
      },
    });
    expect(parsed.data).toBeUndefined();
  });

  it("accepts unknown event types from future Copilot versions", () => {
    const parsed = copilotEventFrameSchema.parse({
      type: "assistant.new_future_event",
      idx: 2,
      event: {
        type: "assistant.new_future_event",
      },
      data: null,
    });
    expect(parsed.type).toBe("assistant.new_future_event");
  });

  it("parses a raw frame", () => {
    const parsed = copilotRawFrameSchema.parse({
      type: "raw",
      idx: 3,
      text: "some unstructured output",
    });
    expect(parsed.type).toBe("raw");
    expect(parsed.text).toBe("some unstructured output");
  });

  it("parses a terminal status frame with exit_code 0", () => {
    const parsed = copilotStatusFrameSchema.parse({
      type: "status",
      status: "done",
      exit_code: 0,
    });
    expect(parsed.type).toBe("status");
    expect(parsed.status).toBe("done");
    expect(parsed.exit_code).toBe(0);
  });

  it("parses a terminal status frame with null exit_code", () => {
    const parsed = copilotStatusFrameSchema.parse({
      type: "status",
      status: "running",
      exit_code: null,
    });
    expect(parsed.exit_code).toBeNull();
  });

  it("stream frame schema parses a structured event frame", () => {
    const parsed = copilotStreamFrameSchema.parse({
      type: "result",
      idx: 4,
      event: {
        type: "result",
        exitCode: 0,
      },
    });
    expect(parsed.type).toBe("result");
  });

  it("stream frame schema parses a raw frame", () => {
    const parsed = copilotStreamFrameSchema.parse({
      type: "raw",
      idx: 5,
      text: "fallback",
    });
    expect(parsed.type).toBe("raw");
  });

  it("stream frame schema parses a status frame", () => {
    const parsed = copilotStreamFrameSchema.parse({
      type: "status",
      status: "done",
      exit_code: 0,
    });
    expect(parsed.type).toBe("status");
  });

  it("rejects mismatched frame type and event.type", () => {
    expect(() =>
      copilotStreamFrameSchema.parse({
        type: "assistant.message",
        idx: 0,
        event: {
          type: "assistant.message_delta",
        },
      })
    ).toThrow();
  });
});

describe("chatSettingsSchema", () => {
  it("parses valid chat settings", () => {
    const parsed = chatSettingsSchema.parse({
      model: "claude-sonnet-4.6",
      mode: "default",
      workspace: "/Users/user/projects",
      add_dirs: ["/Users/user/projects/extra"],
    });
    expect(parsed.model).toBe("claude-sonnet-4.6");
    expect(parsed.add_dirs).toHaveLength(1);
  });

  it("accepts empty add_dirs", () => {
    const parsed = chatSettingsSchema.parse({
      model: "gpt-5.4",
      mode: "default",
      workspace: "/Users/user/projects",
      add_dirs: [],
    });
    expect(parsed.add_dirs).toEqual([]);
  });

  it("rejects empty model", () => {
    expect(() =>
      chatSettingsSchema.parse({
        model: "",
        mode: "default",
        workspace: "/Users/user/projects",
        add_dirs: [],
      })
    ).toThrow();
  });
});

// ── Operator model catalog schema tests ───────────────────────────────────

describe("operatorModelEntrySchema", () => {
  it("parses a minimal model entry", () => {
    const parsed = operatorModelEntrySchema.parse({
      id: "claude-sonnet-4.6",
      display_name: "Claude Sonnet 4.6",
    });
    expect(parsed.id).toBe("claude-sonnet-4.6");
    expect(parsed.display_name).toBe("Claude Sonnet 4.6");
    expect(parsed.provider).toBeUndefined();
    expect(parsed.default).toBeUndefined();
  });

  it("parses a full model entry with provider and default flag", () => {
    const parsed = operatorModelEntrySchema.parse({
      id: "gpt-5.4",
      display_name: "GPT-5.4",
      provider: "openai",
      default: true,
    });
    expect(parsed.provider).toBe("openai");
    expect(parsed.default).toBe(true);
  });

  it("accepts default: false", () => {
    const parsed = operatorModelEntrySchema.parse({
      id: "claude-haiku-4.5",
      display_name: "Claude Haiku 4.5",
      provider: "anthropic",
      default: false,
    });
    expect(parsed.default).toBe(false);
  });
});

describe("operatorModelCatalogResponseSchema", () => {
  it("parses a catalog with multiple models", () => {
    const parsed = operatorModelCatalogResponseSchema.parse({
      models: [
        { id: "claude-sonnet-4.6", display_name: "Claude Sonnet 4.6", default: true },
        { id: "gpt-5.4", display_name: "GPT-5.4", provider: "openai" },
      ],
      default_model: "claude-sonnet-4.6",
    });
    expect(parsed.models).toHaveLength(2);
    expect(parsed.default_model).toBe("claude-sonnet-4.6");
  });

  it("parses an empty catalog", () => {
    const parsed = operatorModelCatalogResponseSchema.parse({
      models: [],
      default_model: null,
    });
    expect(parsed.models).toEqual([]);
    expect(parsed.default_model).toBeNull();
  });

  it("accepts null default_model when no default is configured", () => {
    const parsed = operatorModelCatalogResponseSchema.parse({
      models: [{ id: "claude-sonnet-4.6", display_name: "Claude Sonnet 4.6" }],
      default_model: null,
    });
    expect(parsed.default_model).toBeNull();
  });

  it("rejects catalog missing required default_model field", () => {
    expect(() =>
      operatorModelCatalogResponseSchema.parse({
        models: [],
      })
    ).toThrow();
  });

  // ── Host Profiles ─────────────────────────────────────────────────────

  it("parses a valid host profile", () => {
    const parsed = hostProfileSchema.parse({
      id: "tunnel-1",
      label: "My Laptop Tunnel",
      base_url: "https://xyz.ngrok.io",
      token: "secret-token",
      cli_kind: "copilot",
      is_default: false,
    });
    expect(parsed.id).toBe("tunnel-1");
    expect(parsed.label).toBe("My Laptop Tunnel");
    expect(parsed.base_url).toBe("https://xyz.ngrok.io");
    expect(parsed.token).toBe("secret-token");
    expect(parsed.cli_kind).toBe("copilot");
    expect(parsed.is_default).toBe(false);
  });

  it("accepts unknown cli_kind values for future CLI families", () => {
    const parsed = hostProfileSchema.parse({
      id: "amp-host",
      label: "Amp Host",
      base_url: "https://amp.example.com",
      token: "tok",
      cli_kind: "amp",
      is_default: false,
    });
    expect(parsed.cli_kind).toBe("amp");
  });

  it("accepts empty base_url for same-origin local profile", () => {
    const parsed = hostProfileSchema.parse({
      id: "local",
      label: "Local (same-origin)",
      base_url: "",
      token: "",
      cli_kind: "copilot",
      is_default: true,
    });
    expect(parsed.base_url).toBe("");
    expect(parsed.is_default).toBe(true);
  });

  it("rejects host profile with empty id", () => {
    expect(() =>
      hostProfileSchema.parse({
        id: "",
        label: "Test",
        base_url: "",
        token: "",
        cli_kind: "copilot",
        is_default: false,
      })
    ).toThrow();
  });

  it("rejects host profile with empty label", () => {
    expect(() =>
      hostProfileSchema.parse({
        id: "some-id",
        label: "",
        base_url: "",
        token: "",
        cli_kind: "copilot",
        is_default: false,
      })
    ).toThrow();
  });

  it("rejects host profile with empty cli_kind", () => {
    expect(() =>
      hostProfileSchema.parse({
        id: "some-id",
        label: "Test",
        base_url: "",
        token: "",
        cli_kind: "",
        is_default: false,
      })
    ).toThrow();
  });

  it("rejects host profile missing required fields", () => {
    expect(() => hostProfileSchema.parse({ id: "x", label: "Test" })).toThrow();
  });

  // ── Host Capabilities ─────────────────────────────────────────────────

  it("parses a valid host capabilities response", () => {
    const parsed = hostCapabilitiesSchema.parse({
      cli_kind: "copilot",
      version: "1.2.3",
      supported_modes: ["ask", "edit"],
      supported_features: ["streaming", "model-catalog"],
    });
    expect(parsed.cli_kind).toBe("copilot");
    expect(parsed.version).toBe("1.2.3");
    expect(parsed.supported_modes).toEqual(["ask", "edit"]);
    expect(parsed.supported_features).toContain("streaming");
  });

  it("accepts capabilities without optional version", () => {
    const parsed = hostCapabilitiesSchema.parse({
      cli_kind: "claude",
      supported_modes: ["chat"],
      supported_features: [],
    });
    expect(parsed.cli_kind).toBe("claude");
    expect(parsed.version).toBeUndefined();
  });

  it("accepts null version in capabilities", () => {
    const parsed = hostCapabilitiesSchema.parse({
      cli_kind: "copilot",
      version: null,
      supported_modes: [],
      supported_features: [],
    });
    expect(parsed.version).toBeNull();
  });

  it("accepts unknown future cli_kind in capabilities", () => {
    const parsed = hostCapabilitiesSchema.parse({
      cli_kind: "gemini-cli",
      supported_modes: ["chat"],
      supported_features: ["streaming"],
    });
    expect(parsed.cli_kind).toBe("gemini-cli");
  });

  it("rejects capabilities with empty cli_kind", () => {
    expect(() =>
      hostCapabilitiesSchema.parse({
        cli_kind: "",
        supported_modes: [],
        supported_features: [],
      })
    ).toThrow();
  });

  it("rejects capabilities missing supported_modes", () => {
    expect(() =>
      hostCapabilitiesSchema.parse({
        cli_kind: "copilot",
        supported_features: [],
      })
    ).toThrow();
  });

  it("cliKindSchema accepts any non-empty string", () => {
    expect(cliKindSchema.parse("copilot")).toBe("copilot");
    expect(cliKindSchema.parse("claude")).toBe("claude");
    expect(cliKindSchema.parse("some-future-cli")).toBe("some-future-cli");
    expect(() => cliKindSchema.parse("")).toThrow();
  });
});
