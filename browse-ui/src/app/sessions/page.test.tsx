import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
});
