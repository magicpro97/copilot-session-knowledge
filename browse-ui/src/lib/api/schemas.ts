import { z } from "zod";

import { MUTABLE_OPERATOR_SESSION_MODES } from "./types";

export const sessionRowSchema = z.object({
  id: z.string(),
  path: z.string().nullable(),
  summary: z.string().nullable(),
  source: z.string().nullable(),
  event_count_estimate: z.number().nullable(),
  fts_indexed_at: z.string().nullable(),
  indexed_at_r: z.string().nullable().optional(),
});

export const sessionMetaSchema = sessionRowSchema.extend({
  file_mtime: z.string().nullable(),
});

export const homeResponseSchema = z.array(sessionRowSchema);

export const sessionListResponseSchema = z.object({
  items: z.array(sessionRowSchema),
  total: z.number(),
  page: z.number(),
  page_size: z.number(),
  has_more: z.boolean(),
});

export const timelineEntrySchema = z.object({
  seq: z.number(),
  title: z.string().nullable(),
  doc_type: z.string().nullable(),
  section_name: z.string().nullable(),
  content: z.string().nullable(),
});

export const sessionDetailResponseSchema = z.object({
  meta: sessionMetaSchema,
  timeline: z.array(timelineEntrySchema),
  // Issue #518: root-level boolean. Default false for backward compatibility
  // with older backends that have not yet been upgraded; the frontend treats
  // missing/unknown as "no operator runs" so it does not issue the request.
  has_operator_runs: z.boolean().optional().default(false),
});

export const timelineEventSchema = z.object({
  event_id: z.number(),
  kind: z.string(),
  preview: z.string(),
  byte_offset: z.number().nullable(),
  // Coerce numeric legacy payloads (REAL from SQLite) to string; null passes through.
  file_mtime: z.preprocess((v) => (typeof v === "number" ? String(v) : v), z.string().nullable()),
  color: z.string(),
});

export const timelineEventsResponseSchema = z.object({
  events: z.array(timelineEventSchema),
  total: z.number(),
  session_id: z.string(),
});

export const mindmapResponseSchema = z.object({
  markdown: z.string(),
  title: z.string(),
});

export const searchResultSchema = z.object({
  type: z.enum(["session", "knowledge"]),
  id: z.union([z.string(), z.number()]),
  title: z.string(),
  snippet: z.string().optional(),
  score: z.number(),
  wing: z.string().optional(),
  kind: z.string().optional(),
});

export const searchResponseSchema = z.object({
  query: z.string(),
  results: z.array(searchResultSchema),
  total: z.number(),
  took_ms: z.number(),
});

export const dashboardTotalsSchema = z.object({
  sessions: z.number(),
  knowledge_entries: z.number(),
  relations: z.number(),
  embeddings: z.number(),
});

export const categoryCountSchema = z.object({
  name: z.string(),
  count: z.number(),
});

export const dayCountSchema = z.object({
  date: z.string(),
  count: z.number(),
});

export const weekCountSchema = z.object({
  week: z.string(),
  count: z.number(),
});

export const moduleCountSchema = z.object({
  module: z.string(),
  count: z.number(),
});

export const wingCountSchema = z.object({
  wing: z.string(),
  count: z.number(),
});

export const redFlagSchema = z.object({
  session_id: z.string(),
  events: z.number(),
  summary: z.string().nullable(),
});

export const dashboardStatsSchema = z.object({
  totals: dashboardTotalsSchema,
  by_category: z.array(categoryCountSchema),
  sessions_per_day: z.array(dayCountSchema),
  top_wings: z.array(wingCountSchema),
  red_flags: z.array(redFlagSchema),
  weekly_mistakes: z.array(weekCountSchema),
  top_modules: z.array(moduleCountSchema),
});

export const graphNodeSchema = z.object({
  id: z.string(),
  kind: z.enum(["entry", "entity"]),
  label: z.string(),
  wing: z.string().optional(),
  room: z.string().optional(),
  category: z.string().optional(),
  color: z.string(),
});

export const graphEdgeSchema = z.object({
  source: z.string(),
  target: z.string(),
  relation: z.string(),
});

export const graphLegacyResponseSchema = z.object({
  nodes: z.array(graphNodeSchema),
  edges: z.array(graphEdgeSchema),
  truncated: z.boolean(),
});

export const graphResponseSchema = graphLegacyResponseSchema;

export const evidenceRelationTypeSchema = z.enum([
  "SAME_SESSION",
  "RESOLVED_BY",
  "TAG_OVERLAP",
  // Keep SAME_TOPIC visible in contract, but UI must gate by data presence.
  "SAME_TOPIC",
]);

export const evidenceRelationTypeValueSchema = z.string();

export const evidenceEdgeSchema = z.object({
  source: z.string(),
  target: z.string(),
  relation_type: evidenceRelationTypeValueSchema,
  confidence: z.number(),
});

export const evidenceGraphMetaSchema = z.object({
  edge_source: z.string(),
  relation_types: z.array(evidenceRelationTypeValueSchema),
});

export const evidenceGraphResponseSchema = z.object({
  nodes: z.array(graphNodeSchema),
  edges: z.array(evidenceEdgeSchema),
  truncated: z.boolean(),
  meta: evidenceGraphMetaSchema.optional(),
});

export const embeddingPointSchema = z.object({
  x: z.number(),
  y: z.number(),
  id: z.number(),
  title: z.string(),
  category: z.string(),
});

export const embeddingProjectionSchema = z.object({
  points: z.array(embeddingPointSchema),
  count: z.number().int().nonnegative(),
  cached: z.boolean(),
});

export const similarityNeighborSchema = z.object({
  id: z.number(),
  title: z.string(),
  category: z.string(),
  score: z.number(),
});

export const similarityNeighborsByEntrySchema = z.object({
  entry_id: z.number(),
  neighbors: z.array(similarityNeighborSchema),
});

export const similarityResponseSchema = z.object({
  results: z.array(similarityNeighborsByEntrySchema),
  meta: z
    .object({
      method: z.string().optional(),
      k: z.number().optional(),
    })
    .passthrough()
    .optional(),
});

export const communityTopCountSchema = z.object({
  name: z.string(),
  count: z.number(),
});

export const communityRepresentativeEntrySchema = z.object({
  id: z.number(),
  title: z.string(),
  category: z.string(),
});

export const communitySummarySchema = z.object({
  id: z.string(),
  label: z.string().optional(),
  entry_count: z.number(),
  wings: z.array(z.string()).optional(),
  top_categories: z.array(communityTopCountSchema),
  top_relation_types: z
    .array(
      z.object({
        type: evidenceRelationTypeValueSchema,
        count: z.number(),
      })
    )
    .optional(),
  representative_entries: z.array(communityRepresentativeEntrySchema),
});

export const communitiesResponseSchema = z.object({
  communities: z.array(communitySummarySchema),
});

export const liveEventSchema = z.object({
  id: z.number(),
  category: z.string(),
  title: z.string(),
  wing: z.string(),
  room: z.string(),
  created_at: z.string(),
});

export const diffCheckpointSchema = z.object({
  seq: z.number(),
  title: z.string(),
  file: z.string(),
});

export const diffResultSchema = z.object({
  session_id: z.string(),
  from: diffCheckpointSchema,
  to: diffCheckpointSchema,
  unified_diff: z.string(),
  files: z.array(
    z.object({
      from: z.string(),
      to: z.string(),
    })
  ),
  stats: z.object({
    added: z.number(),
    removed: z.number(),
  }),
});

export const evalAggRowSchema = z.object({
  query: z.string(),
  up: z.number(),
  down: z.number(),
  neutral: z.number(),
  total: z.number(),
});

export const evalCommentSchema = z.object({
  query: z.string(),
  result_id: z.string(),
  verdict: z.union([z.literal(-1), z.literal(0), z.literal(1)]),
  comment: z.string(),
  created_at: z.string(),
});

export const evalResponseSchema = z.object({
  aggregation: z.array(evalAggRowSchema),
  recent_comments: z.array(evalCommentSchema),
});

export const sessionCompareDataSchema = z.object({
  session: sessionMetaSchema.nullable(),
  timeline: z.array(timelineEntrySchema),
});

export const compareResponseSchema = z.object({
  a: sessionCompareDataSchema,
  b: sessionCompareDataSchema,
});

export const healthResponseSchema = z.object({
  status: z.string(),
  schema_version: z.number(),
  sessions: z.number(),
  knowledge_entries: z.number().optional(),
  last_indexed_at: z.string().nullable().optional(),
  sync_status_endpoint: z.string().optional(),
});

/**
 * Shared audit check Zod schema — used by TrendScout, Tentacle, and SkillMetrics routes.
 */
export const auditCheckSchema = z.object({
  id: z.string(),
  title: z.string(),
  status: z.string(),
  detail: z.string(),
});

/**
 * Shared audit block Zod schema — wraps a summary and an array of check items.
 */
export const auditBlockSchema = z.object({
  summary: z.object({
    ok: z.boolean(),
    total_checks: z.number(),
    warning_checks: z.number(),
  }),
  checks: z.array(auditCheckSchema),
});

/**
 * Shared operator-action Zod schema used across all 4 browse diagnostics routes.
 *
 * Required: id, title, description, command, safe (always true — enforced by literal).
 * Optional context fields are route-specific and validated permissively:
 *   - requires_configured_gateway — only in sync actions
 *   - requires_configured_target  — only in scout actions
 */
export const operatorActionSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string(),
  command: z.string().refine((value) => value.trim().length > 0, {
    message: "Operator actions must provide a non-empty command.",
  }),
  safe: z.literal(true),
  requires_configured_gateway: z.boolean().optional(),
  requires_configured_target: z.boolean().optional(),
});

export const syncOperatorActionSchema = operatorActionSchema.extend({
  requires_configured_gateway: z.boolean(),
});

export const trendScoutOperatorActionSchema = operatorActionSchema.extend({
  requires_configured_target: z.boolean(),
});

export const syncConnectionStatusSchema = z.object({
  configured: z.boolean(),
  endpoint: z.string().nullable(),
  config_path: z.string(),
  target: z.string().optional(),
});

export const syncFailureInfoSchema = z.object({
  failed_at: z.string(),
  error_message: z.string(),
  retry_count: z.number(),
});

export const syncRuntimeStatusSchema = z.object({
  generated_at: z.string(),
  db_path: z.string(),
  db_mode: z.string(),
  sync_tables: z.record(z.string(), z.boolean()),
  sync_tables_ready: z.boolean(),
  available_sync_tables: z.number(),
  total_sync_tables: z.number(),
  failed_txns: z.number(),
});

export const syncStatusResponseSchema = z.object({
  status: z.string(),
  configured: z.boolean(),
  connection: syncConnectionStatusSchema,
  rollout: z
    .object({
      client_contract: z.string(),
      direct_db_sync: z.boolean(),
      reference_gateway: z.object({
        mode: z.string(),
        description: z.string(),
      }),
      provider_gateway: z.object({
        mode: z.string(),
        recommended: z.string(),
        description: z.string(),
      }),
    })
    .optional(),
  runtime: syncRuntimeStatusSchema,
  operator_actions: z.array(syncOperatorActionSchema),
  local_replica_id: z.string().nullable(),
  pending_txns: z.number(),
  pending_ops: z.number(),
  committed_txns: z.number(),
  failed_txns: z.number(),
  failed_ops: z.number(),
  cursor_count: z.number(),
  last_committed_at: z.string().nullable(),
  last_failure: syncFailureInfoSchema.nullable(),
});

export const trendScoutConfigStatusSchema = z.object({
  configured: z.boolean(),
  config_path: z.string(),
  script_path: z.string(),
  target_repo: z.string().nullable(),
});

export const trendScoutAnalysisPreviewSchema = z.object({
  enabled: z.boolean(),
  model: z.string(),
  token_env: z.string(),
  token_present: z.boolean(),
});

export const trendScoutGraceWindowStatusSchema = z.object({
  enabled: z.boolean(),
  grace_window_hours: z.number(),
  state_file: z.string(),
  state_file_exists: z.boolean(),
  last_run_utc: z.string().nullable(),
  elapsed_hours: z.number().nullable(),
  remaining_hours: z.number().nullable(),
  would_skip_without_force: z.boolean(),
  reason: z.string().nullable(),
});

export const trendScoutAuditCheckSchema = auditCheckSchema;

export const trendScoutDiscoveryLaneSchema = z.object({
  name: z.string(),
  keyword_count: z.number(),
  topic_count: z.number(),
  language: z.string().nullable(),
  min_stars: z.number(),
});

export const trendScoutStatusResponseSchema = z.object({
  status: z.string(),
  configured: z.boolean(),
  config: trendScoutConfigStatusSchema,
  analysis: trendScoutAnalysisPreviewSchema,
  grace_window: trendScoutGraceWindowStatusSchema,
  audit: auditBlockSchema,
  operator_actions: z.array(trendScoutOperatorActionSchema),
  discovery_lanes: z.array(trendScoutDiscoveryLaneSchema).optional(),
  runtime: z.object({
    generated_at: z.string(),
  }),
});

export const tentacleWorktreeInfoSchema = z.object({
  prepared: z.boolean(),
  path: z.string(),
  stale: z.boolean(),
});

export const tentacleVerificationInfoSchema = z.object({
  coverage_exists: z.boolean(),
  total: z.number(),
  passed: z.number(),
  failed: z.number(),
});

export const tentacleEntrySchema = z.object({
  name: z.string(),
  tentacle_id: z.string(),
  status: z.string(),
  created_at: z.string(),
  description: z.string(),
  scope: z.array(z.string()),
  skills: z.array(z.string()),
  worktree: tentacleWorktreeInfoSchema,
  verification: tentacleVerificationInfoSchema,
  has_handoff: z.boolean().optional(),
  terminal_status: z.string().optional(),
  // Goal-aware optional fields — populated by goal-core when a tentacle is linked to a goal
  goal_id: z.string().optional(),
  goal_name: z.string().optional(),
  goal_iteration: z.number().int().nonnegative().optional(),
});

export const tentacleMarkerInfoSchema = z.object({
  active: z.boolean(),
  path: z.string(),
  age_hours: z.number().nullable(),
  stale: z.boolean(),
});

export const tentacleAuditCheckSchema = auditCheckSchema;

export const tentacleStatusResponseSchema = z.object({
  status: z.string(),
  configured: z.boolean(),
  active_count: z.number(),
  total_count: z.number(),
  worktrees_prepared: z.number(),
  verification_covered: z.number(),
  goal_aware_count: z.number().int().nonnegative().optional(),
  marker: tentacleMarkerInfoSchema,
  tentacles: z.array(tentacleEntrySchema),
  audit: auditBlockSchema,
  operator_actions: z.array(operatorActionSchema),
  runtime: z.object({
    generated_at: z.string(),
  }),
});

// ── Research Pack (/api/scout/research-pack) ─────────────────────────

export const researchPackRepoSchema = z.object({
  full_name: z.string(),
  html_url: z.string(),
  discovery_lane: z.string(),
  score: z.number(),
  stars: z.number(),
  language: z.string().nullable(),
  why_discovered: z.array(z.string()),
  novelty_signals: z.array(z.string()),
  risk_signals: z.array(z.string()),
  recommended_followups: z.array(z.string()),
  tentacle_handoff: z.string().nullable(),
});

export const researchPackResponseSchema = z.object({
  available: z.boolean(),
  path: z.string().optional(),
  generated_at: z.string().nullable().optional(),
  schema_version: z.number().nullable().optional(),
  run_skipped: z.boolean().optional(),
  skip_reason: z.string().nullable().optional(),
  repo_count: z.number(),
  repos: z.array(researchPackRepoSchema),
  error: z.string().nullable().optional(),
});

export const researchPackReloadResponseSchema = z.object({
  ok: z.boolean(),
  command: z.string(),
  exit_code: z.number().nullable(),
  artifact_available: z.boolean(),
  generated_at: z.string().nullable(),
  repo_count: z.number(),
  run_skipped: z.boolean(),
  skip_reason: z.string().nullable(),
  error: z.string().nullable(),
});

export const skillMetricsSummarySchema = z.object({
  total_outcomes: z.number(),
  outcomes_with_skills: z.number(),
  outcomes_with_verification: z.number(),
  outcomes_with_worktree: z.number(),
  pass_rate: z.number().nullable(),
});

export const skillOutcomeEntrySchema = z.object({
  id: z.number(),
  tentacle_name: z.string(),
  tentacle_id: z.string(),
  outcome_status: z.string(),
  recorded_at: z.string(),
  worktree_used: z.boolean(),
  verification_total: z.number(),
  verification_passed: z.number(),
  verification_failed: z.number(),
  todo_total: z.number(),
  todo_done: z.number(),
  learned: z.boolean(),
  duration_seconds: z.number().nullable(),
  summary: z.string().nullable(),
});

export const skillUsageEntrySchema = z.object({
  skill_name: z.string(),
  usage_count: z.number(),
});

export const skillMetricsAuditCheckSchema = auditCheckSchema;

export const skillMetricsResponseSchema = z.object({
  status: z.string(),
  configured: z.boolean(),
  db_path: z.string(),
  tables: z.object({
    tentacle_outcomes: z.boolean(),
    tentacle_outcome_skills: z.boolean(),
    tentacle_verifications: z.boolean(),
  }),
  summary: skillMetricsSummarySchema,
  recent_outcomes: z.array(skillOutcomeEntrySchema),
  skill_usage: z.array(skillUsageEntrySchema),
  audit: auditBlockSchema,
  operator_actions: z.array(operatorActionSchema),
  runtime: z.object({
    generated_at: z.string(),
  }),
});

// ── Skill catalog (/api/skills/catalog GET) ─────────────────────────────────

export const skillSourceKindSchema = z.enum(["global", "project"]);

export const skillStatusSchema = z.enum(["installed", "unavailable"]);

/** A single skill entry returned by `GET /api/skills/catalog`. */
export const skillCatalogEntrySchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  source_path: z.string(),
  source_kind: skillSourceKindSchema,
  status: skillStatusSchema,
});

/** Response from `GET /api/skills/catalog`. */
export const skillCatalogResponseSchema = z.object({
  skills: z.array(skillCatalogEntrySchema),
  total: z.number(),
  sources: z.object({
    global: z.string(),
    project: z.string().nullable(),
  }),
  runtime: z.object({
    generated_at: z.string(),
  }),
});

export const feedbackRequestSchema = z.object({
  query: z.string(),
  result_id: z.string(),
  result_kind: z.string(),
  verdict: z.union([z.literal(-1), z.literal(0), z.literal(1)]),
  comment: z.string().optional(),
});

export const feedbackResponseSchema = z.object({
  ok: z.boolean(),
  id: z.number(),
});

export const retroScoutSchema = z.object({
  available: z.boolean(),
  configured: z.boolean(),
  script_exists: z.boolean(),
  config_path: z.string(),
  target_repo: z.string().nullable(),
  issue_label: z.string().nullable(),
  grace_window_hours: z.number(),
  state_file: z.string(),
  state_file_exists: z.boolean(),
  last_run_utc: z.string().nullable(),
  elapsed_hours: z.number().nullable(),
  remaining_hours: z.number().nullable(),
  would_skip_without_force: z.boolean(),
});

export const retroResponseSchema = z.object({
  retro_score: z.number(),
  grade: z.string(),
  grade_emoji: z.string(),
  mode: z.union([z.literal("local"), z.literal("repo")]),
  generated_at: z.string(),
  available_sections: z.array(z.string()),
  weights: z.record(z.string(), z.number().nullable()).default({}),
  subscores: z.record(z.string(), z.number().nullable()).default({}),
  knowledge: z.record(z.string(), z.unknown()).nullable().default(null),
  skills: z.record(z.string(), z.unknown()).nullable().default(null),
  hooks: z.record(z.string(), z.unknown()).nullable().default(null),
  git: z.record(z.string(), z.unknown()).nullable().default(null),
  // Additive fields from Tentacle 1 — optional/nullable for graceful degradation
  summary: z.string().nullable().optional(),
  score_confidence: z.enum(["low", "medium", "high"]).nullable().optional(),
  distortion_flags: z.array(z.string()).optional(),
  accuracy_notes: z.array(z.string()).optional(),
  improvement_actions: z.array(z.string()).optional(),
  // Additive toward-100 diagnostics — ordered list of section gaps
  toward_100: z
    .array(
      z.object({
        section: z.string(),
        score: z.number(),
        gap: z.number(),
        barriers: z.array(z.string()),
      })
    )
    .nullable()
    .optional(),
  // Additive Trend Scout coverage signal — absent on older payloads
  scout: retroScoutSchema.optional(),
  // Additive session behavior metrics — absent when DB unavailable
  behavior: z
    .object({
      completion_rate: z.number(),
      knowledge_yield: z.number(),
      efficiency_ratio: z.number(),
      one_shot_rate: z.number(),
      session_count: z.number(),
      sessions_with_checkpoints: z.number(),
    })
    .optional(),
});

export const sessionsResponseSchema = z.union([
  sessionListResponseSchema,
  z.array(sessionRowSchema),
]);

// ── Workflow Health (/api/workflow/health) ────────────────────────────

export const workflowFindingSchema = z.object({
  id: z.string(),
  title: z.string(),
  detail: z.string(),
  severity: z.enum(["critical", "warning", "info"]),
  impact: z.string(),
  action: z.string(),
});

export const workflowHealthResponseSchema = z.object({
  findings: z.array(workflowFindingSchema).default([]),
  health_grade: z.string(),
  generated_at: z.string(),
});

// ── Knowledge Insights (/api/knowledge/insights) ─────────────────────

export const knowledgeInsightsOverviewSchema = z.object({
  health_score: z.number(),
  total_entries: z.number(),
  sessions: z.number(),
  high_confidence_pct: z.number(),
  low_confidence_pct: z.number(),
  stale_pct: z.number(),
  relation_density: z.number(),
  embedding_pct: z.number(),
});

export const knowledgeInsightsAlertSchema = z.object({
  id: z.string(),
  title: z.string(),
  severity: z.enum(["info", "warning", "critical"]),
  detail: z.string(),
});

export const knowledgeInsightsActionSchema = z.object({
  id: z.string(),
  title: z.string(),
  detail: z.string(),
  command: z.string(),
});

export const knowledgeInsightsNoiseTitleSchema = z.object({
  title: z.string(),
  category: z.string(),
  entry_count: z.number(),
  avg_confidence: z.number(),
});

export const knowledgeInsightsHotFileSchema = z.object({
  path: z.string(),
  references: z.number(),
});

export const knowledgeInsightsEntrySchema = z.object({
  id: z.number(),
  title: z.string(),
  confidence: z.number(),
  occurrence_count: z.number(),
  last_seen: z.string().nullable(),
  summary: z.string().nullable(),
  session_id: z.string().nullable(),
});

export const knowledgeInsightsEntriesSchema = z.object({
  mistakes: z.array(knowledgeInsightsEntrySchema).default([]),
  patterns: z.array(knowledgeInsightsEntrySchema).default([]),
  decisions: z.array(knowledgeInsightsEntrySchema).default([]),
  tools: z.array(knowledgeInsightsEntrySchema).default([]),
});

export const knowledgeInsightsDimensionSchema = z.object({
  dimension: z.string(),
  current: z.number(),
  max: z.number(),
  gap: z.number(),
  gap_pct: z.number(),
  pct_of_total_gap: z.number(),
});

/** Same shape as knowledgeInsightsDimensionSchema — top_gaps entries are the highest-impact subset. */
export const knowledgeInsightsTopGapSchema = knowledgeInsightsDimensionSchema;

export const knowledgeInsightsToward100Schema = z.object({
  total_gap: z.number(),
  dimensions: z.array(knowledgeInsightsDimensionSchema),
  top_gaps: z.array(knowledgeInsightsTopGapSchema),
});

export const knowledgeInsightsResponseSchema = z.object({
  generated_at: z.string(),
  summary: z.string(),
  overview: knowledgeInsightsOverviewSchema,
  quality_alerts: z.array(knowledgeInsightsAlertSchema).default([]),
  recommended_actions: z.array(knowledgeInsightsActionSchema).default([]),
  recurring_noise_titles: z.array(knowledgeInsightsNoiseTitleSchema).default([]),
  hot_files: z.array(knowledgeInsightsHotFileSchema).default([]),
  entries: knowledgeInsightsEntriesSchema,
  // Additive toward-100 diagnostics — absent on older payloads
  toward_100: knowledgeInsightsToward100Schema.nullable().optional(),
});

// ── Operator/Chat (/api/operator/*) ──────────────────────────────────

/**
 * Copilot event type literals observed from real probe.
 * Unknown types from newer Copilot versions are accepted as raw strings.
 */
export const copilotEventTypeSchema = z.union([
  z.enum([
    "assistant.message_delta",
    "assistant.message",
    "assistant.turn_end",
    "result",
    "session.mcp_server_status_changed",
    "session.mcp_servers_loaded",
    "session.skills_loaded",
    "user.message",
    "assistant.turn_start",
    "assistant.message_start",
    "assistant.reasoning",
  ]),
  z.string(),
]);

const copilotStructuredEventTypeSchema = z
  .string()
  .refine((value) => value.trim().length > 0 && value !== "raw" && value !== "status", {
    message: "Structured Copilot event frames must not use reserved raw/status types.",
  });

/** Structured Copilot JSONL payload preserved from the backend. */
export const copilotEventPayloadSchema = z
  .object({
    type: copilotEventTypeSchema,
  })
  .passthrough();

/** Typed SSE frame for a structured Copilot event. */
export const copilotEventFrameSchema = z
  .object({
    type: copilotStructuredEventTypeSchema,
    idx: z.number().int().nonnegative(),
    event: copilotEventPayloadSchema,
    data: z.unknown().optional(),
  })
  .refine((frame) => frame.event.type === frame.type, {
    message: "Frame type must match event.type",
    path: ["event", "type"],
  });

/** Raw SSE frame for unstructured/fallback text output. */
export const copilotRawFrameSchema = z.object({
  type: z.literal("raw"),
  idx: z.number().int().nonnegative(),
  text: z.string(),
});

/** Terminal SSE frame signalling run completion. */
export const copilotStatusFrameSchema = z.object({
  type: z.literal("status"),
  status: z.string(),
  exit_code: z.number().nullable(),
});

/** Union of all SSE frame shapes. */
export const copilotStreamFrameSchema = z.union([
  copilotEventFrameSchema,
  copilotRawFrameSchema,
  copilotStatusFrameSchema,
]);

/** Operator session as returned by create/list/get endpoints. */
export const operatorSessionSchema = z.object({
  id: z.string(),
  name: z.string(),
  model: z.string(),
  mode: z.string(),
  workspace: z.string(),
  add_dirs: z.array(z.string()),
  created_at: z.string(),
  updated_at: z.string(),
  run_count: z.number().int().nonnegative(),
  last_run_id: z.string().nullable(),
  resume_ready: z.boolean(),
  /** Set to 'cli_adopt' when this session was adopted from a CLI history entry. */
  source: z.string().optional(),
  /**
   * Internal CLI UUID used during adopt flow. Present only on cli_adopt sessions.
   * SECURITY: Must never appear in router URLs, localStorage, or sessionStorage.
   * For internal use only — do not render user-facing.
   */
  resume_target: z.string().nullable().optional(),
  /**
   * ISO timestamp set when the user confirms the adoption.
   * Null/absent means the session is pending confirmation.
   */
  confirmed_at: z.string().nullable().optional(),
});

export const operatorSessionListResponseSchema = z.object({
  sessions: z.array(operatorSessionSchema),
  count: z.number().int().nonnegative(),
});

/** A single CLI history session returned by the discovery endpoint. */
export const cliSessionSchema = z.object({
  cli_session_id: z.string().uuid(),
  title: z.string(),
  mtime: z.string(),
  workspace_hint: z.string().nullable().optional(),
  branch: z.string().nullable().optional(),
  repository: z.string().nullable().optional(),
});

export const cliSessionListResponseSchema = z.object({
  sessions: z.array(cliSessionSchema),
  count: z.number().int().nonnegative(),
  truncated: z.boolean(),
});

/** Request body for `POST /api/operator/sessions/adopt`. CLI UUID in JSON body only. */
export const adoptCliSessionRequestSchema = z.object({
  cli_session_id: z.string().uuid(),
  workspace: z.string().optional(),
  add_dirs: z.array(z.string()).optional(),
  name: z.string().optional(),
});

/** Request body for `POST /api/operator/sessions`. */
export const createOperatorSessionRequestSchema = z.object({
  name: z.string(),
  /** Optional: when absent the backend applies its configured default model. */
  model: z.string().optional(),
  mode: z.string(),
  workspace: z.string(),
  add_dirs: z.array(z.string()).optional(),
});

/** Request body for `PATCH /api/operator/sessions/{id}`. At least one field must be present. */
export const updateOperatorSessionRequestSchema = z
  .object({
    /** New display name (max 128 chars). */
    name: z.string().max(128).optional(),
    /** Model identifier to switch to (max 64 chars). */
    model: z.string().max(64).optional(),
    /** Session mode to switch to (e.g. "interactive", "plan", "autopilot"). */
    mode: z.enum(MUTABLE_OPERATOR_SESSION_MODES).optional(),
  })
  .refine(
    (data) => data.name !== undefined || data.model !== undefined || data.mode !== undefined,
    { message: "At least one mutable field (name, model, mode) must be provided." }
  );

/** Schema for a single file queued before prompt submission. */
export const queuedFileSchema = z.object({
  name: z.string(),
  type: z.string(),
  size: z.number().int().nonnegative(),
  data: z.string(),
});

/** Schema for file metadata returned in run info. */
export const runFileMetadataSchema = z.object({
  name: z.string(),
  type: z.string(),
  size: z.number().int().nonnegative(),
});

/** Request body for `POST /api/operator/sessions/{id}/prompt`. */
export const promptRequestSchema = z.object({
  prompt: z.string().min(1),
  files: z.array(queuedFileSchema).optional(),
});

/** Response from `POST /api/operator/sessions/{id}/prompt`. */
export const promptSubmitResponseSchema = z.object({
  run_id: z.string(),
  session_id: z.string(),
  status: z.string(),
});

/** Minimal run info returned inside operatorRunStatusSchema. */
export const operatorRunInfoSchema = z.object({
  id: z.string(),
  session_id: z.string(),
  prompt: z.string(),
  status: z.string(),
  exit_code: z.number().nullable(),
  started_at: z.string(),
  finished_at: z.string().nullable(),
  events: z.array(copilotStreamFrameSchema),
  files: z.array(runFileMetadataSchema).optional(),
  /** Whether this run used `--resume` to carry prior conversation context. Optional for backward compatibility. */
  resume_used: z.boolean().optional(),
});

/** Response from `GET /api/operator/sessions/{id}/status?run=<run_id>`. */
export const operatorRunStatusSchema = z.object({
  session: operatorSessionSchema,
  run: operatorRunInfoSchema.nullable(),
});

/** Response from `GET /api/operator/sessions/{id}/runs`. */
export const operatorRunsResponseSchema = z.object({
  runs: z.array(operatorRunInfoSchema),
  count: z.number().int().nonnegative(),
});

/** Response from `GET /api/operator/suggest?q=<prefix>`. */
export const pathSuggestResponseSchema = z.object({
  suggestions: z.array(z.string()),
  count: z.number().int().nonnegative(),
});

/** Response from `GET /api/operator/preview?path=<path>`. */
export const filePreviewResponseSchema = z.object({
  path: z.string(),
  content: z.string(),
  mime: z.string(),
  size: z.number().int().nonnegative(),
});

/** Response from `GET /api/operator/diff?a=<a>&b=<b>`. */
export const fileDiffResponseSchema = z.object({
  path_a: z.string(),
  path_b: z.string(),
  unified_diff: z.string(),
  stats: z.object({
    added: z.number().int().nonnegative(),
    removed: z.number().int().nonnegative(),
  }),
});

/** Client-side chat settings used to configure new operator sessions. */
export const chatSettingsSchema = z.object({
  model: z.string().min(1),
  mode: z.string().min(1),
  workspace: z.string(),
  add_dirs: z.array(z.string()),
});

// ── Operator model catalog (/api/operator/models) ─────────────────────────

/** A single model entry returned by the operator model catalog endpoint. */
export const operatorModelEntrySchema = z.object({
  id: z.string(),
  display_name: z.string(),
  provider: z.string().optional(),
  /** True when this is the server-declared default model. */
  default: z.boolean().optional(),
});

/** Response from `GET /api/operator/models`. */
export const operatorModelCatalogResponseSchema = z.object({
  models: z.array(operatorModelEntrySchema),
  /** Server-declared default model id; null when no default is configured. */
  default_model: z.string().nullable(),
});

// ── Debug Log (/api/operator/sessions/{sid}/runs/{rid}/debug) ────────

/**
 * Event taxonomy kinds for BrowseDebugEntry.
 * See docs/DEBUG-LOG-CONTRACT.md §Event Taxonomy.
 */
export const debugKindSchema = z.union([
  z.enum([
    "session_start",
    "turn_start",
    "llm_request",
    "tool_call",
    "hook",
    "subagent",
    "agent_response",
    "error",
    "generic",
    "raw",
  ]),
  z.string(),
]);

/** Severity levels. */
export const debugLevelSchema = z.union([z.enum(["debug", "info", "warn", "error"]), z.string()]);

/** Terminal status. */
export const debugStatusSchema = z.union([z.enum(["ok", "error", "cancelled"]), z.string()]);

/**
 * One entry in the debug log.
 * See docs/DEBUG-LOG-CONTRACT.md §BrowseDebugEntry.
 */
export const browseDebugEntrySchema = z.object({
  idx: z.number().int().nonnegative(),
  timestamp: z.string().nullable(),
  kind: debugKindSchema,
  level: debugLevelSchema.nullable(),
  source: z.string(),
  message: z.string(),
  tool_name: z.string().nullable(),
  duration_ms: z.number().nullable(),
  span_id: z.string().nullable(),
  parent_span_id: z.string().nullable(),
  status: debugStatusSchema.nullable(),
  attrs: z.record(z.string(), z.unknown()).nullable(),
  redacted: z.boolean(),
});

/**
 * Optional, allowlisted scalar/enum subset of `attrs` defined by the
 * backend's rich-metadata contract (issue #533). Used by the Flow
 * render-model helpers — never as the canonical wire schema, since the
 * backend's `attrs` envelope remains an open `record` to accommodate
 * future safe additions without a version bump.
 *
 * See docs/DEBUG-LOG-CONTRACT.md §Rich-metadata Attrs.
 */
export const debugSafeAttrsSchema = z
  .object({
    event_type: z.string().optional(),
    event_phase: z.enum(["start", "end", "complete", "started", "completed", "failed"]).optional(),
    hook_type: z.string().optional(),
    hook_status: z.enum(["ok", "error"]).optional(),
    tool_success: z.boolean().optional(),
    tool_status: z.enum(["ok", "error", "cancelled"]).optional(),
    tool_result_type: z.string().optional(),
    tool_metric_duration_ms: z.number().nonnegative().optional(),
    tool_metric_input_bytes: z.number().nonnegative().optional(),
    tool_metric_output_bytes: z.number().nonnegative().optional(),
    output_tokens: z.number().nonnegative().optional(),
    tool_request_count: z.number().int().nonnegative().optional(),
    skill_name: z.string().optional(),
    skill_path_category: z.enum(["skill_pkg", "absolute_user", "relative", "other"]).optional(),
    skill_content_bytes: z.number().int().nonnegative().optional(),
    notification_kind: z
      .enum(["agent_completed", "shell_completed", "shell_detached_completed"])
      .optional(),
    notification_status: z.string().optional(),
    notification_exit_code: z.number().optional(),
    compaction_kind: z.string().optional(),
    mode: z.string().optional(),
    truncated: z.boolean().optional(),
    bytes_in: z.number().nonnegative().optional(),
    exit_code: z.number().optional(),
    error_category: z.string().optional(),
  })
  .passthrough();

/**
 * HTTP response envelope for GET /api/operator/sessions/{sid}/runs/{rid}/debug.
 * See docs/DEBUG-LOG-CONTRACT.md §WBS-104 Response Shape.
 */
export const debugLogResponseSchema = z.object({
  schema_version: z.string(),
  session_id: z.string(),
  run_id: z.string(),
  total: z.number().int().nonnegative(),
  from: z.number().int().nonnegative(),
  limit: z.number().int().nonnegative(),
  has_more: z.boolean(),
  events: z.array(browseDebugEntrySchema),
});

/**
 * HTTP response envelope for GET /api/session/{sid}/debug-log.
 * Session-scoped debug log — not tied to any operator run.
 */
export const sessionDebugLogResponseSchema = z.object({
  schema_version: z.string(),
  session_id: z.string(),
  from: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  total: z.number().int().nonnegative(),
  has_more: z.boolean(),
  entries: z.array(browseDebugEntrySchema),
});

/**
 * Lightweight debug log entry returned by `projection=skeleton`.
 *
 * Strict schema: only the seven safe fields produced by the backend
 * `_project_skeleton` helper are accepted. Unknown keys are stripped
 * (no `.passthrough()`). This deliberately rejects message, attrs,
 * tool_name, source, level, and redacted — use browseDebugEntrySchema
 * for full entries.
 *
 * See backend _SKELETON_FIELDS in browse/routes/debug_log.py (issue #538).
 */
export const browseDebugSkeletonEntrySchema = z.object({
  idx: z.number().int().nonnegative(),
  timestamp: z.string().nullable(),
  kind: debugKindSchema,
  duration_ms: z.number().nullable(),
  status: debugStatusSchema.nullable(),
  span_id: z.string().nullable(),
  parent_span_id: z.string().nullable(),
});

/**
 * HTTP response envelope for GET /api/session/{sid}/debug-log?projection=skeleton.
 * Session-scoped skeleton debug log for timeline/playback use.
 */
export const sessionDebugSkeletonResponseSchema = z.object({
  schema_version: z.string(),
  session_id: z.string(),
  from: z.number().int().nonnegative(),
  limit: z.number().int().positive(),
  total: z.number().int().nonnegative(),
  has_more: z.boolean(),
  entries: z.array(browseDebugSkeletonEntrySchema),
});

// ── Host Profiles (client-side multi-host support) ─────────────────────

// ── Subagent Activity (GET /api/session/{id}/subagent-activity) ────────────

/** Status for a single subagent span. */
export const subagentStatusSchema = z.enum(["running", "completed", "failed"]);

/** Error category enum — only safe categorised values, never raw error text. */
export const subagentErrorCategorySchema = z.enum([
  "rate_limited",
  "api_error",
  "timeout",
  "internal_error",
  "cancelled",
  "unknown",
]);

/**
 * A single aggregated subagent activity row returned by
 * GET /api/session/{id}/subagent-activity.
 *
 * Raw identifiers (toolCallId, agentId), agentDescription, and raw error
 * messages are never included.  All string fields are bounded and redacted.
 * `redacted` is `true` when any field was sanitised via _redact_text.
 */
export const subagentActivityEntrySchema = z
  .object({
    span_id: z.string().nullable(),
    agent_name: z.string().nullable(),
    agent_display_name: z.string().nullable(),
    model: z.string().nullable(),
    status: subagentStatusSchema,
    started_at: z.string().nullable(),
    ended_at: z.string().nullable(),
    duration_ms: z.number().nullable(),
    total_tool_calls: z.number().int().nonnegative().nullable(),
    total_tokens: z.number().int().nonnegative().nullable(),
    error_category: subagentErrorCategorySchema.nullable(),
    error_preview: z.string().max(120).nullable(),
    start_idx: z.number().int().nonnegative().nullable(),
    end_idx: z.number().int().nonnegative().nullable(),
    redacted: z.boolean(),
  })
  .strict();

/**
 * HTTP response envelope for GET /api/session/{id}/subagent-activity.
 * One-pass aggregation of subagent.started / subagent.completed /
 * subagent.failed events, paired by toolCallId (primary) or agentId
 * (fallback).
 */
export const subagentActivityResponseSchema = z
  .object({
    schema_version: z.literal("1"),
    session_id: z.string(),
    total_subagents_seen: z.number().int().nonnegative(),
    returned: z.number().int().nonnegative(),
    cap: z.number().int().positive(),
    truncated: z.boolean(),
    dropped_pending_starts: z.number().int().nonnegative(),
    entries: z.array(subagentActivityEntrySchema),
  })
  .strict();

// ── Host Profiles (client-side multi-host support) ─────────────────────

/** Permissive CLI family schema — any non-empty string is accepted. */
export const cliKindSchema = z.string().min(1);

/**
 * `/.well-known/browse-host` discovery response (schema version "browse-host/1").
 * Returned by a local backend started with `--hosted-bootstrap`.
 * See issue #49 for the full contract.
 */
export const browseHostBootstrapSchema = z.object({
  schema: z.literal("browse-host/1"),
  status: z.literal("ok"),
  auth: z.enum(["open", "token"]),
  manual_token_required: z.boolean(),
  capabilities: z.array(z.string()),
  cors_origins_configured: z.boolean(),
  // Pairing extensions (issue #58 / #59) — optional for backward compatibility
  // with older backends that have not yet been updated.
  pairing_supported: z.boolean().optional(),
  static_mode_active: z.boolean().optional(),
  demo_mode_badge: z.string().nullable().optional(),
});

/**
 * Host profile stored client-side in localStorage.
 * The "local" id is reserved for the same-origin sentinel and is immutable.
 */
export const hostProfileSchema = z.object({
  id: z.string().min(1),
  label: z.string().min(1),
  /** Remote host URL. LOCAL_HOST is an in-memory sentinel and is not stored through this schema. */
  base_url: z.string().refine(
    (url) => {
      try {
        const { protocol } = new URL(url);
        return protocol === "http:" || protocol === "https:";
      } catch {
        return false;
      }
    },
    { message: "base_url must be a valid URL with http or https scheme" }
  ),
  token: z.string(),
  cli_kind: cliKindSchema,
  is_default: z.boolean(),
  /**
   * Persisted demo-mode badge label (#59). Optional — older profiles without
   * this field are valid and treated as non-demo.
   */
  demo_mode_badge: z.string().nullable().optional(),
  /**
   * Connectivity mode discriminator (#65). Optional — older profiles without
   * this field are treated as "direct" for backwards compatibility.
   */
  connectivity_mode: z.enum(["direct", "tunnel", "broker"]).optional(),
});

/**
 * Runtime capabilities contract returned by `GET /api/operator/capabilities`.
 * The UI uses this to adapt features to what the connected CLI server supports.
 *
 * `protocol` is an optional versioning marker introduced in the v2 contract.
 * When absent the payload is treated as a legacy response (see useHostFeature).
 */
export const hostCapabilitiesSchema = z.object({
  cli_kind: cliKindSchema,
  version: z.string().nullable().optional(),
  supported_modes: z.array(z.string()),
  supported_features: z.array(z.string()),
  /** Present on modern backends (e.g. "v2"). Absent on legacy backends. */
  protocol: z.string().nullable().optional(),
});

// ── Flight Recorder v3 (synthesis §4b) ─────────────────────────────────────
// All four schemas are `.strict()` so the parser rejects any unexpected key.
// This catches both contract drift (new field shipped without UI update) and
// supply-chain tampering (extra fields injected by a malicious intermediary).

export const browseCheckpointSectionsSchema = z
  .object({
    overview: z.boolean(),
    history: z.boolean(),
    work_done: z.boolean(),
    technical_details: z.boolean(),
    important_files: z.boolean(),
    next_steps: z.boolean(),
  })
  .strict();

export const browseCheckpointSummarySchema = z
  .object({
    seq: z.number().int().nonnegative(),
    title: z.string(),
    file_basename: z.string(),
    byte_size: z.number().int().nonnegative(),
    mtime_iso: z.string().nullable(),
    sections: browseCheckpointSectionsSchema,
  })
  .strict();

export const browseCheckpointsResponseSchema = z
  .object({
    schema_version: z.string(),
    session_id: z.string(),
    total: z.number().int().nonnegative(),
    checkpoints: z.array(browseCheckpointSummarySchema),
  })
  .strict();

export const browseRewindSnapshotSummarySchema = z
  .object({
    snapshot_id: z.string(),
    timestamp: z.string().nullable(),
    git_commit: z.string().nullable(),
    git_branch: z.string().nullable(),
    file_count: z.number().int().nonnegative(),
    user_message_present: z.boolean(),
    user_message_byte_size: z.number().int().nonnegative(),
    event_span_id: z.string().nullable(),
  })
  .strict();

export const browseRewindSnapshotsResponseSchema = z
  .object({
    schema_version: z.string(),
    session_id: z.string(),
    total: z.number().int().nonnegative(),
    snapshots: z.array(browseRewindSnapshotSummarySchema),
  })
  .strict();
