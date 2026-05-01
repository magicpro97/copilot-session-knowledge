import "@testing-library/jest-dom";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import InsightsLayout from "@/app/insights/layout";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";

// ── Next.js ──────────────────────────────────────────────────────────────────
vi.mock("next/navigation", () => ({
  useRouter: vi.fn(() => ({ push: vi.fn() })),
}));

// ── API hooks ─────────────────────────────────────────────────────────────────
vi.mock("@/lib/api/hooks", () => ({
  useHealth: vi.fn(() => ({
    data: { status: "ok", schema_version: 5, sessions: 42 },
    isLoading: false,
    isError: false,
  })),
}));

// ── Tab components (lightweight stubs) ───────────────────────────────────────
vi.mock("@/app/insights/knowledge-tab", () => ({
  KnowledgeTab: () => <div data-testid="knowledge-tab-content">Knowledge tab content</div>,
}));
vi.mock("@/app/insights/retro-tab", () => ({
  RetroTab: () => <div data-testid="retro-tab-content">Retro tab content</div>,
}));
vi.mock("@/app/insights/search-quality-tab", () => ({
  SearchQualityTab: () => (
    <div data-testid="search-quality-tab-content">Search quality tab content</div>
  ),
}));
vi.mock("@/app/insights/live-tab", () => ({
  LiveTab: ({ active }: { active: boolean }) => (
    <div data-testid="live-tab-content" data-active={String(active)}>
      Live feed content
    </div>
  ),
}));

// ── Keyboard shortcuts (no-op in tests) ──────────────────────────────────────
vi.mock("@/hooks/use-keyboard-shortcuts", () => ({
  useKeyboardShortcuts: vi.fn(),
}));

// ── Lucide icons ─────────────────────────────────────────────────────────────
vi.mock("lucide-react", () => ({
  Activity: () => <span data-testid="icon-activity" />,
}));

const mockedUseKeyboardShortcuts = vi.mocked(useKeyboardShortcuts);

describe("InsightsLayout — tab navigation", () => {
  beforeEach(() => {
    mockedUseKeyboardShortcuts.mockReset();
    mockedUseKeyboardShortcuts.mockImplementation(() => {});
  });

  it("renders all five tab triggers", () => {
    render(<InsightsLayout>children</InsightsLayout>);
    expect(screen.getByRole("tab", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Knowledge" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Retro" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Search Quality" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Live feed" })).toBeInTheDocument();
  });

  it("uses vertical tab orientation for the menu list", () => {
    render(<InsightsLayout>children</InsightsLayout>);
    expect(screen.getByRole("tablist").closest("[data-orientation='vertical']")).not.toBeNull();
  });

  it("renders children (Overview content) by default", () => {
    render(<InsightsLayout>overview children</InsightsLayout>);
    expect(screen.getByText("overview children")).toBeInTheDocument();
  });

  it("shows Knowledge tab content when Knowledge tab is clicked", async () => {
    render(<InsightsLayout>children</InsightsLayout>);
    fireEvent.click(screen.getByRole("tab", { name: "Knowledge" }));
    expect(await screen.findByTestId("knowledge-tab-content")).toBeInTheDocument();
  });

  it("shows Retro tab content when Retro tab is clicked", async () => {
    render(<InsightsLayout>children</InsightsLayout>);
    fireEvent.click(screen.getByRole("tab", { name: "Retro" }));
    expect(await screen.findByTestId("retro-tab-content")).toBeInTheDocument();
  });

  it("shows Search Quality tab content when Search Quality tab is clicked", async () => {
    render(<InsightsLayout>children</InsightsLayout>);
    fireEvent.click(screen.getByRole("tab", { name: "Search Quality" }));
    expect(await screen.findByTestId("search-quality-tab-content")).toBeInTheDocument();
  });

  it("shows Live feed tab content when Live tab is clicked", async () => {
    render(<InsightsLayout>children</InsightsLayout>);
    fireEvent.click(screen.getByRole("tab", { name: "Live feed" }));
    expect(await screen.findByTestId("live-tab-content")).toBeInTheDocument();
  });

  it("passes active=true to LiveTab only when live tab is active", async () => {
    render(<InsightsLayout>children</InsightsLayout>);
    fireEvent.click(screen.getByRole("tab", { name: "Live feed" }));
    const liveEl = await screen.findByTestId("live-tab-content");
    expect(liveEl).toHaveAttribute("data-active", "true");
  });

  it("registers keyboard shortcuts for keys 1–5", () => {
    render(<InsightsLayout>children</InsightsLayout>);
    expect(mockedUseKeyboardShortcuts).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({ key: "1" }),
        expect.objectContaining({ key: "2" }),
        expect.objectContaining({ key: "3" }),
        expect.objectContaining({ key: "4" }),
        expect.objectContaining({ key: "5" }),
      ])
    );
  });

  it("keyboard shortcut key='2' handler switches to Knowledge tab", async () => {
    render(<InsightsLayout>children</InsightsLayout>);
    const [shortcuts] = mockedUseKeyboardShortcuts.mock.calls[0] as [
      Parameters<typeof useKeyboardShortcuts>[0],
    ];
    const shortcut = shortcuts.find((s) => s.key === "2");
    await act(async () => {
      shortcut?.handler(new KeyboardEvent("keydown"));
    });
    expect(await screen.findByTestId("knowledge-tab-content")).toBeInTheDocument();
  });

  it("keyboard shortcut key='3' handler switches to Retro tab", async () => {
    render(<InsightsLayout>children</InsightsLayout>);
    const [shortcuts] = mockedUseKeyboardShortcuts.mock.calls[0] as [
      Parameters<typeof useKeyboardShortcuts>[0],
    ];
    const shortcut = shortcuts.find((s) => s.key === "3");
    await act(async () => {
      shortcut?.handler(new KeyboardEvent("keydown"));
    });
    expect(await screen.findByTestId("retro-tab-content")).toBeInTheDocument();
  });

  it("keyboard shortcut key='4' handler switches to Search Quality tab", async () => {
    render(<InsightsLayout>children</InsightsLayout>);
    const [shortcuts] = mockedUseKeyboardShortcuts.mock.calls[0] as [
      Parameters<typeof useKeyboardShortcuts>[0],
    ];
    const shortcut = shortcuts.find((s) => s.key === "4");
    await act(async () => {
      shortcut?.handler(new KeyboardEvent("keydown"));
    });
    expect(await screen.findByTestId("search-quality-tab-content")).toBeInTheDocument();
  });

  it("keyboard shortcut key='5' handler switches to Live feed tab", async () => {
    render(<InsightsLayout>children</InsightsLayout>);
    const [shortcuts] = mockedUseKeyboardShortcuts.mock.calls[0] as [
      Parameters<typeof useKeyboardShortcuts>[0],
    ];
    const shortcut = shortcuts.find((s) => s.key === "5");
    await act(async () => {
      shortcut?.handler(new KeyboardEvent("keydown"));
    });
    expect(await screen.findByTestId("live-tab-content")).toBeInTheDocument();
  });

  it("keyboard shortcut key='1' handler returns to Overview tab", async () => {
    render(<InsightsLayout>overview content</InsightsLayout>);
    const [shortcuts] = mockedUseKeyboardShortcuts.mock.calls[0] as [
      Parameters<typeof useKeyboardShortcuts>[0],
    ];
    // Navigate away first
    fireEvent.click(screen.getByRole("tab", { name: "Knowledge" }));
    expect(await screen.findByTestId("knowledge-tab-content")).toBeInTheDocument();
    // Then use shortcut 1 to go back to Overview
    await act(async () => {
      shortcuts.find((s) => s.key === "1")?.handler(new KeyboardEvent("keydown"));
    });
    expect(await screen.findByText("overview content")).toBeInTheDocument();
  });
});

describe("InsightsLayout — hash-based deep-linking", () => {
  beforeEach(() => {
    mockedUseKeyboardShortcuts.mockReset();
    mockedUseKeyboardShortcuts.mockImplementation(() => {});
    window.location.hash = "";
    vi.spyOn(window.history, "replaceState").mockImplementation(() => undefined);
  });

  it("reads #knowledge hash on mount to activate Knowledge tab", () => {
    window.location.hash = "#knowledge";
    render(<InsightsLayout>children</InsightsLayout>);
    expect(screen.getByTestId("knowledge-tab-content")).toBeInTheDocument();
  });

  it("reads #retro hash on mount to activate Retro tab", () => {
    window.location.hash = "#retro";
    render(<InsightsLayout>children</InsightsLayout>);
    expect(screen.getByTestId("retro-tab-content")).toBeInTheDocument();
  });

  it("reads #overview hash on mount and stays on Overview tab", () => {
    window.location.hash = "#overview";
    render(<InsightsLayout>overview content</InsightsLayout>);
    expect(screen.getByText("overview content")).toBeInTheDocument();
  });

  it("ignores unknown hash values and defaults to overview", () => {
    window.location.hash = "#unknown-tab";
    render(<InsightsLayout>overview content</InsightsLayout>);
    expect(screen.getByText("overview content")).toBeInTheDocument();
  });

  it("calls window.history.replaceState with the active tab hash", () => {
    render(<InsightsLayout>children</InsightsLayout>);
    expect(window.history.replaceState).toHaveBeenCalledWith(null, "", "#overview");
  });

  it("updates hash when tab changes via click", async () => {
    render(<InsightsLayout>children</InsightsLayout>);
    fireEvent.click(screen.getByRole("tab", { name: "Knowledge" }));
    expect(window.history.replaceState).toHaveBeenCalledWith(null, "", "#knowledge");
  });
});

describe("InsightsLayout — health badge", () => {
  beforeEach(() => {
    mockedUseKeyboardShortcuts.mockReset();
    mockedUseKeyboardShortcuts.mockImplementation(() => {});
  });

  it("shows health status and session count", () => {
    render(<InsightsLayout>children</InsightsLayout>);
    expect(screen.getByText(/ok.*schema v5.*42 sessions/)).toBeInTheDocument();
  });
});
