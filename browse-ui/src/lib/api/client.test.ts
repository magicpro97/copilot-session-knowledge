// src/lib/api/client.test.ts — smoke test for apiFetch and hostFetch
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mock sessionStorage
const store: Record<string, string> = {};
const sessionStorageMock = {
  getItem: (key: string) => store[key] ?? null,
  setItem: (key: string, val: string) => {
    store[key] = val;
  },
  removeItem: (key: string) => {
    delete store[key];
  },
  clear: () => Object.keys(store).forEach((k) => delete store[k]),
};

Object.defineProperty(globalThis, "sessionStorage", {
  value: sessionStorageMock,
  writable: true,
});

Object.defineProperty(globalThis, "window", {
  value: {
    location: {
      origin: "http://localhost",
      search: "",
      href: "http://localhost/v2/sessions",
    },
    history: { replaceState: vi.fn() },
  },
  writable: true,
});

const LOCAL_HOST_FIXTURE = {
  id: "local",
  label: "Local (same-origin)",
  base_url: "",
  token: "",
  cli_kind: "copilot",
  is_default: true,
};

const REMOTE_HOST_FIXTURE = {
  id: "tunnel-1",
  label: "My Tunnel",
  base_url: "https://xyz.ngrok.io",
  token: "remote-secret",
  cli_kind: "copilot",
  is_default: false,
};

describe("apiFetch", () => {
  beforeEach(() => {
    sessionStorageMock.clear();
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("calls fetch with the correct URL", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    });
    globalThis.fetch = mockFetch;

    const { apiFetch } = await import("./client");
    await apiFetch("/api/test");

    expect(mockFetch).toHaveBeenCalledOnce();
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/api/test");
  });

  it("injects token from sessionStorage", async () => {
    sessionStorageMock.setItem("browse_token", "test-token-123");

    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    globalThis.fetch = mockFetch;

    const { apiFetch } = await import("./client");
    await apiFetch("/api/sessions");

    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("token=test-token-123");
  });

  it("throws on non-ok response", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => "Server Error",
    });
    globalThis.fetch = mockFetch;

    const { apiFetch } = await import("./client");
    await expect(apiFetch("/api/fail")).rejects.toThrow("API 500");
  });
});

describe("hostFetch", () => {
  beforeEach(() => {
    sessionStorageMock.clear();
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("uses same-origin base for local host and injects token in URL", async () => {
    sessionStorageMock.setItem("browse_token", "local-token");

    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    });
    globalThis.fetch = mockFetch;

    const { hostFetch } = await import("./client");
    await hostFetch("/api/operator/sessions", LOCAL_HOST_FIXTURE as never);

    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("http://localhost");
    expect(calledUrl).toContain("token=local-token");

    const calledHeaders = mockFetch.mock.calls[0][1]?.headers as Headers;
    expect(calledHeaders?.has("Authorization")).toBe(false);
  });

  it("uses remote base_url for remote host and sends Authorization header", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ sessions: [], count: 0 }),
    });
    globalThis.fetch = mockFetch;

    const { hostFetch } = await import("./client");
    await hostFetch("/api/operator/sessions", REMOTE_HOST_FIXTURE as never);

    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("https://xyz.ngrok.io");
    expect(calledUrl).not.toContain("token=");

    const calledHeaders = mockFetch.mock.calls[0][1]?.headers as Headers;
    expect(calledHeaders?.get("Authorization")).toBe("Bearer remote-secret");
  });

  it("does not leak remote token in URL", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    globalThis.fetch = mockFetch;

    const { hostFetch } = await import("./client");
    await hostFetch("/api/operator/capabilities", REMOTE_HOST_FIXTURE as never);

    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).not.toContain("remote-secret");
    expect(calledUrl).not.toContain("token=");
  });

  it("throws on non-ok response from remote host", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 403,
      text: async () => "Forbidden",
    });
    globalThis.fetch = mockFetch;

    const { hostFetch } = await import("./client");
    await expect(hostFetch("/api/operator/sessions", REMOTE_HOST_FIXTURE as never)).rejects.toThrow(
      "API 403"
    );
  });

  it("throws Unauthorized on 401 from remote host without redirect", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 401,
      text: async () => "Unauthorized",
    });
    globalThis.fetch = mockFetch;

    const { hostFetch } = await import("./client");
    await expect(hostFetch("/api/operator/sessions", REMOTE_HOST_FIXTURE as never)).rejects.toThrow(
      "Unauthorized"
    );

    // No redirect for remote hosts
    expect(window.location.href).not.toContain("/v2/sessions/login");
  });

  it("uses profile token over sessionStorage token for local host with explicit token", async () => {
    sessionStorageMock.setItem("browse_token", "session-storage-token");

    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    globalThis.fetch = mockFetch;

    const { hostFetch } = await import("./client");
    const localWithToken = { ...LOCAL_HOST_FIXTURE, token: "profile-token" };
    await hostFetch("/api/operator/sessions", localWithToken as never);

    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toContain("token=profile-token");
    expect(calledUrl).not.toContain("session-storage-token");
  });
});
