/**
 * broker-client.test.ts — unit tests for broker-client.ts utilities (issue #63).
 *
 * These tests run in the default jsdom environment because the broker-client
 * helper functions (URL builders, reachability probe) don't depend on the
 * Node.js-only directFetch proxy-bypass path.
 *
 * For the directFetch bypass proof itself, see direct-fetch.test.ts which runs
 * with `@vitest-environment node`.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mock directFetch so we can verify broker-client routes through it.
vi.mock("@/lib/http/direct-fetch", () => ({
  directFetch: vi.fn(),
}));

describe("broker-client", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── probeBrokerReachability ────────────────────────────────────────────────

  describe("probeBrokerReachability", () => {
    it("returns true when the endpoint responds with any HTTP status", async () => {
      const { directFetch } = await import("@/lib/http/direct-fetch");
      vi.mocked(directFetch).mockResolvedValueOnce(new Response(null, { status: 200 }));

      const { probeBrokerReachability } = await import("./broker-client");
      const result = await probeBrokerReachability("https://example.com/broker");

      expect(result).toBe(true);
      expect(vi.mocked(directFetch)).toHaveBeenCalledOnce();
    });

    it("uses HEAD method and the exact broker URL", async () => {
      const { directFetch } = await import("@/lib/http/direct-fetch");
      vi.mocked(directFetch).mockResolvedValueOnce(new Response(null, { status: 204 }));

      const { probeBrokerReachability } = await import("./broker-client");
      await probeBrokerReachability("https://broker.example.com/health");

      const [url, init] = vi.mocked(directFetch).mock.calls[0];
      expect(url).toBe("https://broker.example.com/health");
      expect((init as RequestInit).method).toBe("HEAD");
    });

    it("returns true for any HTTP status < 600 (including 4xx, 5xx)", async () => {
      const { directFetch } = await import("@/lib/http/direct-fetch");
      vi.mocked(directFetch).mockResolvedValueOnce(new Response(null, { status: 503 }));

      const { probeBrokerReachability } = await import("./broker-client");
      const result = await probeBrokerReachability("https://example.com/broker");

      // Any HTTP response means the network path is open.
      expect(result).toBe(true);
    });

    it("returns false on network error (endpoint unreachable)", async () => {
      const { directFetch } = await import("@/lib/http/direct-fetch");
      vi.mocked(directFetch).mockRejectedValueOnce(new TypeError("fetch failed"));

      const { probeBrokerReachability } = await import("./broker-client");
      const result = await probeBrokerReachability("https://unreachable.example.com/");

      expect(result).toBe(false);
    });

    it("returns false on timeout (AbortError)", async () => {
      const { directFetch } = await import("@/lib/http/direct-fetch");
      const abortError = new DOMException("The operation was aborted", "AbortError");
      vi.mocked(directFetch).mockRejectedValueOnce(abortError);

      const { probeBrokerReachability } = await import("./broker-client");
      const result = await probeBrokerReachability("https://slow-broker.example.com/");

      expect(result).toBe(false);
    });

    it("uses directFetch — NOT the global fetch (proxy bypass is required)", async () => {
      // This test verifies that broker reachability probes go through
      // directFetch so they bypass any proxy global dispatcher.
      // Using the global fetch would give a false-positive when a proxy makes
      // an unreachable endpoint appear reachable.
      const { directFetch } = await import("@/lib/http/direct-fetch");
      vi.mocked(directFetch).mockResolvedValueOnce(new Response(null, { status: 200 }));

      const globalFetchSpy = vi.spyOn(globalThis, "fetch");

      const { probeBrokerReachability } = await import("./broker-client");
      await probeBrokerReachability("https://broker.example.com/");

      // directFetch must have been called; global fetch must NOT have been
      // called directly (broker-client must not bypass the bypass).
      expect(vi.mocked(directFetch)).toHaveBeenCalledOnce();
      expect(globalFetchSpy).not.toHaveBeenCalled();
    });
  });

  // ── getTelegramBotUrl ──────────────────────────────────────────────────────

  describe("getTelegramBotUrl", () => {
    it("builds a t.me deep-link URL with bot name and pairing token", async () => {
      const { getTelegramBotUrl } = await import("./broker-client");
      const url = getTelegramBotUrl("MyBrokerBot", "abc123");

      expect(url).toBe("https://t.me/MyBrokerBot?start=abc123");
    });

    it("strips a leading @ from the bot name", async () => {
      const { getTelegramBotUrl } = await import("./broker-client");
      const url = getTelegramBotUrl("@MyBrokerBot", "token-xyz");

      expect(url).not.toContain("@");
      expect(url).toContain("t.me/MyBrokerBot");
    });

    it("percent-encodes special characters in the bot name", async () => {
      const { getTelegramBotUrl } = await import("./broker-client");
      const url = getTelegramBotUrl("My Bot/Name", "tok");

      // Spaces and slashes in the bot name segment must be percent-encoded.
      expect(url).not.toContain(" ");
      // The path segment "My Bot/Name" → "My%20Bot%2FName"
      expect(url).toContain("My%20Bot%2FName");
    });

    it("percent-encodes special characters in the pairing token", async () => {
      const { getTelegramBotUrl } = await import("./broker-client");
      const url = getTelegramBotUrl("BrokerBot", "tok&special=chars");

      expect(url).not.toContain("&");
      expect(url).toContain("tok%26special%3Dchars");
    });
  });

  // ── describeBrokerUrl ──────────────────────────────────────────────────────

  describe("describeBrokerUrl", () => {
    it("returns @BotName (Telegram) for a t.me URL", async () => {
      const { describeBrokerUrl } = await import("./broker-client");
      const desc = describeBrokerUrl("https://t.me/MyBrokerBot?start=abc");

      expect(desc).toBe("@MyBrokerBot (Telegram)");
    });

    it("returns 'Telegram broker' for a bare t.me URL with no path", async () => {
      const { describeBrokerUrl } = await import("./broker-client");
      const desc = describeBrokerUrl("https://t.me/");

      expect(desc).toBe("Telegram broker");
    });

    it("returns the raw URL for non-Telegram broker URLs", async () => {
      const { describeBrokerUrl } = await import("./broker-client");
      const desc = describeBrokerUrl("https://broker.example.com/relay");

      expect(desc).toBe("https://broker.example.com/relay");
    });

    it("returns the raw URL for malformed URLs", async () => {
      const { describeBrokerUrl } = await import("./broker-client");
      const desc = describeBrokerUrl("not-a-url");

      expect(desc).toBe("not-a-url");
    });
  });
});
