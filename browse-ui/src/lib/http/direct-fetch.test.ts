/**
 * @vitest-environment node
 *
 * direct-fetch.test.ts — proxy-bypass proof for `directFetch` (issue #63).
 *
 * These tests run in a real Node.js environment (not jsdom) so that
 * `typeof window === "undefined"` and the Node.js server path in
 * direct-fetch.ts is exercised.
 *
 * ## What is proven
 *
 * 1. **Behavioral proof (primary)** — with a proxy-aware global dispatcher
 *    active (`EnvHttpProxyAgent` + `HTTPS_PROXY=http://127.0.0.1:9999`),
 *    `directFetch` successfully reaches a local test server while plain
 *    `undiciFetch` (no custom dispatcher) is blocked.  The proxy server
 *    receives ZERO requests from `directFetch`.
 *
 * 2. **Browser isolation** — when `window` is defined, `directFetch`
 *    delegates to the global `fetch` without injecting a dispatcher.
 *
 * ## Reproducing manually (command-line proof)
 *
 * ```bash
 * cd browse-ui
 * # Run the vitest suite — the behavioral tests below reproduce this:
 * HTTPS_PROXY=http://127.0.0.1:9999 pnpm test src/lib/http/direct-fetch.test.ts
 * ```
 *
 * ## Why undici.fetch (not globalThis.fetch)?
 *
 * direct-fetch.ts calls undici's own `fetch` with an undici `Agent` dispatcher
 * to avoid the "invalid onRequestStart method" error that occurs when mixing
 * Node.js 24's internal undici with an external undici Agent.  Using undici's
 * own fetch ensures the Agent and fetch are from the same package version.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import * as undiciModule from "undici";
import type { AddressInfo } from "net";
import http from "http";

const { EnvHttpProxyAgent, setGlobalDispatcher, getGlobalDispatcher } = undiciModule;

// ─── helpers ────────────────────────────────────────────────────────────────

/** Spin up a minimal HTTP server and return its base URL + a cleanup fn. */
async function startLocalServer(
  handler: http.RequestListener
): Promise<{ url: string; close: () => Promise<void> }> {
  const server = http.createServer(handler);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${port}`,
    close: () =>
      new Promise<void>((resolve, reject) => server.close((e) => (e ? reject(e) : resolve()))),
  };
}

// ─── tests ──────────────────────────────────────────────────────────────────

describe("directFetch — Node.js proxy-bypass proof (issue #63)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  // ── 1. Behavioral proof — HTTPS_PROXY bypass ───────────────────────────────
  //
  // These are the honest HTTPS_PROXY=http://127.0.0.1:9999 tests demanded by
  // issue #63's acceptance criteria.  They use real undici and a local HTTP
  // server — no external network needed, fully reproducible in CI.
  //
  // DO NOT add vi.resetModules() here: setGlobalDispatcher must affect the
  // same undici instance that direct-fetch.ts uses.

  describe("behavioral proof — bypasses proxy global dispatcher", () => {
    let savedDispatcher: undiciModule.Dispatcher;

    beforeEach(() => {
      savedDispatcher = getGlobalDispatcher();
    });

    afterEach(() => {
      setGlobalDispatcher(savedDispatcher);
      delete process.env.HTTPS_PROXY;
      delete process.env.HTTP_PROXY;
    });

    /**
     * Primary proof: directFetch succeeds while plain undiciFetch is blocked.
     *
     * Setup:
     *   - Global undici dispatcher → EnvHttpProxyAgent (reads HTTPS_PROXY)
     *   - HTTPS_PROXY=http://127.0.0.1:9999 (nothing listening → ECONNREFUSED)
     *   - Target → local HTTP server on a random port (reachable directly)
     *
     * Expected:
     *   - undiciFetch (no custom dispatcher) → ECONNREFUSED at proxy ✓
     *   - directFetch (custom undici.Agent dispatcher) → 200 directly ✓
     */
    it("directFetch succeeds while plain undiciFetch is blocked by proxy (HTTPS_PROXY=http://127.0.0.1:9999)", async () => {
      const { url: targetUrl, close } = await startLocalServer((_req, res) => {
        res.writeHead(200, { "Content-Type": "text/plain" });
        res.end("direct-connection");
      });

      // Simulate corporate proxy: all undici requests routed via HTTPS_PROXY.
      process.env.HTTPS_PROXY = "http://127.0.0.1:9999";
      process.env.HTTP_PROXY = "http://127.0.0.1:9999";
      setGlobalDispatcher(new EnvHttpProxyAgent());

      try {
        // Control: plain undiciFetch routes through the global proxy dispatcher
        // → proxy at 9999 is not running → connection refused.
        await expect(
          undiciModule.fetch(targetUrl, { signal: AbortSignal.timeout(2_000) })
        ).rejects.toThrow();

        // Proof: directFetch overrides the global dispatcher per-request with
        // its own undici.Agent → reaches target directly → 200.
        const { directFetch } = await import("./direct-fetch");
        const res = await directFetch(targetUrl, {
          signal: AbortSignal.timeout(5_000),
        });

        expect(res.status).toBe(200);
        const body = await res.text();
        expect(body).toBe("direct-connection");
      } finally {
        await close();
      }
    }, 15_000);

    it("directFetch never contacts the proxy server (proxy receives 0 requests from directFetch)", async () => {
      // Fake proxy that records any request it receives.
      const proxyHits: string[] = [];
      const { url: proxyUrl, close: closeProxy } = await startLocalServer((req, res) => {
        proxyHits.push(`${req.method} ${req.url}`);
        res.writeHead(200);
        res.end();
      });

      const { url: targetUrl, close: closeTarget } = await startLocalServer((_req, res) => {
        res.writeHead(200);
        res.end("from-target");
      });

      // Route global dispatcher through our fake proxy.
      process.env.HTTP_PROXY = proxyUrl;
      setGlobalDispatcher(new EnvHttpProxyAgent());

      try {
        const { directFetch } = await import("./direct-fetch");
        const res = await directFetch(targetUrl, {
          signal: AbortSignal.timeout(5_000),
        });

        // directFetch must reach the target server directly.
        expect(res.status).toBe(200);
        expect(await res.text()).toBe("from-target");

        // Critical: the fake proxy must have received zero requests.
        // A non-zero value would mean the proxy bypass is not working.
        expect(proxyHits).toHaveLength(0);
      } finally {
        await closeProxy();
        await closeTarget();
      }
    }, 15_000);
  });

  // ── 2. Browser-context isolation ──────────────────────────────────────────

  describe("browser isolation — no dispatcher in window context", () => {
    beforeEach(() => {
      vi.resetModules();
    });

    it("delegates to globalThis.fetch (not undici) when window is defined", async () => {
      // Simulate browser environment.
      vi.stubGlobal("window", { location: { origin: "https://agents.linhngo.dev" } });

      const globalFetchMock = vi
        .fn()
        .mockResolvedValue(new Response("browser-ok", { status: 200 }));
      vi.stubGlobal("fetch", globalFetchMock);

      const { directFetch } = await import("./direct-fetch");
      const res = await directFetch("https://example.com/test");

      // In browser path, globalThis.fetch is called directly.
      expect(globalFetchMock).toHaveBeenCalledOnce();
      // The response comes from the global fetch mock, not undici.
      expect(res.status).toBe(200);
    });

    it("globalThis.fetch receives no injected dispatcher in browser context", async () => {
      vi.stubGlobal("window", { location: { origin: "https://agents.linhngo.dev" } });

      const capturedInits: RequestInit[] = [];
      vi.stubGlobal("fetch", ((_input: unknown, init?: RequestInit) => {
        capturedInits.push(init ?? {});
        return Promise.resolve(new Response("ok", { status: 200 }));
      }) as typeof fetch);

      const { directFetch } = await import("./direct-fetch");
      await directFetch("https://example.com/health");

      // No dispatcher should be injected — browser fetch has no dispatcher concept.
      const dispatcher = (capturedInits[0] as Record<string, unknown>).dispatcher;
      expect(dispatcher).toBeUndefined();
    });
  });
});
