// src/lib/api/types.ts — API contract types (verbatim from 01-system-architecture.md §2.2)

// ── Shared ────────────────────────────────────────────────────────────

export interface SessionRow {
  id: string;
  path: string | null;
  summary: string | null;
  source: string | null;
  event_count_estimate: number | null;
  fts_indexed_at: string | null;
  indexed_at_r?: string | null;
}

export interface SessionMeta extends SessionRow {
  file_mtime: string | null;
}

// ── Home (/  ?format=json) ───────────────────────────────────────────

export type HomeResponse = SessionRow[];

// ── Sessions (/sessions  ?format=json) ───────────────────────────────

export type SessionListResponse = {
  items: SessionRow[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
};

// ── Session Detail (/session/{id}  ?format=json) ────────────────────

export interface TimelineEntry {
  seq: number;
  title: string | null;
  doc_type: string | null;
  section_name: string | null;
  content: string | null;
}

export interface SessionDetailResponse {
  meta: SessionMeta;
  timeline: TimelineEntry[];
  /**
   * True iff the backend has an operator-console session record for this id.
   * Knowledge-only (SQLite/CLI) sessions report `false` so the UI can skip
   * the `/api/operator/sessions/{id}/runs` request and avoid a visible 404.
   * Always present on 200 responses (issue #518).
   */
  has_operator_runs: boolean;
}

// ── Timeline (/api/session/{id}/events) ──────────────────────────────

export interface TimelineEvent {
  event_id: number;
  kind: string;
  preview: string;
  byte_offset: number | null;
  file_mtime: string | null;
  color: string;
}

export interface TimelineEventsResponse {
  events: TimelineEvent[];
  total: number;
  session_id: string;
}

// ── Mindmap (/api/session/{id}/mindmap) ──────────────────────────────

export interface MindmapResponse {
  markdown: string;
  title: string;
}

// ── Search (/api/search) ────────────────────────────────────────────

export interface SearchResult {
  type: "session" | "knowledge";
  id: string | number;
  title: string;
  snippet?: string;
  score: number;
  wing?: string;
  kind?: string;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
  total: number;
  took_ms: number;
}

// ── Dashboard (/api/dashboard/stats) ─────────────────────────────────

export interface DashboardTotals {
  sessions: number;
  knowledge_entries: number;
  relations: number;
  embeddings: number;
}

export interface CategoryCount {
  name: string;
  count: number;
}

export interface DayCount {
  date: string;
  count: number;
}

export interface WeekCount {
  week: string;
  count: number;
}

export interface ModuleCount {
  module: string;
  count: number;
}

export interface WingCount {
  wing: string;
  count: number;
}

export interface RedFlag {
  session_id: string;
  events: number;
  summary: string | null;
}

export interface DashboardStats {
  totals: DashboardTotals;
  by_category: CategoryCount[];
  sessions_per_day: DayCount[];
  top_wings: WingCount[];
  red_flags: RedFlag[];
  weekly_mistakes: WeekCount[];
  top_modules: ModuleCount[];
}

// ── Legacy Graph (/api/graph) ───────────────────────────────────────

export interface GraphNode {
  id: string;
  kind: "entry" | "entity";
  label: string;
  wing?: string;
  room?: string;
  category?: string;
  color: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
}

export interface GraphResponseLegacy {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
}

export type GraphResponse = GraphResponseLegacy;

// ── Evidence Graph (/api/graph/evidence) ────────────────────────────

export type EvidenceRelationType =
  | "SAME_SESSION"
  | "RESOLVED_BY"
  | "TAG_OVERLAP"
  // Keep SAME_TOPIC explicit but gated by runtime data presence.
  | "SAME_TOPIC";

// Runtime payloads can include relation types outside canonical literals.
export type EvidenceRelationTypeValue = EvidenceRelationType | (string & {});

export interface EvidenceEdge {
  source: string;
  target: string;
  relation_type: EvidenceRelationTypeValue;
  confidence: number;
}

export interface EvidenceGraphMeta {
  edge_source: string;
  relation_types: EvidenceRelationTypeValue[];
}

export interface EvidenceGraphResponse {
  nodes: GraphNode[];
  edges: EvidenceEdge[];
  truncated: boolean;
  meta?: EvidenceGraphMeta;
}

// ── Embeddings (/api/embeddings/points) ──────────────────────────────

export interface EmbeddingPoint {
  x: number;
  y: number;
  id: number;
  title: string;
  category: string;
}

export interface EmbeddingProjection {
  points: EmbeddingPoint[];
  count: number;
  cached: boolean;
}

// ── Similarity (/api/graph/similarity) ───────────────────────────────
// Request shape intentionally soft until backend tentacle freezes it.

export interface SimilarityNeighbor {
  id: number;
  title: string;
  category: string;
  score: number;
}

export interface SimilarityNeighborsByEntry {
  entry_id: number;
  neighbors: SimilarityNeighbor[];
}

export interface SimilarityResponse {
  results: SimilarityNeighborsByEntry[];
  meta?: {
    method?: string;
    k?: number;
    [key: string]: unknown;
  };
}

// ── Communities (/api/graph/communities) ─────────────────────────────

export interface CommunityTopCount {
  name: string;
  count: number;
}

export interface CommunityRepresentativeEntry {
  id: number;
  title: string;
  category: string;
}

export interface CommunitySummary {
  id: string;
  label?: string;
  entry_count: number;
  wings?: string[];
  top_categories: CommunityTopCount[];
  top_relation_types?: Array<{ type: EvidenceRelationTypeValue; count: number }>;
  representative_entries: CommunityRepresentativeEntry[];
}

export interface CommunitiesResponse {
  communities: CommunitySummary[];
}

// ── Live (/api/live  — SSE) ─────────────────────────────────────────

export interface LiveEvent {
  id: number;
  category: string;
  title: string;
  wing: string;
  room: string;
  created_at: string;
}

// ── Diff (/api/diff) ────────────────────────────────────────────────

export interface DiffCheckpoint {
  seq: number;
  title: string;
  file: string;
}

export interface DiffResult {
  session_id: string;
  from: DiffCheckpoint;
  to: DiffCheckpoint;
  unified_diff: string;
  files: Array<{ from: string; to: string }>;
  stats: { added: number; removed: number };
}

// ── Eval (/api/eval/stats  — NEEDS NEW ENDPOINT) ────────────────────

export interface EvalAggRow {
  query: string;
  up: number;
  down: number;
  neutral: number;
  total: number;
}

export interface EvalComment {
  query: string;
  result_id: string;
  verdict: -1 | 0 | 1;
  comment: string;
  created_at: string;
}

export interface EvalResponse {
  aggregation: EvalAggRow[];
  recent_comments: EvalComment[];
}

// ── Compare (/api/compare  — NEEDS NEW ENDPOINT) ────────────────────

export interface SessionCompareData {
  session: SessionMeta | null;
  timeline: TimelineEntry[];
}

export interface CompareResponse {
  a: SessionCompareData;
  b: SessionCompareData;
}

// ── Shared audit block (TrendScout, Tentacle, SkillMetrics) ──────────

export interface AuditCheck {
  id: string;
  title: string;
  status: "ok" | "warning" | (string & {});
  detail: string;
}

export interface AuditBlock {
  summary: {
    ok: boolean;
    total_checks: number;
    warning_checks: number;
  };
  checks: AuditCheck[];
}

// ── Health (/healthz) ───────────────────────────────────────────────

export interface HealthResponse {
  status: string;
  /**
   * Issue #560: /healthz is liveness-only. The following corpus/activity
   * fields are no longer emitted by the backend but remain typed as optional
   * so older payloads still parse.
   */
  schema_version?: number;
  sessions?: number;
  knowledge_entries?: number;
  last_indexed_at?: string | null;
  sync_status_endpoint?: string;
}

export interface SyncConnectionStatus {
  configured: boolean;
  endpoint: string | null;
  /** Issue #561: redacted. Backend no longer emits an absolute path. */
  config_path?: string;
  config_path_present?: boolean;
  config_path_label?: string;
  target?: string;
}

export interface SyncFailureInfo {
  failed_at: string;
  error_message: string;
  retry_count: number;
}

export interface SyncRuntimeStatus {
  generated_at: string;
  /** Issue #561: backend no longer emits absolute db_path. */
  db_path?: string;
  db_mode: "memory" | "file" | (string & {});
  sync_tables: Record<string, boolean>;
  sync_tables_ready: boolean;
  available_sync_tables: number;
  total_sync_tables: number;
  failed_txns: number;
}

/**
 * Shared operator-action shape across all browse diagnostics routes.
 *
 * Required: id, title, description, command, safe (always true).
 * Optional context fields are route-specific and may be absent:
 *   - requires_configured_gateway — only in sync actions
 *   - requires_configured_target  — only in scout actions
 */
export interface OperatorAction {
  id: string;
  title: string;
  description: string;
  command: string;
  safe: boolean;
  requires_configured_gateway?: boolean;
  requires_configured_target?: boolean;
}

export interface SyncOperatorAction extends OperatorAction {
  requires_configured_gateway: boolean;
}

export interface TrendScoutOperatorAction extends OperatorAction {
  requires_configured_target: boolean;
}

export interface SyncStatusResponse {
  status: string;
  configured: boolean;
  connection: SyncConnectionStatus;
  rollout?: {
    client_contract: string;
    direct_db_sync: boolean;
    reference_gateway: {
      mode: string;
      description: string;
    };
    provider_gateway: {
      mode: string;
      recommended: string;
      description: string;
    };
  };
  runtime: SyncRuntimeStatus;
  operator_actions: SyncOperatorAction[];
  local_replica_id: string | null;
  pending_txns: number;
  pending_ops: number;
  committed_txns: number;
  failed_txns: number;
  failed_ops: number;
  cursor_count: number;
  last_committed_at: string | null;
  last_failure: SyncFailureInfo | null;
}

export interface TrendScoutConfigStatus {
  configured: boolean;
  config_path: string;
  script_path: string;
  target_repo: string | null;
}

export interface TrendScoutAnalysisPreview {
  enabled: boolean;
  model: string;
  token_env: string;
  token_present: boolean;
}

export interface TrendScoutGraceWindowStatus {
  enabled: boolean;
  grace_window_hours: number;
  state_file: string;
  state_file_exists: boolean;
  last_run_utc: string | null;
  elapsed_hours: number | null;
  remaining_hours: number | null;
  would_skip_without_force: boolean;
  reason: string | null;
}

export type TrendScoutAuditCheck = AuditCheck;

export interface TrendScoutDiscoveryLane {
  name: string;
  keyword_count: number;
  topic_count: number;
  language: string | null;
  min_stars: number;
}

export interface TrendScoutStatusResponse {
  status: string;
  configured: boolean;
  config: TrendScoutConfigStatus;
  analysis: TrendScoutAnalysisPreview;
  grace_window: TrendScoutGraceWindowStatus;
  audit: AuditBlock;
  operator_actions: TrendScoutOperatorAction[];
  discovery_lanes?: TrendScoutDiscoveryLane[];
  runtime: {
    generated_at: string;
  };
}

// ── Tentacles (/api/tentacles/status) ───────────────────────────────

export interface TentacleWorktreeInfo {
  prepared: boolean;
  path: string;
  stale: boolean;
}

export interface TentacleVerificationInfo {
  coverage_exists: boolean;
  total: number;
  passed: number;
  failed: number;
}

export interface TentacleEntry {
  name: string;
  tentacle_id: string;
  status: string;
  created_at: string;
  description: string;
  scope: string[];
  skills: string[];
  worktree: TentacleWorktreeInfo;
  verification: TentacleVerificationInfo;
  has_handoff?: boolean;
  terminal_status?: string;
  /** Goal-aware optional fields — populated by goal-core when a tentacle is linked to a goal */
  goal_id?: string;
  goal_name?: string;
  goal_iteration?: number;
}

export interface TentacleMarkerInfo {
  active: boolean;
  path: string;
  age_hours: number | null;
  stale: boolean;
}

export type TentacleAuditCheck = AuditCheck;

export interface TentacleStatusResponse {
  status: string;
  configured: boolean;
  active_count: number;
  total_count: number;
  worktrees_prepared: number;
  verification_covered: number;
  /** Count of tentacles linked to a goal — optional for backward compatibility */
  goal_aware_count?: number;
  marker: TentacleMarkerInfo;
  tentacles: TentacleEntry[];
  audit: AuditBlock;
  operator_actions: OperatorAction[];
  runtime: {
    generated_at: string;
  };
}

// ── Research Pack (/api/scout/research-pack) ─────────────────────────

export interface ResearchPackRepo {
  full_name: string;
  html_url: string;
  discovery_lane: string;
  score: number;
  stars: number;
  language: string | null;
  why_discovered: string[];
  novelty_signals: string[];
  risk_signals: string[];
  recommended_followups: string[];
  tentacle_handoff: string | null;
}

export interface ResearchPackResponse {
  available: boolean;
  path?: string;
  generated_at?: string | null;
  schema_version?: number | null;
  run_skipped?: boolean;
  skip_reason?: string | null;
  repo_count: number;
  repos: ResearchPackRepo[];
  error?: string | null;
}

export interface ResearchPackReloadResponse {
  ok: boolean;
  command: string;
  exit_code: number | null;
  artifact_available: boolean;
  generated_at: string | null;
  repo_count: number;
  run_skipped: boolean;
  skip_reason: string | null;
  error: string | null;
}

// ── Skill Metrics (/api/skills/metrics) ─────────────────────────────

export interface SkillMetricsSummary {
  total_outcomes: number;
  outcomes_with_skills: number;
  outcomes_with_verification: number;
  outcomes_with_worktree: number;
  pass_rate: number | null;
}

export interface SkillOutcomeEntry {
  id: number;
  tentacle_name: string;
  tentacle_id: string;
  outcome_status: string;
  recorded_at: string;
  worktree_used: boolean;
  verification_total: number;
  verification_passed: number;
  verification_failed: number;
  todo_total: number;
  todo_done: number;
  learned: boolean;
  duration_seconds: number | null;
  summary: string | null;
}

export interface SkillUsageEntry {
  skill_name: string;
  usage_count: number;
}

export type SkillMetricsAuditCheck = AuditCheck;

export interface SkillMetricsResponse {
  status: string;
  configured: boolean;
  db_path: string;
  tables: {
    tentacle_outcomes: boolean;
    tentacle_outcome_skills: boolean;
    tentacle_verifications: boolean;
  };
  summary: SkillMetricsSummary;
  recent_outcomes: SkillOutcomeEntry[];
  skill_usage: SkillUsageEntry[];
  audit: AuditBlock;
  operator_actions: OperatorAction[];
  runtime: {
    generated_at: string;
  };
}

// ── Skill catalog (/api/skills/catalog GET) ──────────────────────────────────

/** Source kind of an installed skill. */
export type SkillSourceKind = "global" | "project";

/** Availability status of an installed skill. */
export type SkillStatus = "installed" | "unavailable";

/** A single skill entry returned by `GET /api/skills/catalog`. */
export interface SkillCatalogEntry {
  id: string;
  name: string;
  description: string;
  source_path: string;
  source_kind: SkillSourceKind;
  status: SkillStatus;
}

/** Response from `GET /api/skills/catalog`. */
export interface SkillCatalogResponse {
  skills: SkillCatalogEntry[];
  total: number;
  sources: {
    global: string;
    project: string | null;
  };
  runtime: {
    generated_at: string;
  };
}

// ── Feedback (/api/feedback  POST) ──────────────────────────────────

export interface FeedbackRequest {
  query: string;
  result_id: string;
  result_kind: string;
  verdict: -1 | 0 | 1;
  comment?: string;
}

export interface FeedbackResponse {
  ok: boolean;
  id: number;
}

// ── Retro (/api/retro/summary) ──────────────────────────────────────

export interface RetroScout {
  available: boolean;
  configured: boolean;
  script_exists: boolean;
  config_path: string;
  target_repo: string | null;
  issue_label: string | null;
  grace_window_hours: number;
  state_file: string;
  state_file_exists: boolean;
  last_run_utc: string | null;
  elapsed_hours: number | null;
  remaining_hours: number | null;
  would_skip_without_force: boolean;
}

export interface RetroSubscores {
  [key: string]: number | null | undefined;
  knowledge?: number | null;
  skills?: number | null;
  hooks?: number | null;
  git?: number | null;
}

export interface RetroWeights {
  [key: string]: number | null | undefined;
  knowledge?: number | null;
  skills?: number | null;
  hooks?: number | null;
  git?: number | null;
}

/** A single section gap item from the `toward_100` list in the retro payload. */
export interface RetroToward100Item {
  section: string;
  score: number;
  gap: number;
  barriers: string[];
}

export interface RetroResponse {
  retro_score: number;
  grade: string;
  grade_emoji: string;
  mode: "local" | "repo";
  generated_at: string;
  available_sections: string[];
  weights: RetroWeights;
  subscores: RetroSubscores;
  knowledge: Record<string, unknown> | null;
  skills: Record<string, unknown> | null;
  hooks: Record<string, unknown> | null;
  git: Record<string, unknown> | null;
  /** Additive fields from Tentacle 1 — absent on older payloads; treat as optional. */
  summary?: string | null;
  score_confidence?: "low" | "medium" | "high" | null;
  distortion_flags?: string[];
  accuracy_notes?: string[];
  improvement_actions?: string[];
  /** Ordered list of section gaps (highest gap first) — absent on older payloads. */
  toward_100?: RetroToward100Item[] | null;
  /** Trend Scout coverage signal — absent on older payloads; degrade gracefully. */
  scout?: RetroScout;
  /** Session behavior metrics — absent when DB unavailable; degrade gracefully. */
  behavior?: RetroBehavior;
}

export interface RetroBehavior {
  completion_rate: number;
  knowledge_yield: number;
  efficiency_ratio: number;
  one_shot_rate: number;
  session_count: number;
  sessions_with_checkpoints: number;
}

// ── Sessions response (flat array compat shim) ───────────────────────

export type SessionsResponse = SessionListResponse | SessionRow[];

// ── Knowledge Insights (/api/knowledge/insights) ─────────────────────

export interface KnowledgeInsightsOverview {
  health_score: number;
  total_entries: number;
  sessions: number;
  high_confidence_pct: number;
  low_confidence_pct: number;
  stale_pct: number;
  relation_density: number;
  embedding_pct: number;
}

export interface KnowledgeInsightsAlert {
  id: string;
  title: string;
  severity: "info" | "warning" | "critical";
  detail: string;
}

export interface KnowledgeInsightsAction {
  id: string;
  title: string;
  detail: string;
  command: string;
}

export interface KnowledgeInsightsNoiseTitle {
  title: string;
  category: string;
  entry_count: number;
  avg_confidence: number;
}

export interface KnowledgeInsightsHotFile {
  path: string;
  references: number;
}

export interface KnowledgeInsightsEntry {
  id: number;
  title: string;
  confidence: number;
  occurrence_count: number;
  last_seen: string | null;
  summary: string | null;
  session_id: string | null;
}

export interface KnowledgeInsightsEntries {
  mistakes: KnowledgeInsightsEntry[];
  patterns: KnowledgeInsightsEntry[];
  decisions: KnowledgeInsightsEntry[];
  tools: KnowledgeInsightsEntry[];
}

// ── Workflow Health (/api/workflow/health) ────────────────────────────

export interface WorkflowFinding {
  id: string;
  title: string;
  detail: string;
  severity: "critical" | "warning" | "info";
  impact: string;
  action: string;
}

export interface WorkflowHealthResponse {
  findings: WorkflowFinding[];
  health_grade: string;
  generated_at: string;
}

// ── Knowledge Insights (/api/knowledge/insights) ─────────────────────

export interface KnowledgeInsightsDimension {
  dimension: string;
  current: number;
  max: number;
  gap: number;
  gap_pct: number;
  pct_of_total_gap: number;
}

/** Same shape as KnowledgeInsightsDimension — top_gaps entries are the highest-impact subset. */
export type KnowledgeInsightsTopGap = KnowledgeInsightsDimension;

/** `toward_100` dict — additive field; absent on older payloads. */
export interface KnowledgeInsightsToward100 {
  total_gap: number;
  dimensions: KnowledgeInsightsDimension[];
  top_gaps: KnowledgeInsightsTopGap[];
}

export interface KnowledgeInsightsResponse {
  generated_at: string;
  summary: string;
  overview: KnowledgeInsightsOverview;
  quality_alerts: KnowledgeInsightsAlert[];
  recommended_actions: KnowledgeInsightsAction[];
  recurring_noise_titles: KnowledgeInsightsNoiseTitle[];
  hot_files: KnowledgeInsightsHotFile[];
  entries: KnowledgeInsightsEntries;
  /** Additive toward-100 diagnostics — absent on older payloads; degrade gracefully. */
  toward_100?: KnowledgeInsightsToward100 | null;
}

// ── Operator/Chat (/api/operator/*) ──────────────────────────────────

/**
 * Copilot event type literals emitted by `copilot --output-format json`.
 * Observed from real probe; keep raw fidelity — UI may derive grouped messages later.
 */
export type CopilotEventType =
  | "assistant.message_delta"
  | "assistant.message"
  | "assistant.turn_end"
  | "result"
  | "session.mcp_server_status_changed"
  | "session.mcp_servers_loaded"
  | "session.skills_loaded"
  | "user.message"
  | "assistant.turn_start"
  | "assistant.message_start"
  | "assistant.reasoning"
  | "tool.execution_start"
  | "tool.execution_complete";

/** Allow unknown event types from newer Copilot versions while preserving autocomplete. */
export type CopilotEventTypeValue = CopilotEventType | (string & {});

/** Structured Copilot JSONL event payload preserved from the backend. */
export interface CopilotEventPayload extends Record<string, unknown> {
  type: CopilotEventTypeValue;
  data?: unknown;
}

/** Typed SSE frame for a structured Copilot event. */
export interface CopilotEventFrame {
  type: CopilotEventTypeValue;
  idx: number;
  event: CopilotEventPayload;
  data?: unknown;
}

/** Raw SSE frame for unstructured/fallback text output. */
export interface CopilotRawFrame {
  type: "raw";
  idx: number;
  text: string;
}

/** Terminal SSE frame signalling run completion. */
export interface CopilotStatusFrame {
  type: "status";
  status: string;
  exit_code: number | null;
}

/** Discriminated union of all SSE frame shapes from `/api/operator/sessions/{id}/stream`. */
export type CopilotStreamFrame = CopilotEventFrame | CopilotRawFrame | CopilotStatusFrame;

/**
 * Safe metadata envelope for an adopted CLI session (issue #555).
 *
 * Surfaced via the `cli_metadata` host capability. All fields are derived
 * from allowlisted scalar `workspace.yaml` keys, scrubbed through
 * `redact_secrets` on the backend. Paths use the safe workspace hint —
 * never absolute paths. All fields are optional and may be null.
 */
export interface CliMetadata {
  cwd_label?: string | null;
  branch?: string | null;
  repository?: string | null;
  cli_kind?: string | null;
  cli_version?: string | null;
  model?: string | null;
  model_version?: string | null;
  host_profile_id?: string | null;
  started_at?: string | null;
  last_activity?: string | null;
  tool_inventory_summary?: string | null;
  /** Reserved — backend currently emits null; will populate when supported. */
  hook_decision_counts?: Record<string, number> | null;
  /** True when all string fields passed through backend redaction. */
  redacted: boolean;
}

/** Operator session as returned by create/list/get endpoints. */
export interface OperatorSession {
  id: string;
  name: string;
  model: string;
  mode: string;
  workspace: string;
  add_dirs: string[];
  created_at: string;
  updated_at: string;
  run_count: number;
  last_run_id: string | null;
  resume_ready: boolean;
  /** Set to 'cli_adopt' when this session was adopted from a CLI history entry. */
  source?: string;
  /**
   * Internal CLI UUID used during adopt flow. Present only on cli_adopt sessions.
   * SECURITY: Must never appear in router URLs, localStorage, or sessionStorage.
   * For internal use only — do not render user-facing.
   */
  resume_target?: string | null;
  /**
   * ISO timestamp set when the user confirms the adoption.
   * Null/absent means the session is pending confirmation.
   */
  confirmed_at?: string | null;
  /**
   * Safe CLI metadata envelope. Present (additive) for cli_adopt sessions
   * when the host advertises the `cli_metadata` capability. May be null
   * when the underlying CLI session is gone.
   */
  cli_metadata?: CliMetadata | null;
}

export interface OperatorSessionListResponse {
  sessions: OperatorSession[];
  count: number;
}

/**
 * Bounded, safe prior-context envelope for a CLI history session (issue #568).
 *
 * Surfaced via the `cli_prior_context` host capability. Counts and timestamps
 * only — backend NEVER includes raw prompts, assistant text, tool args, or
 * absolute paths.
 */
export interface CliSessionPriorContext {
  event_count: number;
  first_event_at?: string | null;
  last_event_at?: string | null;
  last_status?: string | null;
  truncated: boolean;
  redacted: boolean;
}

/** A single CLI history session returned by `GET /api/operator/cli-sessions`. */
export interface CliSession {
  cli_session_id: string;
  title: string;
  mtime: string;
  workspace_hint?: string | null;
  branch?: string | null;
  repository?: string | null;
  /**
   * Bounded summary of the CLI session's events.jsonl. Present only when the
   * host advertises the `cli_prior_context` capability and a readable
   * events.jsonl was found. Always safe to render — counts/timestamps only.
   */
  prior_context?: CliSessionPriorContext | null;
}

export interface CliSessionListResponse {
  sessions: CliSession[];
  count: number;
  truncated: boolean;
}

/** Request body for `POST /api/operator/sessions/adopt`. CLI UUID in JSON body only. */
export interface AdoptCliSessionRequest {
  /** SECURITY: This CLI UUID must only travel in POST JSON body, never in URLs or storage. */
  cli_session_id: string;
  workspace?: string;
  add_dirs?: string[];
  name?: string;
}

/** Request body for `POST /api/operator/sessions`. */
export interface CreateOperatorSessionRequest {
  name: string;
  /** Optional: when absent the backend applies its configured default model. */
  model?: string;
  mode: string;
  workspace: string;
  add_dirs?: string[];
}

/** Session modes that PATCH is allowed to write back to the operator backend. */
export const MUTABLE_OPERATOR_SESSION_MODES = ["interactive", "plan", "autopilot"] as const;

export type MutableOperatorSessionMode = (typeof MUTABLE_OPERATOR_SESSION_MODES)[number];

/** Request body for `PATCH /api/operator/sessions/{id}`. At least one field must be provided. */
export interface UpdateOperatorSessionRequest {
  /** New display name (max 128 chars). */
  name?: string;
  /** Model identifier to switch to (max 64 chars). */
  model?: string;
  /** Session mode to switch to (e.g. "interactive", "plan", "autopilot"). */
  mode?: MutableOperatorSessionMode;
}

/** A file queued for attachment before prompt submission. */
export interface QueuedFile {
  /** User-visible filename. */
  name: string;
  /** MIME type. */
  type: string;
  /** Size in bytes. */
  size: number;
  /** Base64-encoded file content (data URL body, no prefix). Never shown in UI. */
  data: string;
}

/** Metadata for a file attached to a run (returned by backend, no raw content). */
export interface RunFileMetadata {
  /** User-visible filename. */
  name: string;
  /** MIME type. */
  type: string;
  /** Size in bytes. */
  size: number;
}

/** Request body for `POST /api/operator/sessions/{id}/prompt`. */
export interface PromptRequest {
  prompt: string;
  /** Files to stage server-side alongside this prompt. */
  files?: QueuedFile[];
}

/** Response from `POST /api/operator/sessions/{id}/prompt`. */
export interface PromptSubmitResponse {
  run_id: string;
  session_id: string;
  status: string;
}

/** Minimal run info returned inside `OperatorRunStatus`. */
export interface OperatorRunInfo {
  id: string;
  session_id: string;
  prompt: string;
  status: string;
  exit_code: number | null;
  started_at: string;
  finished_at: string | null;
  events: CopilotStreamFrame[];
  /** Files attached to this run (user-visible metadata only, no raw content). */
  files?: RunFileMetadata[];
  /** Whether this run used `--resume` to carry prior conversation context. Optional for backward compatibility. */
  resume_used?: boolean;
}

/** Response from `GET /api/operator/sessions/{id}/status?run=<run_id>`. */
export interface OperatorRunStatus {
  session: OperatorSession;
  run: OperatorRunInfo | null;
}

/** Response from `GET /api/operator/sessions/{id}/runs`. */
export interface OperatorRunsResponse {
  runs: OperatorRunInfo[];
  count: number;
}

/**
 * Issue #564: Public-summary entry returned by `GET /api/operator/runs`
 * (Chat Workbench feed). Strict allowlist — never contains prompt, events,
 * files, proc handles, env, absolute paths, tokens, raw outputs.
 */
export interface OperatorActiveRunSummary {
  id: string;
  session_id: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  exit_code?: number | null;
  resume_used?: boolean;
  session_label?: string | null;
  health?: string | null;
  queue?: unknown;
}

/** Response from `GET /api/operator/runs` (Chat Workbench feed, issue #564). */
export interface OperatorActiveRunsResponse {
  runs: OperatorActiveRunSummary[];
  count: number;
}

/** Response from `GET /api/operator/suggest?q=<prefix>`. */
export interface PathSuggestResponse {
  suggestions: string[];
  count: number;
}

// ── Operator model catalog (/api/operator/models) ─────────────────────────

/** A single model entry returned by the operator model catalog endpoint. */
export interface OperatorModelEntry {
  id: string;
  display_name: string;
  provider?: string;
  /** True when this is the server-declared default model. */
  default?: boolean;
}

/** Response from `GET /api/operator/models`. */
export interface OperatorModelCatalogResponse {
  models: OperatorModelEntry[];
  /** Server-declared default model id; null when no default is configured. */
  default_model: string | null;
}

/** Response from `GET /api/operator/preview?path=<path>`. */
export interface FilePreviewResponse {
  path: string;
  content: string;
  mime: string;
  size: number;
}

/** Response from `GET /api/operator/diff?a=<a>&b=<b>`. */
export interface FileDiffResponse {
  path_a: string;
  path_b: string;
  unified_diff: string;
  stats: {
    added: number;
    removed: number;
  };
}

/** Client-side chat settings used to configure new operator sessions. */
export interface ChatSettings {
  model: string;
  mode: string;
  workspace: string;
  add_dirs: string[];
}

// ── Host Profiles (client-side multi-host support) ─────────────────────

/**
 * CLI family identifier. Lowercase kebab-case to preserve future extensibility.
 * The `(string & {})` union allows runtime-reported families beyond the known set
 * while preserving IDE autocomplete for known values.
 */
export type CliKind = "copilot" | "claude" | (string & {});

/**
 * A client-side saved profile for connecting to an operator host (local or remote).
 * `id === LOCAL_HOST_ID` ("local") is the built-in same-origin default.
 */
export interface HostProfile {
  /** Stable opaque identifier. The value "local" is reserved for the same-origin default. */
  id: string;
  /** User-visible display label, e.g. "My Laptop Tunnel". */
  label: string;
  /**
   * Absolute base URL of the CLI server, e.g. "https://xxx.ngrok.io".
   * Empty string means same-origin (local default).
   */
  base_url: string;
  /** Auth token for this host. Never appended to URLs when a remote base_url is set. */
  token: string;
  /** Which CLI family runs on this host. */
  cli_kind: CliKind;
  /** When true, this profile is selected by default when no active selection exists. */
  is_default: boolean;
  /**
   * Demo-mode badge label (e.g. "Demo mode") when this profile was added from a
   * static-pairing slot (#59). Persisted with the profile so the host row can
   * display a stable "Demo mode" badge even after page reload.
   * Absent/null for normal operator profiles.
   */
  demo_mode_badge?: string | null;
  /**
   * How this host is reached from the browser (#65).
   *
   * - `"direct"`  — standard direct HTTPS (or PNA-loopback) request.
   * - `"tunnel"`  — explicit public HTTPS tunnel (ngrok, Cloudflare Tunnel, VS
   *                 Code port forward). Semantically the same as "direct" from
   *                 the browser's perspective but shown differently in the UI.
   * - `"broker"`  — outbound control-bus relay (e.g. Telegram bot). No direct
   *                 browser→backend connection is made; all traffic is routed
   *                 through the relay service. `checkHostCompatibility` returns
   *                 `broker-required` for these profiles.
   *
   * Absent on profiles saved before this field was introduced — treated as
   * `"direct"` for backwards compatibility.
   */
  connectivity_mode?: "direct" | "tunnel" | "broker";
}

/**
 * `/.well-known/browse-host` discovery response from a local backend started
 * with `--hosted-bootstrap`. Schema version "browse-host/1".
 *
 * When `auth === "token" && manual_token_required === true` the frontend must
 * not invent a token; show a manual-token state and let the user supply it.
 */
export interface BrowseHostBootstrapResponse {
  schema: "browse-host/1";
  status: "ok";
  auth: "open" | "token";
  manual_token_required: boolean;
  capabilities: string[];
  cors_origins_configured: boolean;
  /** True when the backend supports browse:// ticket pairing (#58). */
  pairing_supported?: boolean;
  /** True when a static/demo pairing slot is active (#59). */
  static_mode_active?: boolean;
  /** Display label for the demo-mode banner when a static slot is active (#59). */
  demo_mode_badge?: string | null;
}

// ── Debug Log (/api/operator/sessions/{sid}/runs/{rid}/debug) ────────

/**
 * Event taxonomy kinds for BrowseDebugEntry.
 * See docs/DEBUG-LOG-CONTRACT.md §Event Taxonomy.
 */
export type DebugKind =
  | "session_start"
  | "turn_start"
  | "llm_request"
  | "tool_call"
  | "hook"
  | "subagent"
  | "agent_response"
  | "error"
  | "generic"
  | "raw";

/** Severity levels for BrowseDebugEntry. */
export type DebugLevel = "debug" | "info" | "warn" | "error";

/** Terminal status for a span/tool call. */
export type DebugStatus = "ok" | "error" | "cancelled";

/**
 * One entry in the debug log, corresponding to a single parsed JSONL line
 * from the Copilot CLI output stream.
 * See docs/DEBUG-LOG-CONTRACT.md §BrowseDebugEntry.
 */
export interface BrowseDebugEntry {
  /** Zero-based position; monotonically increasing per session run. */
  idx: number;
  /** Event wall-clock time (ISO-8601). null when absent in source. */
  timestamp: string | null;
  /** Event taxonomy value. */
  kind: DebugKind | (string & {});
  /** Severity level. null when absent in source. */
  level: DebugLevel | (string & {}) | null;
  /** Source identifier, e.g. "operator_console", "hook_runner". */
  source: string;
  /** Human-readable event message, truncated to 200 chars. */
  message: string;
  /** Tool name for tool_call events. null for other kinds. */
  tool_name: string | null;
  /** Elapsed milliseconds for completed tool calls or spans. null when unknown. */
  duration_ms: number | null;
  /** 16 lowercase hex chars. null when no span. */
  span_id: string | null;
  /** 16 lowercase hex chars for the parent span. null when no parent. */
  parent_span_id: string | null;
  /** Terminal status. null for non-terminal events. */
  status: DebugStatus | (string & {}) | null;
  /** Arbitrary key/value attributes, redacted before serving. */
  attrs: Record<string, unknown> | null;
  /** true if one or more fields were modified by the redaction pass. */
  redacted: boolean;
}

/**
 * Allowlisted, redaction-safe scalar/enum keys promoted into
 * `BrowseDebugEntry.attrs` by the backend (`_extract_cli_attrs`).
 *
 * See docs/DEBUG-LOG-CONTRACT.md §Rich-metadata Attrs (issue #533).
 *
 * Every field is optional — the contract is default-deny: if a value
 * fails enum/regex/range validation it is dropped silently. Frontend
 * consumers must therefore degrade gracefully when any subset is
 * missing. None of these fields carry raw user content.
 */
export interface DebugSafeAttrs {
  event_type?: string;
  event_phase?: "start" | "end" | "complete" | "started" | "completed" | "failed";
  hook_type?: string;
  hook_status?: "ok" | "error";
  tool_success?: boolean;
  tool_status?: "ok" | "error" | "cancelled";
  tool_result_type?: string;
  tool_metric_duration_ms?: number;
  tool_metric_input_bytes?: number;
  tool_metric_output_bytes?: number;
  output_tokens?: number;
  tool_request_count?: number;
  skill_name?: string;
  skill_path_category?: "skill_pkg" | "absolute_user" | "relative" | "other";
  skill_content_bytes?: number;
  notification_kind?: "agent_completed" | "shell_completed" | "shell_detached_completed";
  notification_status?: string;
  notification_exit_code?: number;
  compaction_kind?: string;
  mode?: string;
  /** Generic redaction-pass markers (already defined in the base contract). */
  truncated?: boolean;
  bytes_in?: number;
  /** Generic error metadata used by `kind === "error"` entries. */
  exit_code?: number;
  error_category?: string;
}

/**
 * HTTP response envelope for GET /api/operator/sessions/{sid}/runs/{rid}/debug.
 * See docs/DEBUG-LOG-CONTRACT.md §WBS-104 Response Shape.
 */
export interface DebugLogResponse {
  schema_version: string;
  session_id: string;
  run_id: string;
  total: number;
  from: number;
  limit: number;
  has_more: boolean;
  events: BrowseDebugEntry[];
}

/** Query parameters for the debug log fetch function. */
export interface DebugLogParams {
  from?: number;
  limit?: number;
  kind?: string;
  level?: string;
  since?: string;
  /**
   * Response projection mode (issue #538).
   * - "full": full BrowseDebugEntry (default, max limit 100).
   * - "skeleton": lightweight subset only — idx/timestamp/kind/duration_ms/status/span_id/parent_span_id (max limit 5000).
   */
  projection?: "full" | "skeleton";
  /**
   * Upper-bound window filter (ISO-8601). Events with timestamp > until are excluded.
   * Events missing a timestamp are also excluded when any time filter is active.
   */
  until?: string;
  /**
   * Sequential upper-bound index (exclusive). Only events up to this filtered
   * position are returned. Must be >= 0. No-op when to_idx <= from.
   */
  to_idx?: number;
}

/**
 * Lightweight debug log entry returned by `projection=skeleton`.
 *
 * Contains only safe, non-sensitive fields sufficient for timeline rendering.
 * Never includes message, attrs, tool_name, source, level, or redacted.
 * Status is derived from attrs.hook_status / attrs.tool_status by the backend.
 *
 * See backend `_project_skeleton` in browse/routes/debug_log.py.
 */
export interface BrowseDebugSkeletonEntry {
  /** Zero-based position; monotonically increasing per session run. */
  idx: number;
  /** Event wall-clock time (ISO-8601). null when absent in source. */
  timestamp: string | null;
  /** Event taxonomy value. */
  kind: DebugKind | (string & {});
  /** Elapsed milliseconds for completed spans. null when unknown. */
  duration_ms: number | null;
  /** Derived terminal status (ok/error/cancelled). null for non-terminal or unknown. */
  status: DebugStatus | (string & {}) | null;
  /** 16 lowercase hex chars. null when no span. */
  span_id: string | null;
  /** 16 lowercase hex chars for the parent span. null when no parent. */
  parent_span_id: string | null;
}

/**
 * HTTP response envelope for GET /api/session/{sid}/debug-log?projection=skeleton.
 * Session-scoped skeleton debug log — lightweight projection for timeline use.
 */
export interface SessionDebugSkeletonResponse {
  schema_version: string;
  session_id: string;
  from: number;
  limit: number;
  total: number;
  has_more: boolean;
  entries: BrowseDebugSkeletonEntry[];
}

/**
 * HTTP response envelope for GET /api/session/{sid}/debug-log.
 * Session-scoped debug log — not tied to any operator run.
 */
export interface SessionDebugLogResponse {
  schema_version: string;
  session_id: string;
  from: number;
  limit: number;
  total: number;
  has_more: boolean;
  entries: BrowseDebugEntry[];
}

/**
 * Runtime capabilities contract returned by `GET /api/operator/capabilities`.
 * Describes what the connected CLI server supports so the UI can adapt.
 *
 * `protocol` is an optional versioning marker introduced in the v2 contract.
 * When absent the payload is from a legacy backend (see useHostFeature for
 * the backward-compatibility rule).
 */
export interface HostCapabilities {
  cli_kind: CliKind;
  version?: string | null;
  supported_modes: string[];
  supported_features: string[];
  /** Present on modern backends (e.g. "v2"). Absent on legacy backends. */
  protocol?: string | null;
}

// ── Flight Recorder v3 (synthesis §4a) ─────────────────────────────────────
// Bounded, read-only summary envelopes for the timeline/debug-log UI.
// Backend never returns raw checkpoint bodies, userMessage text, raw eventIds,
// `files{}` blobs, or backupHashes — only the contracted fields below.

/** Section presence flags for a single checkpoint file. */
export interface BrowseCheckpointSections {
  overview: boolean;
  history: boolean;
  work_done: boolean;
  technical_details: boolean;
  important_files: boolean;
  next_steps: boolean;
}

/**
 * One checkpoint summary row. `byte_size` is the on-disk file size (clamped),
 * `title` is run through the project redaction filter, and `file_basename` is
 * a sanitized leaf name (no slashes, no traversal).
 */
export interface BrowseCheckpointSummary {
  seq: number;
  title: string;
  file_basename: string;
  byte_size: number;
  /**
   * UTC ISO-8601 modification time of the underlying checkpoint file
   * (e.g. "2026-05-21T17:08:31.421Z"). May be `null` when the backend
   * cannot stat the file. Used by the timeline chapter rail to anchor
   * checkpoint boundaries to real time instead of arbitrary equal slices.
   * Carries no path or body content.
   */
  mtime_iso: string | null;
  sections: BrowseCheckpointSections;
}

/** HTTP response envelope for GET /api/session/{id}/checkpoints. */
export interface BrowseCheckpointsResponse {
  schema_version: string;
  session_id: string;
  total: number;
  checkpoints: BrowseCheckpointSummary[];
}

/**
 * One rewind-snapshot summary row. `event_span_id` is the same 16-hex hash
 * `_span_id_from_raw(eventId, "rewind", idx)` used by the debug-log timeline,
 * so snapshots can be aligned with debug spans.  `user_message_byte_size`
 * is the UTF-8 size only — the raw text is NEVER returned.
 */
export interface BrowseRewindSnapshotSummary {
  snapshot_id: string;
  timestamp: string | null;
  git_commit: string | null;
  git_branch: string | null;
  file_count: number;
  user_message_present: boolean;
  user_message_byte_size: number;
  event_span_id: string | null;
}

/** HTTP response envelope for GET /api/session/{id}/rewind-snapshots. */
export interface BrowseRewindSnapshotsResponse {
  schema_version: string;
  session_id: string;
  total: number;
  snapshots: BrowseRewindSnapshotSummary[];
}

// ── Subagent Activity (GET /api/session/{id}/subagent-activity) ────────────

/** Status for a single subagent span. */
export type SubagentStatus = "running" | "completed" | "failed";

/** Error category enum — only categorised values, never raw error text. */
export type SubagentErrorCategory =
  | "rate_limited"
  | "api_error"
  | "timeout"
  | "internal_error"
  | "cancelled"
  | "unknown";

/**
 * A single aggregated subagent activity row returned by
 * GET /api/session/{id}/subagent-activity.
 *
 * Raw identifiers (toolCallId, agentId), agentDescription, and raw error
 * messages are never included.  `redacted` is `true` when any string field
 * was sanitised via _redact_text.
 */
export interface SubagentActivityEntry {
  span_id: string | null;
  agent_name: string | null;
  agent_display_name: string | null;
  model: string | null;
  status: SubagentStatus;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  total_tool_calls: number | null;
  total_tokens: number | null;
  error_category: SubagentErrorCategory | null;
  error_preview: string | null;
  start_idx: number | null;
  end_idx: number | null;
  redacted: boolean;
}

/**
 * HTTP response envelope for GET /api/session/{id}/subagent-activity.
 * One-pass aggregation of subagent.started / subagent.completed /
 * subagent.failed events, paired by toolCallId (primary) or agentId
 * (fallback).
 */
export interface SubagentActivityResponse {
  schema_version: "1";
  session_id: string;
  total_subagents_seen: number;
  returned: number;
  cap: number;
  truncated: boolean;
  dropped_pending_starts: number;
  entries: SubagentActivityEntry[];
}

// ── Subagent Internals (GET /api/session/{id}/subagent-internals) ───────────

export interface SubagentInternalToolEvent {
  idx: number;
  end_idx: number | null;
  tool_name: string | null;
  status: "running" | "completed" | "failed" | "unknown";
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  input_bytes: number | null;
  output_bytes: number | null;
}

export interface SubagentInternalModelEvent {
  idx: number;
  timestamp: string | null;
  output_tokens: number | null;
  tool_request_count: number | null;
}

export interface SubagentInternals {
  tool_call_count: number;
  tool_success_count: number;
  tool_failure_count: number;
  llm_turn_count: number;
  output_tokens_total: number;
  internal_event_count: number;
  tool_names: string[];
  tools: SubagentInternalToolEvent[];
  tools_truncated: boolean;
  model_events: SubagentInternalModelEvent[];
  model_events_truncated: boolean;
}

export interface SubagentInternalsEntry {
  agent_key_hash: string;
  span_id: string | null;
  agent_name: string | null;
  agent_display_name: string | null;
  model: string | null;
  status: SubagentStatus;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  start_idx: number | null;
  end_idx: number | null;
  redacted: boolean;
  internals: SubagentInternals;
}

export interface SubagentInternalsResponse {
  schema_version: "1";
  session_id: string;
  total_agents_seen: number;
  returned: number;
  cap: number;
  truncated: boolean;
  dropped_pending_starts: number;
  skill_correlation_supported: boolean;
  uncorrelated_skill_invocations: number;
  session_skill_names: string[];
  entries: SubagentInternalsEntry[];
}

// ── Mission Atlas (GET /api/session/{id}/mission-atlas) ──────────────────────

/** A {name, count} pair used for top-tools / top-skills / top-agent-names. */
export interface MissionAtlasNameCount {
  name: string;
  count: number;
}

/**
 * One temporal bucket produced by the backend's sliding-window aggregation.
 * Only aggregate numeric fields and safe metadata are exposed; raw entry
 * content, paths, args, and messages are never present.
 */
export interface MissionAtlasBucket {
  bucket_idx: number;
  start_idx: number | null;
  end_idx: number | null;
  event_count: number;
  /** Milliseconds relative to session start. Null when no timestamps available. */
  start_rel_ms: number | null;
  end_rel_ms: number | null;
  ts_start: string | null;
  ts_end: string | null;
  /** Per-lane event counts keyed by lane name (tool, hook, skill, etc.). */
  lanes: Record<string, number>;
  dominant_lane: string | null;
  error_count: number;
  /** True for synthetic gap buckets with no events. */
  is_gap: boolean;
}

/**
 * A notable point-in-time event (checkpoint, skill, rewind, error, etc.).
 * `idx` is the raw entry index usable for debug-log seek; null when no
 * specific event index was recorded.
 */
export interface MissionAtlasMilestone {
  idx: number | null;
  timestamp: string | null;
  kind: string;
  label: string;
  bucket_idx: number | null;
}

/** Artifact counts derived from the session's event log — safe numeric only. */
export interface MissionAtlasArtifactCounts {
  checkpoint_files: number;
  rewind_snapshots: number;
  todos_total: number;
  todos_done: number;
  todos_blocked: number;
  todo_deps: number;
  files: number;
  compactions: number;
}

/** Per-lane totals across the full session. */
export interface MissionAtlasLaneTotals {
  tool: number;
  hook: number;
  skill: number;
  subagent: number;
  model: number;
  turn: number;
  system: number;
  error: number;
  generic: number;
}

/** Backend caps/limits for this response. */
export interface MissionAtlasCaps {
  top_n: number;
  milestones: number;
  error_sample: number;
  buckets_min: number;
  buckets_max: number;
}

export interface MissionAtlasTruncated {
  tools: boolean;
  skills: boolean;
  agents: boolean;
  milestones: boolean;
}

export interface MissionAtlasErrorSample {
  idx: number;
  timestamp: string | null;
  event_type: string;
  error_category: string;
}

/**
 * HTTP response envelope for GET /api/session/{id}/mission-atlas (and plural
 * alias /api/sessions/{id}/mission-atlas).
 *
 * Contains only safe aggregate fields — no raw entry content, no file paths,
 * no tool args, no error messages beyond error_count. error_sample contains
 * safe short category labels only (safe fields set by the backend).
 */
export interface SessionMissionAtlasResponse {
  schema_version: "1";
  session_id: string;
  total_events: number;
  event_file_bytes: number | null;
  first_event_at: string | null;
  last_event_at: string | null;
  duration_ms: number | null;
  bucket_count: number;
  buckets: MissionAtlasBucket[];
  lane_totals: MissionAtlasLaneTotals;
  top_tools: MissionAtlasNameCount[];
  top_skills: MissionAtlasNameCount[];
  top_agent_names: MissionAtlasNameCount[];
  milestones: MissionAtlasMilestone[];
  artifact_counts: MissionAtlasArtifactCounts;
  error_count: number;
  /** Safe category samples only — never raw error messages or paths. */
  error_sample: MissionAtlasErrorSample[];
  caps: MissionAtlasCaps;
  truncated: MissionAtlasTruncated;
}
