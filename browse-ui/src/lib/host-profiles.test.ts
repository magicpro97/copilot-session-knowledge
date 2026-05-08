import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HostProfile } from "@/lib/api/types";
import {
  LOCAL_HOST,
  checkHostCompatibility,
  getHostProfiles,
  isOperatorHostEnabled,
  saveHostProfile,
  suggestBrokerFromProbeFail,
} from "@/lib/host-profiles";

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

// ── broker-required (#65) ─────────────────────────────────────────────────────

describe("checkHostCompatibility — broker-required", () => {
  it("returns broker-required when connectivity_mode === 'broker'", () => {
    const host = makeHost({
      base_url: "https://t.me/my_bot?start=abc123",
      connectivity_mode: "broker",
    });
    const result = checkHostCompatibility(HTTPS_ORIGIN, host);
    expect(result.compatible).toBe(true);
    expect(result.code).toBe("broker-required");
    expect(result.reason).toMatch(/broker/i);
    // The broker-required branch must include recommendedBroker (#65)
    if (result.code === "broker-required") {
      expect(["telegram", "discord", "ably"]).toContain(result.recommendedBroker);
    }
  });

  it("returns broker-required for a broker host on an HTTP control plane too", () => {
    const host = makeHost({
      base_url: "https://t.me/my_bot",
      connectivity_mode: "broker",
    });
    const result = checkHostCompatibility(HTTP_ORIGIN, host);
    expect(result.compatible).toBe(true);
    expect(result.code).toBe("broker-required");
    if (result.code === "broker-required") {
      expect(result.recommendedBroker).toBe("telegram");
    }
  });

  it("does NOT return broker-required when connectivity_mode is absent (legacy profile)", () => {
    const host = makeHost({ base_url: "https://xxxx.ngrok.io" });
    // connectivity_mode is undefined — normal direct path
    const result = checkHostCompatibility(HTTPS_ORIGIN, host);
    expect(result.code).toBe("ok");
  });

  it("does NOT return broker-required when connectivity_mode === 'direct'", () => {
    const host = makeHost({
      base_url: "https://xxxx.ngrok.io",
      connectivity_mode: "direct",
    });
    const result = checkHostCompatibility(HTTPS_ORIGIN, host);
    expect(result.code).toBe("ok");
  });

  it("does NOT return broker-required when connectivity_mode === 'tunnel'", () => {
    const host = makeHost({
      base_url: "https://xxxx.ngrok.io",
      connectivity_mode: "tunnel",
    });
    const result = checkHostCompatibility(HTTPS_ORIGIN, host);
    expect(result.code).toBe("ok");
  });

  it("broker-required takes priority over pna-required check for loopback broker URLs", () => {
    // A broker profile whose base_url happens to be http://localhost should still
    // return broker-required, not pna-required — connectivity_mode wins.
    const host = makeHost({
      base_url: "http://localhost:9999",
      connectivity_mode: "broker",
    });
    const result = checkHostCompatibility(HTTPS_ORIGIN, host);
    expect(result.code).toBe("broker-required");
  });
});

// ── connectivity_mode localStorage migration (#65) ───────────────────────────

describe("HostProfile connectivity_mode migration safety", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads a profile saved without connectivity_mode (legacy) and treats it as ok-compatible", () => {
    // Simulate a profile saved before connectivity_mode was introduced.
    const legacyProfile = {
      id: "legacy-host",
      label: "Legacy Host",
      base_url: "https://xxxx.ngrok.io",
      token: "tok",
      cli_kind: "copilot",
      is_default: false,
    };
    localStorage.setItem("browse_host_profiles", JSON.stringify([legacyProfile]));

    const profiles = getHostProfiles();
    expect(profiles).toHaveLength(1);
    // Legacy profiles have connectivity_mode undefined — safe default.
    expect(profiles[0].connectivity_mode).toBeUndefined();

    // Compatibility should be "ok" — legacy profiles are treated as direct.
    const compat = checkHostCompatibility(HTTPS_ORIGIN, profiles[0]);
    expect(compat.code).toBe("ok");
  });

  it("round-trips a broker profile through localStorage without data loss", () => {
    const brokerProfile: HostProfile = {
      id: "broker-1",
      label: "My Telegram Broker",
      base_url: "https://t.me/my_bot?start=secret",
      token: "secret",
      cli_kind: "copilot",
      is_default: false,
      connectivity_mode: "broker",
    };
    saveHostProfile(brokerProfile);
    const loaded = getHostProfiles().find((p) => p.id === "broker-1");
    expect(loaded).toBeDefined();
    expect(loaded?.connectivity_mode).toBe("broker");
    expect(loaded?.base_url).toBe("https://t.me/my_bot?start=secret");
  });

  it("round-trips a tunnel profile through localStorage preserving connectivity_mode", () => {
    const tunnelProfile: HostProfile = {
      id: "tunnel-1",
      label: "ngrok Tunnel",
      base_url: "https://abc123.ngrok.io",
      token: "tok2",
      cli_kind: "copilot",
      is_default: false,
      connectivity_mode: "tunnel",
    };
    saveHostProfile(tunnelProfile);
    const loaded = getHostProfiles().find((p) => p.id === "tunnel-1");
    expect(loaded?.connectivity_mode).toBe("tunnel");
  });
});

// ── suggestBrokerFromProbeFail (#65 — diagnosis-driven broker tab) ────────────

describe("suggestBrokerFromProbeFail", () => {
  it("returns broker-required for a non-auth probe failure", () => {
    const result = suggestBrokerFromProbeFail("https://fail.example.com", false);
    expect(result).not.toBeNull();
    expect(result?.code).toBe("broker-required");
    expect(result?.compatible).toBe(true);
    expect(result?.reason).toMatch(/direct connection/i);
    // Narrow to broker-required branch to access recommendedBroker
    if (result?.code === "broker-required") {
      expect(result.recommendedBroker).toBe("telegram");
    } else {
      throw new Error("Expected broker-required branch");
    }
  });

  it("returns null for an auth error (401/403) — broker mode is not the fix", () => {
    expect(suggestBrokerFromProbeFail("https://auth-fail.example.com", true)).toBeNull();
  });

  it("returns broker-required even when targetUrl is empty", () => {
    const result = suggestBrokerFromProbeFail("", false);
    expect(result?.code).toBe("broker-required");
    expect(result?.reason).toMatch(/the remote host/i);
  });

  it("recommendedBroker is always a known relay platform value", () => {
    const result = suggestBrokerFromProbeFail("https://any.host.example.com", false);
    expect(result).not.toBeNull();
    if (result?.code === "broker-required") {
      expect(["telegram", "discord", "ably"]).toContain(result.recommendedBroker);
    } else {
      throw new Error("Expected broker-required branch");
    }
  });

  it("includes the target URL in the reason for operator guidance", () => {
    const url = "https://my-tunnel.ngrok.io";
    const result = suggestBrokerFromProbeFail(url, false);
    expect(result?.reason).toContain(url);
  });
});
