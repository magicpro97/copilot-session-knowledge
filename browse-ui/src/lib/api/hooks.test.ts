import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  combineQueryStrings,
  createOperatorStreamPath,
  createOperatorStreamUrl,
  createLiveStreamUrl,
  createArrayQueryString,
  createQueryString,
  normalizeSessionsResponse,
  queryKeys,
  useCreateOperatorSession,
  useUpdateOperatorSession,
  useSkillCatalog,
  useCliSessions,
  useCliSession,
  useAdoptCliSession,
  useConfirmAdoptedSession,
} from "@/lib/api/hooks";
import { LOCAL_HOST, LOCAL_HOST_ID } from "@/lib/host-profiles";

vi.mock("@/lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/client")>();
  return {
    ...actual,
    apiFetch: vi.fn(),
    hostFetch: vi.fn(),
  };
});

import { hostFetch } from "@/lib/api/client";

const REMOTE_HOST = {
  id: "tunnel-1",
  label: "My Tunnel",
  base_url: "https://xyz.ngrok.io",
  token: "secret",
  cli_kind: "copilot" as const,
  is_default: false,
};

describe("api hooks helpers", () => {
  it("builds query strings while skipping empty values", () => {
    expect(createQueryString({ page: 1, q: "abc", source: null })).toBe("?page=1&q=abc");
    expect(createQueryString({ q: "", page: undefined })).toBe("");
  });

  it("serializes search filters with backend parameter names", () => {
    expect(
      createArrayQueryString({
        q: "abc",
        src: ["copilot", "claude"],
        kind: ["pattern"],
        in: ["title", "content"],
      })
    ).toBe("?q=abc&src=copilot%2Cclaude&kind=pattern&in=title%2Ccontent");
  });

  it("normalizes legacy array sessions response", () => {
    const normalized = normalizeSessionsResponse([
      {
        id: "abc",
        path: null,
        summary: null,
        source: "copilot",
        event_count_estimate: 1,
        fts_indexed_at: null,
      },
    ]);

    expect(normalized.items).toHaveLength(1);
    expect(normalized.total).toBe(1);
    expect(normalized.page).toBe(1);
  });

  it("preserves envelope sessions response", () => {
    const normalized = normalizeSessionsResponse({
      items: [],
      total: 0,
      page: 2,
      page_size: 50,
      has_more: false,
    });

    expect(normalized.page).toBe(2);
    expect(normalized.page_size).toBe(50);
  });

  it("builds stable query keys", () => {
    expect(queryKeys.sessionDetail("abc")).toEqual(["session-detail", "local", "abc"]);
    expect(queryKeys.health()).toEqual(["health", "local"]);
    expect(queryKeys.syncStatus()).toEqual(["sync-status", "local"]);
    expect(queryKeys.scoutStatus()).toEqual(["scout-status", "local"]);
    expect(queryKeys.tentacleStatus()).toEqual(["tentacle-status", "local"]);
    expect(queryKeys.scoutResearchPack()).toEqual(["scout-research-pack", "local"]);
    expect(queryKeys.dashboard()).toEqual(["dashboard", "local"]);
    expect(queryKeys.eval()).toEqual(["eval", "local"]);
    expect(queryKeys.retro()).toEqual(["retro", "repo", "local"]);
    expect(queryKeys.retro("local")).toEqual(["retro", "local", "local"]);
    expect(queryKeys.knowledgeInsights()).toEqual(["knowledge-insights", "local"]);
    expect(queryKeys.graph({ wing: ["alpha"], limit: 10 })).toEqual([
      "graph",
      "local",
      { wing: ["alpha"], limit: 10 },
    ]);
    expect(queryKeys.graphLegacy({ wing: ["alpha"], limit: 10 })).toEqual([
      "graph-legacy",
      "local",
      { wing: ["alpha"], limit: 10 },
    ]);
    expect(queryKeys.graph({ wing: ["alpha"], limit: 10 })).not.toEqual(
      queryKeys.graphLegacy({ wing: ["alpha"], limit: 10 })
    );
  });

  it("combineQueryStrings merges filters and limit when both present", () => {
    expect(combineQueryStrings("?wing=alpha", "?limit=50")).toBe("?wing=alpha&limit=50");
  });

  it("combineQueryStrings returns limit alone when filters are empty", () => {
    expect(combineQueryStrings("", "?limit=50")).toBe("?limit=50");
  });

  it("combineQueryStrings returns filters alone when limit is empty", () => {
    expect(combineQueryStrings("?wing=alpha", "")).toBe("?wing=alpha");
  });

  it("combineQueryStrings returns empty string when both are empty", () => {
    expect(combineQueryStrings("", "")).toBe("");
  });

  it("sessions query string does not include sort param", () => {
    // sort is applied client-side and must not be forwarded to the server
    const qs = createQueryString({
      page: 1,
      page_size: 20,
      q: "test",
      source: "copilot",
    });
    expect(qs).not.toContain("sort");
    expect(qs).toContain("page=1");
    expect(qs).toContain("q=test");
  });

  // ── Operator/Chat query keys (host-scoped) ───────────────────────────

  it("operator query keys default to LOCAL_HOST_ID when no host is given", () => {
    expect(queryKeys.operatorSessions()).toEqual(["operator-sessions", LOCAL_HOST_ID]);
    expect(queryKeys.operatorSession("abc-123")).toEqual([
      "operator-session",
      LOCAL_HOST_ID,
      "abc-123",
    ]);
    expect(queryKeys.operatorStatus("s1", "r1")).toEqual([
      "operator-status",
      LOCAL_HOST_ID,
      "s1",
      "r1",
    ]);
    expect(queryKeys.operatorRuns("s1")).toEqual(["operator-runs", LOCAL_HOST_ID, "s1"]);
    expect(queryKeys.operatorSuggest("~/proj")).toEqual([
      "operator-suggest",
      LOCAL_HOST_ID,
      "~/proj",
      false,
    ]);
    expect(queryKeys.operatorPreview("/path/to/file.ts")).toEqual([
      "operator-preview",
      LOCAL_HOST_ID,
      "/path/to/file.ts",
    ]);
    expect(queryKeys.operatorDiff("file_a.ts", "file_b.ts")).toEqual([
      "operator-diff",
      LOCAL_HOST_ID,
      "file_a.ts",
      "file_b.ts",
    ]);
    expect(queryKeys.operatorModels()).toEqual(["operator-models", LOCAL_HOST_ID]);
    expect(queryKeys.operatorCapabilities()).toEqual(["operator-capabilities", LOCAL_HOST_ID]);
  });

  it("operator query keys are scoped by hostId to prevent cache collisions", () => {
    expect(queryKeys.operatorSessions(LOCAL_HOST_ID)).not.toEqual(
      queryKeys.operatorSessions(REMOTE_HOST.id)
    );
    expect(queryKeys.operatorSession("abc", LOCAL_HOST_ID)).not.toEqual(
      queryKeys.operatorSession("abc", REMOTE_HOST.id)
    );
    expect(queryKeys.operatorRuns("s1", LOCAL_HOST_ID)).not.toEqual(
      queryKeys.operatorRuns("s1", REMOTE_HOST.id)
    );
    expect(queryKeys.operatorStatus("s1", "r1", LOCAL_HOST_ID)).not.toEqual(
      queryKeys.operatorStatus("s1", "r1", REMOTE_HOST.id)
    );
    expect(queryKeys.operatorSuggest("~/proj", false, LOCAL_HOST_ID)).not.toEqual(
      queryKeys.operatorSuggest("~/proj", false, REMOTE_HOST.id)
    );
    expect(queryKeys.operatorPreview("/f.ts", LOCAL_HOST_ID)).not.toEqual(
      queryKeys.operatorPreview("/f.ts", REMOTE_HOST.id)
    );
    expect(queryKeys.operatorDiff("a", "b", LOCAL_HOST_ID)).not.toEqual(
      queryKeys.operatorDiff("a", "b", REMOTE_HOST.id)
    );
    expect(queryKeys.operatorModels(LOCAL_HOST_ID)).not.toEqual(
      queryKeys.operatorModels(REMOTE_HOST.id)
    );
    expect(queryKeys.operatorCapabilities(LOCAL_HOST_ID)).not.toEqual(
      queryKeys.operatorCapabilities(REMOTE_HOST.id)
    );
  });

  it("operator session key is distinct from browse session key", () => {
    expect(queryKeys.operatorSession("abc")).not.toEqual(queryKeys.sessionDetail("abc"));
  });

  it("operator status key distinguishes different run ids", () => {
    expect(queryKeys.operatorStatus("s1", "r1")).not.toEqual(queryKeys.operatorStatus("s1", "r2"));
  });

  it("operator diff key distinguishes different path pairs", () => {
    expect(queryKeys.operatorDiff("a.ts", "b.ts")).not.toEqual(
      queryKeys.operatorDiff("b.ts", "a.ts")
    );
  });

  it("operatorSuggest key distinguishes hidden vs visible results", () => {
    expect(queryKeys.operatorSuggest("~/proj", false)).not.toEqual(
      queryKeys.operatorSuggest("~/proj", true)
    );
  });

  it("builds operator stream path with encoded session id and run query (backward compat)", () => {
    expect(createOperatorStreamPath("sess 1", "run-1")).toBe(
      "/api/operator/sessions/sess%201/stream?run=run-1"
    );
  });

  it("createOperatorStreamUrl uses remote base_url without token in URL (issue #32)", () => {
    const url = createOperatorStreamUrl("sess-1", "run-1", REMOTE_HOST);
    expect(url).toContain("https://xyz.ngrok.io");
    expect(url).toContain("sess-1");
    expect(url).toContain("run=run-1");
    // Token must NOT be in the URL — callers must send it via Authorization header.
    expect(url).not.toContain("token=");
    expect(url).not.toContain("secret");
  });

  it("createOperatorStreamUrl uses same-origin for local host", () => {
    const url = createOperatorStreamUrl("sess-1", "run-1", LOCAL_HOST);
    expect(url).not.toContain("ngrok");
    expect(url).toContain("sess-1");
    expect(url).toContain("run=run-1");
  });

  it("createOperatorStreamUrl does not add token param when host token is empty", () => {
    const url = createOperatorStreamUrl("sess-1", "run-1", LOCAL_HOST);
    expect(url).not.toContain("token=");
  });

  it("createOperatorStreamUrl preserves base_url path prefix (issue #31)", () => {
    const hostWithPrefix = { ...REMOTE_HOST, base_url: "https://proxy.example.com/copilot" };
    const url = createOperatorStreamUrl("sess-1", "run-1", hostWithPrefix);
    expect(url).toBe(
      "https://proxy.example.com/copilot/api/operator/sessions/sess-1/stream?run=run-1"
    );
  });

  it("createOperatorStreamUrl remote host has no token in URL even when token is set (issue #32)", () => {
    const hostWithToken = { ...REMOTE_HOST, token: "should-not-appear" };
    const url = createOperatorStreamUrl("sess-1", "run-1", hostWithToken);
    expect(url).not.toContain("should-not-appear");
    expect(url).not.toContain("token=");
  });

  it("createQueryString keeps empty operator suggest query empty for top-level results", () => {
    expect(createQueryString({ q: "" })).toBe("");
  });

  it("useCreateOperatorSession posts to a per-call host override", async () => {
    vi.mocked(hostFetch).mockResolvedValue({
      id: "session-override",
      name: "Overridden host session",
      model: "gpt-5.4",
      mode: "interactive",
      workspace: "~/project",
      add_dirs: [],
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-01-01T00:00:00Z",
      run_count: 0,
      last_run_id: null,
      resume_ready: false,
    });

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useCreateOperatorSession(LOCAL_HOST), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({
        payload: {
          name: "Override",
          workspace: "~/project",
          model: "gpt-5.4",
          mode: "interactive",
        },
        host: REMOTE_HOST,
      });
    });

    expect(vi.mocked(hostFetch).mock.calls.at(-1)?.[1]).toMatchObject({
      id: REMOTE_HOST.id,
      base_url: REMOTE_HOST.base_url,
    });
  });

  it("useUpdateOperatorSession sends PATCH to the correct endpoint", async () => {
    const updatedSession = {
      id: "sess-update",
      name: "Updated Name",
      model: "gpt-5.4",
      mode: "interactive",
      workspace: "~/project",
      add_dirs: [],
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-06-01T00:00:00Z",
      run_count: 1,
      last_run_id: null,
      resume_ready: false,
    };
    vi.mocked(hostFetch).mockResolvedValue(updatedSession);

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useUpdateOperatorSession("sess-update", LOCAL_HOST), {
      wrapper,
    });

    await act(async () => {
      await result.current.mutateAsync({ payload: { name: "Updated Name" } });
    });

    const [calledPath, calledHost, calledInit] = vi.mocked(hostFetch).mock.calls.at(-1) ?? [];
    expect(calledPath).toContain("/api/operator/sessions/sess-update");
    expect((calledInit as RequestInit | undefined)?.method).toBe("PATCH");
    expect(calledHost).toMatchObject({ id: LOCAL_HOST.id });
  });

  it("useUpdateOperatorSession uses a per-call host override", async () => {
    const updatedSession = {
      id: "sess-override",
      name: "Remote Updated",
      model: "claude-sonnet-4.6",
      mode: "plan",
      workspace: "~/other",
      add_dirs: [],
      created_at: "2024-01-01T00:00:00Z",
      updated_at: "2024-07-01T00:00:00Z",
      run_count: 0,
      last_run_id: null,
      resume_ready: false,
    };
    vi.mocked(hostFetch).mockResolvedValue(updatedSession);

    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useUpdateOperatorSession("sess-override", LOCAL_HOST), {
      wrapper,
    });

    await act(async () => {
      await result.current.mutateAsync({ payload: { mode: "plan" }, host: REMOTE_HOST });
    });

    const [, calledHost] = vi.mocked(hostFetch).mock.calls.at(-1) ?? [];
    expect(calledHost).toMatchObject({ id: REMOTE_HOST.id, base_url: REMOTE_HOST.base_url });
  });

  // ── Insights data query keys (host-scoped) ───────────────────────────

  it("insights query keys default to LOCAL_HOST_ID when no host is given", () => {
    expect(queryKeys.scoutResearchPack()).toEqual(["scout-research-pack", LOCAL_HOST_ID]);
    expect(queryKeys.dashboard()).toEqual(["dashboard", LOCAL_HOST_ID]);
    expect(queryKeys.eval()).toEqual(["eval", LOCAL_HOST_ID]);
    expect(queryKeys.retro()).toEqual(["retro", "repo", LOCAL_HOST_ID]);
    expect(queryKeys.retro("local")).toEqual(["retro", "local", LOCAL_HOST_ID]);
    expect(queryKeys.knowledgeInsights()).toEqual(["knowledge-insights", LOCAL_HOST_ID]);
  });

  it("insights query keys are scoped by hostId to prevent cache collisions", () => {
    expect(queryKeys.scoutResearchPack(LOCAL_HOST_ID)).not.toEqual(
      queryKeys.scoutResearchPack(REMOTE_HOST.id)
    );
    expect(queryKeys.dashboard(LOCAL_HOST_ID)).not.toEqual(queryKeys.dashboard(REMOTE_HOST.id));
    expect(queryKeys.eval(LOCAL_HOST_ID)).not.toEqual(queryKeys.eval(REMOTE_HOST.id));
    expect(queryKeys.retro("repo", LOCAL_HOST_ID)).not.toEqual(
      queryKeys.retro("repo", REMOTE_HOST.id)
    );
    expect(queryKeys.knowledgeInsights(LOCAL_HOST_ID)).not.toEqual(
      queryKeys.knowledgeInsights(REMOTE_HOST.id)
    );
  });

  it("workflowHealth query key defaults to LOCAL_HOST_ID", () => {
    expect(queryKeys.workflowHealth()).toEqual(["workflow-health", LOCAL_HOST_ID]);
  });

  it("workflowHealth query key is scoped by hostId to prevent cache collisions", () => {
    expect(queryKeys.workflowHealth(LOCAL_HOST_ID)).not.toEqual(
      queryKeys.workflowHealth(REMOTE_HOST.id)
    );
    expect(queryKeys.workflowHealth(REMOTE_HOST.id)).toEqual(["workflow-health", REMOTE_HOST.id]);
  });

  it("skillCatalog query key defaults to LOCAL_HOST_ID", () => {
    expect(queryKeys.skillCatalog()).toEqual(["skill-catalog", LOCAL_HOST_ID]);
  });

  it("skillCatalog query key is scoped by hostId to prevent cache collisions", () => {
    expect(queryKeys.skillCatalog(LOCAL_HOST_ID)).not.toEqual(
      queryKeys.skillCatalog(REMOTE_HOST.id)
    );
    expect(queryKeys.skillCatalog(REMOTE_HOST.id)).toEqual(["skill-catalog", REMOTE_HOST.id]);
  });

  it("skillCatalog query key is distinct from skillMetrics query key", () => {
    expect(queryKeys.skillCatalog()).not.toEqual(queryKeys.skillMetrics());
  });
});

describe("createLiveStreamUrl", () => {
  it("uses remote base_url without putting the token in the URL", () => {
    const url = createLiveStreamUrl(REMOTE_HOST);
    expect(url).toBe("https://xyz.ngrok.io/api/live");
    expect(url).not.toContain("token=");
    expect(url).not.toContain("secret");
  });

  it("uses same-origin for local host (no base_url)", () => {
    // In test environment window.location.origin returns http://localhost:3000 (jsdom default)
    const url = createLiveStreamUrl(LOCAL_HOST);
    expect(url).toContain("/api/live");
    expect(url).not.toContain("token=");
  });

  it("does not add token param when host has no token", () => {
    const hostNoToken = { ...REMOTE_HOST, token: "" };
    const url = createLiveStreamUrl(hostNoToken);
    expect(url).not.toContain("token");
    expect(url).toContain("/api/live");
  });

  it("preserves base_url path prefix (issue #31)", () => {
    const hostWithPrefix = { ...REMOTE_HOST, base_url: "https://proxy.example.com/copilot" };
    const url = createLiveStreamUrl(hostWithPrefix);
    expect(url).toBe("https://proxy.example.com/copilot/api/live");
  });
});

describe("useSkillCatalog", () => {
  const EMPTY_CATALOG = {
    skills: [],
    total: 0,
    sources: { global: "/home/user/.copilot/skills", project: null },
    runtime: { generated_at: "2026-01-01T00:00:00Z" },
  };

  it("fetches the skill catalog and returns the parsed response", async () => {
    vi.mocked(hostFetch).mockResolvedValue(EMPTY_CATALOG);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useSkillCatalog(LOCAL_HOST), { wrapper });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(result.current.data?.skills).toEqual([]);
    expect(result.current.data?.total).toBe(0);
    expect(result.current.data?.sources.global).toBe("/home/user/.copilot/skills");
    expect(result.current.data?.sources.project).toBeNull();
  });

  it("uses the skillCatalog query key scoped to the host", async () => {
    vi.mocked(hostFetch).mockResolvedValue(EMPTY_CATALOG);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    renderHook(() => useSkillCatalog(REMOTE_HOST), { wrapper });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    const cache = queryClient.getQueryCache().getAll();
    const found = cache.some(
      (q) =>
        Array.isArray(q.queryKey) &&
        q.queryKey[0] === "skill-catalog" &&
        q.queryKey[1] === REMOTE_HOST.id
    );
    expect(found).toBe(true);
  });

  it("does not fetch when enabled=false", () => {
    vi.mocked(hostFetch).mockClear();
    vi.mocked(hostFetch).mockResolvedValue(EMPTY_CATALOG);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useSkillCatalog(LOCAL_HOST, false), { wrapper });

    expect(result.current.isFetching).toBe(false);
    expect(result.current.data).toBeUndefined();
    expect(vi.mocked(hostFetch)).not.toHaveBeenCalled();
  });
});

// ── useCliSessions ────────────────────────────────────────────────────────────

describe("useCliSessions", () => {
  const CLI_SESSIONS_RESPONSE = { sessions: [], count: 0, truncated: false };

  it("does not fetch when no operator token is present", () => {
    vi.mocked(hostFetch).mockClear();

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useCliSessions(LOCAL_HOST), { wrapper });

    expect(result.current.isFetching).toBe(false);
    expect(vi.mocked(hostFetch)).not.toHaveBeenCalled();
  });

  it("calls hostFetch with noRedirectOn401 option when token is present", async () => {
    vi.mocked(hostFetch).mockClear();
    vi.mocked(hostFetch).mockResolvedValue(CLI_SESSIONS_RESPONSE);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    renderHook(() => useCliSessions(REMOTE_HOST), { wrapper });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    const [, , , calledOptions] = vi.mocked(hostFetch).mock.calls.at(-1) ?? [];
    expect(calledOptions).toEqual({ noRedirectOn401: true });
  });

  it("does not retry 401 responses", async () => {
    vi.mocked(hostFetch).mockClear();
    // Production hostRequest/hostFetch throws new Error("Unauthorized") for 401 —
    // not "HTTP 401 Unauthorized". This test uses the real production message.
    vi.mocked(hostFetch).mockRejectedValue(new Error("Unauthorized"));

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: 3, retryDelay: 1 } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    renderHook(() => useCliSessions(REMOTE_HOST), { wrapper });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(vi.mocked(hostFetch)).toHaveBeenCalledTimes(1);
  });

  it("remote host with empty token + local session token does NOT fetch (issue #530)", () => {
    // A session token stored locally must not enable probes to a remote host
    // whose profile has no token — hostRequest would send no Authorization
    // for remote requests, so the probe would 401 immediately.
    vi.mocked(hostFetch).mockClear();
    sessionStorage.setItem("browse_token", "local-session-token");

    try {
      const remoteHostNoToken = {
        id: "tunnel-no-token",
        label: "Tunnel (no profile token)",
        base_url: "https://xyz.ngrok.io",
        token: "",
        cli_kind: "copilot" as const,
        is_default: false,
      };

      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const wrapper = ({ children }: { children: ReactNode }) =>
        createElement(QueryClientProvider, { client: queryClient }, children);

      const { result } = renderHook(() => useCliSessions(remoteHostNoToken), { wrapper });

      expect(result.current.isFetching).toBe(false);
      expect(vi.mocked(hostFetch)).not.toHaveBeenCalled();
    } finally {
      sessionStorage.removeItem("browse_token");
    }
  });

  it("local host with session-storage token DOES fetch (issue #530)", async () => {
    // The local host should honour the session-storage token even when
    // host.token is empty.
    vi.mocked(hostFetch).mockClear();
    vi.mocked(hostFetch).mockResolvedValue(CLI_SESSIONS_RESPONSE);
    sessionStorage.setItem("browse_token", "local-session-token");

    try {
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const wrapper = ({ children }: { children: ReactNode }) =>
        createElement(QueryClientProvider, { client: queryClient }, children);

      renderHook(() => useCliSessions(LOCAL_HOST), { wrapper });

      await act(async () => {
        await new Promise((r) => setTimeout(r, 50));
      });

      expect(vi.mocked(hostFetch)).toHaveBeenCalled();
    } finally {
      sessionStorage.removeItem("browse_token");
    }
  });

  it("token gate does not trigger URL ingestion or history.replaceState side effects (issue #530)", () => {
    // hasOperatorToken is evaluated during React render via the enabled flag.
    // It must use peekToken (pure) rather than getToken (has side effects).
    vi.mocked(hostFetch).mockClear();
    const replaceStateSpy = vi.spyOn(window.history, "replaceState");

    try {
      // Simulate ?token= in the URL that getToken() would consume but peekToken() ignores
      const originalHref = window.location.href;
      window.history.replaceState({}, "", "?token=url-injected-token");
      replaceStateSpy.mockClear(); // clear the call we just made above

      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const wrapper = ({ children }: { children: ReactNode }) =>
        createElement(QueryClientProvider, { client: queryClient }, children);

      // Render with LOCAL_HOST (no token) — enabled guard evaluates during render
      renderHook(() => useCliSessions(LOCAL_HOST), { wrapper });

      // replaceState must NOT have been called as a side effect of the enabled check
      expect(replaceStateSpy).not.toHaveBeenCalled();
      // And since sessionStorage has no token and host.token is empty, no fetch either
      expect(vi.mocked(hostFetch)).not.toHaveBeenCalled();

      // Restore original URL
      window.history.replaceState({}, "", originalHref);
    } finally {
      replaceStateSpy.mockRestore();
    }
  });
});

// ── useCliSession ────────────────────────────────────────────────────────────

describe("useCliSession", () => {
  const CLI_SESSION_ID = "f47ac10b-58cc-4372-a567-0e02b2c3d479";
  const CLI_SESSION = {
    cli_session_id: CLI_SESSION_ID,
    title: "My project session",
    mtime: "2024-06-01T10:00:00Z",
    workspace_hint: "~/projects/myapp",
    branch: "main",
  };

  it("fetches a single CLI session by id", async () => {
    vi.mocked(hostFetch).mockResolvedValue(CLI_SESSION);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useCliSession(CLI_SESSION_ID, REMOTE_HOST), { wrapper });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(result.current.data?.cli_session_id).toBe(CLI_SESSION_ID);
    expect(result.current.data?.title).toBe("My project session");
  });

  it("uses the cliSession query key scoped to host and id", async () => {
    vi.mocked(hostFetch).mockResolvedValue(CLI_SESSION);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    renderHook(() => useCliSession(CLI_SESSION_ID, REMOTE_HOST), { wrapper });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    const cache = queryClient.getQueryCache().getAll();
    const found = cache.some(
      (q) =>
        Array.isArray(q.queryKey) &&
        q.queryKey[0] === "cli-session" &&
        q.queryKey[1] === REMOTE_HOST.id &&
        q.queryKey[2] === CLI_SESSION_ID
    );
    expect(found).toBe(true);
  });

  it("fetches from the correct endpoint path and passes noRedirectOn401", async () => {
    vi.mocked(hostFetch).mockResolvedValue(CLI_SESSION);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    renderHook(() => useCliSession(CLI_SESSION_ID, REMOTE_HOST), { wrapper });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    const [calledPath, , , calledOptions] = vi.mocked(hostFetch).mock.calls.at(-1) ?? [];
    expect(calledPath).toContain(`/api/operator/cli-sessions/${CLI_SESSION_ID}`);
    // Path must not contain token or other params
    expect(calledPath).not.toContain("token=");
    // Must suppress local redirect on 401 (capability probe)
    expect(calledOptions).toEqual({ noRedirectOn401: true });
  });

  it("does not fetch when enabled=false", () => {
    vi.mocked(hostFetch).mockClear();
    vi.mocked(hostFetch).mockResolvedValue(CLI_SESSION);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useCliSession(CLI_SESSION_ID, LOCAL_HOST, false), {
      wrapper,
    });

    expect(result.current.isFetching).toBe(false);
    expect(vi.mocked(hostFetch)).not.toHaveBeenCalled();
  });

  it("does not fetch when no operator token is present", () => {
    vi.mocked(hostFetch).mockClear();

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    // LOCAL_HOST has no token; sessionStorage is empty — hook must stay disabled
    const { result } = renderHook(() => useCliSession(CLI_SESSION_ID, LOCAL_HOST), {
      wrapper,
    });

    expect(result.current.isFetching).toBe(false);
    expect(vi.mocked(hostFetch)).not.toHaveBeenCalled();
  });

  it("does not fetch when id is empty", () => {
    vi.mocked(hostFetch).mockClear();

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useCliSession("", LOCAL_HOST, true), { wrapper });

    expect(result.current.isFetching).toBe(false);
    expect(vi.mocked(hostFetch)).not.toHaveBeenCalled();
  });

  it("does not retry 404 responses", async () => {
    vi.mocked(hostFetch).mockClear();
    vi.mocked(hostFetch).mockRejectedValue(new Error("HTTP 404"));

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: 3, retryDelay: 1 } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    renderHook(() => useCliSession("19538abc-11f0-4625-bc7f-9ef1929a5c02", REMOTE_HOST), {
      wrapper,
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(vi.mocked(hostFetch)).toHaveBeenCalledTimes(1);
  });

  it("does not retry 401 responses", async () => {
    vi.mocked(hostFetch).mockClear();
    // Production hostRequest/hostFetch throws new Error("Unauthorized") for 401.
    vi.mocked(hostFetch).mockRejectedValue(new Error("Unauthorized"));

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: 3, retryDelay: 1 } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    renderHook(() => useCliSession("19538abc-11f0-4625-bc7f-9ef1929a5c02", REMOTE_HOST), {
      wrapper,
    });

    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(vi.mocked(hostFetch)).toHaveBeenCalledTimes(1);
  });
});

// ── useAdoptCliSession ───────────────────────────────────────────────────────

describe("useAdoptCliSession", () => {
  const ADOPTED_SESSION = {
    id: "operator-session-xyz",
    name: "Adopted Session",
    model: "gpt-5.4",
    mode: "interactive",
    workspace: "~/projects/myapp",
    add_dirs: [],
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    run_count: 0,
    last_run_id: null,
    resume_ready: false,
    source: "cli_adopt",
  };

  it("sends cli_session_id in POST JSON body (not as URL param)", async () => {
    vi.mocked(hostFetch).mockResolvedValue(ADOPTED_SESSION);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useAdoptCliSession(LOCAL_HOST), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({
        payload: { cli_session_id: "a1b2c3d4-e5f6-4789-abcd-ef0123456789" },
      });
    });

    const [calledPath, , calledInit] = vi.mocked(hostFetch).mock.calls.at(-1) ?? [];

    // Path uses the adopt endpoint — CLI UUID is NOT in the URL
    expect(calledPath).toBe("/api/operator/sessions/adopt");
    expect(calledPath).not.toContain("a1b2c3d4-e5f6-4789-abcd-ef0123456789");

    // CLI UUID is in the POST body
    const body = JSON.parse((calledInit as RequestInit | undefined)?.body as string) as {
      cli_session_id: string;
    };
    expect(body.cli_session_id).toBe("a1b2c3d4-e5f6-4789-abcd-ef0123456789");
    expect((calledInit as RequestInit | undefined)?.method).toBe("POST");
  });

  it("returns the operator session id (not CLI UUID) on success", async () => {
    vi.mocked(hostFetch).mockResolvedValue(ADOPTED_SESSION);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useAdoptCliSession(LOCAL_HOST), { wrapper });

    let returned: { id: string } | undefined;
    await act(async () => {
      returned = await result.current.mutateAsync({
        payload: { cli_session_id: "a1b2c3d4-e5f6-4789-abcd-ef0123456789" },
      });
    });

    // The returned id is the operator session id
    expect(returned?.id).toBe("operator-session-xyz");
    // Confirm the returned id is NOT the CLI UUID
    expect(returned?.id).not.toBe("a1b2c3d4-e5f6-4789-abcd-ef0123456789");
  });

  it("uses a per-call host override for the adopt POST", async () => {
    vi.mocked(hostFetch).mockResolvedValue(ADOPTED_SESSION);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useAdoptCliSession(LOCAL_HOST), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({
        payload: { cli_session_id: "a1b2c3d4-e5f6-4789-abcd-ef0123456789" },
        host: REMOTE_HOST,
      });
    });

    const [, calledHost] = vi.mocked(hostFetch).mock.calls.at(-1) ?? [];
    expect(calledHost).toMatchObject({ id: REMOTE_HOST.id, base_url: REMOTE_HOST.base_url });
  });

  it("confirm path uses operator session id, not CLI UUID", async () => {
    // Adopt then confirm — confirm must only reference the operator session id
    vi.mocked(hostFetch)
      .mockResolvedValueOnce(ADOPTED_SESSION)
      .mockResolvedValueOnce({
        ...ADOPTED_SESSION,
        confirmed_at: "2024-01-01T00:00:01Z",
        resume_ready: true,
      });

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const { result } = renderHook(() => useAdoptCliSession(LOCAL_HOST), { wrapper });

    let returned: { id: string } | undefined;
    await act(async () => {
      returned = await result.current.mutateAsync({
        payload: { cli_session_id: "a1b2c3d4-e5f6-4789-abcd-ef0123456789" },
      });
    });

    // The operator session id from the adopt response
    const operatorId = returned?.id ?? "";
    expect(operatorId).toBe("operator-session-xyz");

    const confirm = renderHook(() => useConfirmAdoptedSession(operatorId, LOCAL_HOST), { wrapper });

    await act(async () => {
      await confirm.result.current.mutateAsync();
    });

    const [calledPath, , calledInit] = vi.mocked(hostFetch).mock.calls.at(-1) ?? [];
    expect(calledPath).toBe("/api/operator/sessions/operator-session-xyz/confirm");
    expect(calledPath).not.toContain("a1b2c3d4-e5f6-4789-abcd-ef0123456789");
    expect((calledInit as RequestInit | undefined)?.method).toBe("POST");
  });
});

// ── Debug log params & query keys (issue #538 / #539) ─────────────────────────

describe("debug log params and query keys (projection, until, to_idx)", () => {
  it("sessionDebugLog query key includes full params object", () => {
    const params = { from: 0, limit: 10, projection: "skeleton" as const };
    const key = queryKeys.sessionDebugLog("sess-1", params, LOCAL_HOST_ID);
    expect(key).toEqual(["session-debug-log", LOCAL_HOST_ID, "sess-1", params]);
  });

  it("sessionDebugLog query keys differ when params differ", () => {
    const keyFull = queryKeys.sessionDebugLog("s", { projection: "full" }, LOCAL_HOST_ID);
    const keySkel = queryKeys.sessionDebugLog("s", { projection: "skeleton" }, LOCAL_HOST_ID);
    expect(keyFull).not.toEqual(keySkel);
  });

  it("sessionDebugLogSkeleton key is under a different prefix from sessionDebugLog", () => {
    const skeletonKey = queryKeys.sessionDebugLogSkeleton("s", {}, LOCAL_HOST_ID);
    const fullKey = queryKeys.sessionDebugLog("s", {}, LOCAL_HOST_ID);
    expect(skeletonKey[0]).toBe("session-debug-log-skeleton");
    expect(fullKey[0]).toBe("session-debug-log");
    expect(skeletonKey).not.toEqual(fullKey);
  });

  it("sessionDebugLogSkeleton key differs when window params differ", () => {
    const key1 = queryKeys.sessionDebugLogSkeleton(
      "s",
      { from: 0, until: "2024-01-01T00:00:00Z" },
      LOCAL_HOST_ID
    );
    const key2 = queryKeys.sessionDebugLogSkeleton(
      "s",
      { from: 0, until: "2024-01-02T00:00:00Z" },
      LOCAL_HOST_ID
    );
    expect(key1).not.toEqual(key2);
  });

  it("sessionDebugLogSkeleton key includes to_idx in params", () => {
    const key = queryKeys.sessionDebugLogSkeleton("s", { to_idx: 500 }, LOCAL_HOST_ID);
    expect(key[3]).toMatchObject({ to_idx: 500 });
  });

  it("debugLog key is host-scoped", () => {
    const local = queryKeys.debugLog("s", "r", {}, LOCAL_HOST_ID);
    const remote = queryKeys.debugLog("s", "r", {}, "remote-host-1");
    expect(local[1]).toBe(LOCAL_HOST_ID);
    expect(remote[1]).toBe("remote-host-1");
    expect(local).not.toEqual(remote);
  });

  it("createQueryString includes projection, until, to_idx", () => {
    const qs = createQueryString({
      from: 0,
      limit: 5000,
      projection: "skeleton",
      until: "2024-01-01T12:00:00Z",
      to_idx: 200,
    });
    expect(qs).toContain("projection=skeleton");
    expect(qs).toContain("until=2024-01-01T12%3A00%3A00Z");
    expect(qs).toContain("to_idx=200");
    expect(qs).toContain("from=0");
    expect(qs).toContain("limit=5000");
  });

  it("createQueryString omits undefined params", () => {
    const qs = createQueryString({ projection: undefined, until: undefined, to_idx: undefined });
    expect(qs).toBe("");
  });

  it("createQueryString omits null params", () => {
    const qs = createQueryString({
      projection: null as unknown as string,
      until: null as unknown as string,
    });
    expect(qs).toBe("");
  });
});

// ── Flight Recorder v3 (synthesis §4b / §6b) ───────────────────────────────

describe("Flight Recorder v3 queryKeys", () => {
  const SID = "33169957-0dc1-4998-86c0-d2beba02e8b4";

  it("sessionCheckpoints key is stable and host-scoped", () => {
    expect(queryKeys.sessionCheckpoints(SID)).toEqual(["session-checkpoints", "local", SID]);
    expect(queryKeys.sessionCheckpoints(SID, "remote-1")).toEqual([
      "session-checkpoints",
      "remote-1",
      SID,
    ]);
  });

  it("sessionRewindSnapshots key is stable and host-scoped", () => {
    expect(queryKeys.sessionRewindSnapshots(SID)).toEqual([
      "session-rewind-snapshots",
      "local",
      SID,
    ]);
    expect(queryKeys.sessionRewindSnapshots(SID, "remote-1")).toEqual([
      "session-rewind-snapshots",
      "remote-1",
      SID,
    ]);
  });

  it("checkpoints and rewind-snapshots keys are disjoint", () => {
    const a = JSON.stringify(queryKeys.sessionCheckpoints(SID));
    const b = JSON.stringify(queryKeys.sessionRewindSnapshots(SID));
    expect(a).not.toEqual(b);
  });

  it("host-scoping isolates caches across hosts", () => {
    const local = JSON.stringify(queryKeys.sessionCheckpoints(SID, "local"));
    const remote = JSON.stringify(queryKeys.sessionCheckpoints(SID, "remote-1"));
    expect(local).not.toEqual(remote);
  });
});
