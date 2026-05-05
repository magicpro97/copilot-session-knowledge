import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HostProfile } from "@/lib/api/types";
import { LOCAL_HOST, checkHostCompatibility, isOperatorHostEnabled } from "@/lib/host-profiles";

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makeHost(overrides: Partial<HostProfile> = {}): HostProfile {
  return {
    id: "test-host",
    label: "Test Host",
    base_url: "https://agent.example.com",
    token: "tok",
    cli_kind: "copilot",
    is_default: false,
    ...overrides,
  };
}

const HTTPS_ORIGIN = "https://browse.example.com";
const HTTP_ORIGIN = "http://localhost";

// ── checkHostCompatibility ────────────────────────────────────────────────────

describe("checkHostCompatibility", () => {
  it("is compatible for LOCAL_HOST (empty base_url) regardless of control-plane scheme", () => {
    expect(checkHostCompatibility(HTTPS_ORIGIN, LOCAL_HOST)).toMatchObject({
      compatible: true,
      code: "ok",
    });
    expect(checkHostCompatibility(HTTP_ORIGIN, LOCAL_HOST)).toMatchObject({
      compatible: true,
      code: "ok",
    });
  });

  it("is compatible for a remote HTTPS host from an HTTPS control plane", () => {
    const host = makeHost({ base_url: "https://xxxx.ngrok.io" });
    expect(checkHostCompatibility(HTTPS_ORIGIN, host)).toMatchObject({
      compatible: true,
      code: "ok",
    });
  });

  it("is compatible for a remote HTTPS host from an HTTP control plane", () => {
    const host = makeHost({ base_url: "https://xxxx.ngrok.io" });
    expect(checkHostCompatibility(HTTP_ORIGIN, host)).toMatchObject({
      compatible: true,
      code: "ok",
    });
  });

  it("returns pna-required (compatible: true) for http://localhost from an HTTPS control plane", () => {
    const host = makeHost({ base_url: "http://localhost:8792" });
    const result = checkHostCompatibility(HTTPS_ORIGIN, host);
    expect(result.compatible).toBe(true);
    expect(result.code).toBe("pna-required");
    expect(result.reason).toMatch(/PNA|Private Network Access/i);
    expect(result.reason).toMatch(/Chromium|Edge/i);
    expect(result.reason).toMatch(/tunnel/i);
  });

  it("returns pna-required (compatible: true) for http://127.0.0.1 from an HTTPS control plane", () => {
    const host = makeHost({ base_url: "http://127.0.0.1:11434" });
    const result = checkHostCompatibility(HTTPS_ORIGIN, host);
    expect(result.compatible).toBe(true);
    expect(result.code).toBe("pna-required");
  });

  it("returns pna-required for http://127.0.0.2 (non-trivial 127.x.x.x) from HTTPS", () => {
    const host = makeHost({ base_url: "http://127.0.0.2:9000" });
    const result = checkHostCompatibility(HTTPS_ORIGIN, host);
    expect(result.compatible).toBe(true);
    expect(result.code).toBe("pna-required");
  });

  it("returns pna-required for http://0.0.0.0 from an HTTPS control plane", () => {
    const host = makeHost({ base_url: "http://0.0.0.0:8792" });
    const result = checkHostCompatibility(HTTPS_ORIGIN, host);
    expect(result.compatible).toBe(true);
    expect(result.code).toBe("pna-required");
  });

  it("returns pna-required for http://[::1] from an HTTPS control plane", () => {
    const host = makeHost({ base_url: "http://[::1]:8080" });
    const result = checkHostCompatibility(HTTPS_ORIGIN, host);
    expect(result.compatible).toBe(true);
    expect(result.code).toBe("pna-required");
  });

  it("is compatible for http://localhost from an HTTP control plane (local dev)", () => {
    const host = makeHost({ base_url: "http://localhost:8792" });
    expect(checkHostCompatibility(HTTP_ORIGIN, host)).toMatchObject({
      compatible: true,
      code: "ok",
    });
  });

  it("is compatible for http://127.0.0.1 from an HTTP control plane", () => {
    const host = makeHost({ base_url: "http://127.0.0.1:8792" });
    expect(checkHostCompatibility(HTTP_ORIGIN, host)).toMatchObject({
      compatible: true,
      code: "ok",
    });
  });

  it("is incompatible for a non-loopback HTTP host from an HTTPS control plane", () => {
    const host = makeHost({ base_url: "http://remote.example.com" });
    const result = checkHostCompatibility(HTTPS_ORIGIN, host);
    expect(result.compatible).toBe(false);
    expect(result.code).toBe("mixed-content-http");
    expect(result.reason).toMatch(/HTTPS|HTTP|loopback|hosted-bootstrap/i);
  });

  it("fails open (compatible) for an unparseable control-plane origin", () => {
    const host = makeHost({ base_url: "http://localhost:8792" });
    expect(checkHostCompatibility("not-a-url", host)).toMatchObject({
      compatible: true,
      code: "ok",
    });
  });

  it("fails open (compatible) for a malformed host base_url", () => {
    const host = makeHost({ base_url: "not-a-url" });
    expect(checkHostCompatibility(HTTPS_ORIGIN, host)).toMatchObject({
      compatible: true,
      code: "ok",
    });
  });
});

// ── isOperatorHostEnabled ─────────────────────────────────────────────────────

describe("isOperatorHostEnabled", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns false for LOCAL_HOST without NEXT_PUBLIC_API_BASE", () => {
    expect(isOperatorHostEnabled(LOCAL_HOST, "/")).toBe(false);
  });

  it("returns true for a remote HTTPS host from an HTTP control plane", () => {
    // jsdom default origin is http://localhost — compatible with any remote host
    const host = makeHost({ base_url: "https://xxxx.ngrok.io" });
    expect(isOperatorHostEnabled(host, "/")).toBe(true);
  });

  it("returns true for a remote HTTPS host from an HTTPS control plane", () => {
    vi.stubGlobal("location", {
      ...window.location,
      origin: HTTPS_ORIGIN,
      protocol: "https:",
    });
    const host = makeHost({ base_url: "https://xxxx.ngrok.io" });
    expect(isOperatorHostEnabled(host, "/")).toBe(true);
  });

  it("returns true for an HTTP loopback host when control plane is HTTPS (pna-required, compatible)", () => {
    vi.stubGlobal("location", {
      ...window.location,
      origin: HTTPS_ORIGIN,
      protocol: "https:",
    });
    const host = makeHost({ base_url: "http://localhost:8792" });
    expect(isOperatorHostEnabled(host, "/")).toBe(true);
  });

  it("returns true for http://127.0.0.1 when control plane is HTTPS (pna-required)", () => {
    vi.stubGlobal("location", {
      ...window.location,
      origin: HTTPS_ORIGIN,
      protocol: "https:",
    });
    const host = makeHost({ base_url: "http://127.0.0.1:8792" });
    expect(isOperatorHostEnabled(host, "/")).toBe(true);
  });

  it("returns true for http://0.0.0.0 when control plane is HTTPS (pna-required)", () => {
    vi.stubGlobal("location", {
      ...window.location,
      origin: HTTPS_ORIGIN,
      protocol: "https:",
    });
    const host = makeHost({ base_url: "http://0.0.0.0:8792" });
    expect(isOperatorHostEnabled(host, "/")).toBe(true);
  });

  it("returns false for non-loopback HTTP when control plane is HTTPS", () => {
    vi.stubGlobal("location", {
      ...window.location,
      origin: HTTPS_ORIGIN,
      protocol: "https:",
    });
    const host = makeHost({ base_url: "http://remote.example.com" });
    expect(isOperatorHostEnabled(host, "/")).toBe(false);
  });

  it("returns true for an HTTP loopback host when control plane is HTTP (local dev)", () => {
    // jsdom default origin is http://localhost — compatible path preserved
    const host = makeHost({ base_url: "http://localhost:8792" });
    expect(isOperatorHostEnabled(host, "/")).toBe(true);
  });
});
