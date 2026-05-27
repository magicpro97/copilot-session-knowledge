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
  updateOperatorSessionRequestSchema,
  evalResponseSchema,
  fileDiffResponseSchema,
  filePreviewResponseSchema,
  browseHostBootstrapSchema,
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
  sessionDetailResponseSchema,
  promptRequestSchema,
  promptSubmitResponseSchema,
  trendScoutStatusResponseSchema,
  trendScoutDiscoveryLaneSchema,
  syncStatusResponseSchema,
  healthResponseSchema,
  tentacleStatusResponseSchema,
  skillMetricsResponseSchema,
  skillCatalogResponseSchema,
  operatorActionSchema,
  syncOperatorActionSchema,
  searchResponseSchema,
  sessionListResponseSchema,
  trendScoutOperatorActionSchema,
  timelineEventSchema,
  timelineEventsResponseSchema,
  operatorModelEntrySchema,
  operatorModelCatalogResponseSchema,
  cliSessionSchema,
  adoptCliSessionRequestSchema,
  browseDebugSkeletonEntrySchema,
  sessionDebugSkeletonResponseSchema,
  sessionDebugLogResponseSchema,
  browseDebugEntrySchema,
  sessionMissionAtlasResponseSchema,
  missionAtlasBucketSchema,
  missionAtlasErrorSampleSchema,
  missionAtlasMilestoneSchema,
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

  it("parses sync diagnostics status response (redacted, no absolute paths)", () => {
    const parsed = syncStatusResponseSchema.parse({
      status: "pending",
      configured: true,
      connection: {
        configured: true,
        endpoint: "https://sync.local",
        // Issue #561: backend emits non-PII presence flag + stable label
        // instead of an absolute config_path.
        config_path_present: true,
        config_path_label: "~/.copilot/tools/sync-config.json",
      },
      runtime: {
        generated_at: "2026-01-01T00:00:00Z",
        // Issue #561: db_path intentionally absent.
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
    expect(parsed.connection.config_path_present).toBe(true);
    expect(parsed.connection.config_path_label).toBe("~/.copilot/tools/sync-config.json");
    expect(parsed.connection.config_path).toBeUndefined();
    expect(parsed.runtime.db_path).toBeUndefined();
    expect(parsed.runtime.db_mode).toBe("file");
    expect(parsed.operator_actions[0].safe).toBe(true);
    expect(parsed.failed_ops).toBe(1);
  });

  it("parses liveness-only /healthz payload (issue #560)", () => {
    // New contract: only status + sync_status_endpoint are emitted.
    const parsed = healthResponseSchema.parse({
      status: "ok",
      sync_status_endpoint: "/api/sync/status",
    });
    expect(parsed.status).toBe("ok");
    expect(parsed.sync_status_endpoint).toBe("/api/sync/status");
    expect(parsed.schema_version).toBeUndefined();
    expect(parsed.sessions).toBeUndefined();
    expect(parsed.knowledge_entries).toBeUndefined();
    expect(parsed.last_indexed_at).toBeUndefined();
  });

  it("still parses legacy /healthz payload with corpus fields", () => {
    // Back-compat: older backends that still emit these must not throw.
    const parsed = healthResponseSchema.parse({
      status: "ok",
      schema_version: 7,
      sessions: 42,
      knowledge_entries: 100,
      last_indexed_at: "2026-01-01T00:00:00Z",
    });
    expect(parsed.schema_version).toBe(7);
    expect(parsed.sessions).toBe(42);
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

  // ── Skill catalog schema tests ───────────────────────────────────────

  it("parses a valid skill catalog response", () => {
    const parsed = skillCatalogResponseSchema.parse({
      skills: [
        {
          id: "my-skill",
          name: "My Skill",
          description: "Does something useful",
          source_path: "/home/user/.copilot/skills/my-skill/SKILL.md",
          source_kind: "global",
          status: "installed",
        },
      ],
      total: 1,
      sources: {
        global: "/home/user/.copilot/skills",
        project: null,
      },
      runtime: { generated_at: "2026-01-01T00:00:00Z" },
    });
    expect(parsed.skills).toHaveLength(1);
    expect(parsed.skills[0].id).toBe("my-skill");
    expect(parsed.skills[0].source_kind).toBe("global");
    expect(parsed.skills[0].status).toBe("installed");
    expect(parsed.total).toBe(1);
    expect(parsed.sources.global).toBe("/home/user/.copilot/skills");
    expect(parsed.sources.project).toBeNull();
  });

  it("parses an empty skill catalog response", () => {
    const parsed = skillCatalogResponseSchema.parse({
      skills: [],
      total: 0,
      sources: {
        global: "/home/user/.copilot/skills",
        project: null,
      },
      runtime: { generated_at: "2026-01-01T00:00:00Z" },
    });
    expect(parsed.skills).toHaveLength(0);
    expect(parsed.total).toBe(0);
  });

  it("parses skill catalog with project skills", () => {
    const parsed = skillCatalogResponseSchema.parse({
      skills: [
        {
          id: "proj-skill",
          name: "Proj Skill",
          description: "A project skill",
          source_path: "/repo/.github/skills/proj-skill/SKILL.md",
          source_kind: "project",
          status: "installed",
        },
      ],
      total: 1,
      sources: {
        global: "/home/user/.copilot/skills",
        project: "/repo/.github/skills",
      },
      runtime: { generated_at: "2026-01-01T00:00:00Z" },
    });
    expect(parsed.skills[0].source_kind).toBe("project");
    expect(parsed.sources.project).toBe("/repo/.github/skills");
  });

  it("rejects skill catalog entry with invalid source_kind", () => {
    expect(() =>
      skillCatalogResponseSchema.parse({
        skills: [
          {
            id: "bad",
            name: "Bad",
            description: "",
            source_path: "/path",
            source_kind: "unknown",
            status: "installed",
          },
        ],
        total: 1,
        sources: { global: "/g", project: null },
        runtime: { generated_at: "2026-01-01T00:00:00Z" },
      })
    ).toThrow();
  });

  it("rejects skill catalog entry with invalid status", () => {
    expect(() =>
      skillCatalogResponseSchema.parse({
        skills: [
          {
            id: "bad",
            name: "Bad",
            description: "",
            source_path: "/path",
            source_kind: "global",
            status: "broken",
          },
        ],
        total: 1,
        sources: { global: "/g", project: null },
        runtime: { generated_at: "2026-01-01T00:00:00Z" },
      })
    ).toThrow();
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

describe("updateOperatorSessionRequestSchema", () => {
  it("parses a valid update with all fields", () => {
    const parsed = updateOperatorSessionRequestSchema.parse({
      name: "Renamed session",
      model: "claude-sonnet-4.6",
      mode: "interactive",
    });
    expect(parsed.name).toBe("Renamed session");
    expect(parsed.model).toBe("claude-sonnet-4.6");
    expect(parsed.mode).toBe("interactive");
  });

  it("accepts update with only name", () => {
    const parsed = updateOperatorSessionRequestSchema.parse({ name: "Just a rename" });
    expect(parsed.name).toBe("Just a rename");
    expect(parsed.model).toBeUndefined();
    expect(parsed.mode).toBeUndefined();
  });

  it("accepts update with only model", () => {
    const parsed = updateOperatorSessionRequestSchema.parse({ model: "gpt-5.4" });
    expect(parsed.model).toBe("gpt-5.4");
  });

  it("accepts update with only mode", () => {
    const parsed = updateOperatorSessionRequestSchema.parse({ mode: "autopilot" });
    expect(parsed.mode).toBe("autopilot");
  });

  it("rejects update with an unsupported mode", () => {
    expect(() => updateOperatorSessionRequestSchema.parse({ mode: "default" })).toThrow();
  });

  it("rejects empty update (no fields provided)", () => {
    expect(() => updateOperatorSessionRequestSchema.parse({})).toThrow();
  });

  it("rejects name longer than 128 chars", () => {
    expect(() => updateOperatorSessionRequestSchema.parse({ name: "a".repeat(129) })).toThrow();
  });

  it("rejects model longer than 64 chars", () => {
    expect(() => updateOperatorSessionRequestSchema.parse({ model: "m".repeat(65) })).toThrow();
  });

  it("rejects mode outside the known session mode list", () => {
    expect(() => updateOperatorSessionRequestSchema.parse({ mode: "x".repeat(65) })).toThrow();
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

  it("preserves an optional host_id (#556)", () => {
    const parsed = promptRequestSchema.parse({ prompt: "hi", host_id: "tunnel-1" });
    expect(parsed.host_id).toBe("tunnel-1");
  });

  it("rejects host_id longer than 64 chars (#556)", () => {
    expect(() => promptRequestSchema.parse({ prompt: "hi", host_id: "x".repeat(65) })).toThrow();
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

  it("rejects empty base_url because LOCAL_HOST is not stored through the schema", () => {
    expect(() =>
      hostProfileSchema.parse({
        id: "local",
        label: "Local (same-origin)",
        base_url: "",
        token: "",
        cli_kind: "copilot",
        is_default: true,
      })
    ).toThrow();
  });

  it("rejects empty base_url on remote host input", () => {
    expect(() =>
      hostProfileSchema.parse({
        id: "remote-empty",
        label: "Remote Empty",
        base_url: "",
        token: "",
        cli_kind: "copilot",
        is_default: false,
      })
    ).toThrow();
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

  // ── hostProfileSchema base_url URL validation (issue #45) ─────────────

  it("accepts https base_url", () => {
    const parsed = hostProfileSchema.parse({
      id: "h1",
      label: "HTTPS Host",
      base_url: "https://abc123.ngrok.io",
      token: "",
      cli_kind: "copilot",
      is_default: false,
    });
    expect(parsed.base_url).toBe("https://abc123.ngrok.io");
  });

  it("accepts https base_url with path prefix", () => {
    const parsed = hostProfileSchema.parse({
      id: "h2",
      label: "HTTPS Prefixed",
      base_url: "https://host.example.com/prefix/",
      token: "",
      cli_kind: "copilot",
      is_default: false,
    });
    expect(parsed.base_url).toBe("https://host.example.com/prefix/");
  });

  it("accepts http base_url (local tunnel or HTTP server)", () => {
    const parsed = hostProfileSchema.parse({
      id: "h3",
      label: "HTTP Host",
      base_url: "http://tunnel.example.com",
      token: "",
      cli_kind: "copilot",
      is_default: false,
    });
    expect(parsed.base_url).toBe("http://tunnel.example.com");
  });

  it("rejects base_url with no scheme (bare hostname)", () => {
    expect(() =>
      hostProfileSchema.parse({
        id: "h4",
        label: "Bad",
        base_url: "localhost",
        token: "",
        cli_kind: "copilot",
        is_default: false,
      })
    ).toThrow();
  });

  it("rejects base_url with javascript: scheme", () => {
    expect(() =>
      hostProfileSchema.parse({
        id: "h5",
        label: "XSS",
        base_url: "javascript:alert(1)",
        token: "",
        cli_kind: "copilot",
        is_default: false,
      })
    ).toThrow();
  });

  it("rejects base_url that is not a valid URL", () => {
    expect(() =>
      hostProfileSchema.parse({
        id: "h6",
        label: "Garbage",
        base_url: "not a url at all",
        token: "",
        cli_kind: "copilot",
        is_default: false,
      })
    ).toThrow();
  });

  // ── Browse host bootstrap ──────────────────────────────────────────────

  it("parses a valid browse-host bootstrap response", () => {
    const parsed = browseHostBootstrapSchema.parse({
      schema: "browse-host/1",
      status: "ok",
      auth: "token",
      manual_token_required: true,
      capabilities: ["discovery", "healthz", "api"],
      cors_origins_configured: true,
    });
    expect(parsed.schema).toBe("browse-host/1");
    expect(parsed.auth).toBe("token");
  });

  it("rejects unknown browse-host bootstrap schema versions", () => {
    expect(() =>
      browseHostBootstrapSchema.parse({
        schema: "browse-host/2",
        status: "ok",
        auth: "open",
        manual_token_required: false,
        capabilities: [],
        cors_origins_configured: true,
      })
    ).toThrow();
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

  it("accepts capabilities with protocol field (modern backend)", () => {
    const parsed = hostCapabilitiesSchema.parse({
      cli_kind: "copilot",
      version: "2.0.0",
      supported_modes: ["ask", "edit"],
      supported_features: ["chat", "sessions", "search"],
      protocol: "v2",
    });
    expect(parsed.protocol).toBe("v2");
    expect(parsed.supported_features).toContain("chat");
  });

  it("accepts capabilities without protocol field (legacy backend)", () => {
    const parsed = hostCapabilitiesSchema.parse({
      cli_kind: "copilot",
      supported_modes: ["ask"],
      supported_features: [],
      // no protocol field
    });
    expect(parsed.protocol).toBeUndefined();
  });

  it("accepts null protocol in capabilities", () => {
    const parsed = hostCapabilitiesSchema.parse({
      cli_kind: "copilot",
      supported_modes: [],
      supported_features: [],
      protocol: null,
    });
    expect(parsed.protocol).toBeNull();
  });

  it("cliKindSchema accepts any non-empty string", () => {
    expect(cliKindSchema.parse("copilot")).toBe("copilot");
    expect(cliKindSchema.parse("claude")).toBe("claude");
    expect(cliKindSchema.parse("some-future-cli")).toBe("some-future-cli");
    expect(() => cliKindSchema.parse("")).toThrow();
  });
});

// ── Issue #518: root-level has_operator_runs on session detail ──────────────

describe("sessionDetailResponseSchema – has_operator_runs (issue #518)", () => {
  const baseMeta = {
    id: "33169957-0dc1-4998-86c0-d2beba02e8b4",
    path: null,
    summary: null,
    source: "copilot",
    event_count_estimate: 0,
    fts_indexed_at: null,
    file_mtime: null,
  };

  it("parses payloads with has_operator_runs: true at root", () => {
    const parsed = sessionDetailResponseSchema.parse({
      meta: baseMeta,
      timeline: [],
      has_operator_runs: true,
    });
    expect(parsed.has_operator_runs).toBe(true);
  });

  it("parses payloads with has_operator_runs: false at root", () => {
    const parsed = sessionDetailResponseSchema.parse({
      meta: baseMeta,
      timeline: [],
      has_operator_runs: false,
    });
    expect(parsed.has_operator_runs).toBe(false);
  });

  it("defaults has_operator_runs to false when absent (older backend)", () => {
    const parsed = sessionDetailResponseSchema.parse({ meta: baseMeta, timeline: [] });
    expect(parsed.has_operator_runs).toBe(false);
  });

  it("rejects non-boolean has_operator_runs", () => {
    expect(() =>
      sessionDetailResponseSchema.parse({
        meta: baseMeta,
        timeline: [],
        has_operator_runs: "yes",
      })
    ).toThrow();
  });
});

describe("cliSessionSchema", () => {
  it("accepts a valid UUID cli_session_id", () => {
    const parsed = cliSessionSchema.parse({
      cli_session_id: "f47ac10b-58cc-4372-a567-0e02b2c3d479",
      title: "Test session",
      mtime: "2024-06-01T10:00:00Z",
    });
    expect(parsed.cli_session_id).toBe("f47ac10b-58cc-4372-a567-0e02b2c3d479");
  });

  it("rejects a non-UUID cli_session_id", () => {
    expect(() =>
      cliSessionSchema.parse({
        cli_session_id: "e2e-session-0001-abcdef",
        title: "Test session",
        mtime: "2024-06-01T10:00:00Z",
      })
    ).toThrow();
  });
});

describe("adoptCliSessionRequestSchema", () => {
  it("accepts a valid UUID cli_session_id", () => {
    const parsed = adoptCliSessionRequestSchema.parse({
      cli_session_id: "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    });
    expect(parsed.cli_session_id).toBe("f47ac10b-58cc-4372-a567-0e02b2c3d479");
  });

  it("rejects a non-UUID cli_session_id", () => {
    expect(() => adoptCliSessionRequestSchema.parse({ cli_session_id: "not-a-uuid" })).toThrow();
  });
});

// ── Skeleton projection schemas (issue #538 / #539) ──────────────────────────

describe("browseDebugSkeletonEntrySchema (projection=skeleton)", () => {
  const VALID_SKELETON = {
    idx: 0,
    timestamp: "2024-01-01T12:00:00.000Z",
    kind: "tool_call",
    duration_ms: 42,
    status: "ok",
    span_id: "abcdef0123456789",
    parent_span_id: null,
  };

  it("parses a valid skeleton entry", () => {
    const parsed = browseDebugSkeletonEntrySchema.parse(VALID_SKELETON);
    expect(parsed.idx).toBe(0);
    expect(parsed.kind).toBe("tool_call");
    expect(parsed.duration_ms).toBe(42);
    expect(parsed.status).toBe("ok");
  });

  it("accepts all null-able fields as null", () => {
    expect(() =>
      browseDebugSkeletonEntrySchema.parse({
        idx: 1,
        timestamp: null,
        kind: "generic",
        duration_ms: null,
        status: null,
        span_id: null,
        parent_span_id: null,
      })
    ).not.toThrow();
  });

  it("strips forbidden fields — message, source, tool_name, attrs, redacted, level", () => {
    const raw = {
      ...VALID_SKELETON,
      message: "secret content",
      source: "operator_console",
      tool_name: "bash",
      attrs: { hook_status: "ok" },
      redacted: true,
      level: "info",
    };
    const parsed = browseDebugSkeletonEntrySchema.parse(raw);
    expect("message" in parsed).toBe(false);
    expect("source" in parsed).toBe(false);
    expect("tool_name" in parsed).toBe(false);
    expect("attrs" in parsed).toBe(false);
    expect("redacted" in parsed).toBe(false);
    expect("level" in parsed).toBe(false);
  });

  it("rejects negative idx", () => {
    expect(() => browseDebugSkeletonEntrySchema.parse({ ...VALID_SKELETON, idx: -1 })).toThrow();
  });

  it("rejects missing idx", () => {
    const { idx: _, ...withoutIdx } = VALID_SKELETON;
    expect(() => browseDebugSkeletonEntrySchema.parse(withoutIdx)).toThrow();
  });

  it("accepts unknown future kind strings (open kind schema)", () => {
    expect(() =>
      browseDebugSkeletonEntrySchema.parse({ ...VALID_SKELETON, kind: "future_kind_v99" })
    ).not.toThrow();
  });
});

describe("sessionDebugSkeletonResponseSchema", () => {
  it("parses a valid skeleton response envelope", () => {
    const raw = {
      schema_version: "1",
      session_id: "sess-001",
      from: 0,
      limit: 1000,
      total: 2,
      has_more: false,
      entries: [
        {
          idx: 0,
          timestamp: null,
          kind: "turn_start",
          duration_ms: null,
          status: null,
          span_id: null,
          parent_span_id: null,
        },
        {
          idx: 1,
          timestamp: "2024-01-01T12:00:01Z",
          kind: "tool_call",
          duration_ms: 50,
          status: "ok",
          span_id: "aa",
          parent_span_id: null,
        },
      ],
    };
    const parsed = sessionDebugSkeletonResponseSchema.parse(raw);
    expect(parsed.session_id).toBe("sess-001");
    expect(parsed.entries).toHaveLength(2);
    expect(parsed.entries[1].span_id).toBe("aa");
  });

  it("rejects when entries have forbidden fields (strict skeleton)", () => {
    // message field is not in skeleton schema → should be stripped (no error, just stripped)
    const raw = {
      schema_version: "1",
      session_id: "s",
      from: 0,
      limit: 10,
      total: 1,
      has_more: false,
      entries: [
        {
          idx: 0,
          timestamp: null,
          kind: "generic",
          duration_ms: null,
          status: null,
          span_id: null,
          parent_span_id: null,
          message: "hidden",
        },
      ],
    };
    const parsed = sessionDebugSkeletonResponseSchema.parse(raw);
    expect("message" in parsed.entries[0]).toBe(false);
  });
});

describe("sessionDebugLogResponseSchema (full projection — unchanged)", () => {
  it("still parses full BrowseDebugEntry entries with all fields", () => {
    const raw = {
      schema_version: "1",
      session_id: "s1",
      from: 0,
      limit: 10,
      total: 1,
      has_more: false,
      entries: [
        {
          idx: 0,
          timestamp: "2024-01-01T12:00:00Z",
          kind: "tool_call",
          level: "info",
          source: "operator_console",
          message: "running bash",
          tool_name: "bash",
          duration_ms: 100,
          span_id: "abc123",
          parent_span_id: null,
          status: "ok",
          attrs: { tool_status: "ok" },
          redacted: false,
        },
      ],
    };
    const parsed = sessionDebugLogResponseSchema.parse(raw);
    expect(parsed.entries[0].tool_name).toBe("bash");
    expect(parsed.entries[0].message).toBe("running bash");
    expect(parsed.entries[0].attrs).toEqual({ tool_status: "ok" });
  });
});

describe("browseDebugEntrySchema (full — unchanged by skeleton addition)", () => {
  it("still requires message, source, redacted fields", () => {
    expect(() =>
      browseDebugEntrySchema.parse({
        idx: 0,
        timestamp: null,
        kind: "generic",
        duration_ms: null,
        status: null,
        span_id: null,
        parent_span_id: null,
        // Missing level, source, message, tool_name, attrs, redacted
      })
    ).toThrow();
  });
});

// ── Flight Recorder v3 (synthesis §4b / §6b) ───────────────────────────────

import {
  browseCheckpointSummarySchema,
  browseCheckpointsResponseSchema,
  browseRewindSnapshotSummarySchema,
  browseRewindSnapshotsResponseSchema,
} from "./schemas";

describe("Flight Recorder v3 — browseCheckpointsResponseSchema", () => {
  const happy = {
    schema_version: "1",
    session_id: "33169957-0dc1-4998-86c0-d2beba02e8b4",
    total: 1,
    checkpoints: [
      {
        seq: 1,
        title: "Wave 1 shipped",
        file_basename: "001-wave1.md",
        byte_size: 4096,
        mtime_iso: "2026-05-21T17:08:31.421Z",
        sections: {
          overview: true,
          history: true,
          work_done: true,
          technical_details: false,
          important_files: false,
          next_steps: true,
        },
      },
    ],
  };

  it("parses a happy-path response", () => {
    expect(() => browseCheckpointsResponseSchema.parse(happy)).not.toThrow();
  });

  it("rejects extra top-level keys (.strict)", () => {
    expect(() => browseCheckpointsResponseSchema.parse({ ...happy, extra: "leak" })).toThrow();
  });

  it("rejects extra summary keys (.strict)", () => {
    const bad = {
      ...happy,
      checkpoints: [{ ...happy.checkpoints[0], evil: 1 }],
    };
    expect(() => browseCheckpointsResponseSchema.parse(bad)).toThrow();
  });

  it("rejects extra section flags (.strict)", () => {
    const bad = {
      ...happy,
      checkpoints: [
        {
          ...happy.checkpoints[0],
          sections: { ...happy.checkpoints[0].sections, secret: true },
        },
      ],
    };
    expect(() => browseCheckpointsResponseSchema.parse(bad)).toThrow();
  });

  it("rejects negative byte_size", () => {
    const bad = {
      ...happy,
      checkpoints: [{ ...happy.checkpoints[0], byte_size: -1 }],
    };
    expect(() => browseCheckpointsResponseSchema.parse(bad)).toThrow();
  });

  it("summary schema rejects missing sections", () => {
    const { sections, ...rest } = happy.checkpoints[0];
    void sections;
    expect(() => browseCheckpointSummarySchema.parse(rest)).toThrow();
  });

  it("accepts null mtime_iso (backend could not stat file)", () => {
    const withNullMtime = {
      ...happy,
      checkpoints: [{ ...happy.checkpoints[0], mtime_iso: null }],
    };
    expect(() => browseCheckpointsResponseSchema.parse(withNullMtime)).not.toThrow();
  });

  it("rejects missing mtime_iso field (.strict)", () => {
    const { mtime_iso, ...rest } = happy.checkpoints[0];
    void mtime_iso;
    expect(() => browseCheckpointSummarySchema.parse(rest)).toThrow();
  });
});

describe("Flight Recorder v3 — browseRewindSnapshotsResponseSchema", () => {
  const happy = {
    schema_version: "1",
    session_id: "33169957-0dc1-4998-86c0-d2beba02e8b4",
    total: 1,
    snapshots: [
      {
        snapshot_id: "ed7b1d8a-72d5-4c5d-9b71-1e0e3a3e1234",
        timestamp: "2026-05-21T16:39:31.728Z",
        git_commit: "0".repeat(40),
        git_branch: "main",
        file_count: 3,
        user_message_present: true,
        user_message_byte_size: 11,
        event_span_id: "0123456789abcdef",
      },
    ],
  };

  it("parses a happy-path response", () => {
    expect(() => browseRewindSnapshotsResponseSchema.parse(happy)).not.toThrow();
  });

  it("allows nullable commit/branch/timestamp/event_span_id", () => {
    const nulls = {
      ...happy,
      snapshots: [
        {
          ...happy.snapshots[0],
          timestamp: null,
          git_commit: null,
          git_branch: null,
          event_span_id: null,
        },
      ],
    };
    expect(() => browseRewindSnapshotsResponseSchema.parse(nulls)).not.toThrow();
  });

  it("rejects userMessage text leak (.strict)", () => {
    const bad = {
      ...happy,
      snapshots: [{ ...happy.snapshots[0], userMessage: "leak" }],
    };
    expect(() => browseRewindSnapshotsResponseSchema.parse(bad)).toThrow();
  });

  it("rejects files{} leak (.strict)", () => {
    const bad = {
      ...happy,
      snapshots: [{ ...happy.snapshots[0], files: { a: {} } }],
    };
    expect(() => browseRewindSnapshotsResponseSchema.parse(bad)).toThrow();
  });

  it("rejects raw eventId leak (.strict)", () => {
    const bad = {
      ...happy,
      snapshots: [{ ...happy.snapshots[0], eventId: "33169957-0dc1-4998-86c0-d2beba02e8b4" }],
    };
    expect(() => browseRewindSnapshotsResponseSchema.parse(bad)).toThrow();
  });

  it("rejects backupHashes leak (.strict)", () => {
    const bad = {
      ...happy,
      snapshots: [{ ...happy.snapshots[0], backupHashes: ["x"] }],
    };
    expect(() => browseRewindSnapshotsResponseSchema.parse(bad)).toThrow();
  });

  it("summary schema requires snapshot_id", () => {
    const { snapshot_id, ...rest } = happy.snapshots[0];
    void snapshot_id;
    expect(() => browseRewindSnapshotSummarySchema.parse(rest)).toThrow();
  });

  it("rejects negative file_count", () => {
    const bad = {
      ...happy,
      snapshots: [{ ...happy.snapshots[0], file_count: -1 }],
    };
    expect(() => browseRewindSnapshotsResponseSchema.parse(bad)).toThrow();
  });
});

// ── Subagent Activity schemas ─────────────────────────────────────────────

import {
  subagentActivityEntrySchema,
  subagentActivityResponseSchema,
  subagentInternalsEntrySchema,
  subagentInternalsResponseSchema,
} from "./schemas";

describe("subagentActivityEntrySchema", () => {
  const happyEntry = {
    span_id: "0123456789abcdef",
    agent_name: "coder",
    agent_display_name: "Coder Agent",
    model: "gpt-4",
    status: "completed" as const,
    started_at: "2025-01-01T00:00:00Z",
    ended_at: "2025-01-01T00:00:01Z",
    duration_ms: 1234.0,
    total_tool_calls: 3,
    total_tokens: 500,
    error_category: null,
    error_preview: null,
    start_idx: 0,
    end_idx: 1,
    redacted: false,
  };

  it("parses a valid completed entry", () => {
    expect(() => subagentActivityEntrySchema.parse(happyEntry)).not.toThrow();
  });

  it("parses a running entry with many null fields", () => {
    const running = {
      ...happyEntry,
      status: "running" as const,
      ended_at: null,
      duration_ms: null,
      total_tool_calls: null,
      total_tokens: null,
      error_category: null,
      error_preview: null,
      end_idx: null,
    };
    expect(() => subagentActivityEntrySchema.parse(running)).not.toThrow();
  });

  it("parses a failed entry with error_category and error_preview", () => {
    const failed = {
      ...happyEntry,
      status: "failed" as const,
      error_category: "rate_limited" as const,
      error_preview: "Authentication failed: [REDACTED]",
    };
    expect(() => subagentActivityEntrySchema.parse(failed)).not.toThrow();
  });

  it("rejects unknown status value", () => {
    const bad = { ...happyEntry, status: "unknown_status" };
    expect(() => subagentActivityEntrySchema.parse(bad)).toThrow();
  });

  it("rejects unknown error_category value", () => {
    const bad = { ...happyEntry, status: "failed", error_category: "raw_vendor_code" };
    expect(() => subagentActivityEntrySchema.parse(bad)).toThrow();
  });

  it("rejects extra fields (strict schema — no passthrough)", () => {
    const bad = { ...happyEntry, agentDescription: "SECRET" };
    expect(() => subagentActivityEntrySchema.parse(bad)).toThrow();
  });

  it("rejects toolCallId leakage (strict schema)", () => {
    const bad = { ...happyEntry, toolCallId: "tcid-123" };
    expect(() => subagentActivityEntrySchema.parse(bad)).toThrow();
  });

  it("rejects agentId leakage (strict schema)", () => {
    const bad = { ...happyEntry, agentId: "agent-abc" };
    expect(() => subagentActivityEntrySchema.parse(bad)).toThrow();
  });

  it("rejects error_preview longer than 120 chars", () => {
    const bad = { ...happyEntry, error_preview: "x".repeat(121) };
    expect(() => subagentActivityEntrySchema.parse(bad)).toThrow();
  });

  it("accepts error_preview of exactly 120 chars", () => {
    const ok = { ...happyEntry, error_preview: "x".repeat(120) };
    expect(() => subagentActivityEntrySchema.parse(ok)).not.toThrow();
  });

  it("rejects negative total_tool_calls", () => {
    const bad = { ...happyEntry, total_tool_calls: -1 };
    expect(() => subagentActivityEntrySchema.parse(bad)).toThrow();
  });

  it("rejects negative total_tokens", () => {
    const bad = { ...happyEntry, total_tokens: -1 };
    expect(() => subagentActivityEntrySchema.parse(bad)).toThrow();
  });
});

describe("subagentActivityResponseSchema", () => {
  const happyResponse = {
    schema_version: "1" as const,
    session_id: "33169957-0dc1-4998-86c0-d2beba02e8b4",
    total_subagents_seen: 2,
    returned: 2,
    cap: 1000,
    truncated: false,
    dropped_pending_starts: 0,
    entries: [
      {
        span_id: "0123456789abcdef",
        agent_name: "coder",
        agent_display_name: null,
        model: "gpt-4",
        status: "completed" as const,
        started_at: "2025-01-01T00:00:00Z",
        ended_at: "2025-01-01T00:00:01Z",
        duration_ms: 1000,
        total_tool_calls: 1,
        total_tokens: 200,
        error_category: null,
        error_preview: null,
        start_idx: 0,
        end_idx: 1,
        redacted: false,
      },
    ],
  };

  it("parses a happy-path response", () => {
    expect(() => subagentActivityResponseSchema.parse(happyResponse)).not.toThrow();
  });

  it("rejects schema_version other than '1'", () => {
    const bad = { ...happyResponse, schema_version: "2" };
    expect(() => subagentActivityResponseSchema.parse(bad)).toThrow();
  });

  it("rejects extra fields on response envelope (strict)", () => {
    const bad = { ...happyResponse, extra_field: "leak" };
    expect(() => subagentActivityResponseSchema.parse(bad)).toThrow();
  });

  it("parses empty entries list", () => {
    const empty = { ...happyResponse, entries: [], returned: 0, total_subagents_seen: 0 };
    expect(() => subagentActivityResponseSchema.parse(empty)).not.toThrow();
  });

  it("rejects truncated not boolean", () => {
    const bad = { ...happyResponse, truncated: 0 };
    expect(() => subagentActivityResponseSchema.parse(bad)).toThrow();
  });

  it("rejects cap <= 0", () => {
    const bad = { ...happyResponse, cap: 0 };
    expect(() => subagentActivityResponseSchema.parse(bad)).toThrow();
  });
});

// ── Subagent Internals schemas ───────────────────────────────────────────────

describe("subagentInternalsResponseSchema", () => {
  const happyEntry = {
    agent_key_hash: "0123456789abcdef",
    span_id: "span-a",
    agent_name: "code-review",
    agent_display_name: "Code Review",
    model: "claude-sonnet-4.6",
    status: "completed" as const,
    started_at: "2025-01-01T00:00:00Z",
    ended_at: "2025-01-01T00:00:05Z",
    duration_ms: 5000,
    start_idx: 0,
    end_idx: 5,
    redacted: false,
    internals: {
      internal_event_count: 3,
      tool_call_count: 1,
      tool_success_count: 1,
      tool_failure_count: 0,
      llm_turn_count: 1,
      output_tokens_total: 700,
      tool_names: ["view"],
      tools: [
        {
          idx: 1,
          end_idx: 2,
          tool_name: "view",
          status: "completed" as const,
          started_at: "2025-01-01T00:00:01Z",
          ended_at: "2025-01-01T00:00:02Z",
          duration_ms: 1000,
          input_bytes: 12,
          output_bytes: 2048,
        },
      ],
      tools_truncated: false,
      model_events: [
        {
          idx: 3,
          timestamp: "2025-01-01T00:00:03Z",
          output_tokens: 700,
          tool_request_count: 1,
        },
      ],
      model_events_truncated: false,
    },
  };

  const happyResponse = {
    schema_version: "1" as const,
    session_id: "33169957-0dc1-4998-86c0-d2beba02e8b4",
    total_agents_seen: 1,
    returned: 1,
    cap: 1000,
    truncated: false,
    dropped_pending_starts: 0,
    skill_correlation_supported: false,
    uncorrelated_skill_invocations: 1,
    session_skill_names: ["code-reviewer"],
    entries: [happyEntry],
  };

  it("parses a happy-path response", () => {
    expect(() => subagentInternalsResponseSchema.parse(happyResponse)).not.toThrow();
  });

  it("parses a running row with nullable end fields", () => {
    const running = {
      ...happyEntry,
      status: "running" as const,
      ended_at: null,
      duration_ms: null,
      end_idx: null,
      internals: {
        ...happyEntry.internals,
        tools: [
          {
            ...happyEntry.internals.tools[0],
            end_idx: null,
            status: "running" as const,
            ended_at: null,
            duration_ms: null,
            input_bytes: null,
            output_bytes: null,
          },
        ],
      },
    };
    expect(() => subagentInternalsEntrySchema.parse(running)).not.toThrow();
  });

  it("rejects raw id leakage and agentDescription via strict schema", () => {
    expect(() =>
      subagentInternalsEntrySchema.parse({ ...happyEntry, toolCallId: "raw" })
    ).toThrow();
    expect(() =>
      subagentInternalsEntrySchema.parse({ ...happyEntry, agentDescription: "SECRET" })
    ).toThrow();
  });

  it("requires opaque 16-hex agent_key_hash", () => {
    expect(() =>
      subagentInternalsEntrySchema.parse({ ...happyEntry, agent_key_hash: "raw-id" })
    ).toThrow();
  });

  it("rejects extra fields on nested tool and model events", () => {
    const badTool = {
      ...happyEntry,
      internals: {
        ...happyEntry.internals,
        tools: [{ ...happyEntry.internals.tools[0], args: { path: "/secret" } }],
      },
    };
    const badModel = {
      ...happyEntry,
      internals: {
        ...happyEntry.internals,
        model_events: [{ ...happyEntry.internals.model_events[0], content: "secret" }],
      },
    };
    expect(() => subagentInternalsEntrySchema.parse(badTool)).toThrow();
    expect(() => subagentInternalsEntrySchema.parse(badModel)).toThrow();
  });

  it("rejects extra fields on response envelope", () => {
    expect(() =>
      subagentInternalsResponseSchema.parse({ ...happyResponse, raw_agent_ids: [] })
    ).toThrow();
  });

  it("rejects negative counters", () => {
    const bad = {
      ...happyResponse,
      entries: [
        {
          ...happyEntry,
          internals: { ...happyEntry.internals, tool_call_count: -1 },
        },
      ],
    };
    expect(() => subagentInternalsResponseSchema.parse(bad)).toThrow();
  });
});

// ── Mission Atlas schema tests ────────────────────────────────────────────────

const HAPPY_ATLAS_RESPONSE = {
  schema_version: "1" as const,
  session_id: "abc-123",
  total_events: 55000,
  event_file_bytes: 4194304,
  first_event_at: "2025-01-01T00:00:00Z",
  last_event_at: "2025-01-01T01:30:00Z",
  duration_ms: 5400000,
  bucket_count: 3,
  buckets: [
    {
      bucket_idx: 0,
      start_idx: 0,
      end_idx: 999,
      event_count: 1000,
      start_rel_ms: 0,
      end_rel_ms: 60000,
      ts_start: "2025-01-01T00:00:00Z",
      ts_end: "2025-01-01T00:01:00Z",
      lanes: { tool: 400, model: 300, turn: 300 },
      dominant_lane: "tool",
      error_count: 0,
      is_gap: false,
    },
    {
      bucket_idx: 1,
      start_idx: null,
      end_idx: null,
      event_count: 0,
      start_rel_ms: 60000,
      end_rel_ms: 120000,
      ts_start: "2025-01-01T00:01:00Z",
      ts_end: "2025-01-01T00:02:00Z",
      lanes: {},
      dominant_lane: null,
      error_count: 0,
      is_gap: true,
    },
    {
      bucket_idx: 2,
      start_idx: 2000,
      end_idx: 2999,
      event_count: 500,
      start_rel_ms: 120000,
      end_rel_ms: 180000,
      ts_start: null,
      ts_end: null,
      lanes: { error: 50, tool: 450 },
      dominant_lane: "tool",
      error_count: 50,
      is_gap: false,
    },
  ],
  lane_totals: {
    tool: 850,
    hook: 10,
    skill: 5,
    subagent: 3,
    model: 300,
    turn: 300,
    system: 20,
    error: 50,
    generic: 12,
  },
  top_tools: [
    { name: "bash", count: 300 },
    { name: "view", count: 200 },
  ],
  top_skills: [{ name: "code-reviewer", count: 5 }],
  top_agent_names: [{ name: "general-purpose", count: 3 }],
  milestones: [
    {
      idx: 42,
      timestamp: "2025-01-01T00:00:30Z",
      kind: "checkpoint",
      label: "Checkpoint 1",
      bucket_idx: 0,
    },
    {
      idx: null,
      timestamp: null,
      kind: "task_complete",
      label: "Task done",
      bucket_idx: 2,
    },
  ],
  artifact_counts: {
    checkpoint_files: 2,
    rewind_snapshots: 1,
    todos_total: 10,
    todos_done: 8,
    todos_blocked: 1,
    todo_deps: 5,
    files: 42,
    compactions: 3,
  },
  error_count: 50,
  error_sample: [
    {
      idx: 12,
      timestamp: "2025-01-01T00:00:12Z",
      event_type: "error",
      error_category: "rate_limited",
    },
    { idx: 13, timestamp: null, event_type: "error", error_category: "tool_failure" },
  ],
  caps: { top_n: 20, milestones: 200, error_sample: 5, buckets_min: 8, buckets_max: 200 },
  truncated: { tools: false, skills: false, agents: false, milestones: false },
};

describe("sessionMissionAtlasResponseSchema", () => {
  it("parses a complete valid response", () => {
    const result = sessionMissionAtlasResponseSchema.parse(HAPPY_ATLAS_RESPONSE);
    expect(result.schema_version).toBe("1");
    expect(result.total_events).toBe(55000);
    expect(result.buckets).toHaveLength(3);
    expect(result.buckets[1].is_gap).toBe(true);
    expect(result.milestones[0].idx).toBe(42);
    expect(result.milestones[1].idx).toBeNull();
    expect(result.artifact_counts.checkpoint_files).toBe(2);
    expect(result.lane_totals.tool).toBe(850);
    expect(result.top_tools[0].name).toBe("bash");
  });

  it("accepts nullable optional fields (timestamps, file bytes)", () => {
    const nulled = {
      ...HAPPY_ATLAS_RESPONSE,
      event_file_bytes: null,
      first_event_at: null,
      last_event_at: null,
      duration_ms: null,
    };
    const result = sessionMissionAtlasResponseSchema.parse(nulled);
    expect(result.event_file_bytes).toBeNull();
    expect(result.duration_ms).toBeNull();
  });

  it("rejects extra fields (security: prevents raw content leaking through)", () => {
    expect(() =>
      sessionMissionAtlasResponseSchema.parse({
        ...HAPPY_ATLAS_RESPONSE,
        raw_events: [{ content: "secret" }],
      })
    ).toThrow();
  });

  it("rejects wrong schema_version", () => {
    expect(() =>
      sessionMissionAtlasResponseSchema.parse({ ...HAPPY_ATLAS_RESPONSE, schema_version: "2" })
    ).toThrow();
  });

  it("rejects negative event counts", () => {
    expect(() =>
      sessionMissionAtlasResponseSchema.parse({ ...HAPPY_ATLAS_RESPONSE, total_events: -1 })
    ).toThrow();
  });

  it("rejects negative lane totals", () => {
    expect(() =>
      sessionMissionAtlasResponseSchema.parse({
        ...HAPPY_ATLAS_RESPONSE,
        lane_totals: { ...HAPPY_ATLAS_RESPONSE.lane_totals, tool: -1 },
      })
    ).toThrow();
  });

  it("rejects caps with non-positive values", () => {
    expect(() =>
      sessionMissionAtlasResponseSchema.parse({
        ...HAPPY_ATLAS_RESPONSE,
        caps: { top_n: 0, milestones: 200, error_sample: 5, buckets_min: 8, buckets_max: 200 },
      })
    ).toThrow();
  });
});

describe("missionAtlasBucketSchema", () => {
  it("parses a gap bucket", () => {
    const result = missionAtlasBucketSchema.parse(HAPPY_ATLAS_RESPONSE.buckets[1]);
    expect(result.is_gap).toBe(true);
    expect(result.event_count).toBe(0);
    expect(result.dominant_lane).toBeNull();
  });

  it("rejects extra fields", () => {
    expect(() =>
      missionAtlasBucketSchema.parse({ ...HAPPY_ATLAS_RESPONSE.buckets[0], raw_content: "secret" })
    ).toThrow();
  });
});

describe("missionAtlasMilestoneSchema", () => {
  it("accepts milestone with null idx", () => {
    const result = missionAtlasMilestoneSchema.parse(HAPPY_ATLAS_RESPONSE.milestones[1]);
    expect(result.idx).toBeNull();
    expect(result.kind).toBe("task_complete");
  });

  it("rejects extra fields", () => {
    expect(() =>
      missionAtlasMilestoneSchema.parse({
        ...HAPPY_ATLAS_RESPONSE.milestones[0],
        raw_path: "/secret",
      })
    ).toThrow();
  });

  it("rejects oversized timestamp strings", () => {
    expect(() =>
      missionAtlasMilestoneSchema.parse({
        ...HAPPY_ATLAS_RESPONSE.milestones[0],
        timestamp: "2025-01-01T00:00:00Z" + "X".repeat(80),
      })
    ).toThrow();
  });
});

describe("missionAtlasErrorSampleSchema", () => {
  it("rejects oversized timestamp strings", () => {
    expect(() =>
      missionAtlasErrorSampleSchema.parse({
        idx: 1,
        timestamp: "2025-01-01T00:00:00Z" + "X".repeat(80),
        event_type: "error",
        error_category: "unknown",
      })
    ).toThrow();
  });
});
