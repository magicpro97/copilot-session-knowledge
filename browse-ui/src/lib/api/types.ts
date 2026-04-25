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

// ── Graph (/api/graph) ──────────────────────────────────────────────

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

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
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

// ── Health (/healthz) ───────────────────────────────────────────────

export interface HealthResponse {
  status: string;
  schema_version: number;
  sessions: number;
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

// ── Sessions response (flat array compat shim) ───────────────────────

export type SessionsResponse = SessionListResponse | SessionRow[];
