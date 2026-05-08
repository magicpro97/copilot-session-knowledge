import { describe, expect, it, vi, afterEach } from "vitest";

import { parsePairingUrl, verifyPairingTicketServer } from "./pairing";

function toBase64Url(value: string): string {
  return Buffer.from(value, "utf-8")
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function makeTicket(
  overrides: Partial<{
    url: string;
    nonce: string;
    created_at: number;
    max_age_seconds: number;
    token_hmac: string;
  }> = {}
): string {
  const payload = {
    url: "http://127.0.0.1:8765",
    nonce: "0123456789abcdef0123456789abcdef",
    created_at: 1_700_000_000,
    token_hmac: "signed-proof",
    ...overrides,
  };
  return `browse://connect?ticket=${toBase64Url(JSON.stringify(payload))}`;
}

describe("parsePairingUrl", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("rejects regular tickets older than the default 5-minute window", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date((1_700_000_000 + 600) * 1000));

    const result = parsePairingUrl(makeTicket());

    expect(result.ok).toBe(false);
    if (result.ok) {
      throw new Error("Expected stale regular ticket to be rejected.");
    }
    expect(result.error).toContain("expired");
  });

  it("accepts static-slot tickets that carry a longer signed TTL", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date((1_700_000_000 + 600) * 1000));

    const result = parsePairingUrl(makeTicket({ max_age_seconds: 86_400 }));

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.baseUrl).toBe("http://127.0.0.1:8765");
      expect(result.payload.max_age_seconds).toBe(86_400);
    }
  });

  it("rejects tickets whose base URL does not use http(s)", () => {
    const result = parsePairingUrl(makeTicket({ url: "file:///etc/passwd" }));

    expect(result.ok).toBe(false);
    if (result.ok) {
      throw new Error("Expected non-http(s) ticket URL to be rejected.");
    }
    expect(result.error).toContain("http or https");
  });
});

describe("verifyPairingTicketServer", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("treats reachable non-JSON HTTP responses as reachable server rejections", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<html>Not Found</html>", {
          status: 404,
          headers: { "Content-Type": "text/html" },
        })
      )
    );

    const result = await verifyPairingTicketServer(
      "http://127.0.0.1:8765",
      "browse://connect?ticket=dummy-ticket"
    );

    expect(result.ok).toBe(false);
    if (result.ok) {
      throw new Error("Expected non-JSON HTTP verification response to be rejected.");
    }
    expect(result.serverReachable).toBe(true);
    expect(result.error).toContain("HTTP 404");
  });
});
