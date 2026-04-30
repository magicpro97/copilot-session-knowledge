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
  schema_version: number;
  sessions: number;
  knowledge_entries?: number;
  last_indexed_at?: string | null;
  sync_status_endpoint?: string;
}

export interface SyncConnectionStatus {
  configured: boolean;
  endpoint: string | null;
  config_path: string;
  target?: string;
}

export interface SyncFailureInfo {
  failed_at: string;
  error_message: string;
  retry_count: number;
}

export interface SyncRuntimeStatus {
  generated_at: string;
  db_path: string;
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
  /** Trend Scout coverage signal — absent on older payloads; degrade gracefully. */
  scout?: RetroScout;
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

export interface KnowledgeInsightsResponse {
  generated_at: string;
  summary: string;
  overview: KnowledgeInsightsOverview;
  quality_alerts: KnowledgeInsightsAlert[];
  recommended_actions: KnowledgeInsightsAction[];
  recurring_noise_titles: KnowledgeInsightsNoiseTitle[];
  hot_files: KnowledgeInsightsHotFile[];
  entries: KnowledgeInsightsEntries;
}
