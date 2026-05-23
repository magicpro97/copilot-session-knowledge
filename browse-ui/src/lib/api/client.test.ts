// src/lib/api/client.test.ts — smoke test for apiFetch, hostFetch, and buildHostUrl
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

const REMOTE_HOST_WITH_PREFIX_FIXTURE = {
  id: "prefixed-1",
  label: "Prefixed Tunnel",
  base_url: "https://proxy.example.com/copilot",
  token: "prefix-secret",
  cli_kind: "copilot",
  is_default: false,
};

describe("buildHostUrl", () => {
  it("appends path to base without path prefix", async () => {
    const { buildHostUrl } = await import("./client");
    const url = buildHostUrl("https://xyz.ngrok.io", "/api/sessions");
    expect(url.toString()).toBe("https://xyz.ngrok.io/api/sessions");
  });

  it("preserves base path prefix when building URL (issue #31)", async () => {
    const { buildHostUrl } = await import("./client");
    const url = buildHostUrl("https://proxy.example.com/copilot", "/api/sessions");
    expect(url.toString()).toBe("https://proxy.example.com/copilot/api/sessions");
  });

  it("handles trailing slash on base path prefix", async () => {
    const { buildHostUrl } = await import("./client");
    const url = buildHostUrl("https://proxy.example.com/copilot/", "/api/sessions");
    expect(url.toString()).toBe("https://proxy.example.com/copilot/api/sessions");
  });

  it("handles path without leading slash", async () => {
    const { buildHostUrl } = await import("./client");
    const url = buildHostUrl("https://xyz.ngrok.io", "api/sessions");
    expect(url.toString()).toBe("https://xyz.ngrok.io/api/sessions");
  });

  it("keeps query strings in the URL search instead of encoding them into the pathname", async () => {
    const { buildHostUrl } = await import("./client");
    const url = buildHostUrl("https://xyz.ngrok.io", "/api/sessions?page=1&page_size=20");
    expect(url.toString()).toBe("https://xyz.ngrok.io/api/sessions?page=1&page_size=20");
  });

  it("preserves base path prefixes when the appended path includes query and hash", async () => {
    const { buildHostUrl } = await import("./client");
    const url = buildHostUrl(
      "https://proxy.example.com/copilot",
      "/api/retro/summary?mode=repo#details"
    );
    expect(url.toString()).toBe(
      "https://proxy.example.com/copilot/api/retro/summary?mode=repo#details"
    );
  });
});

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

  it("sends token via Authorization header, not as a URL query param (issue #34)", async () => {
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
    expect(calledUrl).not.toContain("token=");

    const calledHeaders = mockFetch.mock.calls[0][1]?.headers as Headers;
    expect(calledHeaders?.get("Authorization")).toBe("Bearer test-token-123");
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
    window.location.href = "http://localhost/v2/sessions";
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("uses same-origin base for local host and sends token via Authorization header (issue #34)", async () => {
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
    expect(calledUrl).not.toContain("token=");

    const calledHeaders = mockFetch.mock.calls[0][1]?.headers as Headers;
    expect(calledHeaders?.get("Authorization")).toBe("Bearer local-token");
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

  it("preserves base_url path prefix when routing to remote host (issue #31)", async () => {
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    globalThis.fetch = mockFetch;

    const { hostFetch } = await import("./client");
    await hostFetch("/api/operator/sessions", REMOTE_HOST_WITH_PREFIX_FIXTURE as never);

    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).toBe("https://proxy.example.com/copilot/api/operator/sessions");
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

  it("can suppress local 401 redirect for optional capability probes", async () => {
    window.location.href = "http://localhost/v2/sessions/e2e-session-0001-abcdef";
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 401,
      text: async () => "Unauthorized",
    });
    globalThis.fetch = mockFetch;

    const { hostFetch } = await import("./client");
    await expect(
      hostFetch(
        "/api/operator/cli-sessions/f47ac10b-58cc-4372-a567-0e02b2c3d479",
        LOCAL_HOST_FIXTURE as never,
        undefined,
        {
          noRedirectOn401: true,
        }
      )
    ).rejects.toThrow("Unauthorized");

    expect(window.location.href).toBe("http://localhost/v2/sessions/e2e-session-0001-abcdef");
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
    expect(calledUrl).not.toContain("token=");

    const calledHeaders = mockFetch.mock.calls[0][1]?.headers as Headers;
    expect(calledHeaders?.get("Authorization")).toBe("Bearer profile-token");
    expect(calledHeaders?.get("Authorization")).not.toContain("session-storage-token");
  });
});

describe("hostRequest LNA loopback hint (issue #517)", () => {
  const LOOPBACK_HTTPS_HOST = {
    id: "local-https",
    label: "Local HTTPS",
    base_url: "https://127.0.0.1:8765",
    token: "loopback-token",
    cli_kind: "copilot",
    is_default: false,
  };

  const REMOTE_HTTPS_HOST = {
    id: "remote",
    label: "Remote",
    base_url: "https://xyz.ngrok.io",
    token: "remote-token",
    cli_kind: "copilot",
    is_default: false,
  };

  beforeEach(() => {
    sessionStorageMock.clear();
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    // Restore default window for other suites.
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
      configurable: true,
    });
  });

  function setHttpsPage(): void {
    Object.defineProperty(globalThis, "window", {
      value: {
        location: {
          protocol: "https:",
          origin: "https://agents.linhngo.dev",
          search: "",
          href: "https://agents.linhngo.dev/v2/sessions",
        },
        history: { replaceState: vi.fn() },
      },
      writable: true,
      configurable: true,
    });
  }

  it("sets targetAddressSpace=loopback when fetching a loopback host from an HTTPS page", async () => {
    setHttpsPage();
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    globalThis.fetch = mockFetch;

    const { hostFetch } = await import("./client");
    await hostFetch("/api/operator/capabilities", LOOPBACK_HTTPS_HOST as never);

    const init = mockFetch.mock.calls[0][1] as { targetAddressSpace?: string };
    expect(init.targetAddressSpace).toBe("loopback");
  });

  it("does NOT set targetAddressSpace for remote (non-loopback) hosts", async () => {
    setHttpsPage();
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    globalThis.fetch = mockFetch;

    const { hostFetch } = await import("./client");
    await hostFetch("/api/operator/capabilities", REMOTE_HTTPS_HOST as never);

    const init = mockFetch.mock.calls[0][1] as { targetAddressSpace?: string };
    expect(init.targetAddressSpace).toBeUndefined();
  });

  it("does NOT set targetAddressSpace when the page origin is not HTTPS", async () => {
    // Default window has protocol unset (treated as non-https).
    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    globalThis.fetch = mockFetch;

    const { hostFetch } = await import("./client");
    await hostFetch("/api/operator/capabilities", LOOPBACK_HTTPS_HOST as never);

    const init = mockFetch.mock.calls[0][1] as { targetAddressSpace?: string };
    expect(init.targetAddressSpace).toBeUndefined();
  });
});
