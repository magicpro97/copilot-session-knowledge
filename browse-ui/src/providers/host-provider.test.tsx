import "@testing-library/jest-dom";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LOCAL_HOST, saveHostProfile, setSelectedHostId } from "@/lib/host-profiles";
import { HostProvider, useHostState } from "@/providers/host-provider";

// Reset the loopback probe negative cache between tests so state doesn't bleed.
vi.mock("@/lib/hosts/local-bootstrap", () => ({
  probeLocalBootstrap: vi.fn(async () => ({ status: "unavailable" })),
  resetLocalBootstrapCache: vi.fn(),
}));

let pathnameMock = "/chat";

vi.mock("next/navigation", () => ({
  usePathname: vi.fn(() => pathnameMock),
}));

function HostStateProbe() {
  const { host, diagnosticsEnabled } = useHostState();
  return (
    <>
      <div data-testid="host-id">{host.id}</div>
      <div data-testid="diagnostics-enabled">{String(diagnosticsEnabled)}</div>
    </>
  );
}

describe("HostProvider", () => {
  beforeEach(() => {
    pathnameMock = "/chat";
    localStorage.clear();
    window.history.pushState({}, "", "/chat");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("enables diagnostics for LOCAL_HOST when same-origin health probe succeeds", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
      }))
    );

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    expect(screen.getByTestId("host-id")).toHaveTextContent(LOCAL_HOST.id);
    await waitFor(() => {
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });
  });

  it("keeps diagnostics disabled for LOCAL_HOST when same-origin health probe fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
      }))
    );

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    expect(screen.getByTestId("host-id")).toHaveTextContent(LOCAL_HOST.id);
    await waitFor(() => {
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("false");
    });
  });

  it("does not overwrite a later remote-host selection when the local health probe resolves", async () => {
    let resolveFetch: ((value: { ok: boolean }) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<{ ok: boolean }>((resolve) => {
            resolveFetch = resolve;
          })
      )
    );

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    expect(screen.getByTestId("host-id")).toHaveTextContent(LOCAL_HOST.id);

    act(() => {
      saveHostProfile({
        id: "remote-a",
        label: "Remote A",
        base_url: "https://agent.example.com",
        token: "secret",
        cli_kind: "copilot",
        is_default: false,
      });
      setSelectedHostId("remote-a");
    });

    await waitFor(() => {
      expect(screen.getByTestId("host-id")).toHaveTextContent("remote-a");
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });

    await act(async () => {
      resolveFetch?.({ ok: true });
    });

    await waitFor(() => {
      expect(screen.getByTestId("host-id")).toHaveTextContent("remote-a");
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });
  });

  it("does not fetch /healthz for LOCAL_HOST when served from a hosted (non-local) origin", async () => {
    const { probeLocalBootstrap } = await import("@/lib/hosts/local-bootstrap");
    vi.mocked(probeLocalBootstrap).mockResolvedValue({ status: "unavailable" });

    // Simulate a hosted static origin — no same-origin backend.
    vi.stubGlobal("location", {
      ...window.location,
      origin: "https://agents-linhngo-dev.web.app",
      protocol: "https:",
      hostname: "agents-linhngo-dev.web.app",
      host: "agents-linhngo-dev.web.app",
      href: "https://agents-linhngo-dev.web.app/chat/",
    });

    // Any fetch call should not be the same-origin /healthz probe.
    const fetchSpy = vi.fn(async () => ({ ok: false }));
    vi.stubGlobal("fetch", fetchSpy);

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    // Flush all microtasks/effects.
    await act(async () => {});

    // No same-origin /healthz probe must have been issued.
    const healthzCalls = (fetchSpy.mock.calls as unknown as Array<[unknown, unknown?]>).filter(
      ([url]) => String(url).includes("/healthz")
    );
    expect(healthzCalls).toHaveLength(0);

    // Diagnostics must remain disabled (probe returned unavailable).
    expect(screen.getByTestId("host-id")).toHaveTextContent(LOCAL_HOST.id);
    expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("false");
  });

  it("does fetch /healthz for LOCAL_HOST when served from a real local origin", async () => {
    // jsdom default origin is http://localhost — real local dev scenario.
    const fetchSpy = vi.fn(async () => ({ ok: true }));
    vi.stubGlobal("fetch", fetchSpy);

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });

    // The /healthz probe must have been issued exactly once.
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy).toHaveBeenCalledWith(
      "/healthz",
      expect.objectContaining({ cache: "no-store" })
    );
  });

  it("enables diagnostics for an HTTP loopback host explicitly selected when the control plane is HTTPS (pna-required)", async () => {
    // Simulate being served from a hosted HTTPS origin (e.g. GitHub Pages, Vercel).
    vi.stubGlobal("location", {
      ...window.location,
      origin: "https://browse.example.com",
      protocol: "https:",
      hostname: "browse.example.com",
      host: "browse.example.com",
      href: "https://browse.example.com/chat",
    });

    act(() => {
      saveHostProfile({
        id: "loopback-host",
        label: "Local Copilot (loopback)",
        base_url: "http://localhost:8792",
        token: "tok",
        cli_kind: "copilot",
        is_default: false,
      });
      setSelectedHostId("loopback-host");
    });

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("host-id")).toHaveTextContent("loopback-host");
      // pna-required: compatible: true → isOperatorHostEnabled returns true.
      // The browser may or may not actually succeed; the provider defers to the
      // browser's PNA behavior.
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });
  });

  it("keeps diagnostics enabled for an HTTP loopback host when the control plane is also HTTP", async () => {
    // jsdom default origin is http://localhost — local dev scenario; loopback is reachable.
    act(() => {
      saveHostProfile({
        id: "local-agent",
        label: "Local Agent",
        base_url: "http://localhost:8792",
        token: "tok",
        cli_kind: "copilot",
        is_default: false,
      });
      setSelectedHostId("local-agent");
    });

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("host-id")).toHaveTextContent("local-agent");
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });
  });
});

// ── Loopback bootstrap detection (issue #49) ──────────────────────────────────

describe("HostProvider — loopback bootstrap detection", () => {
  beforeEach(async () => {
    pathnameMock = "/chat";
    localStorage.clear();
    window.history.pushState({}, "", "/chat");
    // Use a hosted origin so the loopback path is triggered.
    vi.stubGlobal("location", {
      ...window.location,
      origin: "https://agents.example.com",
      protocol: "https:",
      hostname: "agents.example.com",
      host: "agents.example.com",
      href: "https://agents.example.com/chat",
    });
    // Clear call history between tests so expectations aren't contaminated.
    const { probeLocalBootstrap } = await import("@/lib/hosts/local-bootstrap");
    vi.mocked(probeLocalBootstrap).mockClear();
    vi.mocked(probeLocalBootstrap).mockResolvedValue({ status: "unavailable" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("activates an ephemeral loopback host when probe detects a local backend", async () => {
    const { probeLocalBootstrap } = await import("@/lib/hosts/local-bootstrap");
    vi.mocked(probeLocalBootstrap).mockResolvedValueOnce({
      status: "detected",
      url: "http://127.0.0.1:8765",
      response: {
        schema: "browse-host/1",
        status: "ok",
        auth: "open",
        manual_token_required: false,
        capabilities: ["chat"],
        cors_origins_configured: true,
      },
    });

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("host-id")).toHaveTextContent("local-bootstrap");
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });
  });

  it("keeps an auto-detected loopback host active across route changes", async () => {
    const { probeLocalBootstrap } = await import("@/lib/hosts/local-bootstrap");
    vi.mocked(probeLocalBootstrap).mockResolvedValueOnce({
      status: "detected",
      url: "http://127.0.0.1:8765",
      response: {
        schema: "browse-host/1",
        status: "ok",
        auth: "open",
        manual_token_required: false,
        capabilities: ["chat"],
        cors_origins_configured: true,
      },
    });

    const { rerender } = render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId("host-id")).toHaveTextContent("local-bootstrap");
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });

    await act(async () => {
      pathnameMock = "/sessions";
      rerender(
        <HostProvider>
          <HostStateProbe />
        </HostProvider>
      );
    });

    expect(screen.getByTestId("host-id")).toHaveTextContent("local-bootstrap");
    expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
  });

  it("applies an in-flight loopback probe result after a route change", async () => {
    const { probeLocalBootstrap } = await import("@/lib/hosts/local-bootstrap");
    let resolveProbe!: (v: Awaited<ReturnType<typeof probeLocalBootstrap>>) => void;
    vi.mocked(probeLocalBootstrap).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveProbe = resolve;
      })
    );

    const { rerender } = render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    await act(async () => {
      pathnameMock = "/sessions";
      rerender(
        <HostProvider>
          <HostStateProbe />
        </HostProvider>
      );
    });

    await act(async () => {
      resolveProbe({
        status: "detected",
        url: "http://127.0.0.1:8765",
        response: {
          schema: "browse-host/1",
          status: "ok",
          auth: "open",
          manual_token_required: false,
          capabilities: ["chat"],
          cors_origins_configured: true,
        },
      });
    });

    await waitFor(() => {
      expect(screen.getByTestId("host-id")).toHaveTextContent("local-bootstrap");
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });
  });

  it("keeps diagnostics disabled when probe returns unavailable", async () => {
    const { probeLocalBootstrap } = await import("@/lib/hosts/local-bootstrap");
    vi.mocked(probeLocalBootstrap).mockResolvedValueOnce({ status: "unavailable" });

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    await act(async () => {});

    expect(screen.getByTestId("host-id")).toHaveTextContent(LOCAL_HOST.id);
    expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("false");
  });

  it("keeps diagnostics disabled when probe returns auth-required", async () => {
    const { probeLocalBootstrap } = await import("@/lib/hosts/local-bootstrap");
    vi.mocked(probeLocalBootstrap).mockResolvedValueOnce({
      status: "auth-required",
      url: "http://127.0.0.1:8765",
      response: {
        schema: "browse-host/1",
        status: "ok",
        auth: "token",
        manual_token_required: true,
        capabilities: ["chat"],
        cors_origins_configured: true,
      },
    });

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    await act(async () => {});

    // auth-required: do not auto-activate; user must supply token manually.
    expect(screen.getByTestId("host-id")).toHaveTextContent(LOCAL_HOST.id);
    expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("false");
  });

  it("does NOT override an explicit remote host selection even when probe detects a local backend", async () => {
    const { probeLocalBootstrap } = await import("@/lib/hosts/local-bootstrap");
    // Probe will resolve after a delay — simulated via a controllable promise.
    let resolveProbe!: (v: Awaited<ReturnType<typeof probeLocalBootstrap>>) => void;
    vi.mocked(probeLocalBootstrap).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveProbe = resolve;
      })
    );

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    // While probe is in-flight, user explicitly selects a remote host.
    await act(async () => {
      saveHostProfile({
        id: "remote-explicit",
        label: "Remote Explicit",
        base_url: "https://agent.example.com",
        token: "tok",
        cli_kind: "copilot",
        is_default: false,
      });
      setSelectedHostId("remote-explicit");
    });

    await waitFor(() => {
      expect(screen.getByTestId("host-id")).toHaveTextContent("remote-explicit");
    });

    // Now the probe resolves with a detected backend.
    await act(async () => {
      resolveProbe({
        status: "detected",
        url: "http://127.0.0.1:8765",
        response: {
          schema: "browse-host/1",
          status: "ok",
          auth: "open",
          manual_token_required: false,
          capabilities: ["chat"],
          cors_origins_configured: true,
        },
      });
    });

    // Explicit selection must NOT be overridden.
    await waitFor(() => {
      expect(screen.getByTestId("host-id")).toHaveTextContent("remote-explicit");
      expect(screen.getByTestId("diagnostics-enabled")).toHaveTextContent("true");
    });
  });

  it("does NOT probe loopback when an explicit remote host is already selected", async () => {
    const { probeLocalBootstrap } = await import("@/lib/hosts/local-bootstrap");

    // Pre-select a remote host before rendering.
    act(() => {
      saveHostProfile({
        id: "pre-selected",
        label: "Pre-selected",
        base_url: "https://pre.example.com",
        token: "",
        cli_kind: "copilot",
        is_default: false,
      });
      setSelectedHostId("pre-selected");
    });

    render(
      <HostProvider>
        <HostStateProbe />
      </HostProvider>
    );

    await act(async () => {});

    expect(probeLocalBootstrap).not.toHaveBeenCalled();
    expect(screen.getByTestId("host-id")).toHaveTextContent("pre-selected");
  });
});
