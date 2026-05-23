// src/lib/http/loopback.test.ts — unit tests for the loopback LNA helper.
import { describe, it, expect, beforeEach, afterEach } from "vitest";

import { isLoopbackUrl, withLoopbackHint } from "./loopback";

type WindowShape = { location: { protocol: string; origin?: string; href?: string } };

function setWindow(win: WindowShape | undefined): void {
  if (win === undefined) {
    // Remove window entirely to simulate SSR.
    delete (globalThis as { window?: unknown }).window;
    return;
  }
  Object.defineProperty(globalThis, "window", {
    value: win,
    writable: true,
    configurable: true,
  });
}

describe("isLoopbackUrl", () => {
  it("returns true for localhost", () => {
    expect(isLoopbackUrl("http://localhost/")).toBe(true);
    expect(isLoopbackUrl("https://localhost:8765/api/sessions")).toBe(true);
  });

  it("returns true for 127.0.0.0/8 IPv4", () => {
    expect(isLoopbackUrl("http://127.0.0.1:8765/")).toBe(true);
    expect(isLoopbackUrl("https://127.0.0.1:8765/api/operator/capabilities")).toBe(true);
    expect(isLoopbackUrl("http://127.255.0.1/")).toBe(true);
    expect(isLoopbackUrl("http://127.1.2.3:9000/")).toBe(true);
  });

  it("returns true for IPv6 ::1 (bracketed)", () => {
    expect(isLoopbackUrl("http://[::1]:8765/")).toBe(true);
    expect(isLoopbackUrl("https://[::1]/api/sessions")).toBe(true);
  });

  it("returns false for non-loopback hosts", () => {
    expect(isLoopbackUrl("https://agents.linhngo.dev/")).toBe(false);
    expect(isLoopbackUrl("https://xyz.ngrok.io/api/sessions")).toBe(false);
    expect(isLoopbackUrl("http://10.0.0.1/")).toBe(false);
    expect(isLoopbackUrl("http://192.168.1.1/")).toBe(false);
    expect(isLoopbackUrl("http://126.0.0.1/")).toBe(false);
    expect(isLoopbackUrl("http://128.0.0.1/")).toBe(false);
  });

  it("returns false for spoofed hostnames", () => {
    expect(isLoopbackUrl("http://127.0.0.1.evil.com/")).toBe(false);
    expect(isLoopbackUrl("https://localhost.evil.com/")).toBe(false);
    expect(isLoopbackUrl("http://evil.com/?host=127.0.0.1")).toBe(false);
    expect(isLoopbackUrl("http://evil.com#127.0.0.1")).toBe(false);
    // userinfo trick: actual host is evil.com, not 127.0.0.1
    expect(isLoopbackUrl("http://127.0.0.1@evil.com/")).toBe(false);
    // partial-quad / non-quad forms not accepted (note: WHATWG URL normalizes
    // shorthand IPv4 like `127.0.1` → `127.0.0.1`, so those are legitimately
    // loopback and not tested here).
    expect(isLoopbackUrl("http://127.0.0.1.0/")).toBe(false);
  });

  it("returns false for invalid URLs", () => {
    expect(isLoopbackUrl("not a url")).toBe(false);
    expect(isLoopbackUrl("")).toBe(false);
  });

  it("accepts URL instances", () => {
    expect(isLoopbackUrl(new URL("https://127.0.0.1:8765/x"))).toBe(true);
    expect(isLoopbackUrl(new URL("https://example.com/"))).toBe(false);
  });
});

describe("withLoopbackHint", () => {
  beforeEach(() => {
    setWindow({ location: { protocol: "https:", origin: "https://agents.linhngo.dev" } });
  });

  afterEach(() => {
    setWindow({ location: { protocol: "http:", origin: "http://localhost" } });
  });

  it("adds targetAddressSpace=loopback for loopback target under https page", () => {
    const init = withLoopbackHint("https://127.0.0.1:8765/api/sessions");
    expect((init as { targetAddressSpace?: string }).targetAddressSpace).toBe("loopback");
  });

  it("preserves all existing init fields", () => {
    const headers = new Headers({ Authorization: "Bearer abc" });
    const signal = new AbortController().signal;
    const original: RequestInit = {
      method: "POST",
      headers,
      body: '{"x":1}',
      signal,
      cache: "no-store",
      credentials: "include",
    };

    const out = withLoopbackHint("https://127.0.0.1:8765/api/sessions", original);

    expect(out.method).toBe("POST");
    expect(out.headers).toBe(headers);
    expect(out.body).toBe('{"x":1}');
    expect(out.signal).toBe(signal);
    expect(out.cache).toBe("no-store");
    expect(out.credentials).toBe("include");
    expect((out as { targetAddressSpace?: string }).targetAddressSpace).toBe("loopback");
    // Should not mutate the caller's object.
    expect((original as { targetAddressSpace?: string }).targetAddressSpace).toBeUndefined();
  });

  it("does NOT add the hint for non-loopback targets", () => {
    const out = withLoopbackHint("https://agents.linhngo.dev/api/sessions", { method: "GET" });
    expect((out as { targetAddressSpace?: string }).targetAddressSpace).toBeUndefined();
    expect(out.method).toBe("GET");
  });

  it("does NOT add the hint when the page origin is http://", () => {
    setWindow({ location: { protocol: "http:", origin: "http://localhost" } });
    const out = withLoopbackHint("http://127.0.0.1:8765/api/sessions");
    expect((out as { targetAddressSpace?: string }).targetAddressSpace).toBeUndefined();
  });

  it("does NOT add the hint in non-browser (SSR) contexts", () => {
    setWindow(undefined);
    const out = withLoopbackHint("https://127.0.0.1:8765/api/sessions");
    expect((out as { targetAddressSpace?: string }).targetAddressSpace).toBeUndefined();
  });

  it("does NOT add the hint for spoofed loopback-looking hosts", () => {
    const out = withLoopbackHint("https://127.0.0.1.evil.com/api/sessions");
    expect((out as { targetAddressSpace?: string }).targetAddressSpace).toBeUndefined();
  });

  it("returns a fresh object when init is undefined", () => {
    const out = withLoopbackHint("https://127.0.0.1:8765/x");
    expect((out as { targetAddressSpace?: string }).targetAddressSpace).toBe("loopback");
  });
});
