import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Mock } from "vitest";
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
}));

import { useSessionDetail } from "@/lib/api/hooks";

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
    (useSessionDetail as Mock).mockReturnValueOnce({
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
});
