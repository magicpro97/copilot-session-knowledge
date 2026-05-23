import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Mock } from "vitest";
import { LOCAL_HOST } from "@/lib/host-profiles";
import type { HostState } from "@/providers/host-provider";
import { SessionDetailClient } from "./session-detail-client";

// ── next/navigation ─────────────────────────────────────────────────────────
const mockRouterPush = vi.fn();
vi.mock("next/navigation", () => ({
  useParams: vi.fn(() => ({ id: "test-session-123" })),
  usePathname: vi.fn(() => "/sessions/test-session-123"),
  useRouter: vi.fn(() => ({ push: mockRouterPush })),
}));

// ── api hooks ────────────────────────────────────────────────────────────────
vi.mock("@/lib/api/hooks", () => ({
  useSessionDetail: vi.fn(() => ({
    data: {
      meta: {
        summary: "A short session summary",
        source: "copilot",
        event_count_estimate: 42,
        fts_indexed_at: "2024-01-01T00:00:00Z",
      },
      timeline: [],
    },
    error: null,
    isLoading: false,
  })),
  useOperatorRuns: vi.fn(() => ({
    data: { runs: [], count: 0 },
    error: null,
    isLoading: false,
  })),
  useCliSession: vi.fn(() => ({
    data: null,
    isLoading: false,
    isError: false,
  })),
  useAdoptCliSession: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
  })),
}));

import {
  useOperatorRuns,
  useSessionDetail,
  useCliSession,
  useAdoptCliSession,
} from "@/lib/api/hooks";

let hostStateMock: HostState = { host: LOCAL_HOST, diagnosticsEnabled: true };
vi.mock("@/providers/host-provider", () => ({
  useHostState: vi.fn(() => hostStateMock),
}));

let sessionsSupported = true;
let cliAdoptSupported = false; // default: CTA hidden unless test opts-in
vi.mock("@/lib/hosts", () => ({
  useHostFeature: vi.fn((host: unknown, feature: string) => {
    if (feature === "sessions") return { supported: sessionsSupported, loading: false };
    if (feature === "cli_adopt") return { supported: cliAdoptSupported, loading: false };
    return { supported: true, loading: false };
  }),
}));

const defaultSessionDetail = {
  data: {
    meta: {
      summary: "A short session summary",
      source: "copilot",
      event_count_estimate: 42,
      fts_indexed_at: "2024-01-01T00:00:00Z",
    },
    timeline: [],
    has_operator_runs: false,
  },
  error: null,
  isLoading: false,
};

// ── heavy tab content ────────────────────────────────────────────────────────
vi.mock("./overview-tab", () => ({
  OverviewTab: () => <div data-testid="overview-tab">Overview content</div>,
}));
vi.mock("./timeline-tab", () => ({
  TimelineTab: () => <div data-testid="timeline-tab">Timeline content</div>,
}));
vi.mock("./mindmap-tab", () => ({
  MindmapTab: () => <div data-testid="mindmap-tab">Mindmap content</div>,
}));
vi.mock("./checkpoints-tab", () => ({
  CheckpointsTab: () => <div data-testid="checkpoints-tab">Checkpoints content</div>,
}));
vi.mock("./debug-log-tab", () => ({
  DebugLogTab: ({
    runsLoading,
    noRunEmptyState,
  }: {
    runsLoading?: boolean;
    noRunEmptyState?: string | null;
  }) => (
    <div
      data-testid="debug-log-tab"
      data-runs-loading={String(Boolean(runsLoading))}
      data-no-run-empty-state={noRunEmptyState ?? "null"}
    >
      DebugLog content
    </div>
  ),
}));

// ── compare sheet ────────────────────────────────────────────────────────────
vi.mock("@/components/data/compare-sheet", () => ({
  CompareSheet: ({ open }: { open: boolean }) =>
    open ? <div data-testid="compare-sheet">Compare</div> : null,
}));

// ── keyboard shortcuts (no-op in tests) ─────────────────────────────────────
vi.mock("@/hooks/use-keyboard-shortcuts", () => ({
  useKeyboardShortcuts: vi.fn(),
}));

// ── lucide icons (avoid canvas issues in jsdom) ──────────────────────────────
vi.mock("lucide-react", () => ({
  Download: () => <span data-testid="icon-download" />,
  GitCompare: () => <span data-testid="icon-compare" />,
  Loader2: () => <span data-testid="icon-loader" />,
  ServerCog: () => <span data-testid="icon-server-cog" />,
  Terminal: () => <span data-testid="icon-terminal" />,
}));

// ── breadcrumbs / banner ─────────────────────────────────────────────────────
vi.mock("@/components/layout/breadcrumbs", () => ({
  Breadcrumbs: () => <nav data-testid="breadcrumbs" />,
}));
vi.mock("@/components/data/banner", () => ({
  Banner: ({ title }: { title: string }) => <div data-testid="banner">{title}</div>,
}));
vi.mock("@/components/data/session-badges", () => ({
  SourceBadge: () => <span data-testid="source-badge" />,
  TimeRelative: () => <span data-testid="time-relative" />,
}));

// ── window.history shim ──────────────────────────────────────────────────────
const replaceState = vi.spyOn(window.history, "replaceState").mockImplementation(() => {});

describe("SessionDetailClient – layout/nav", () => {
  beforeEach(() => {
    replaceState.mockClear();
    mockRouterPush.mockClear();
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };
    sessionsSupported = true;
    cliAdoptSupported = false;
    (useSessionDetail as Mock).mockImplementation(() => defaultSessionDetail);
    (useOperatorRuns as Mock).mockImplementation(() => ({
      data: { runs: [], count: 0 },
      error: null,
      isLoading: false,
    }));
    (useCliSession as Mock).mockImplementation(() => ({
      data: null,
      isLoading: false,
      isError: false,
    }));
    (useAdoptCliSession as Mock).mockImplementation(() => ({
      mutate: vi.fn(),
      isPending: false,
    }));
    // Reset hash
    Object.defineProperty(window, "location", {
      writable: true,
      value: { ...window.location, hash: "", href: "http://localhost/sessions/test-session-123" },
    });
  });

  it("renders all four tab triggers", () => {
    render(<SessionDetailClient />);
    expect(screen.getByRole("tab", { name: /overview/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /timeline/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /mindmap/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /checkpoints/i })).toBeInTheDocument();
  });

  it("tab nav wrapper carries sticky positioning classes", () => {
    render(<SessionDetailClient />);
    const tabTrigger = screen.getByRole("tab", { name: /overview/i });
    // Walk up to the sticky wrapper div
    const stickyWrapper = tabTrigger.closest('[class*="sticky"]');
    expect(stickyWrapper).toBeInTheDocument();
    expect(stickyWrapper).toHaveClass("sticky");
    expect(stickyWrapper).toHaveClass("top-0");
    expect(stickyWrapper).toHaveClass("z-10");
  });

  it("tab nav wrapper has overflow-x-auto for narrow-width scrollability", () => {
    render(<SessionDetailClient />);
    const tabTrigger = screen.getByRole("tab", { name: /overview/i });
    const stickyWrapper = tabTrigger.closest('[class*="sticky"]');
    expect(stickyWrapper).toHaveClass("overflow-x-auto");
  });

  it("header CardTitle clamps long summaries (line-clamp-2 applied)", () => {
    (useSessionDetail as Mock).mockReturnValue({
      data: {
        meta: {
          summary: "A".repeat(300),
          source: "copilot",
          event_count_estimate: 1,
          fts_indexed_at: null,
        },
        timeline: [],
      },
      error: null,
      isLoading: false,
    });

    render(<SessionDetailClient />);
    // The CardTitle should carry line-clamp-2
    const title = screen.getByText("A".repeat(300));
    expect(title).toHaveClass("line-clamp-2");
  });

  it("Export and Compare buttons are rendered in the header", () => {
    render(<SessionDetailClient />);
    expect(screen.getByRole("button", { name: /export/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /compare/i })).toBeInTheDocument();
  });

  it("clicking Timeline tab switches active panel", () => {
    render(<SessionDetailClient />);
    const timelineTrigger = screen.getByRole("tab", { name: /timeline/i });
    fireEvent.click(timelineTrigger);
    expect(screen.getByTestId("timeline-tab")).toBeVisible();
  });

  it("clicking Mindmap tab switches active panel", () => {
    render(<SessionDetailClient />);
    fireEvent.click(screen.getByRole("tab", { name: /mindmap/i }));
    expect(screen.getByTestId("mindmap-tab")).toBeVisible();
  });

  it("clicking Checkpoints tab switches active panel", () => {
    render(<SessionDetailClient />);
    fireEvent.click(screen.getByRole("tab", { name: /checkpoints/i }));
    expect(screen.getByTestId("checkpoints-tab")).toBeVisible();
  });

  it("only enables operator run lookup on the Debug Log tab", () => {
    // Default fixture: knowledge-only session (has_operator_runs=false).
    // The gating must keep enabled=false even when Debug Log is active.
    render(<SessionDetailClient />);
    expect((useOperatorRuns as Mock).mock.calls.at(-1)).toEqual([
      "test-session-123",
      false,
      LOCAL_HOST,
    ]);

    fireEvent.click(screen.getByRole("tab", { name: /debug log/i }));

    // Knowledge-only: still disabled on Debug Log (issue #518 gate).
    expect((useOperatorRuns as Mock).mock.calls.at(-1)).toEqual([
      "test-session-123",
      false,
      LOCAL_HOST,
    ]);
  });

  it("enables operator run lookup on Debug Log only when has_operator_runs is true", () => {
    (useSessionDetail as Mock).mockImplementation(() => ({
      ...defaultSessionDetail,
      data: { ...defaultSessionDetail.data, has_operator_runs: true },
    }));

    render(<SessionDetailClient />);
    expect((useOperatorRuns as Mock).mock.calls.at(-1)).toEqual([
      "test-session-123",
      false,
      LOCAL_HOST,
    ]);

    fireEvent.click(screen.getByRole("tab", { name: /debug log/i }));

    expect((useOperatorRuns as Mock).mock.calls.at(-1)).toEqual([
      "test-session-123",
      true,
      LOCAL_HOST,
    ]);
  });

  it("keeps operator run lookup disabled on Debug Log for knowledge-only sessions", () => {
    // Explicit RED-fix proof for issue #518: even with Debug Log active and the
    // session detail loaded, has_operator_runs:false must suppress the request.
    (useSessionDetail as Mock).mockImplementation(() => ({
      ...defaultSessionDetail,
      data: { ...defaultSessionDetail.data, has_operator_runs: false },
    }));

    render(<SessionDetailClient />);
    fireEvent.click(screen.getByRole("tab", { name: /debug log/i }));

    expect((useOperatorRuns as Mock).mock.calls.at(-1)).toEqual([
      "test-session-123",
      false,
      LOCAL_HOST,
    ]);
  });

  it("passes operator run loading state to the Debug Log tab", () => {
    (useOperatorRuns as Mock).mockImplementation(() => ({
      data: undefined,
      error: null,
      isLoading: true,
    }));

    render(<SessionDetailClient />);
    fireEvent.click(screen.getByRole("tab", { name: /debug log/i }));

    expect(screen.getByTestId("debug-log-tab")).toHaveAttribute("data-runs-loading", "true");
  });

  it("updates URL hash when tab changes", () => {
    render(<SessionDetailClient />);
    fireEvent.click(screen.getByRole("tab", { name: /timeline/i }));
    expect(replaceState).toHaveBeenCalledWith(null, "", "#timeline");
  });

  it("hash in URL drives initial active tab", () => {
    Object.defineProperty(window, "location", {
      writable: true,
      value: {
        ...window.location,
        hash: "#mindmap",
        href: "http://localhost/sessions/test-session-123#mindmap",
      },
    });
    render(<SessionDetailClient />);
    expect(screen.getByRole("tab", { name: /mindmap/i })).toHaveAttribute("aria-selected", "true");
  });

  it("shows session summary in header title", () => {
    render(<SessionDetailClient />);
    expect(screen.getByText("A short session summary")).toBeInTheDocument();
  });

  it("keeps hosted root detail idle until a live host is available", () => {
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: false };

    render(<SessionDetailClient />);

    expect(screen.getByText("No agent host selected")).toBeInTheDocument();
    expect((useSessionDetail as Mock).mock.calls.at(-1)).toEqual([
      "test-session-123",
      false,
      LOCAL_HOST,
    ]);
  });

  it("passes the selected remote host to session detail queries", () => {
    const remoteHost = {
      id: "remote-host",
      label: "Remote host",
      base_url: "https://agent.example.test",
      token: "secret",
      cli_kind: "copilot" as const,
      is_default: false,
    };
    hostStateMock = { host: remoteHost, diagnosticsEnabled: true };

    render(<SessionDetailClient />);

    expect((useSessionDetail as Mock).mock.calls.at(-1)).toEqual([
      "test-session-123",
      true,
      remoteHost,
    ]);
  });

  it("keeps detail idle when the selected host lacks session support", () => {
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

    render(<SessionDetailClient />);

    expect(screen.getByText("Not supported by this host")).toBeInTheDocument();
    expect((useSessionDetail as Mock).mock.calls.at(-1)).toEqual([
      "test-session-123",
      false,
      remoteHost,
    ]);
  });
});

// ── CTA: Adopt in Chat (issue #530 Gap 1) ────────────────────────────────────

describe("SessionDetailClient — Adopt in Chat CTA", () => {
  beforeEach(() => {
    replaceState.mockClear();
    mockRouterPush.mockClear();
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };
    sessionsSupported = true;
    cliAdoptSupported = true;
    (useSessionDetail as Mock).mockImplementation(() => defaultSessionDetail);
    (useOperatorRuns as Mock).mockImplementation(() => ({
      data: { runs: [], count: 0 },
      error: null,
      isLoading: false,
    }));
    (useCliSession as Mock).mockImplementation(() => ({
      data: null,
      isLoading: false,
      isError: false,
    }));
    (useAdoptCliSession as Mock).mockImplementation(() => ({
      mutate: vi.fn(),
      isPending: false,
    }));
    Object.defineProperty(window, "location", {
      writable: true,
      value: { ...window.location, hash: "", href: "http://localhost/sessions/test-session-123" },
    });
  });

  it("does not show Adopt in Chat button when useCliSession returns no data", () => {
    render(<SessionDetailClient />);
    expect(screen.queryByTestId("adopt-in-chat-btn")).not.toBeInTheDocument();
  });

  it("shows Adopt in Chat button when useCliSession returns a session", () => {
    (useCliSession as Mock).mockImplementation(() => ({
      data: {
        cli_session_id: "cli-uuid-secret",
        title: "My CLI session",
        mtime: "2024-01-01T00:00:00Z",
      },
      isLoading: false,
      isError: false,
    }));

    render(<SessionDetailClient />);
    expect(screen.getByTestId("adopt-in-chat-btn")).toBeInTheDocument();
  });

  it("hides Adopt in Chat button when cli_adopt is not supported", () => {
    cliAdoptSupported = false;
    (useCliSession as Mock).mockImplementation(() => ({
      data: {
        cli_session_id: "cli-uuid-secret",
        title: "My CLI session",
        mtime: "2024-01-01T00:00:00Z",
      },
      isLoading: false,
      isError: false,
    }));

    render(<SessionDetailClient />);
    // Even if useCliSession returns data, if cli_adopt unsupported it won't be called
    // and the button should not appear.
    expect(screen.queryByTestId("adopt-in-chat-btn")).not.toBeInTheDocument();
  });

  it("clicking Adopt calls adoptMutation with cli_session_id in payload body, not URL", async () => {
    const mockMutate = vi.fn(
      (
        args: { payload: { cli_session_id: string }; host: unknown },
        opts: { onSuccess: (s: { id: string }) => void; onError: () => void }
      ) => {
        // Simulate success immediately
        opts.onSuccess({ id: "operator-session-456" });
      }
    );
    (useAdoptCliSession as Mock).mockImplementation(() => ({
      mutate: mockMutate,
      isPending: false,
    }));
    (useCliSession as Mock).mockImplementation(() => ({
      data: {
        cli_session_id: "cli-uuid-secret-789",
        title: "My CLI session",
        mtime: "2024-01-01T00:00:00Z",
      },
      isLoading: false,
      isError: false,
    }));

    render(<SessionDetailClient />);
    const btn = screen.getByTestId("adopt-in-chat-btn");
    fireEvent.click(btn);

    await waitFor(() => {
      expect(mockMutate).toHaveBeenCalledOnce();
      const [callArgs] = mockMutate.mock.calls[0];
      // CLI UUID is in the payload body only
      expect(callArgs.payload.cli_session_id).toBe("cli-uuid-secret-789");
    });
  });

  it("navigates to /chat?s=<operator_id> after successful adopt — CLI UUID not in URL", async () => {
    const mockMutate = vi.fn(
      (args: unknown, opts: { onSuccess: (s: { id: string }) => void; onError: () => void }) => {
        opts.onSuccess({ id: "operator-session-456" });
      }
    );
    (useAdoptCliSession as Mock).mockImplementation(() => ({
      mutate: mockMutate,
      isPending: false,
    }));
    (useCliSession as Mock).mockImplementation(() => ({
      data: {
        cli_session_id: "cli-uuid-must-not-appear",
        title: "My CLI session",
        mtime: "2024-01-01T00:00:00Z",
      },
      isLoading: false,
      isError: false,
    }));

    render(<SessionDetailClient />);
    fireEvent.click(screen.getByTestId("adopt-in-chat-btn"));

    await waitFor(() => {
      expect(mockRouterPush).toHaveBeenCalledOnce();
      const [navigatedUrl] = mockRouterPush.mock.calls[0] as [string];
      // Operator session id is in URL
      expect(navigatedUrl).toContain("s=operator-session-456");
      // CLI UUID must NEVER appear in the navigated URL
      expect(navigatedUrl).not.toContain("cli-uuid-must-not-appear");
      // No resume_target param
      expect(navigatedUrl).not.toContain("resume_target");
      // Starts with /chat
      expect(navigatedUrl.startsWith("/chat")).toBe(true);
    });
  });

  it("appends h=<host.id> for non-local hosts after adopt", async () => {
    const remoteHost = {
      id: "remote-host-99",
      label: "Remote",
      base_url: "https://agent.example.test",
      token: "secret",
      cli_kind: "copilot" as const,
      is_default: false,
    };
    hostStateMock = { host: remoteHost, diagnosticsEnabled: true };

    const mockMutate = vi.fn(
      (args: unknown, opts: { onSuccess: (s: { id: string }) => void; onError: () => void }) => {
        opts.onSuccess({ id: "op-remote-789" });
      }
    );
    (useAdoptCliSession as Mock).mockImplementation(() => ({
      mutate: mockMutate,
      isPending: false,
    }));
    (useCliSession as Mock).mockImplementation(() => ({
      data: {
        cli_session_id: "cli-remote-uuid",
        title: "Remote CLI session",
        mtime: "2024-01-01T00:00:00Z",
      },
      isLoading: false,
      isError: false,
    }));

    render(<SessionDetailClient />);
    fireEvent.click(screen.getByTestId("adopt-in-chat-btn"));

    await waitFor(() => {
      const [navigatedUrl] = mockRouterPush.mock.calls[0] as [string];
      expect(navigatedUrl).toContain("s=op-remote-789");
      expect(navigatedUrl).toContain("h=remote-host-99");
      // CLI UUID not in URL
      expect(navigatedUrl).not.toContain("cli-remote-uuid");
    });
  });

  it("does not add h= param for local host after adopt", async () => {
    const mockMutate = vi.fn(
      (args: unknown, opts: { onSuccess: (s: { id: string }) => void; onError: () => void }) => {
        opts.onSuccess({ id: "op-local-111" });
      }
    );
    (useAdoptCliSession as Mock).mockImplementation(() => ({
      mutate: mockMutate,
      isPending: false,
    }));
    (useCliSession as Mock).mockImplementation(() => ({
      data: {
        cli_session_id: "cli-local-uuid",
        title: "Local CLI session",
        mtime: "2024-01-01T00:00:00Z",
      },
      isLoading: false,
      isError: false,
    }));

    render(<SessionDetailClient />);
    fireEvent.click(screen.getByTestId("adopt-in-chat-btn"));

    await waitFor(() => {
      const [navigatedUrl] = mockRouterPush.mock.calls[0] as [string];
      expect(navigatedUrl).toContain("s=op-local-111");
      expect(navigatedUrl).not.toContain("h=");
    });
  });
});

// ── DebugLogTab noRunEmptyState wiring (issue #518 + Debug Log UX) ────────────

describe("SessionDetailClient — DebugLogTab noRunEmptyState wiring", () => {
  beforeEach(() => {
    replaceState.mockClear();
    mockRouterPush.mockClear();
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };
    sessionsSupported = true;
    cliAdoptSupported = true;
    (useSessionDetail as Mock).mockImplementation(() => defaultSessionDetail);
    (useOperatorRuns as Mock).mockImplementation(() => ({
      data: { runs: [], count: 0 },
      error: null,
      isLoading: false,
    }));
    (useCliSession as Mock).mockImplementation(() => ({
      data: null,
      isLoading: false,
      isError: false,
    }));
    (useAdoptCliSession as Mock).mockImplementation(() => ({
      mutate: vi.fn(),
      isPending: false,
    }));
    Object.defineProperty(window, "location", {
      writable: true,
      value: { ...window.location, hash: "", href: "http://localhost/sessions/test-session-123" },
    });
  });

  it("passes noRunEmptyState=cli-adoptable when has_operator_runs=false and session is adoptable", () => {
    cliAdoptSupported = true;
    (useCliSession as Mock).mockImplementation(() => ({
      data: { cli_session_id: "cli-uuid-abc", title: "CLI session", mtime: "2024-01-01T00:00:00Z" },
      isLoading: false,
      isError: false,
    }));

    render(<SessionDetailClient />);
    fireEvent.click(screen.getByRole("tab", { name: /debug log/i }));

    const tab = screen.getByTestId("debug-log-tab");
    expect(tab).toHaveAttribute("data-no-run-empty-state", "cli-adoptable");
  });

  it("passes noRunEmptyState=knowledge-only when has_operator_runs=false and session is not adoptable", () => {
    cliAdoptSupported = false;
    (useCliSession as Mock).mockImplementation(() => ({
      data: null,
      isLoading: false,
      isError: false,
    }));

    render(<SessionDetailClient />);
    fireEvent.click(screen.getByRole("tab", { name: /debug log/i }));

    const tab = screen.getByTestId("debug-log-tab");
    expect(tab).toHaveAttribute("data-no-run-empty-state", "knowledge-only");
  });

  it("passes noRunEmptyState=null when has_operator_runs=true", () => {
    (useSessionDetail as Mock).mockImplementation(() => ({
      ...defaultSessionDetail,
      data: { ...defaultSessionDetail.data, has_operator_runs: true },
    }));

    render(<SessionDetailClient />);
    fireEvent.click(screen.getByRole("tab", { name: /debug log/i }));

    const tab = screen.getByTestId("debug-log-tab");
    expect(tab).toHaveAttribute("data-no-run-empty-state", "null");
  });

  it("issue #518 guard: useOperatorRuns stays disabled on Debug Log for knowledge sessions (has_operator_runs=false)", () => {
    render(<SessionDetailClient />);
    fireEvent.click(screen.getByRole("tab", { name: /debug log/i }));

    // Must be called with enabled=false even on Debug Log tab.
    expect((useOperatorRuns as Mock).mock.calls.at(-1)).toEqual([
      "test-session-123",
      false,
      LOCAL_HOST,
    ]);
  });
});
