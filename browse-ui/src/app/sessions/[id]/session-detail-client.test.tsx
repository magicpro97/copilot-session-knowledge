import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Mock } from "vitest";
import { LOCAL_HOST } from "@/lib/host-profiles";
import type { HostState } from "@/providers/host-provider";
import { SessionDetailClient } from "./session-detail-client";

// ── next/navigation ─────────────────────────────────────────────────────────
vi.mock("next/navigation", () => ({
  useParams: vi.fn(() => ({ id: "test-session-123" })),
  usePathname: vi.fn(() => "/sessions/test-session-123"),
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
}));

import { useOperatorRuns, useSessionDetail } from "@/lib/api/hooks";

let hostStateMock: HostState = { host: LOCAL_HOST, diagnosticsEnabled: true };
vi.mock("@/providers/host-provider", () => ({
  useHostState: vi.fn(() => hostStateMock),
}));

let sessionsSupported = true;
vi.mock("@/lib/hosts", () => ({
  useHostFeature: vi.fn(() => ({ supported: sessionsSupported, loading: false })),
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
  DebugLogTab: ({ runsLoading }: { runsLoading?: boolean }) => (
    <div data-testid="debug-log-tab" data-runs-loading={String(Boolean(runsLoading))}>
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
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };
    sessionsSupported = true;
    (useSessionDetail as Mock).mockImplementation(() => defaultSessionDetail);
    (useOperatorRuns as Mock).mockImplementation(() => ({
      data: { runs: [], count: 0 },
      error: null,
      isLoading: false,
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
