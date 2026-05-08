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
    // 2 primary probes (one per candidate) + 2 secondary no-cors probes = 4 total
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("ignores malformed discovery payloads and reports unavailable", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ schema: "wrong" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await probeLocalBootstrap();

    expect(result).toEqual(expect.objectContaining({ status: "unavailable" }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
