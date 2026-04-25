"use client";

import { useQuery } from "@tanstack/react-query";

import { CACHE_TIMES, DEFAULT_PAGE_SIZE, STALE_TIMES } from "@/lib/constants";
import { apiFetch } from "@/lib/api/client";
import {
  compareResponseSchema,
  dashboardStatsSchema,
  embeddingProjectionSchema,
  evalResponseSchema,
  graphResponseSchema,
  healthResponseSchema,
  searchResponseSchema,
  sessionDetailResponseSchema,
  sessionListResponseSchema,
  sessionsResponseSchema,
} from "@/lib/api/schemas";
import type {
  CompareResponse,
  DashboardStats,
  EmbeddingProjection,
  EvalResponse,
  GraphResponse,
  HealthResponse,
  SearchResponse,
  SessionDetailResponse,
  SessionListResponse,
  SessionsResponse,
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

export const queryKeys = {
  sessions: (params: SessionsQueryParams = {}) => ["sessions", params] as const,
  sessionDetail: (sessionId: string) => ["session-detail", sessionId] as const,
  search: (params: SearchQueryParams) => ["search", params] as const,
  health: () => ["health"] as const,
  dashboard: () => ["dashboard"] as const,
  graph: (params: GraphQueryParams = {}) => ["graph", params] as const,
  embeddings: () => ["embeddings"] as const,
  eval: () => ["eval"] as const,
  compare: (a: string, b: string) => ["compare", a, b] as const,
};

function withLeadingSlash(path: string): string {
  return path.startsWith("/") ? path : `/${path}`;
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
    new Set(
      values
        .map((value) => value.trim())
        .filter((value) => value.length > 0)
    )
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

export function normalizeSessionsResponse(
  input: SessionsResponse
): SessionListResponse {
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
  const queryString = createQueryString({
    page: params.page,
    page_size: params.pageSize,
    q: params.query,
    source: params.source,
    has_summary: params.hasSummary,
    sort: params.sort,
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
      const data = await apiFetch<SearchResponse>(
        withLeadingSlash(`/api/search${queryString}`)
      );
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

export function useDashboard() {
  return useQuery({
    queryKey: queryKeys.dashboard(),
    staleTime: STALE_TIMES.dashboard,
    gcTime: CACHE_TIMES.dashboard,
    queryFn: async (): Promise<DashboardStats> => {
      const data = await apiFetch<DashboardStats>(
        withLeadingSlash("/api/dashboard/stats")
      );
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
  const graphQueryString = graphFiltersQueryString
    ? `${graphFiltersQueryString}${graphLimitQueryString ? `&${graphLimitQueryString.slice(1)}` : ""}`
    : graphLimitQueryString;

  return useQuery({
    queryKey: queryKeys.graph(normalizedParams),
    staleTime: STALE_TIMES.graph,
    gcTime: CACHE_TIMES.graph,
    queryFn: async (): Promise<GraphResponse> => {
      const data = await apiFetch<GraphResponse>(
        withLeadingSlash(`/api/graph${graphQueryString}`)
      );
      return graphResponseSchema.parse(data);
    },
  });
}

export function useEmbeddings() {
  return useQuery({
    queryKey: queryKeys.embeddings(),
    staleTime: STALE_TIMES.embeddings,
    gcTime: CACHE_TIMES.embeddings,
    queryFn: async (): Promise<EmbeddingProjection> => {
      const data = await apiFetch<EmbeddingProjection>(
        withLeadingSlash("/api/embeddings/points")
      );
      return embeddingProjectionSchema.parse(data);
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
      const data = await apiFetch<CompareResponse>(
        withLeadingSlash(`/api/compare${queryString}`)
      );
      return compareResponseSchema.parse(data);
    },
  });
}
