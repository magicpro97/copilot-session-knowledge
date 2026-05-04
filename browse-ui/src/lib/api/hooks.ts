"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { CACHE_TIMES, DEFAULT_PAGE_SIZE, STALE_TIMES } from "@/lib/constants";
import { apiFetch, hostFetch } from "@/lib/api/client";
import { LOCAL_HOST, LOCAL_HOST_ID } from "@/lib/host-profiles";
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
  similarityResponseSchema,
  sessionsResponseSchema,
  workflowHealthResponseSchema,
  operatorSessionListResponseSchema,
  operatorSessionSchema,
  promptRequestSchema,
  promptSubmitResponseSchema,
  operatorRunStatusSchema,
  operatorRunsResponseSchema,
  pathSuggestResponseSchema,
  filePreviewResponseSchema,
  fileDiffResponseSchema,
  createOperatorSessionRequestSchema,
  operatorModelCatalogResponseSchema,
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
  SessionDetailResponse,
  SessionListResponse,
  SessionsResponse,
  WorkflowHealthResponse,
  OperatorSession,
  OperatorSessionListResponse,
  CreateOperatorSessionRequest,
  PromptRequest,
  PromptSubmitResponse,
  OperatorRunStatus,
  OperatorRunsResponse,
  PathSuggestResponse,
  FilePreviewResponse,
  FileDiffResponse,
  OperatorModelCatalogResponse,
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
  sessions: (params: SessionsQueryParams = {}) => ["sessions", params] as const,
  sessionDetail: (sessionId: string) => ["session-detail", sessionId] as const,
  search: (params: SearchQueryParams) => ["search", params] as const,
  health: (hostId = LOCAL_HOST_ID) => ["health", hostId] as const,
  syncStatus: (hostId = LOCAL_HOST_ID) => ["sync-status", hostId] as const,
  scoutStatus: (hostId = LOCAL_HOST_ID) => ["scout-status", hostId] as const,
  scoutResearchPack: (hostId = LOCAL_HOST_ID) => ["scout-research-pack", hostId] as const,
  tentacleStatus: (hostId = LOCAL_HOST_ID) => ["tentacle-status", hostId] as const,
  skillMetrics: (hostId = LOCAL_HOST_ID) => ["skill-metrics", hostId] as const,
  dashboard: (hostId = LOCAL_HOST_ID) => ["dashboard", hostId] as const,
  graphLegacy: (params: GraphQueryParams = {}) => ["graph-legacy", params] as const,
  graph: (params: GraphQueryParams = {}) => ["graph", params] as const,
  graphEvidence: (params: EvidenceGraphQueryParams = {}) => ["graph-evidence", params] as const,
  graphSimilarity: (params: SimilarityQueryParams = {}) => ["graph-similarity", params] as const,
  graphCommunities: () => ["graph-communities"] as const,
  embeddings: () => ["embeddings"] as const,
  eval: (hostId = LOCAL_HOST_ID) => ["eval", hostId] as const,
  retro: (mode: "repo" | "local" = "repo", hostId = LOCAL_HOST_ID) =>
    ["retro", mode, hostId] as const,
  knowledgeInsights: (hostId = LOCAL_HOST_ID) => ["knowledge-insights", hostId] as const,
  compare: (a: string, b: string) => ["compare", a, b] as const,
  workflowHealth: (hostId = LOCAL_HOST_ID) => ["workflow-health", hostId] as const,
  // Operator/Chat — all keys are scoped by hostId to prevent cross-host cache collisions
  operatorSessions: (hostId = LOCAL_HOST_ID) => ["operator-sessions", hostId] as const,
  operatorSession: (id: string, hostId = LOCAL_HOST_ID) =>
    ["operator-session", hostId, id] as const,
  operatorStatus: (sessionId: string, runId: string, hostId = LOCAL_HOST_ID) =>
    ["operator-status", hostId, sessionId, runId] as const,
  operatorRuns: (sessionId: string, hostId = LOCAL_HOST_ID) =>
    ["operator-runs", hostId, sessionId] as const,
  operatorSuggest: (q: string, hidden = false, hostId = LOCAL_HOST_ID) =>
    ["operator-suggest", hostId, q, hidden] as const,
  operatorPreview: (path: string, hostId = LOCAL_HOST_ID) =>
    ["operator-preview", hostId, path] as const,
  operatorDiff: (pathA: string, pathB: string, hostId = LOCAL_HOST_ID) =>
    ["operator-diff", hostId, pathA, pathB] as const,
  operatorModels: (hostId = LOCAL_HOST_ID) => ["operator-models", hostId] as const,
  operatorCapabilities: (hostId = LOCAL_HOST_ID) => ["operator-capabilities", hostId] as const,
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

export function useSessions(params: SessionsQueryParams = {}) {
  // sort is applied client-side; do not forward to the backend
  const queryString = createQueryString({
    page: params.page,
    page_size: params.pageSize,
    q: params.query,
    source: params.source,
    has_summary: params.hasSummary,
  });

  return useQuery({
    queryKey: queryKeys.sessions(params),
    staleTime: STALE_TIMES.sessions,
    gcTime: CACHE_TIMES.sessions,
    queryFn: async (): Promise<SessionListResponse> => {
      const data = await apiFetch<SessionsResponse>(
        withLeadingSlash(`/api/sessions${queryString}`)
      );
      return normalizeSessionsResponse(data);
    },
  });
}

export function useSessionDetail(sessionId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.sessionDetail(sessionId),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(sessionId),
    queryFn: async (): Promise<SessionDetailResponse> => {
      const data = await apiFetch<SessionDetailResponse>(
        withLeadingSlash(`/api/sessions/${encodeURIComponent(sessionId)}`)
      );
      return sessionDetailResponseSchema.parse(data);
    },
  });
}

export function useSearch(params: SearchQueryParams, enabled = true) {
  const queryString = createArrayQueryString({
    q: params.query,
    src: params.sources,
    kind: params.kinds,
    in: params.cols,
  });

  return useQuery({
    queryKey: queryKeys.search(params),
    staleTime: STALE_TIMES.search,
    gcTime: CACHE_TIMES.search,
    enabled: enabled && params.query.trim().length > 0,
    queryFn: async (): Promise<SearchResponse> => {
      const data = await apiFetch<SearchResponse>(withLeadingSlash(`/api/search${queryString}`));
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

export function useGraph(params: GraphQueryParams = {}) {
  const normalizedParams = normalizeGraphParams(params);
  const graphFiltersQueryString = createArrayQueryString({
    wing: normalizedParams.wing,
    room: normalizedParams.room,
    kind: normalizedParams.kind,
  });
  const graphLimitQueryString = createQueryString({ limit: normalizedParams.limit });
  const graphQueryString = combineQueryStrings(graphFiltersQueryString, graphLimitQueryString);

  return useQuery({
    queryKey: queryKeys.graphLegacy(normalizedParams),
    staleTime: STALE_TIMES.graph,
    gcTime: CACHE_TIMES.graph,
    queryFn: async (): Promise<GraphResponse> => {
      const data = await apiFetch<GraphResponse>(withLeadingSlash(`/api/graph${graphQueryString}`));
      return graphResponseSchema.parse(data);
    },
  });
}

export function useEvidenceGraph(params: EvidenceGraphQueryParams = {}) {
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
    queryKey: queryKeys.graphEvidence({
      ...normalizedParams,
      relation_type: normalizedRelationTypes,
    }),
    staleTime: STALE_TIMES.graph,
    gcTime: CACHE_TIMES.graph,
    queryFn: async (): Promise<EvidenceGraphResponse> => {
      const data = await apiFetch<EvidenceGraphResponse>(
        withLeadingSlash(`/api/graph/evidence${graphQueryString}`)
      );
      return evidenceGraphResponseSchema.parse(data);
    },
  });
}

export function useEmbeddings() {
  return useQuery({
    queryKey: queryKeys.embeddings(),
    staleTime: STALE_TIMES.embeddings,
    gcTime: CACHE_TIMES.embeddings,
    queryFn: async (): Promise<EmbeddingProjection> => {
      const data = await apiFetch<EmbeddingProjection>(withLeadingSlash("/api/embeddings/points"));
      return embeddingProjectionSchema.parse(data);
    },
  });
}

export function useSimilarity(params: SimilarityQueryParams = {}, enabled = true) {
  const queryString = createSoftQueryString(params);
  return useQuery({
    queryKey: queryKeys.graphSimilarity(params),
    staleTime: STALE_TIMES.graph,
    gcTime: CACHE_TIMES.graph,
    enabled,
    queryFn: async (): Promise<SimilarityResponse> => {
      const data = await apiFetch<SimilarityResponse>(
        withLeadingSlash(`/api/graph/similarity${queryString}`)
      );
      return similarityResponseSchema.parse(data);
    },
  });
}

export function useCommunities(enabled = true) {
  return useQuery({
    queryKey: queryKeys.graphCommunities(),
    staleTime: STALE_TIMES.graph,
    gcTime: CACHE_TIMES.graph,
    enabled,
    queryFn: async (): Promise<CommunitiesResponse> => {
      const data = await apiFetch<CommunitiesResponse>(withLeadingSlash("/api/graph/communities"));
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

export function useCompare(sessionA: string, sessionB: string, enabled = true) {
  const queryString = createQueryString({
    a: sessionA,
    b: sessionB,
  });

  return useQuery({
    queryKey: queryKeys.compare(sessionA, sessionB),
    staleTime: STALE_TIMES.compare,
    gcTime: CACHE_TIMES.compare,
    enabled: enabled && Boolean(sessionA) && Boolean(sessionB),
    queryFn: async (): Promise<CompareResponse> => {
      const data = await apiFetch<CompareResponse>(withLeadingSlash(`/api/compare${queryString}`));
      return compareResponseSchema.parse(data);
    },
  });
}

export function useSubmitFeedback() {
  return useMutation({
    mutationFn: async (payload: FeedbackRequest): Promise<FeedbackResponse> => {
      const data = await apiFetch<FeedbackResponse>(withLeadingSlash("/api/feedback"), {
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
    enabled: enabled && Boolean(sessionId),
    queryFn: async (): Promise<OperatorRunsResponse> => {
      const data = await hostFetch<OperatorRunsResponse>(
        withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}/runs`),
        host
      );
      return operatorRunsResponseSchema.parse(data);
    },
  });
}

export function useCreateOperatorSession(host: HostProfile = LOCAL_HOST) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: CreateOperatorSessionRequest): Promise<OperatorSession> => {
      const data = await hostFetch<OperatorSession>(
        withLeadingSlash("/api/operator/sessions"),
        host,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(createOperatorSessionRequestSchema.parse(payload)),
        }
      );
      return operatorSessionSchema.parse(data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorSessions(host.id) });
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
 * Because `EventSource` does not support custom request headers, the auth token
 * is appended as a query parameter for both local and remote hosts. The UI
 * must use this function (not `createOperatorStreamPath`) when connecting to
 * non-local hosts.
 */
export function createOperatorStreamUrl(
  sessionId: string,
  runId: string,
  host: HostProfile
): string {
  const path = `/api/operator/sessions/${encodeURIComponent(sessionId)}/stream`;
  const base = host.base_url || (typeof window !== "undefined" ? window.location.origin : "");
  const url = new URL(path, base);
  url.searchParams.set("run", runId);
  if (host.token) {
    url.searchParams.set("token", host.token);
  }
  return url.toString();
}

/**
 * Returns the full SSE URL for the `/api/live` stream, targeting the given host.
 *
 * Because `EventSource` does not support custom request headers, the auth token
 * is appended as a query parameter when connecting to a remote host.
 */
export function createLiveStreamUrl(host: HostProfile): string {
  const path = "/api/live";
  const base = host.base_url || (typeof window !== "undefined" ? window.location.origin : "");
  const url = new URL(path, base);
  if (host.token) {
    url.searchParams.set("token", host.token);
  }
  return url.toString();
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
