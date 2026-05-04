import "@testing-library/jest-dom";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import InsightsLayout from "@/app/insights/layout";
import { useInsightsTab } from "@/app/insights/insights-tab-context";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";
import {
  BROWSE_HOST_CHANGE_EVENT,
  LOCAL_HOST,
  LOCAL_HOST_ID,
  deleteHostProfile,
  saveHostProfile,
  setSelectedHostId,
} from "@/lib/host-profiles";
import type { HostState } from "@/providers/host-provider";

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

// ── Host provider — controlled via hostStateMock ──────────────────────────────
const hostStateMock: HostState = { host: LOCAL_HOST, diagnosticsEnabled: false };
vi.mock("@/providers/host-provider", () => ({
  useHostState: vi.fn(() => hostStateMock),
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
vi.mock("@/app/insights/workflow-tab", () => ({
  WorkflowTab: () => <div data-testid="workflow-tab-content">Workflow tab content</div>,
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
    // Enable diagnostics for tab navigation tests (simulates local /v2 serve).
    hostStateMock.host = LOCAL_HOST;
    hostStateMock.diagnosticsEnabled = true;
    window.history.pushState({}, "", "/v2/insights");
  });

  it("renders all six tab triggers", () => {
    render(<InsightsLayout>children</InsightsLayout>);
    expect(screen.getByRole("tab", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Knowledge" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Retro" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Search Quality" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Live feed" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Workflow" })).toBeInTheDocument();
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

  it("shows Workflow tab content when Workflow tab is clicked", async () => {
    render(<InsightsLayout>children</InsightsLayout>);
    fireEvent.click(screen.getByRole("tab", { name: "Workflow" }));
    expect(await screen.findByTestId("workflow-tab-content")).toBeInTheDocument();
  });

  it("passes active=true to LiveTab only when live tab is active", async () => {
    render(<InsightsLayout>children</InsightsLayout>);
    fireEvent.click(screen.getByRole("tab", { name: "Live feed" }));
    const liveEl = await screen.findByTestId("live-tab-content");
    expect(liveEl).toHaveAttribute("data-active", "true");
  });

  it("registers keyboard shortcuts for keys 1–6", () => {
    render(<InsightsLayout>children</InsightsLayout>);
    expect(mockedUseKeyboardShortcuts).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({ key: "1" }),
        expect.objectContaining({ key: "2" }),
        expect.objectContaining({ key: "3" }),
        expect.objectContaining({ key: "4" }),
        expect.objectContaining({ key: "5" }),
        expect.objectContaining({ key: "6" }),
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
    hostStateMock.host = LOCAL_HOST;
    hostStateMock.diagnosticsEnabled = true;
    window.history.pushState({}, "", "/v2/insights");
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

  it("reads #workflow hash on mount to activate Workflow tab", () => {
    window.location.hash = "#workflow";
    render(<InsightsLayout>children</InsightsLayout>);
    expect(screen.getByTestId("workflow-tab-content")).toBeInTheDocument();
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
    // Enable diagnostics so health data renders
    hostStateMock.host = LOCAL_HOST;
    hostStateMock.diagnosticsEnabled = true;
    window.history.pushState({}, "", "/v2/insights");
  });

  it("shows health status and session count", () => {
    render(<InsightsLayout>children</InsightsLayout>);
    expect(screen.getByText(/ok.*schema v5.*42 sessions/)).toBeInTheDocument();
  });
});

describe("InsightsLayout — hosted-safe diagnostics", () => {
  beforeEach(() => {
    mockedUseKeyboardShortcuts.mockReset();
    mockedUseKeyboardShortcuts.mockImplementation(() => {});
    // Simulate Firebase-hosted root (no /v2/ path, no remote host in storage)
    hostStateMock.host = LOCAL_HOST;
    hostStateMock.diagnosticsEnabled = false;
    window.history.pushState({}, "", "/insights");
    localStorage.clear();
  });

  it("shows 'select an agent host' guidance in health badge when no host is configured", () => {
    render(<InsightsLayout>children</InsightsLayout>);
    expect(screen.getByText("Health: select an agent host")).toBeInTheDocument();
  });

  it("does not show loading or error states when diagnostics are disabled", () => {
    render(<InsightsLayout>children</InsightsLayout>);
    expect(screen.queryByText("Health: loading…")).not.toBeInTheDocument();
    expect(screen.queryByText("Health: unavailable")).not.toBeInTheDocument();
  });

  it("still renders all tab triggers even when diagnostics are disabled", () => {
    render(<InsightsLayout>children</InsightsLayout>);
    expect(screen.getByRole("tab", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Knowledge" })).toBeInTheDocument();
  });
});

describe("InsightsLayout — context provides host and diagnosticsEnabled", () => {
  beforeEach(() => {
    mockedUseKeyboardShortcuts.mockReset();
    mockedUseKeyboardShortcuts.mockImplementation(() => {});
  });

  // Render a spy as the overview children; overview is always inside the context provider.
  function ContextSpy({ capture }: { capture: (v: ReturnType<typeof useInsightsTab>) => void }) {
    const ctx = useInsightsTab();
    capture(ctx);
    return null;
  }

  it("exposes diagnosticsEnabled=false and LOCAL_HOST when on hosted static root (no remote host)", () => {
    hostStateMock.host = LOCAL_HOST;
    hostStateMock.diagnosticsEnabled = false;

    let captured: ReturnType<typeof useInsightsTab> | undefined;
    render(
      <InsightsLayout>
        <ContextSpy capture={(v) => (captured = v)} />
      </InsightsLayout>
    );

    expect(captured?.diagnosticsEnabled).toBe(false);
    expect(captured?.host.id).toBe(LOCAL_HOST_ID);
  });

  it("exposes diagnosticsEnabled=true when on local /v2/ path", () => {
    hostStateMock.host = LOCAL_HOST;
    hostStateMock.diagnosticsEnabled = true;

    let captured: ReturnType<typeof useInsightsTab> | undefined;
    render(
      <InsightsLayout>
        <ContextSpy capture={(v) => (captured = v)} />
      </InsightsLayout>
    );

    expect(captured?.diagnosticsEnabled).toBe(true);
    expect(captured?.host.id).toBe(LOCAL_HOST_ID);
  });
});

// ── New tests: HostProvider persistence / default resolution / same-tab ──────

describe("host-profiles helpers — default-host semantics", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("getEffectiveHost returns LOCAL_HOST when storage is empty", async () => {
    const { getEffectiveHost } = await import("@/lib/host-profiles");
    expect(getEffectiveHost().id).toBe(LOCAL_HOST_ID);
  });

  it("getDefaultHost returns LOCAL_HOST when no remote profile is marked is_default", async () => {
    const { getDefaultHost } = await import("@/lib/host-profiles");
    saveHostProfile({
      id: "r1",
      label: "Remote",
      base_url: "https://r1.example.com",
      token: "",
      cli_kind: "copilot",
      is_default: false,
    });
    expect(getDefaultHost().id).toBe(LOCAL_HOST_ID);
  });

  it("getDefaultHost returns the is_default remote profile when one exists", async () => {
    const { getDefaultHost } = await import("@/lib/host-profiles");
    saveHostProfile({
      id: "r2",
      label: "Default Remote",
      base_url: "https://r2.example.com",
      token: "",
      cli_kind: "copilot",
      is_default: true,
    });
    expect(getDefaultHost().id).toBe("r2");
  });

  it("getEffectiveHost falls back to is_default profile when selected id is missing from storage", async () => {
    const { getEffectiveHost } = await import("@/lib/host-profiles");
    // Save a default-marked profile but do NOT call setSelectedHostId
    saveHostProfile({
      id: "r3",
      label: "Auto-default",
      base_url: "https://r3.example.com",
      token: "",
      cli_kind: "copilot",
      is_default: true,
    });
    expect(getEffectiveHost().id).toBe("r3");
  });

  it("getEffectiveHost keeps an explicit LOCAL_HOST selection even when a remote default exists", async () => {
    const { getEffectiveHost } = await import("@/lib/host-profiles");
    saveHostProfile({
      id: "r3-local",
      label: "Default Remote",
      base_url: "https://r3-local.example.com",
      token: "",
      cli_kind: "copilot",
      is_default: true,
    });
    setSelectedHostId(LOCAL_HOST_ID);
    expect(getEffectiveHost().id).toBe(LOCAL_HOST_ID);
  });

  it("setSelectedHostId dispatches BROWSE_HOST_CHANGE_EVENT in the same tab", async () => {
    saveHostProfile({
      id: "r4",
      label: "Remote 4",
      base_url: "https://r4.example.com",
      token: "",
      cli_kind: "copilot",
      is_default: false,
    });
    const handler = vi.fn();
    window.addEventListener(BROWSE_HOST_CHANGE_EVENT, handler);
    setSelectedHostId("r4");
    expect(handler).toHaveBeenCalledTimes(1);
    window.removeEventListener(BROWSE_HOST_CHANGE_EVENT, handler);
  });

  it("clearSelectedHostId dispatches BROWSE_HOST_CHANGE_EVENT in the same tab", async () => {
    const { clearSelectedHostId } = await import("@/lib/host-profiles");
    setSelectedHostId("r4");
    const handler = vi.fn();
    window.addEventListener(BROWSE_HOST_CHANGE_EVENT, handler);
    clearSelectedHostId();
    expect(handler).toHaveBeenCalledTimes(1);
    window.removeEventListener(BROWSE_HOST_CHANGE_EVENT, handler);
  });

  it("saveHostProfile dispatches BROWSE_HOST_CHANGE_EVENT in the same tab", () => {
    const handler = vi.fn();
    window.addEventListener(BROWSE_HOST_CHANGE_EVENT, handler);
    saveHostProfile({
      id: "r5",
      label: "Remote 5",
      base_url: "https://r5.example.com",
      token: "",
      cli_kind: "copilot",
      is_default: false,
    });
    expect(handler).toHaveBeenCalledTimes(1);
    window.removeEventListener(BROWSE_HOST_CHANGE_EVENT, handler);
  });

  it("deleteHostProfile dispatches BROWSE_HOST_CHANGE_EVENT and clears deleted default fallback", async () => {
    const { getEffectiveHost } = await import("@/lib/host-profiles");
    saveHostProfile({
      id: "r6",
      label: "Default Remote",
      base_url: "https://r6.example.com",
      token: "",
      cli_kind: "copilot",
      is_default: true,
    });
    expect(getEffectiveHost().id).toBe("r6");

    const handler = vi.fn();
    window.addEventListener(BROWSE_HOST_CHANGE_EVENT, handler);
    deleteHostProfile("r6");

    expect(getEffectiveHost().id).toBe(LOCAL_HOST_ID);
    expect(handler).toHaveBeenCalledTimes(1);
    window.removeEventListener(BROWSE_HOST_CHANGE_EVENT, handler);
  });

  it("replaceHostProfiles dispatches a single BROWSE_HOST_CHANGE_EVENT for batched updates", async () => {
    const { getDefaultHost, replaceHostProfiles } = await import("@/lib/host-profiles");
    saveHostProfile({
      id: "r7",
      label: "Remote 7",
      base_url: "https://r7.example.com",
      token: "",
      cli_kind: "copilot",
      is_default: false,
    });
    saveHostProfile({
      id: "r8",
      label: "Remote 8",
      base_url: "https://r8.example.com",
      token: "",
      cli_kind: "copilot",
      is_default: false,
    });

    const handler = vi.fn();
    window.addEventListener(BROWSE_HOST_CHANGE_EVENT, handler);

    replaceHostProfiles([
      {
        id: "r7",
        label: "Remote 7",
        base_url: "https://r7.example.com",
        token: "",
        cli_kind: "copilot",
        is_default: false,
      },
      {
        id: "r8",
        label: "Remote 8",
        base_url: "https://r8.example.com",
        token: "",
        cli_kind: "copilot",
        is_default: true,
      },
    ]);

    expect(handler).toHaveBeenCalledTimes(1);
    expect(getDefaultHost().id).toBe("r8");
    window.removeEventListener(BROWSE_HOST_CHANGE_EVENT, handler);
  });
});
