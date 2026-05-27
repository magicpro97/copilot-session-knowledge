"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { CACHE_TIMES, DEFAULT_PAGE_SIZE, STALE_TIMES } from "@/lib/constants";
import { hostFetch, buildHostUrl } from "@/lib/api/client";
import { LOCAL_HOST, LOCAL_HOST_ID } from "@/lib/host-profiles";
import { peekToken } from "@/lib/auth";
import {
  compareResponseSchema,
  communitiesResponseSchema,
  dashboardStatsSchema,
  evidenceGraphResponseSchema,
  embeddingProjectionSchema,
  evalResponseSchema,
  feedbackRequestSchema,
  feedbackResponseSchema,
  researchPackResponseSchema,
  researchPackReloadResponseSchema,
  retroResponseSchema,
  graphResponseSchema,
  healthResponseSchema,
  hostCapabilitiesSchema,
  knowledgeInsightsResponseSchema,
  searchResponseSchema,
  sessionDetailResponseSchema,
  sessionListResponseSchema,
  syncStatusResponseSchema,
  trendScoutStatusResponseSchema,
  tentacleStatusResponseSchema,
  skillMetricsResponseSchema,
  skillCatalogResponseSchema,
  similarityResponseSchema,
  sessionsResponseSchema,
  workflowHealthResponseSchema,
  operatorSessionListResponseSchema,
  operatorSessionSchema,
  promptRequestSchema,
  promptSubmitResponseSchema,
  operatorRunStatusSchema,
  operatorRunsResponseSchema,
  cancelRunResponseSchema,
  operatorActiveRunsResponseSchema,
  operatorQueueResponseSchema,
  pathSuggestResponseSchema,
  preflightResponseSchema,
  usageResponseSchema,
  usageOverrideResponseSchema,
  filePreviewResponseSchema,
  fileDiffResponseSchema,
  createOperatorSessionRequestSchema,
  updateOperatorSessionRequestSchema,
  operatorModelCatalogResponseSchema,
  debugLogResponseSchema,
  sessionDebugLogResponseSchema,
  sessionDebugSkeletonResponseSchema,
  browseCheckpointsResponseSchema,
  browseRewindSnapshotsResponseSchema,
  subagentActivityResponseSchema,
  subagentInternalsResponseSchema,
  sessionMissionAtlasResponseSchema,
  cliSessionListResponseSchema,
  cliSessionSchema,
  adoptCliSessionRequestSchema,
} from "@/lib/api/schemas";
import type {
  CompareResponse,
  CommunitiesResponse,
  DashboardStats,
  EvidenceGraphResponse,
  EvidenceRelationType,
  EmbeddingProjection,
  EvalResponse,
  FeedbackRequest,
  FeedbackResponse,
  HostCapabilities,
  HostProfile,
  KnowledgeInsightsResponse,
  ResearchPackResponse,
  ResearchPackReloadResponse,
  RetroResponse,
  GraphResponse,
  HealthResponse,
  SearchResponse,
  SimilarityResponse,
  SyncStatusResponse,
  TrendScoutStatusResponse,
  TentacleStatusResponse,
  SkillMetricsResponse,
  SkillCatalogResponse,
  SessionDetailResponse,
  SessionListResponse,
  SessionsResponse,
  WorkflowHealthResponse,
  OperatorSession,
  OperatorSessionListResponse,
  CreateOperatorSessionRequest,
  UpdateOperatorSessionRequest,
  PromptRequest,
  PromptSubmitResponse,
  PreflightResponse,
  UsageResponse,
  UsageOverrideResponse,
  OperatorRunStatus,
  OperatorRunsResponse,
  OperatorActiveRunsResponse,
  OperatorQueueResponse,
  PathSuggestResponse,
  FilePreviewResponse,
  FileDiffResponse,
  OperatorModelCatalogResponse,
  DebugLogResponse,
  DebugLogParams,
  SessionDebugLogResponse,
  SessionDebugSkeletonResponse,
  BrowseCheckpointsResponse,
  BrowseRewindSnapshotsResponse,
  SubagentActivityResponse,
  SubagentInternalsResponse,
  SessionMissionAtlasResponse,
  CliSession,
  CliSessionListResponse,
  AdoptCliSessionRequest,
} from "@/lib/api/types";

export type SessionsQueryParams = {
  page?: number;
  pageSize?: number;
  query?: string;
  source?: string;
  hasSummary?: boolean;
  sort?: string;
};

export type SearchQueryParams = {
  query: string;
  sources?: string[];
  kinds?: string[];
  cols?: string[];
};

export type GraphQueryParams = {
  wing?: string[];
  room?: string[];
  kind?: string[];
  limit?: number;
};

export type EvidenceGraphQueryParams = GraphQueryParams & {
  relation_type?: EvidenceRelationType[];
};

export type SimilarityQueryParams = Record<
  string,
  string | number | boolean | Array<string | number | boolean> | null | undefined
>;

export const queryKeys = {
  sessions: (params: SessionsQueryParams = {}, hostId = LOCAL_HOST_ID) =>
    ["sessions", hostId, params] as const,
  sessionDetail: (sessionId: string, hostId = LOCAL_HOST_ID) =>
    ["session-detail", hostId, sessionId] as const,
  search: (params: SearchQueryParams, hostId = LOCAL_HOST_ID) =>
    ["search", hostId, params] as const,
  health: (hostId = LOCAL_HOST_ID) => ["health", hostId] as const,
  syncStatus: (hostId = LOCAL_HOST_ID) => ["sync-status", hostId] as const,
  scoutStatus: (hostId = LOCAL_HOST_ID) => ["scout-status", hostId] as const,
  scoutResearchPack: (hostId = LOCAL_HOST_ID) => ["scout-research-pack", hostId] as const,
  tentacleStatus: (hostId = LOCAL_HOST_ID) => ["tentacle-status", hostId] as const,
  skillMetrics: (hostId = LOCAL_HOST_ID) => ["skill-metrics", hostId] as const,
  skillCatalog: (hostId = LOCAL_HOST_ID) => ["skill-catalog", hostId] as const,
  dashboard: (hostId = LOCAL_HOST_ID) => ["dashboard", hostId] as const,
  graphLegacy: (params: GraphQueryParams = {}, hostId = LOCAL_HOST_ID) =>
    ["graph-legacy", hostId, params] as const,
  graph: (params: GraphQueryParams = {}, hostId = LOCAL_HOST_ID) =>
    ["graph", hostId, params] as const,
  graphEvidence: (params: EvidenceGraphQueryParams = {}, hostId = LOCAL_HOST_ID) =>
    ["graph-evidence", hostId, params] as const,
  graphSimilarity: (params: SimilarityQueryParams = {}, hostId = LOCAL_HOST_ID) =>
    ["graph-similarity", hostId, params] as const,
  graphCommunities: (hostId = LOCAL_HOST_ID) => ["graph-communities", hostId] as const,
  embeddings: (hostId = LOCAL_HOST_ID) => ["embeddings", hostId] as const,
  eval: (hostId = LOCAL_HOST_ID) => ["eval", hostId] as const,
  retro: (mode: "repo" | "local" = "repo", hostId = LOCAL_HOST_ID) =>
    ["retro", mode, hostId] as const,
  knowledgeInsights: (hostId = LOCAL_HOST_ID) => ["knowledge-insights", hostId] as const,
  compare: (a: string, b: string, hostId = LOCAL_HOST_ID) => ["compare", hostId, a, b] as const,
  workflowHealth: (hostId = LOCAL_HOST_ID) => ["workflow-health", hostId] as const,
  // Operator/Chat — all keys are scoped by hostId to prevent cross-host cache collisions
  operatorSessions: (hostId = LOCAL_HOST_ID) => ["operator-sessions", hostId] as const,
  operatorSession: (id: string, hostId = LOCAL_HOST_ID) =>
    ["operator-session", hostId, id] as const,
  operatorStatus: (sessionId: string, runId: string, hostId = LOCAL_HOST_ID) =>
    ["operator-status", hostId, sessionId, runId] as const,
  operatorRuns: (sessionId: string, hostId = LOCAL_HOST_ID) =>
    ["operator-runs", hostId, sessionId] as const,
  operatorActiveRuns: (hostId = LOCAL_HOST_ID) => ["operator-active-runs", hostId] as const,
  operatorQueue: (hostId = LOCAL_HOST_ID) => ["operator-queue", hostId] as const,
  operatorSuggest: (q: string, hidden = false, hostId = LOCAL_HOST_ID) =>
    ["operator-suggest", hostId, q, hidden] as const,
  operatorPreview: (path: string, hostId = LOCAL_HOST_ID) =>
    ["operator-preview", hostId, path] as const,
  operatorDiff: (pathA: string, pathB: string, hostId = LOCAL_HOST_ID) =>
    ["operator-diff", hostId, pathA, pathB] as const,
  operatorModels: (hostId = LOCAL_HOST_ID) => ["operator-models", hostId] as const,
  operatorCapabilities: (hostId = LOCAL_HOST_ID) => ["operator-capabilities", hostId] as const,
  operatorUsage: (hostId = LOCAL_HOST_ID, sessionId?: string) =>
    ["operator-usage", hostId, sessionId] as const,
  /**
   * #556: Length-2 prefix used for invalidation. TanStack Query v5 prefix
   * matching is positional, so `["operator-usage", hostId]` matches every
   * session-scoped variant `["operator-usage", hostId, <sessionId>]`,
   * while `operatorUsage(hostId)` (which trails with `undefined`) would not.
   */
  operatorUsageAll: (hostId = LOCAL_HOST_ID) => ["operator-usage", hostId] as const,
  cliSessions: (hostId = LOCAL_HOST_ID) => ["cli-sessions", hostId] as const,
  cliSession: (id: string, hostId = LOCAL_HOST_ID) => ["cli-session", hostId, id] as const,
  debugLog: (
    sessionId: string,
    runId: string,
    params: DebugLogParams = {},
    hostId = LOCAL_HOST_ID
  ) => ["debug-log", hostId, sessionId, runId, params] as const,
  sessionDebugLog: (sessionId: string, params: DebugLogParams = {}, hostId = LOCAL_HOST_ID) =>
    ["session-debug-log", hostId, sessionId, params] as const,
  sessionDebugLogSkeleton: (
    sessionId: string,
    params: Omit<DebugLogParams, "projection"> = {},
    hostId = LOCAL_HOST_ID
  ) => ["session-debug-log-skeleton", hostId, sessionId, params] as const,
  // Flight Recorder v3 (synthesis §4b) — bounded summary envelopes.
  sessionCheckpoints: (sessionId: string, hostId = LOCAL_HOST_ID) =>
    ["session-checkpoints", hostId, sessionId] as const,
  sessionRewindSnapshots: (sessionId: string, hostId = LOCAL_HOST_ID) =>
    ["session-rewind-snapshots", hostId, sessionId] as const,
  subagentActivity: (sessionId: string, hostId = LOCAL_HOST_ID) =>
    ["subagent-activity", hostId, sessionId] as const,
  subagentInternals: (sessionId: string, hostId = LOCAL_HOST_ID) =>
    ["subagent-internals", hostId, sessionId] as const,
  sessionMissionAtlas: (sessionId: string, hostId = LOCAL_HOST_ID) =>
    ["session-mission-atlas", hostId, sessionId] as const,
};

function withLeadingSlash(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
}

/** Merge a filters query string and a limit query string into one. */
export function combineQueryStrings(filtersQs: string, limitQs: string): string {
  return filtersQs ? `${filtersQs}${limitQs ? `&${limitQs.slice(1)}` : ""}` : limitQs;
}

export function createQueryString(
  input: Record<string, string | number | boolean | null | undefined>
): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(input)) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}

export function createArrayQueryString(
  input: Record<string, string | string[] | null | undefined>
): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(input)) {
    if (Array.isArray(value)) {
      if (value.length > 0) {
        params.set(key, value.join(","));
      }
      continue;
    }
    if (value) params.set(key, value);
  }
  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}

function normalizeListParam(values?: string[]): string[] | undefined {
  if (!values?.length) return undefined;
  const normalized = Array.from(
    new Set(values.map((value) => value.trim()).filter((value) => value.length > 0))
  ).sort((a, b) => a.localeCompare(b));
  return normalized.length > 0 ? normalized : undefined;
}

function normalizeGraphParams(params: GraphQueryParams = {}): GraphQueryParams {
  const normalizedLimit =
    typeof params.limit === "number" && Number.isFinite(params.limit)
      ? Math.max(1, Math.floor(params.limit))
      : undefined;

  return {
    wing: normalizeListParam(params.wing),
    room: normalizeListParam(params.room),
    kind: normalizeListParam(params.kind),
    limit: normalizedLimit,
  };
}

const evidenceRelationTypes: readonly EvidenceRelationType[] = [
  "SAME_SESSION",
  "RESOLVED_BY",
  "TAG_OVERLAP",
  "SAME_TOPIC",
];

function normalizeEvidenceRelationTypes(
  values?: EvidenceRelationType[]
): EvidenceRelationType[] | undefined {
  const normalized = normalizeListParam(values) ?? [];
  const allowed = normalized.filter((value): value is EvidenceRelationType =>
    evidenceRelationTypes.includes(value as EvidenceRelationType)
  );
  return allowed.length > 0 ? allowed : undefined;
}

function createSoftQueryString(input: SimilarityQueryParams = {}): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(input)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        params.append(key, String(item));
      }
      continue;
    }
    params.set(key, String(value));
  }
  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}

export function normalizeSessionsResponse(input: SessionsResponse): SessionListResponse {
  const parsed = sessionsResponseSchema.parse(input);
  if (Array.isArray(parsed)) {
    return {
      items: parsed,
      total: parsed.length,
      page: 1,
      page_size: parsed.length || DEFAULT_PAGE_SIZE,
      has_more: false,
    };
  }
  return sessionListResponseSchema.parse(parsed);
}

export function useSessions(
  params: SessionsQueryParams = {},
  host: HostProfile = LOCAL_HOST,
  enabled = true
) {
  // sort is applied client-side; do not forward to the backend
  const queryString = createQueryString({
    page: params.page,
    page_size: params.pageSize,
    q: params.query,
    source: params.source,
    has_summary: params.hasSummary,
  });

  return useQuery({
    queryKey: queryKeys.sessions(params, host.id),
    staleTime: STALE_TIMES.sessions,
    gcTime: CACHE_TIMES.sessions,
    enabled,
    queryFn: async (): Promise<SessionListResponse> => {
      const data = await hostFetch<SessionsResponse>(
        withLeadingSlash(`/api/sessions${queryString}`),
        host
      );
      return normalizeSessionsResponse(data);
    },
  });
}

export function useSessionDetail(
  sessionId: string,
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  return useQuery({
    queryKey: queryKeys.sessionDetail(sessionId, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(sessionId),
    queryFn: async (): Promise<SessionDetailResponse> => {
      const data = await hostFetch<SessionDetailResponse>(
        withLeadingSlash(`/api/sessions/${encodeURIComponent(sessionId)}`),
        host
      );
      return sessionDetailResponseSchema.parse(data);
    },
  });
}

export function useSearch(
  params: SearchQueryParams,
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  const queryString = createArrayQueryString({
    q: params.query,
    src: params.sources,
    kind: params.kinds,
    in: params.cols,
  });

  return useQuery({
    queryKey: queryKeys.search(params, host.id),
    staleTime: STALE_TIMES.search,
    gcTime: CACHE_TIMES.search,
    enabled: enabled && params.query.trim().length > 0,
    queryFn: async (): Promise<SearchResponse> => {
      const data = await hostFetch<SearchResponse>(
        withLeadingSlash(`/api/search${queryString}`),
        host
      );
      return searchResponseSchema.parse(data);
    },
  });
}

export function useHealth(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.health(host.id),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled,
    queryFn: async (): Promise<HealthResponse> => {
      const data = await hostFetch<HealthResponse>(withLeadingSlash("/healthz"), host);
      return healthResponseSchema.parse(data);
    },
  });
}

export function useSyncStatus(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.syncStatus(host.id),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled,
    queryFn: async (): Promise<SyncStatusResponse> => {
      const data = await hostFetch<SyncStatusResponse>(withLeadingSlash("/api/sync/status"), host);
      return syncStatusResponseSchema.parse(data);
    },
  });
}

export function useScoutStatus(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.scoutStatus(host.id),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled,
    queryFn: async (): Promise<TrendScoutStatusResponse> => {
      const data = await hostFetch<TrendScoutStatusResponse>(
        withLeadingSlash("/api/scout/status"),
        host
      );
      return trendScoutStatusResponseSchema.parse(data);
    },
  });
}

export function useScoutResearchPack(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.scoutResearchPack(host.id),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled,
    queryFn: async (): Promise<ResearchPackResponse> => {
      const data = await hostFetch<ResearchPackResponse>(
        withLeadingSlash("/api/scout/research-pack"),
        host
      );
      return researchPackResponseSchema.parse(data);
    },
  });
}

export function useReloadScoutResearchPack(host: HostProfile = LOCAL_HOST) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (): Promise<ResearchPackReloadResponse> => {
      const data = await hostFetch<ResearchPackReloadResponse>(
        withLeadingSlash("/api/scout/research-pack/reload"),
        host,
        { method: "POST" }
      );
      return researchPackReloadResponseSchema.parse(data);
    },
    onSuccess: async (data) => {
      if (!data.ok) return;
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.scoutResearchPack(host.id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.scoutStatus(host.id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.retro("repo", host.id) }),
      ]);
    },
  });
}

export function useDashboard(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.dashboard(host.id),
    staleTime: STALE_TIMES.dashboard,
    gcTime: CACHE_TIMES.dashboard,
    enabled,
    queryFn: async (): Promise<DashboardStats> => {
      const data = await hostFetch<DashboardStats>(withLeadingSlash("/api/dashboard/stats"), host);
      return dashboardStatsSchema.parse(data);
    },
  });
}

export function useGraph(
  params: GraphQueryParams = {},
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  const normalizedParams = normalizeGraphParams(params);
  const graphFiltersQueryString = createArrayQueryString({
    wing: normalizedParams.wing,
    room: normalizedParams.room,
    kind: normalizedParams.kind,
  });
  const graphLimitQueryString = createQueryString({ limit: normalizedParams.limit });
  const graphQueryString = combineQueryStrings(graphFiltersQueryString, graphLimitQueryString);

  return useQuery({
    queryKey: queryKeys.graphLegacy(normalizedParams, host.id),
    staleTime: STALE_TIMES.graph,
    gcTime: CACHE_TIMES.graph,
    enabled,
    queryFn: async (): Promise<GraphResponse> => {
      const data = await hostFetch<GraphResponse>(
        withLeadingSlash(`/api/graph${graphQueryString}`),
        host
      );
      return graphResponseSchema.parse(data);
    },
  });
}

export function useEvidenceGraph(
  params: EvidenceGraphQueryParams = {},
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  const normalizedParams = normalizeGraphParams(params);
  const normalizedRelationTypes = normalizeEvidenceRelationTypes(params.relation_type);
  const graphFiltersQueryString = createArrayQueryString({
    wing: normalizedParams.wing,
    room: normalizedParams.room,
    kind: normalizedParams.kind,
    relation_type: normalizedRelationTypes,
  });
  const graphLimitQueryString = createQueryString({ limit: normalizedParams.limit });
  const graphQueryString = combineQueryStrings(graphFiltersQueryString, graphLimitQueryString);

  return useQuery({
    queryKey: queryKeys.graphEvidence(
      {
        ...normalizedParams,
        relation_type: normalizedRelationTypes,
      },
      host.id
    ),
    staleTime: STALE_TIMES.graph,
    gcTime: CACHE_TIMES.graph,
    enabled,
    queryFn: async (): Promise<EvidenceGraphResponse> => {
      const data = await hostFetch<EvidenceGraphResponse>(
        withLeadingSlash(`/api/graph/evidence${graphQueryString}`),
        host
      );
      return evidenceGraphResponseSchema.parse(data);
    },
  });
}

export function useEmbeddings(enabled = true, host: HostProfile = LOCAL_HOST) {
  return useQuery({
    queryKey: queryKeys.embeddings(host.id),
    staleTime: STALE_TIMES.embeddings,
    gcTime: CACHE_TIMES.embeddings,
    enabled,
    queryFn: async (): Promise<EmbeddingProjection> => {
      const data = await hostFetch<EmbeddingProjection>(
        withLeadingSlash("/api/embeddings/points"),
        host
      );
      return embeddingProjectionSchema.parse(data);
    },
  });
}

export function useSimilarity(
  params: SimilarityQueryParams = {},
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  const queryString = createSoftQueryString(params);
  return useQuery({
    queryKey: queryKeys.graphSimilarity(params, host.id),
    staleTime: STALE_TIMES.graph,
    gcTime: CACHE_TIMES.graph,
    enabled,
    queryFn: async (): Promise<SimilarityResponse> => {
      const data = await hostFetch<SimilarityResponse>(
        withLeadingSlash(`/api/graph/similarity${queryString}`),
        host
      );
      return similarityResponseSchema.parse(data);
    },
  });
}

export function useCommunities(enabled = true, host: HostProfile = LOCAL_HOST) {
  return useQuery({
    queryKey: queryKeys.graphCommunities(host.id),
    staleTime: STALE_TIMES.graph,
    gcTime: CACHE_TIMES.graph,
    enabled,
    queryFn: async (): Promise<CommunitiesResponse> => {
      const data = await hostFetch<CommunitiesResponse>(
        withLeadingSlash("/api/graph/communities"),
        host
      );
      return communitiesResponseSchema.parse(data);
    },
  });
}

export function useEval(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.eval(host.id),
    staleTime: STALE_TIMES.eval,
    gcTime: CACHE_TIMES.eval,
    enabled,
    queryFn: async (): Promise<EvalResponse> => {
      const data = await hostFetch<EvalResponse>(withLeadingSlash("/api/eval/stats"), host);
      return evalResponseSchema.parse(data);
    },
  });
}

export function useTentacleStatus(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.tentacleStatus(host.id),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled,
    queryFn: async (): Promise<TentacleStatusResponse> => {
      const data = await hostFetch<TentacleStatusResponse>(
        withLeadingSlash("/api/tentacles/status"),
        host
      );
      return tentacleStatusResponseSchema.parse(data);
    },
  });
}

export function useSkillMetrics(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.skillMetrics(host.id),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled,
    queryFn: async (): Promise<SkillMetricsResponse> => {
      const data = await hostFetch<SkillMetricsResponse>(
        withLeadingSlash("/api/skills/metrics"),
        host
      );
      return skillMetricsResponseSchema.parse(data);
    },
  });
}

/**
 * Fetches the installed skill catalog from the connected host.
 *
 * Calls `GET /api/skills/catalog` and returns the list of globally- and
 * project-installed Copilot skills along with their metadata.  The response
 * degrades gracefully: when no skills are installed the endpoint returns an
 * empty `skills` array (not an error).
 */
export function useSkillCatalog(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.skillCatalog(host.id),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled,
    queryFn: async (): Promise<SkillCatalogResponse> => {
      const data = await hostFetch<SkillCatalogResponse>(
        withLeadingSlash("/api/skills/catalog"),
        host
      );
      return skillCatalogResponseSchema.parse(data);
    },
  });
}

export function useCompare(
  sessionA: string,
  sessionB: string,
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  const queryString = createQueryString({
    a: sessionA,
    b: sessionB,
  });

  return useQuery({
    queryKey: queryKeys.compare(sessionA, sessionB, host.id),
    staleTime: STALE_TIMES.compare,
    gcTime: CACHE_TIMES.compare,
    enabled: enabled && Boolean(sessionA) && Boolean(sessionB),
    queryFn: async (): Promise<CompareResponse> => {
      const data = await hostFetch<CompareResponse>(
        withLeadingSlash(`/api/compare${queryString}`),
        host
      );
      return compareResponseSchema.parse(data);
    },
  });
}

export function useSubmitFeedback(host: HostProfile = LOCAL_HOST) {
  return useMutation({
    mutationFn: async (payload: FeedbackRequest): Promise<FeedbackResponse> => {
      const data = await hostFetch<FeedbackResponse>(withLeadingSlash("/api/feedback"), host, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(feedbackRequestSchema.parse(payload)),
      });
      return feedbackResponseSchema.parse(data);
    },
  });
}

export function useRetro(
  mode: "repo" | "local" = "repo",
  host: HostProfile = LOCAL_HOST,
  enabled = true
) {
  return useQuery({
    queryKey: queryKeys.retro(mode, host.id),
    staleTime: STALE_TIMES.retro,
    gcTime: CACHE_TIMES.retro,
    enabled,
    queryFn: async (): Promise<RetroResponse> => {
      const data = await hostFetch<RetroResponse>(
        withLeadingSlash(`/api/retro/summary?mode=${mode}`),
        host
      );
      return retroResponseSchema.parse(data);
    },
  });
}

export function useKnowledgeInsights(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.knowledgeInsights(host.id),
    staleTime: STALE_TIMES.insights,
    gcTime: CACHE_TIMES.insights,
    enabled,
    queryFn: async (): Promise<KnowledgeInsightsResponse> => {
      const data = await hostFetch<KnowledgeInsightsResponse>(
        withLeadingSlash("/api/knowledge/insights"),
        host
      );
      return knowledgeInsightsResponseSchema.parse(data);
    },
  });
}

export function useWorkflowHealth(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.workflowHealth(host.id),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled,
    queryFn: async (): Promise<WorkflowHealthResponse> => {
      const data = await hostFetch<WorkflowHealthResponse>(
        withLeadingSlash("/api/workflow/health"),
        host
      );
      return workflowHealthResponseSchema.parse(data);
    },
  });
}

// ── Operator/Chat hooks (/api/operator/*) ─────────────────────────────
// All operator hooks accept an optional `host` parameter as the LAST argument
// (defaulting to LOCAL_HOST) so existing callers that pass `enabled: boolean`
// positionally are backward-compatible. Query keys are scoped by host.id.

export function useOperatorSessions(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.operatorSessions(host.id),
    staleTime: STALE_TIMES.sessions,
    gcTime: CACHE_TIMES.sessions,
    enabled,
    queryFn: async (): Promise<OperatorSessionListResponse> => {
      const data = await hostFetch<OperatorSessionListResponse>(
        withLeadingSlash("/api/operator/sessions"),
        host
      );
      return operatorSessionListResponseSchema.parse(data);
    },
  });
}

export function useOperatorSession(id: string, enabled = true, host: HostProfile = LOCAL_HOST) {
  return useQuery({
    queryKey: queryKeys.operatorSession(id, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(id),
    queryFn: async (): Promise<OperatorSession> => {
      const data = await hostFetch<OperatorSession>(
        withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(id)}`),
        host
      );
      return operatorSessionSchema.parse(data);
    },
  });
}

export function useOperatorRuns(sessionId: string, enabled = true, host: HostProfile = LOCAL_HOST) {
  return useQuery({
    queryKey: queryKeys.operatorRuns(sessionId, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    refetchOnMount: "always",
    enabled: enabled && Boolean(sessionId),
    // Knowledge sessions (SQLite-backed CLI sessions) are not in the operator
    // store, so this endpoint returns 404 for them. Don't retry on 404 — it
    // won't resolve itself.
    retry: (failureCount, error) => {
      if (error instanceof Error && error.message.includes("404")) return false;
      return failureCount < 3;
    },
    queryFn: async (): Promise<OperatorRunsResponse> => {
      try {
        const data = await hostFetch<OperatorRunsResponse>(
          withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}/runs`),
          host
        );
        return operatorRunsResponseSchema.parse(data);
      } catch (err) {
        // Knowledge sessions return 404 — treat as empty runs list so the
        // debug-log tab shows an empty state rather than an error.
        if (err instanceof Error && err.message.includes("404")) {
          return { runs: [], count: 0 };
        }
        throw err;
      }
    },
  });
}

/**
 * Issue #564: Chat Workbench feed.
 *
 * Polls `GET /api/operator/runs` for the currently active (non-terminal) runs.
 * The response is a strict public-summary allowlist — never contains prompt,
 * events, files, or other sensitive run internals.
 *
 * Hosts that don't advertise the `runs_workbench` capability should call this
 * with `enabled=false`; the caller is responsible for gating via
 * `useHostFeature(host, "runs_workbench")` so legacy backends remain
 * untouched. When `enabled` flips off the query stays idle.
 *
 * 404 responses (e.g. legacy backend that does not implement the endpoint)
 * are normalized to an empty runs list so the workbench panel renders an
 * empty state rather than an error.
 */
export function useOperatorActiveRuns(enabled = true, host: HostProfile = LOCAL_HOST) {
  return useQuery({
    queryKey: queryKeys.operatorActiveRuns(host.id),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    refetchOnMount: "always",
    enabled,
    retry: (failureCount, error) => {
      if (error instanceof Error && error.message.includes("404")) return false;
      return failureCount < 2;
    },
    queryFn: async (): Promise<OperatorActiveRunsResponse> => {
      try {
        const data = await hostFetch<OperatorActiveRunsResponse>(
          withLeadingSlash("/api/operator/runs"),
          host
        );
        return operatorActiveRunsResponseSchema.parse(data);
      } catch (err) {
        if (err instanceof Error && err.message.includes("404")) {
          return { runs: [], count: 0 };
        }
        throw err;
      }
    },
  });
}

/**
 * #559: Fetch the run admission queue (`GET /api/operator/queue`).
 * Gated by `run_queue` capability. Normalizes 404 to an empty list
 * so older backends don't break the UI.
 */
export function useOperatorQueue(enabled = true, host: HostProfile = LOCAL_HOST) {
  return useQuery({
    queryKey: queryKeys.operatorQueue(host.id),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    refetchOnMount: "always",
    enabled,
    retry: (failureCount, error) => {
      if (error instanceof Error && error.message.includes("404")) return false;
      return failureCount < 2;
    },
    queryFn: async (): Promise<OperatorQueueResponse> => {
      try {
        const data = await hostFetch<OperatorQueueResponse>(
          withLeadingSlash("/api/operator/queue"),
          host
        );
        return operatorQueueResponseSchema.parse(data);
      } catch (err) {
        if (err instanceof Error && err.message.includes("404")) {
          return { entries: [], count: 0 };
        }
        throw err;
      }
    },
  });
}

export function useCreateOperatorSession(host: HostProfile = LOCAL_HOST) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      payload,
      host: overrideHost,
    }: {
      payload: CreateOperatorSessionRequest;
      host?: HostProfile;
    }): Promise<OperatorSession> => {
      const targetHost = overrideHost ?? host;
      const data = await hostFetch<OperatorSession>(
        withLeadingSlash("/api/operator/sessions"),
        targetHost,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(createOperatorSessionRequestSchema.parse(payload)),
        }
      );
      return operatorSessionSchema.parse(data);
    },
    onSuccess: (_data, variables) => {
      const targetHost = variables.host ?? host;
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorSessions(targetHost.id) });
    },
  });
}

export function useDeleteOperatorSession(host: HostProfile = LOCAL_HOST) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (sessionId: string): Promise<void> => {
      await hostFetch<unknown>(
        withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}/delete`),
        host,
        { method: "POST" }
      );
    },
    onSuccess: (_data, sessionId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorSessions(host.id) });
      queryClient.removeQueries({ queryKey: queryKeys.operatorSession(sessionId, host.id) });
      queryClient.removeQueries({ queryKey: queryKeys.operatorRuns(sessionId, host.id) });
    },
  });
}

/**
 * Issue #563: Cancel a single in-flight operator run.
 *
 * Idempotent — the server reports ``already_terminal=true`` with
 * ``code="RUN_ALREADY_TERMINAL"`` when the run had already reached a terminal
 * status (done / failed / timeout / cancelled) by the time the request
 * arrived.  Callers may safely retry without producing duplicate effects.
 *
 * On success we invalidate the per-session status, runs list, and the
 * active-runs workbench feed so the UI reflects the cancelled state.
 */
export function useCancelOperatorRun(sessionId: string, host: HostProfile = LOCAL_HOST) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (runId: string) => {
      const data = await hostFetch<unknown>(
        withLeadingSlash(
          `/api/operator/sessions/${encodeURIComponent(sessionId)}` +
            `/runs/${encodeURIComponent(runId)}/cancel`
        ),
        host,
        { method: "POST" }
      );
      return cancelRunResponseSchema.parse(data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.operatorSession(sessionId, host.id),
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.operatorRuns(sessionId, host.id),
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.operatorActiveRuns(host.id),
      });
    },
  });
}

export function useUpdateOperatorSession(sessionId: string, host: HostProfile = LOCAL_HOST) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      payload,
      host: overrideHost,
    }: {
      payload: UpdateOperatorSessionRequest;
      host?: HostProfile;
    }): Promise<OperatorSession> => {
      const targetHost = overrideHost ?? host;
      const data = await hostFetch<OperatorSession>(
        withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}`),
        targetHost,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(updateOperatorSessionRequestSchema.parse(payload)),
        }
      );
      return operatorSessionSchema.parse(data);
    },
    onSuccess: (_data, variables) => {
      const targetHost = variables.host ?? host;
      queryClient.invalidateQueries({
        queryKey: queryKeys.operatorSession(sessionId, targetHost.id),
      });
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorSessions(targetHost.id) });
    },
  });
}

export function useSubmitPrompt(sessionId: string, host: HostProfile = LOCAL_HOST) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: PromptRequest): Promise<PromptSubmitResponse> => {
      const data = await hostFetch<PromptSubmitResponse>(
        withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}/prompt`),
        host,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(promptRequestSchema.parse(payload)),
        }
      );
      return promptSubmitResponseSchema.parse(data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorSession(sessionId, host.id) });
      // #556: Invalidate every session-scoped operator-usage query for this host
      // by using the length-2 prefix (positional prefix match in TanStack v5).
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorUsageAll(host.id) });
    },
  });
}

/**
 * POST /api/operator/sessions/{id}/preflight — token/cost preview.
 *
 * #557: Provides a lightweight estimation without persisting the prompt body.
 * Call this before submission to show the user estimated tokens and quota.
 */
export function usePromptPreflight(sessionId: string, host: HostProfile = LOCAL_HOST) {
  return useMutation({
    mutationFn: async (payload: PromptRequest): Promise<PreflightResponse> => {
      const data = await hostFetch<PreflightResponse>(
        withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}/preflight`),
        host,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: payload.prompt, files: payload.files }),
        }
      );
      return preflightResponseSchema.parse(data);
    },
  });
}

/**
 * GET /api/operator/usage — current usage counts and quota.
 *
 * #556: Returns prompts this hour/today and remaining quota.
 */
export function useOperatorUsage(
  sessionId?: string,
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  return useQuery({
    queryKey: queryKeys.operatorUsage(host.id, sessionId),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled,
    queryFn: async (): Promise<UsageResponse> => {
      const qs = sessionId ? createQueryString({ session: sessionId }) : "";
      const data = await hostFetch<UsageResponse>(
        withLeadingSlash(`/api/operator/usage${qs}`),
        host
      );
      return usageResponseSchema.parse(data);
    },
  });
}

/**
 * #557: Request body for `POST /api/operator/prompt/preflight` — the generic
 * (non-session-bound) preflight endpoint. Attachment payload is metadata only.
 */
export interface GenericPreflightRequest {
  prompt: string;
  model?: string;
  host_id?: string;
  session_id?: string;
  files?: Array<{ name: string; type: string; size: number }>;
}

/**
 * POST /api/operator/prompt/preflight — generic (non-session-bound) preflight.
 *
 * #557: Same response shape as `usePromptPreflight` but requires no existing
 * session row. Used by the Composer to estimate tokens, context fit, and
 * quota status as the user types, before any prompt is submitted.
 *
 * The prompt body is NEVER persisted. Only attachment metadata is sent
 * (`{name, type, size}`); attachment bytes are never re-read for preflight.
 */
export function useGenericPromptPreflight(host: HostProfile = LOCAL_HOST) {
  return useMutation({
    mutationFn: async (payload: GenericPreflightRequest): Promise<PreflightResponse> => {
      const body: Record<string, unknown> = { prompt: payload.prompt };
      if (payload.model) body.model = payload.model;
      if (payload.host_id) body.host_id = payload.host_id;
      if (payload.session_id) body.session_id = payload.session_id;
      if (payload.files && payload.files.length > 0) body.files = payload.files;

      const data = await hostFetch<PreflightResponse>(
        withLeadingSlash(`/api/operator/prompt/preflight`),
        host,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      );
      return preflightResponseSchema.parse(data);
    },
  });
}

/**
 * #556: Request body for `POST /api/operator/usage/override`. The audit record
 * never contains prompt content — only the structured `reason` code.
 */
export interface UsageOverrideRequest {
  reason: string;
  session_id?: string;
  host_id?: string;
  model_id?: string;
  actor?: string;
}

/**
 * POST /api/operator/usage/override — record a soft-cap override.
 *
 * #556: The user explicitly acknowledges a near-quota warning before
 * resubmitting with `override_acknowledged: true`. This route stores an
 * audit record with the structured reason code (never the prompt body).
 *
 * On success, invalidates the `operatorUsage` query so the UI reflects
 * the new override audit count and any policy-driven changes.
 */
export function useUsageOverride(host: HostProfile = LOCAL_HOST) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: UsageOverrideRequest): Promise<UsageOverrideResponse> => {
      const body: Record<string, unknown> = { reason: payload.reason };
      if (payload.session_id) body.session_id = payload.session_id;
      if (payload.host_id) body.host_id = payload.host_id;
      if (payload.model_id) body.model_id = payload.model_id;
      if (payload.actor) body.actor = payload.actor;

      const data = await hostFetch<UsageOverrideResponse>(
        withLeadingSlash(`/api/operator/usage/override`),
        host,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      );
      return usageOverrideResponseSchema.parse(data);
    },
    onSuccess: () => {
      // #556: Invalidate every session-scoped operator-usage query for this host
      // (length-2 prefix; see note on queryKeys.operatorUsageAll).
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorUsageAll(host.id) });
    },
  });
}

export function useOperatorStatus(
  sessionId: string,
  runId: string,
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  return useQuery({
    queryKey: queryKeys.operatorStatus(sessionId, runId, host.id),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled: enabled && Boolean(sessionId) && Boolean(runId),
    queryFn: async (): Promise<OperatorRunStatus> => {
      const qs = createQueryString({ run: runId });
      const data = await hostFetch<OperatorRunStatus>(
        withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}/status${qs}`),
        host
      );
      return operatorRunStatusSchema.parse(data);
    },
  });
}

export function createOperatorStreamPath(sessionId: string, runId: string): string {
  const qs = createQueryString({ run: runId });
  return withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}/stream${qs}`);
}

/**
 * Returns the full stream URL for a given host profile.
 *
 * The auth token is intentionally NOT appended to the URL; callers must use
 * `fetch` with an `Authorization: Bearer` header for remote hosts (fixes #32:
 * token must not appear in browser-visible URLs).  EventSource cannot be used
 * for remote hosts because it does not support custom request headers.
 *
 * For same-origin / local hosts the URL has no token; cookies handle auth.
 *
 * Path prefixes in `host.base_url` are preserved — fixes #31.
 */
export function createOperatorStreamUrl(
  sessionId: string,
  runId: string,
  host: HostProfile
): string {
  const path = `/api/operator/sessions/${encodeURIComponent(sessionId)}/stream`;
  const isRemote = host.base_url.length > 0;
  const base = isRemote
    ? host.base_url
    : typeof window !== "undefined"
      ? window.location.origin
      : "";
  // buildHostUrl preserves any path prefix in base_url (issue #31).
  const url = buildHostUrl(base, path);
  url.searchParams.set("run", runId);
  // Token is omitted from URL on purpose: remote callers must send it via
  // Authorization header (use fetch, not EventSource).
  return url.toString();
}

/**
 * Returns the full SSE URL for the `/api/live` stream, targeting the given host.
 *
 * The auth token is intentionally NOT appended to the URL; remote callers must
 * use fetch-based streaming with an `Authorization: Bearer ...` header so
 * credentials do not leak into browser-visible URLs (fixes #32).
 *
 * Path prefixes in `host.base_url` are preserved — fixes #31.
 */
export function createLiveStreamUrl(host: HostProfile): string {
  const path = "/api/live";
  const isRemote = host.base_url.length > 0;
  const base = isRemote
    ? host.base_url
    : typeof window !== "undefined"
      ? window.location.origin
      : "";
  // buildHostUrl preserves any path prefix in base_url (issue #31).
  return buildHostUrl(base, path).toString();
}

export function usePathSuggest(
  q: string,
  hidden = false,
  host: HostProfile = LOCAL_HOST,
  enabled = true
) {
  return useQuery({
    queryKey: queryKeys.operatorSuggest(q, hidden, host.id),
    staleTime: STALE_TIMES.search,
    gcTime: CACHE_TIMES.search,
    enabled,
    queryFn: async (): Promise<PathSuggestResponse> => {
      const qs = createQueryString({ q, ...(hidden ? { hidden: "true" } : {}) });
      const data = await hostFetch<PathSuggestResponse>(
        withLeadingSlash(`/api/operator/suggest${qs}`),
        host
      );
      return pathSuggestResponseSchema.parse(data);
    },
  });
}

export function useFilePreview(path: string, enabled = true, host: HostProfile = LOCAL_HOST) {
  return useQuery({
    queryKey: queryKeys.operatorPreview(path, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(path),
    queryFn: async (): Promise<FilePreviewResponse> => {
      const qs = createQueryString({ path });
      const data = await hostFetch<FilePreviewResponse>(
        withLeadingSlash(`/api/operator/preview${qs}`),
        host
      );
      return filePreviewResponseSchema.parse(data);
    },
  });
}

export function useFileDiff(
  pathA: string,
  pathB: string,
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  return useQuery({
    queryKey: queryKeys.operatorDiff(pathA, pathB, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(pathA) && Boolean(pathB),
    queryFn: async (): Promise<FileDiffResponse> => {
      const qs = createQueryString({ a: pathA, b: pathB });
      const data = await hostFetch<FileDiffResponse>(
        withLeadingSlash(`/api/operator/diff${qs}`),
        host
      );
      return fileDiffResponseSchema.parse(data);
    },
  });
}

export function useOperatorModelCatalog(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.operatorModels(host.id),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled,
    queryFn: async (): Promise<OperatorModelCatalogResponse> => {
      const data = await hostFetch<OperatorModelCatalogResponse>(
        withLeadingSlash("/api/operator/models"),
        host
      );
      return operatorModelCatalogResponseSchema.parse(data);
    },
  });
}

/**
 * Fetches the runtime capabilities of an operator host.
 *
 * Calls `GET /api/operator/capabilities` on the given host to discover
 * the CLI kind, supported modes, and supported features. Use this to
 * adapt UI capabilities to the selected remote host.
 */
export function useHostCapabilities(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.operatorCapabilities(host.id),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled,
    queryFn: async (): Promise<HostCapabilities> => {
      const data = await hostFetch<HostCapabilities>(
        withLeadingSlash("/api/operator/capabilities"),
        host
      );
      return hostCapabilitiesSchema.parse(data);
    },
  });
}

// ── Debug Log (/api/operator/sessions/{sid}/runs/{rid}/debug) ─────────

/**
 * Query hook for the debug log of a specific operator session run.
 *
 * Fetches GET /api/operator/sessions/{sessionId}/runs/{runId}/debug with
 * optional filter params (from, limit, kind, level, since, projection,
 * until, to_idx).
 *
 * Returns React Query result with typed DebugLogResponse.
 */
export function useDebugLog(
  sessionId: string,
  runId: string,
  params: DebugLogParams = {},
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  return useQuery({
    queryKey: queryKeys.debugLog(sessionId, runId, params, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(sessionId) && Boolean(runId),
    queryFn: async (): Promise<DebugLogResponse> => {
      const qs = createQueryString({
        from: params.from,
        limit: params.limit,
        kind: params.kind,
        level: params.level,
        since: params.since,
        projection: params.projection,
        until: params.until,
        to_idx: params.to_idx,
      });
      const path = withLeadingSlash(
        `/api/operator/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/debug${qs}`
      );
      const data = await hostFetch<DebugLogResponse>(path, host);
      return debugLogResponseSchema.parse(data);
    },
  });
}

// ── Session-Scoped Debug Log (/api/session/{sid}/debug-log) ───────────────

/**
 * Query hook for the session-scoped debug log.
 *
 * Fetches GET /api/session/{sessionId}/debug-log with optional filter params
 * (from, limit, kind, level, since, projection, until, to_idx). Used when
 * there is no operator run (i.e. the session is a CLI/knowledge session with
 * has_operator_runs=false).
 *
 * For projection=skeleton use useSessionDebugLogSkeleton instead.
 * Existing callers passing no projection receive full entries (default backend
 * behavior) and the response parses as SessionDebugLogResponse — backward-
 * compatible.
 *
 * Returns React Query result with typed SessionDebugLogResponse.
 */
export function useSessionDebugLog(
  sessionId: string,
  params: DebugLogParams = {},
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  return useQuery({
    queryKey: queryKeys.sessionDebugLog(sessionId, params, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(sessionId),
    queryFn: async (): Promise<SessionDebugLogResponse> => {
      const qs = createQueryString({
        from: params.from,
        limit: params.limit,
        kind: params.kind,
        level: params.level,
        since: params.since,
        projection: params.projection,
        until: params.until,
        to_idx: params.to_idx,
      });
      const path = withLeadingSlash(`/api/session/${encodeURIComponent(sessionId)}/debug-log${qs}`);
      const data = await hostFetch<SessionDebugLogResponse>(path, host);
      return sessionDebugLogResponseSchema.parse(data);
    },
  });
}

// ── Session-Scoped Debug Log Skeleton (/api/session/{sid}/debug-log?projection=skeleton) ─

/**
 * Query hook for the session-scoped debug log skeleton projection.
 *
 * Fetches GET /api/session/{sessionId}/debug-log?projection=skeleton with
 * optional window params (from, limit up to 5000, kind, since, until, to_idx).
 * Returns lightweight BrowseDebugSkeletonEntry records suitable for timeline
 * and playback rendering without exposing message/attrs/tool_name/source.
 *
 * Query key is under "session-debug-log-skeleton" and is params-scoped, so
 * different window params get distinct cache entries. The projection param is
 * always "skeleton" and is not included in the key discriminator (it is
 * implicit in the key prefix).
 *
 * Returns React Query result with typed SessionDebugSkeletonResponse.
 */
export function useSessionDebugLogSkeleton(
  sessionId: string,
  params: Omit<DebugLogParams, "projection"> = {},
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  return useQuery({
    queryKey: queryKeys.sessionDebugLogSkeleton(sessionId, params, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(sessionId),
    queryFn: async (): Promise<SessionDebugSkeletonResponse> => {
      const qs = createQueryString({
        from: params.from,
        limit: params.limit,
        kind: params.kind,
        level: params.level,
        since: params.since,
        projection: "skeleton",
        until: params.until,
        to_idx: params.to_idx,
      });
      const path = withLeadingSlash(`/api/session/${encodeURIComponent(sessionId)}/debug-log${qs}`);
      const data = await hostFetch<SessionDebugSkeletonResponse>(path, host);
      return sessionDebugSkeletonResponseSchema.parse(data);
    },
  });
}

// ── Subagent Activity ────────────────────────────────────────────────────────

/**
 * Returns aggregated subagent activity rows for a CLI session.
 *
 * Fetches GET /api/session/{id}/subagent-activity, which performs a one-pass
 * streaming aggregation of subagent.started / subagent.completed /
 * subagent.failed events paired by toolCallId (primary) or agentId (fallback).
 *
 * - Query key is scoped by (hostId, sessionId) — no pagination params because
 *   the endpoint returns a single bounded response (cap 1000).
 * - Uses sessionDetail stale/gc times (same as useSessionDebugLog).
 * - Disabled when sessionId is empty.
 * - Response is parsed and validated against subagentActivityResponseSchema
 *   (strict z.object, no passthrough).
 */
export function useSubagentActivity(
  sessionId: string,
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  return useQuery({
    queryKey: queryKeys.subagentActivity(sessionId, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(sessionId),
    queryFn: async (): Promise<SubagentActivityResponse> => {
      const path = withLeadingSlash(
        `/api/session/${encodeURIComponent(sessionId)}/subagent-activity`
      );
      const data = await hostFetch<SubagentActivityResponse>(path, host);
      return subagentActivityResponseSchema.parse(data);
    },
  });
}

/**
 * Returns safe per-subagent internals for a CLI session.
 *
 * Fetches GET /api/session/{id}/subagent-internals, a bounded aggregation of
 * child tool/model events keyed by the backend's opaque span ids.  The route
 * intentionally reports skill loads only at session level because current CLI
 * skill events do not carry per-agent correlation fields.
 */
export function useSubagentInternals(
  sessionId: string,
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  return useQuery({
    queryKey: queryKeys.subagentInternals(sessionId, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(sessionId),
    queryFn: async (): Promise<SubagentInternalsResponse> => {
      const path = withLeadingSlash(
        `/api/session/${encodeURIComponent(sessionId)}/subagent-internals`
      );
      const data = await hostFetch<SubagentInternalsResponse>(path, host);
      return subagentInternalsResponseSchema.parse(data);
    },
  });
}

// ── CLI Session Discovery & Adopt/Confirm ──────────────────────────────────

/**
 * Returns true when an operator token is available for this host.
 *
 * Token semantics mirror those of `hostRequest`:
 *   - Remote host (`host.base_url` is non-empty): only `host.token` counts.
 *     `hostRequest` sends `host.token` as the Authorization header for remote
 *     requests and ignores the local session-storage token entirely.  Allowing
 *     a local session token to gate a remote probe would let the query fire but
 *     then fail with 401 because `hostRequest` would send no Authorization.
 *   - Local host (`host.base_url` is empty): either `host.token` **or** the
 *     local session-storage token (read via the pure {@link peekToken} helper)
 *     is sufficient.
 *
 * Using `peekToken()` — not `getToken()` — avoids triggering URL ingestion
 * (`?token=` parsing) and `history.replaceState` side effects during React
 * render (React Query calls `enabled` synchronously during the render phase).
 */
function hasOperatorToken(host: HostProfile): boolean {
  const isRemote = host.base_url.length > 0;
  if (isRemote) {
    return Boolean(host.token);
  }
  return Boolean(host.token) || Boolean(peekToken());
}

/**
 * Returns `true` when the error should suppress retries on a capability probe.
 *
 * - 404: backend doesn't support the endpoint (older version).
 * - "Unauthorized": `hostRequest`/`hostFetch` throws `new Error("Unauthorized")`
 *   (not "HTTP 401 …") for any 401 response.  Matching the literal production
 *   message keeps the predicate narrow — it doesn't swallow generic errors.
 */
function isNoRetryProbeError(error: unknown): boolean {
  return (
    error instanceof Error && (error.message.includes("404") || error.message === "Unauthorized")
  );
}

/**
 * Fetches CLI history sessions from `GET /api/operator/cli-sessions`.
 *
 * 404 responses (older backends that don't support cli_adopt) are surfaced as
 * errors rather than silently returning an empty list. The `retry` config
 * already disables retries for 404, so the error is surfaced once and the
 * dialog shows the "unavailable" state via `cliUnavailable`.
 */
export function useCliSessions(host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.cliSessions(host.id),
    staleTime: STALE_TIMES.sessions,
    gcTime: CACHE_TIMES.sessions,
    enabled: enabled && hasOperatorToken(host),
    retry: (failureCount, error) => {
      // Don't retry unavailable/unauthenticated capability probes.
      if (isNoRetryProbeError(error)) {
        return false;
      }
      return failureCount < 3;
    },
    queryFn: async (): Promise<CliSessionListResponse> => {
      const data = await hostFetch<CliSessionListResponse>(
        withLeadingSlash("/api/operator/cli-sessions"),
        host,
        undefined,
        { noRedirectOn401: true }
      );
      return cliSessionListResponseSchema.parse(data);
    },
  });
}

/**
 * Fetches a single CLI history session from `GET /api/operator/cli-sessions/{cli_session_id}`.
 */
export function useCliSession(id: string, host: HostProfile = LOCAL_HOST, enabled = true) {
  return useQuery({
    queryKey: queryKeys.cliSession(id, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(id) && hasOperatorToken(host),
    retry: (failureCount, error) => {
      if (isNoRetryProbeError(error)) {
        return false;
      }
      return failureCount < 3;
    },
    queryFn: async (): Promise<CliSession> => {
      const data = await hostFetch<CliSession>(
        withLeadingSlash(`/api/operator/cli-sessions/${encodeURIComponent(id)}`),
        host,
        undefined,
        { noRedirectOn401: true }
      );
      return cliSessionSchema.parse(data);
    },
  });
}

/**
 * Mutation hook that adopts a CLI session via `POST /api/operator/sessions/adopt`.
 *
 * SECURITY CONTRACT: The CLI UUID (`cli_session_id`) lives only in the POST
 * JSON body. The returned `OperatorSession.id` (the operator UUID) is the only
 * ID that may appear in router navigation, query params, or storage.
 */
export function useAdoptCliSession(host: HostProfile = LOCAL_HOST) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      payload,
      host: overrideHost,
    }: {
      payload: AdoptCliSessionRequest;
      host?: HostProfile;
    }): Promise<OperatorSession> => {
      const targetHost = overrideHost ?? host;
      // Validate payload — ensures cli_session_id is present before sending.
      const validated = adoptCliSessionRequestSchema.parse(payload);
      const data = await hostFetch<OperatorSession>(
        withLeadingSlash("/api/operator/sessions/adopt"),
        targetHost,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(validated),
        }
      );
      return operatorSessionSchema.parse(data);
    },
    onSuccess: (_data, variables) => {
      const targetHost = variables.host ?? host;
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorSessions(targetHost.id) });
    },
  });
}

/**
 * Mutation hook that confirms an adopted CLI session via
 * `POST /api/operator/sessions/{operator_id}/confirm`.
 *
 * After confirmation `confirmed_at` is set and `resume_ready` becomes `true`.
 * The composer is unblocked once `confirmed_at` is truthy.
 *
 * SECURITY: Only the operator session id (`sessionId`) appears here — the
 * CLI UUID must never be used in this path.
 */
export function useConfirmAdoptedSession(sessionId: string, host: HostProfile = LOCAL_HOST) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (): Promise<OperatorSession> => {
      const data = await hostFetch<OperatorSession>(
        withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}/confirm`),
        host,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        }
      );
      return operatorSessionSchema.parse(data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorSession(sessionId, host.id) });
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorSessions(host.id) });
    },
  });
}

// ── Flight Recorder v3 (synthesis §4b) ─────────────────────────────────────

/**
 * GET /api/session/{id}/checkpoints — bounded summary list.
 *
 * Backend returns only section presence booleans, sanitized titles, safe
 * basenames, and byte sizes; never raw checkpoint bodies.  Cache lifetimes
 * match `sessionDetail` so navigating between timeline tabs is instant.
 */
export function useSessionCheckpoints(
  sessionId: string,
  host: HostProfile = LOCAL_HOST,
  enabled = true
) {
  return useQuery({
    queryKey: queryKeys.sessionCheckpoints(sessionId, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(sessionId),
    queryFn: async (): Promise<BrowseCheckpointsResponse> => {
      const path = withLeadingSlash(`/api/session/${encodeURIComponent(sessionId)}/checkpoints`);
      const data = await hostFetch<BrowseCheckpointsResponse>(path, host);
      return browseCheckpointsResponseSchema.parse(data);
    },
  });
}

/**
 * GET /api/session/{id}/rewind-snapshots — bounded summary list.
 *
 * Backend exposes only sanitized commit/branch strings, byte-size counters,
 * and the hashed `event_span_id` for joining against debug-log spans.  The
 * raw `userMessage`, `eventId`, `files{}`, and `backupHashes` fields are
 * never returned.
 */
export function useSessionRewindSnapshots(
  sessionId: string,
  host: HostProfile = LOCAL_HOST,
  enabled = true
) {
  return useQuery({
    queryKey: queryKeys.sessionRewindSnapshots(sessionId, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(sessionId),
    queryFn: async (): Promise<BrowseRewindSnapshotsResponse> => {
      const path = withLeadingSlash(
        `/api/session/${encodeURIComponent(sessionId)}/rewind-snapshots`
      );
      const data = await hostFetch<BrowseRewindSnapshotsResponse>(path, host);
      return browseRewindSnapshotsResponseSchema.parse(data);
    },
  });
}

/**
 * GET /api/session/{id}/mission-atlas — full-session event aggregate.
 *
 * Returns only safe aggregate fields (counts, timestamps, lane totals,
 * bucketed heatmap, milestone rail). Raw entry content, file paths, tool
 * args, and error messages are never included. The UI must not display
 * error_sample content verbatim — treat it as an opaque category label.
 *
 * Non-fatal: caller should handle query.error gracefully and continue
 * rendering the TimelinePlayer even when this query fails.
 */
export function useSessionMissionAtlas(
  sessionId: string,
  enabled = true,
  host: HostProfile = LOCAL_HOST
) {
  return useQuery({
    queryKey: queryKeys.sessionMissionAtlas(sessionId, host.id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(sessionId),
    queryFn: async (): Promise<SessionMissionAtlasResponse> => {
      const path = withLeadingSlash(`/api/session/${encodeURIComponent(sessionId)}/mission-atlas`);
      const data = await hostFetch<SessionMissionAtlasResponse>(path, host);
      return sessionMissionAtlasResponseSchema.parse(data);
    },
  });
}
