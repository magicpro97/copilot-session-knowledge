// src/lib/api/client.test.ts — smoke test for apiFetch
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
