import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import type { Mock } from "vitest";
import { LOCAL_HOST } from "@/lib/host-profiles";
import type { HostState } from "@/providers/host-provider";

vi.mock("next/navigation", () => ({
  useRouter: vi.fn(() => ({ push: vi.fn() })),
}));

vi.mock("@/lib/api/hooks", () => ({
  useSessions: vi.fn(() => ({
    data: {
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
      has_more: false,
    },
    isError: false,
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  })),
}));

vi.mock("@/hooks/use-keyboard-shortcuts", () => ({
  useKeyboardShortcuts: vi.fn(),
}));

let sessionsSupported = true;
vi.mock("@/lib/hosts", () => ({
  useHostFeature: vi.fn(() => ({ supported: sessionsSupported, loading: false })),
}));

let hostStateMock: HostState = { host: LOCAL_HOST, diagnosticsEnabled: true };
vi.mock("@/providers/host-provider", () => ({
  useHostState: vi.fn(() => hostStateMock),
}));

import { useSessions } from "@/lib/api/hooks";

const SessionsPage = (await import("@/app/sessions/page")).default;

describe("SessionsPage", () => {
  beforeEach(() => {
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };
    sessionsSupported = true;
    (useSessions as Mock).mockClear();
    (useSessions as Mock).mockReturnValue({
      data: {
        items: [],
        total: 0,
        page: 1,
        page_size: 20,
        has_more: false,
      },
      isError: false,
      isLoading: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    });
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps hosted root sessions idle until a live host is available", () => {
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: false };

    render(<SessionsPage />);

    expect(screen.getByText("No agent host selected")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Run the browse server locally or select a remote agent host in the header to load sessions."
      )
    ).toBeInTheDocument();
    expect((useSessions as Mock).mock.calls.at(-1)).toEqual([
      { page: 1, pageSize: 20, query: "" },
      LOCAL_HOST,
      false,
    ]);
  });

  it("loads sessions against the shared host when diagnostics are enabled", () => {
    const remoteHost = {
      id: "remote-host",
      label: "Remote host",
      base_url: "https://agent.example.test",
      token: "secret",
      cli_kind: "copilot" as const,
      is_default: false,
    };
    hostStateMock = { host: remoteHost, diagnosticsEnabled: true };

    render(<SessionsPage />);

    expect((useSessions as Mock).mock.calls.at(-1)).toEqual([
      { page: 1, pageSize: 20, query: "" },
      remoteHost,
      true,
    ]);
    expect(screen.queryByText("No agent host selected")).not.toBeInTheDocument();
  });

  it("does not fetch sessions when the selected host lacks session support", () => {
    const remoteHost = {
      id: "remote-host",
      label: "Remote host",
      base_url: "https://agent.example.test",
      token: "secret",
      cli_kind: "copilot" as const,
      is_default: false,
    };
    hostStateMock = { host: remoteHost, diagnosticsEnabled: true };
    sessionsSupported = false;

    render(<SessionsPage />);

    expect(screen.getByText("Not supported by this host")).toBeInTheDocument();
    expect((useSessions as Mock).mock.calls.at(-1)).toEqual([
      { page: 1, pageSize: 20, query: "" },
      remoteHost,
      false,
    ]);
  });

  // ── Hosted empty-state: DiagnosticPanel fallback guidance ─────────────────

  it("does not show DiagnosticPanel on local origin with no diagnostics", () => {
    // jsdom default origin is http://localhost — treated as local, not hosted.
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: false, probeResult: null };

    render(<SessionsPage />);

    // Empty state still shown
    expect(screen.getByText("No agent host selected")).toBeInTheDocument();
    // DiagnosticPanel returns null for local origins — no panel content
    expect(screen.queryByTestId("diagnostic-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("configured-browser-fallback")).not.toBeInTheDocument();
  });

  it("shows no-host-configured diagnostic panel on hosted origin with no probe result", () => {
    vi.stubGlobal("window", {
      ...window,
      location: { ...window.location, origin: "https://agents.linhngo.dev" },
    });
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: false, probeResult: null };

    render(<SessionsPage />);

    expect(screen.getByText("No agent host selected")).toBeInTheDocument();
    const panel = screen.getByTestId("diagnostic-panel");
    expect(panel).toBeInTheDocument();
    expect(panel).toHaveTextContent(/No agent host configured/);
    expect(panel).toHaveTextContent(/Settings → Hosts/);
  });

  it("shows hosted HTTPS-local-backend-required panel on hosted origin when all HTTPS probes return unknown", () => {
    vi.stubGlobal("window", {
      ...window,
      location: { ...window.location, origin: "https://agents.linhngo.dev" },
    });
    hostStateMock = {
      host: LOCAL_HOST,
      diagnosticsEnabled: false,
      probeResult: {
        status: "unavailable",
        reasons: [
          { url: "https://127.0.0.1:8765", reason: "network-error", daemonState: "unknown" },
          { url: "https://localhost:8765", reason: "network-error", daemonState: "unknown" },
        ],
      },
    };

    render(<SessionsPage />);

    const panel = screen.getByTestId("diagnostic-panel");
    expect(panel).toHaveAttribute(
      "aria-label",
      "Connectivity diagnostic: HTTPS local backend required"
    );
    expect(panel).toHaveTextContent(/mkcert/);
    expect(panel).toHaveTextContent(/ngrok http 8765/);
    expect(panel).toHaveTextContent(/127\.0\.0\.1:8765/);
  });

  it("shows configured-browser-fallback on hosted origin — Chrome/Edge (Chromium) is treated as supported", () => {
    vi.stubGlobal("window", {
      ...window,
      location: { ...window.location, origin: "https://agents.linhngo.dev" },
    });
    // Simulate Edge UA (Chromium-based, like the reporter)
    vi.stubGlobal("navigator", {
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    });
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: false, probeResult: null };

    render(<SessionsPage />);

    const fallback = screen.getByTestId("configured-browser-fallback");
    expect(fallback).toBeInTheDocument();

    // Shows HTTPS-first local app CTA plus HTTP fallback for no-TLS backends.
    const cta = screen.getByTestId("open-configured-browser-cta");
    expect(cta).toHaveAttribute("href", "https://127.0.0.1:8765/");
    expect(cta).toHaveAttribute("target", "_blank");
    expect(screen.getByTestId("open-configured-browser-http-fallback")).toHaveAttribute(
      "href",
      "http://127.0.0.1:8765/"
    );

    // Chromium (Chrome/Edge) is supported — actionable LNA/PNA message
    expect(fallback).toHaveTextContent(/Chrome\/Edge/i);

    // Scan command present
    expect(fallback).toHaveTextContent(/browse --list-browsers/);

    // Start command present — opens a configured browser without security bypass flags
    expect(fallback).toHaveTextContent(/browse --hosted-bootstrap --open-browser chrome/);
  });

  it("shows configured-browser-fallback on hosted origin — Safari is reported as unsupported", () => {
    vi.stubGlobal("window", {
      ...window,
      location: { ...window.location, origin: "https://agents.linhngo.dev" },
    });
    vi.stubGlobal("navigator", {
      userAgent:
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    });
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: false, probeResult: null };

    render(<SessionsPage />);

    const fallback = screen.getByTestId("configured-browser-fallback");
    expect(fallback).toBeInTheDocument();

    // Safari is explicitly marked as unsupported for hosted-to-local recovery
    expect(fallback).toHaveTextContent(/Safari is not supported/i);
    // Recommends Chrome/Edge as the workaround
    expect(fallback).toHaveTextContent(/Use Chrome\/Edge/i);
  });

  it("shows configured-browser-fallback with Firefox-specific message on hosted origin", () => {
    vi.stubGlobal("window", {
      ...window,
      location: { ...window.location, origin: "https://agents.linhngo.dev" },
    });
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    });
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: false, probeResult: null };

    render(<SessionsPage />);

    const fallback = screen.getByTestId("configured-browser-fallback");
    expect(fallback).toBeInTheDocument();
    // Firefox-specific guidance: use Chrome/Edge for hosted detection
    expect(fallback).toHaveTextContent(/Firefox/i);
    expect(fallback).toHaveTextContent(/Chrome\/Edge/i);
  });
});
