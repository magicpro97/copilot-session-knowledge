import { z } from "zod";

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

export const knowledgeInsightsResponseSchema = z.object({
  generated_at: z.string(),
  summary: z.string(),
  overview: knowledgeInsightsOverviewSchema,
  quality_alerts: z.array(knowledgeInsightsAlertSchema).default([]),
  recommended_actions: z.array(knowledgeInsightsActionSchema).default([]),
  recurring_noise_titles: z.array(knowledgeInsightsNoiseTitleSchema).default([]),
  hot_files: z.array(knowledgeInsightsHotFileSchema).default([]),
  entries: knowledgeInsightsEntriesSchema,
});
