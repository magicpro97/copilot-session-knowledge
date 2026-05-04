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
} from "@/lib/api/hooks";
import { LOCAL_HOST, LOCAL_HOST_ID } from "@/lib/host-profiles";

vi.mock("@/lib/api/client", () => ({
  apiFetch: vi.fn(),
  hostFetch: vi.fn(),
}));

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
    expect(queryKeys.sessionDetail("abc")).toEqual(["session-detail", "abc"]);
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
      { wing: ["alpha"], limit: 10 },
    ]);
    expect(queryKeys.graphLegacy({ wing: ["alpha"], limit: 10 })).toEqual([
      "graph-legacy",
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

  it("createOperatorStreamUrl uses remote base_url and appends token as query param", () => {
    const url = createOperatorStreamUrl("sess-1", "run-1", REMOTE_HOST);
    expect(url).toContain("https://xyz.ngrok.io");
    expect(url).toContain("sess-1");
    expect(url).toContain("run=run-1");
    expect(url).toContain("token=secret");
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
});

describe("createLiveStreamUrl", () => {
  it("uses remote base_url and appends token as query param", () => {
    const url = createLiveStreamUrl(REMOTE_HOST);
    expect(url).toBe("https://xyz.ngrok.io/api/live?token=secret");
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
});
