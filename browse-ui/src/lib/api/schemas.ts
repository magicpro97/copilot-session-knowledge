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
  file_mtime: z.string().nullable(),
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

export const sessionsResponseSchema = z.union([
  sessionListResponseSchema,
  z.array(sessionRowSchema),
]);
