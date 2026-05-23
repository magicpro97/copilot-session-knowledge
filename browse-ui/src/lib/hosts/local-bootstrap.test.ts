import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { probeLocalBootstrap, resetLocalBootstrapCache } from "./local-bootstrap";

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    json: vi.fn(async () => body),
  } as unknown as Response;
}

describe("probeLocalBootstrap", () => {
  beforeEach(() => {
    resetLocalBootstrapCache();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    resetLocalBootstrapCache();
  });

  it("probes 127.0.0.1 first and returns detected for open-auth backends", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        schema: "browse-host/1",
        status: "ok",
        auth: "open",
        manual_token_required: false,
        capabilities: ["discovery", "healthz", "api"],
        cors_origins_configured: true,
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await probeLocalBootstrap();

    expect(result.status).toBe("detected");
    expect(result).toMatchObject({ url: "http://127.0.0.1:8765" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const firstCall = (fetchMock.mock.calls as unknown as Array<[unknown, unknown?]>)[0];
    expect(firstCall[0]).toBe("http://127.0.0.1:8765/.well-known/browse-host");
  });

  it("falls through to localhost when the 127.0.0.1 candidate fails", async () => {
    // Three calls now: (1) primary 127.0.0.1 probe → fails; (2) secondary no-cors
    // probe to 127.0.0.1/ to detect daemon state → also fails; (3) primary localhost probe → succeeds.
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline")) // (1) 127.0.0.1 primary
      .mockRejectedValueOnce(new Error("offline")) // (2) 127.0.0.1 secondary no-cors
      .mockResolvedValueOnce(
        jsonResponse({
          schema: "browse-host/1",
          status: "ok",
          auth: "open",
          manual_token_required: false,
          capabilities: ["discovery"],
          cors_origins_configured: true,
        })
      ); // (3) localhost primary
    vi.stubGlobal("fetch", fetchMock);

    const result = await probeLocalBootstrap();

    expect(result.status).toBe("detected");
    expect(result).toMatchObject({ url: "http://localhost:8765" });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("returns auth-required without inventing a token", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({
          schema: "browse-host/1",
          status: "ok",
          auth: "token",
          manual_token_required: true,
          capabilities: ["discovery"],
          cors_origins_configured: true,
        })
      )
    );

    const result = await probeLocalBootstrap();

    expect(result.status).toBe("auth-required");
    if (result.status === "auth-required") {
      expect(result.response.manual_token_required).toBe(true);
      expect(result.response.auth).toBe("token");
    }
  });

  it("applies a negative cache after all candidates fail", async () => {
    const fetchMock = vi.fn(async () => {
      throw new Error("offline");
    });
    vi.stubGlobal("fetch", fetchMock);

    const first = await probeLocalBootstrap();
    const second = await probeLocalBootstrap();

    expect(first).toEqual(expect.objectContaining({ status: "unavailable" }));
    expect(second).toEqual({ status: "cached-negative" });
    // 4 primary probes (one per candidate) + 4 secondary no-cors probes = 8 total
    expect(fetchMock).toHaveBeenCalledTimes(8);
  });

  it("ignores malformed discovery payloads and reports unavailable", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ schema: "wrong" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await probeLocalBootstrap();

    expect(result).toEqual(expect.objectContaining({ status: "unavailable" }));
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  // ── Hosted HTTPS origin policy (#517) ───────────────────────────────────────
  // From a hosted HTTPS origin (e.g. https://agents.linhngo.dev) the probe must
  // NOT call any http:// loopback URL, and must NOT attempt the secondary
  // no-cors probe. Both behaviors are user-visible Chrome LNA/PNA console
  // errors and a potential trust-downgrade vector if HTTP loopback ever
  // succeeded.

  describe("from hosted HTTPS origin", () => {
    beforeEach(() => {
      vi.stubGlobal("window", {
        location: { protocol: "https:", hostname: "agents.linhngo.dev" },
      });
    });

    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it("only probes HTTPS loopback candidates (no http://)", async () => {
      const fetchMock = vi.fn(async () => {
        throw new Error("offline");
      });
      vi.stubGlobal("fetch", fetchMock);

      const result = await probeLocalBootstrap();

      expect(result.status).toBe("unavailable");
      const calls = (fetchMock.mock.calls as unknown as Array<[string, unknown?]>).map((c) => c[0]);
      // No http:// URL should EVER be passed to fetch from a hosted HTTPS origin.
      for (const url of calls) {
        expect(url.startsWith("http://")).toBe(false);
      }
      // Both HTTPS candidates probed, no others.
      expect(calls).toEqual([
        "https://127.0.0.1:8765/.well-known/browse-host",
        "https://localhost:8765/.well-known/browse-host",
      ]);
      // Exactly 2 primary probes; NO secondary no-cors probes.
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it("does not attempt the secondary no-cors probe on network-error", async () => {
      const fetchMock = vi.fn(async () => {
        throw new TypeError("Failed to fetch"); // network-error path
      });
      vi.stubGlobal("fetch", fetchMock);

      const result = await probeLocalBootstrap();

      expect(result.status).toBe("unavailable");
      if (result.status === "unavailable") {
        // All reasons should be network-error with daemonState "unknown"
        // (since we deliberately skip the secondary probe on hosted HTTPS).
        for (const reason of result.reasons ?? []) {
          expect(reason.reason).toBe("network-error");
          expect(reason.daemonState).toBe("unknown");
        }
      }
      // 2 HTTPS candidates × 1 primary probe each (no secondary) = 2 fetch calls.
      expect(fetchMock).toHaveBeenCalledTimes(2);
      const calls = (fetchMock.mock.calls as unknown as Array<[string, RequestInit?]>).map(
        (c) => c[1]
      );
      // No fetch call should have used mode: "no-cors" from hosted HTTPS.
      for (const init of calls) {
        expect(init?.mode).not.toBe("no-cors");
      }
    });

    it("returns detected when an HTTPS loopback backend answers", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          jsonResponse({
            schema: "browse-host/1",
            status: "ok",
            auth: "open",
            manual_token_required: false,
            capabilities: ["discovery"],
            cors_origins_configured: true,
          })
        )
      );

      const result = await probeLocalBootstrap();
      expect(result.status).toBe("detected");
      expect(result).toMatchObject({ url: "https://127.0.0.1:8765" });
    });
  });
});
