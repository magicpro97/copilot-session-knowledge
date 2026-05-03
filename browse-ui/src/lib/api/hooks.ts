"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { CACHE_TIMES, DEFAULT_PAGE_SIZE, STALE_TIMES } from "@/lib/constants";
import { apiFetch } from "@/lib/api/client";
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
  health: () => ["health"] as const,
  syncStatus: () => ["sync-status"] as const,
  scoutStatus: () => ["scout-status"] as const,
  scoutResearchPack: () => ["scout-research-pack"] as const,
  tentacleStatus: () => ["tentacle-status"] as const,
  skillMetrics: () => ["skill-metrics"] as const,
  dashboard: () => ["dashboard"] as const,
  graphLegacy: (params: GraphQueryParams = {}) => ["graph-legacy", params] as const,
  graph: (params: GraphQueryParams = {}) => ["graph", params] as const,
  graphEvidence: (params: EvidenceGraphQueryParams = {}) => ["graph-evidence", params] as const,
  graphSimilarity: (params: SimilarityQueryParams = {}) => ["graph-similarity", params] as const,
  graphCommunities: () => ["graph-communities"] as const,
  embeddings: () => ["embeddings"] as const,
  eval: () => ["eval"] as const,
  retro: (mode: "repo" | "local" = "repo") => ["retro", mode] as const,
  knowledgeInsights: () => ["knowledge-insights"] as const,
  compare: (a: string, b: string) => ["compare", a, b] as const,
  workflowHealth: () => ["workflow-health"] as const,
  // Operator/Chat
  operatorSessions: () => ["operator-sessions"] as const,
  operatorSession: (id: string) => ["operator-session", id] as const,
  operatorStatus: (sessionId: string, runId: string) =>
    ["operator-status", sessionId, runId] as const,
  operatorRuns: (sessionId: string) => ["operator-runs", sessionId] as const,
  operatorSuggest: (q: string, hidden = false) => ["operator-suggest", q, hidden] as const,
  operatorPreview: (path: string) => ["operator-preview", path] as const,
  operatorDiff: (pathA: string, pathB: string) => ["operator-diff", pathA, pathB] as const,
  operatorModels: () => ["operator-models"] as const,
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

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health(),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    queryFn: async (): Promise<HealthResponse> => {
      const data = await apiFetch<HealthResponse>(withLeadingSlash("/healthz"));
      return healthResponseSchema.parse(data);
    },
  });
}

export function useSyncStatus() {
  return useQuery({
    queryKey: queryKeys.syncStatus(),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    queryFn: async (): Promise<SyncStatusResponse> => {
      const data = await apiFetch<SyncStatusResponse>(withLeadingSlash("/api/sync/status"));
      return syncStatusResponseSchema.parse(data);
    },
  });
}

export function useScoutStatus() {
  return useQuery({
    queryKey: queryKeys.scoutStatus(),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    queryFn: async (): Promise<TrendScoutStatusResponse> => {
      const data = await apiFetch<TrendScoutStatusResponse>(withLeadingSlash("/api/scout/status"));
      return trendScoutStatusResponseSchema.parse(data);
    },
  });
}

export function useScoutResearchPack() {
  return useQuery({
    queryKey: queryKeys.scoutResearchPack(),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    queryFn: async (): Promise<ResearchPackResponse> => {
      const data = await apiFetch<ResearchPackResponse>(
        withLeadingSlash("/api/scout/research-pack")
      );
      return researchPackResponseSchema.parse(data);
    },
  });
}

export function useReloadScoutResearchPack() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (): Promise<ResearchPackReloadResponse> => {
      const data = await apiFetch<ResearchPackReloadResponse>(
        withLeadingSlash("/api/scout/research-pack/reload"),
        { method: "POST" }
      );
      return researchPackReloadResponseSchema.parse(data);
    },
    onSuccess: async (data) => {
      if (!data.ok) return;
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.scoutResearchPack() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.scoutStatus() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.retro("repo") }),
      ]);
    },
  });
}

export function useDashboard() {
  return useQuery({
    queryKey: queryKeys.dashboard(),
    staleTime: STALE_TIMES.dashboard,
    gcTime: CACHE_TIMES.dashboard,
    queryFn: async (): Promise<DashboardStats> => {
      const data = await apiFetch<DashboardStats>(withLeadingSlash("/api/dashboard/stats"));
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

export function useEval() {
  return useQuery({
    queryKey: queryKeys.eval(),
    staleTime: STALE_TIMES.eval,
    gcTime: CACHE_TIMES.eval,
    queryFn: async (): Promise<EvalResponse> => {
      const data = await apiFetch<EvalResponse>(withLeadingSlash("/api/eval/stats"));
      return evalResponseSchema.parse(data);
    },
  });
}

export function useTentacleStatus() {
  return useQuery({
    queryKey: queryKeys.tentacleStatus(),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    queryFn: async (): Promise<TentacleStatusResponse> => {
      const data = await apiFetch<TentacleStatusResponse>(
        withLeadingSlash("/api/tentacles/status")
      );
      return tentacleStatusResponseSchema.parse(data);
    },
  });
}

export function useSkillMetrics() {
  return useQuery({
    queryKey: queryKeys.skillMetrics(),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    queryFn: async (): Promise<SkillMetricsResponse> => {
      const data = await apiFetch<SkillMetricsResponse>(withLeadingSlash("/api/skills/metrics"));
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

export function useRetro(mode: "repo" | "local" = "repo") {
  return useQuery({
    queryKey: queryKeys.retro(mode),
    staleTime: STALE_TIMES.retro,
    gcTime: CACHE_TIMES.retro,
    queryFn: async (): Promise<RetroResponse> => {
      const data = await apiFetch<RetroResponse>(
        withLeadingSlash(`/api/retro/summary?mode=${mode}`)
      );
      return retroResponseSchema.parse(data);
    },
  });
}

export function useKnowledgeInsights() {
  return useQuery({
    queryKey: queryKeys.knowledgeInsights(),
    staleTime: STALE_TIMES.insights,
    gcTime: CACHE_TIMES.insights,
    queryFn: async (): Promise<KnowledgeInsightsResponse> => {
      const data = await apiFetch<KnowledgeInsightsResponse>(
        withLeadingSlash("/api/knowledge/insights")
      );
      return knowledgeInsightsResponseSchema.parse(data);
    },
  });
}

export function useWorkflowHealth() {
  return useQuery({
    queryKey: queryKeys.workflowHealth(),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    queryFn: async (): Promise<WorkflowHealthResponse> => {
      const data = await apiFetch<WorkflowHealthResponse>(withLeadingSlash("/api/workflow/health"));
      return workflowHealthResponseSchema.parse(data);
    },
  });
}

// ── Operator/Chat hooks (/api/operator/*) ─────────────────────────────

export function useOperatorSessions() {
  return useQuery({
    queryKey: queryKeys.operatorSessions(),
    staleTime: STALE_TIMES.sessions,
    gcTime: CACHE_TIMES.sessions,
    queryFn: async (): Promise<OperatorSessionListResponse> => {
      const data = await apiFetch<OperatorSessionListResponse>(
        withLeadingSlash("/api/operator/sessions")
      );
      return operatorSessionListResponseSchema.parse(data);
    },
  });
}

export function useOperatorSession(id: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.operatorSession(id),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(id),
    queryFn: async (): Promise<OperatorSession> => {
      const data = await apiFetch<OperatorSession>(
        withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(id)}`)
      );
      return operatorSessionSchema.parse(data);
    },
  });
}

export function useOperatorRuns(sessionId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.operatorRuns(sessionId),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(sessionId),
    queryFn: async (): Promise<OperatorRunsResponse> => {
      const data = await apiFetch<OperatorRunsResponse>(
        withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}/runs`)
      );
      return operatorRunsResponseSchema.parse(data);
    },
  });
}

export function useCreateOperatorSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: CreateOperatorSessionRequest): Promise<OperatorSession> => {
      const data = await apiFetch<OperatorSession>(withLeadingSlash("/api/operator/sessions"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(createOperatorSessionRequestSchema.parse(payload)),
      });
      return operatorSessionSchema.parse(data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorSessions() });
    },
  });
}

export function useDeleteOperatorSession() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (sessionId: string): Promise<void> => {
      await apiFetch<unknown>(
        withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}/delete`),
        { method: "POST" }
      );
    },
    onSuccess: (_data, sessionId) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorSessions() });
      queryClient.removeQueries({ queryKey: queryKeys.operatorSession(sessionId) });
      queryClient.removeQueries({ queryKey: queryKeys.operatorRuns(sessionId) });
    },
  });
}

export function useSubmitPrompt(sessionId: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (payload: PromptRequest): Promise<PromptSubmitResponse> => {
      const data = await apiFetch<PromptSubmitResponse>(
        withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}/prompt`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(promptRequestSchema.parse(payload)),
        }
      );
      return promptSubmitResponseSchema.parse(data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.operatorSession(sessionId) });
    },
  });
}

export function useOperatorStatus(sessionId: string, runId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.operatorStatus(sessionId, runId),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled: enabled && Boolean(sessionId) && Boolean(runId),
    queryFn: async (): Promise<OperatorRunStatus> => {
      const qs = createQueryString({ run: runId });
      const data = await apiFetch<OperatorRunStatus>(
        withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}/status${qs}`)
      );
      return operatorRunStatusSchema.parse(data);
    },
  });
}

export function createOperatorStreamPath(sessionId: string, runId: string): string {
  const qs = createQueryString({ run: runId });
  return withLeadingSlash(`/api/operator/sessions/${encodeURIComponent(sessionId)}/stream${qs}`);
}

export function usePathSuggest(q: string, hidden = false, enabled = true) {
  return useQuery({
    queryKey: queryKeys.operatorSuggest(q, hidden),
    staleTime: STALE_TIMES.search,
    gcTime: CACHE_TIMES.search,
    enabled,
    queryFn: async (): Promise<PathSuggestResponse> => {
      const qs = createQueryString({ q, ...(hidden ? { hidden: "true" } : {}) });
      const data = await apiFetch<PathSuggestResponse>(
        withLeadingSlash(`/api/operator/suggest${qs}`)
      );
      return pathSuggestResponseSchema.parse(data);
    },
  });
}

export function useFilePreview(path: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.operatorPreview(path),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(path),
    queryFn: async (): Promise<FilePreviewResponse> => {
      const qs = createQueryString({ path });
      const data = await apiFetch<FilePreviewResponse>(
        withLeadingSlash(`/api/operator/preview${qs}`)
      );
      return filePreviewResponseSchema.parse(data);
    },
  });
}

export function useFileDiff(pathA: string, pathB: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.operatorDiff(pathA, pathB),
    staleTime: STALE_TIMES.sessionDetail,
    gcTime: CACHE_TIMES.sessionDetail,
    enabled: enabled && Boolean(pathA) && Boolean(pathB),
    queryFn: async (): Promise<FileDiffResponse> => {
      const qs = createQueryString({ a: pathA, b: pathB });
      const data = await apiFetch<FileDiffResponse>(withLeadingSlash(`/api/operator/diff${qs}`));
      return fileDiffResponseSchema.parse(data);
    },
  });
}

export function useOperatorModelCatalog(enabled = true) {
  return useQuery({
    queryKey: queryKeys.operatorModels(),
    staleTime: STALE_TIMES.health,
    gcTime: CACHE_TIMES.health,
    enabled,
    queryFn: async (): Promise<OperatorModelCatalogResponse> => {
      const data = await apiFetch<OperatorModelCatalogResponse>(
        withLeadingSlash("/api/operator/models")
      );
      return operatorModelCatalogResponseSchema.parse(data);
    },
  });
}
