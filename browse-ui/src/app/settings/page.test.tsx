import "@testing-library/jest-dom";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useHealth,
  useScoutStatus,
  useSkillMetrics,
  useSyncStatus,
  useTentacleStatus,
} from "@/lib/api/hooks";
import type { TentacleStatusResponse } from "@/lib/api/types";
import { LOCAL_HOST } from "@/lib/host-profiles";
import { useKeyboardPlatform } from "@/hooks/use-keyboard-platform";
import type { HostState } from "@/providers/host-provider";

vi.mock("@/lib/api/hooks", () => ({
  useHealth: vi.fn(),
  useScoutStatus: vi.fn(),
  useSkillMetrics: vi.fn(),
  useSyncStatus: vi.fn(),
  useTentacleStatus: vi.fn(),
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "system", setTheme: vi.fn() }),
}));

vi.mock("@/hooks/use-density", () => ({
  useDensity: () => ["compact", vi.fn()],
}));

// Mock the shared host-provider so settings page tests control diagnosticsEnabled.
let hostStateMock: HostState = { host: LOCAL_HOST, diagnosticsEnabled: true };
vi.mock("@/providers/host-provider", () => ({
  useHostState: vi.fn(() => hostStateMock),
}));

let diagnosticsSupported = true;
vi.mock("@/lib/hosts", () => ({
  useHostFeature: vi.fn(() => ({ supported: diagnosticsSupported, loading: false })),
}));

vi.mock("@/lib/hosts/local-bootstrap", () => ({
  probeLocalBootstrap: vi.fn(async () => ({ status: "unavailable" })),
  resetLocalBootstrapCache: vi.fn(),
}));

vi.mock("@/hooks/use-keyboard-platform", () => ({
  useKeyboardPlatform: vi.fn().mockReturnValue("unknown"),
}));

type TentacleQuery = ReturnType<typeof useTentacleStatus>;
type GenericQuery = {
  data: undefined;
  isLoading: boolean;
  isError: boolean;
  isSuccess: boolean;
  error: null;
  refetch: () => void;
};

const mockedUseTentacleStatus = vi.mocked(useTentacleStatus);
const mockedUseHealth = vi.mocked(useHealth);
const mockedUseSyncStatus = vi.mocked(useSyncStatus);
const mockedUseScoutStatus = vi.mocked(useScoutStatus);
const mockedUseSkillMetrics = vi.mocked(useSkillMetrics);
const mockedUseKeyboardPlatform = vi.mocked(useKeyboardPlatform);

function makeIdleQuery(): GenericQuery {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    isSuccess: false,
    error: null,
    refetch: vi.fn(),
  };
}

function makeTentacleQuery(overrides: Partial<TentacleQuery>): TentacleQuery {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    isSuccess: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  } as TentacleQuery;
}

const baseTentacleData: TentacleStatusResponse = {
  status: "ready",
  configured: true,
  active_count: 0,
  total_count: 2,
  worktrees_prepared: 1,
  verification_covered: 1,
  marker: { active: false, path: "/markers/dispatched", age_hours: null, stale: false },
  tentacles: [],
  audit: { summary: { ok: true, total_checks: 3, warning_checks: 0 }, checks: [] },
  operator_actions: [],
  runtime: { generated_at: "2026-01-01T00:00:00Z" },
};

beforeEach(() => {
  // Simulate local browse so diagnostics are enabled for existing tests
  window.history.pushState({}, "", "/v2/settings");
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => {}))
  );
  localStorage.clear();
  hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };
  diagnosticsSupported = true;
  mockedUseKeyboardPlatform.mockReturnValue("unknown");
  mockedUseHealth.mockReturnValue(makeIdleQuery() as ReturnType<typeof useHealth>);
  mockedUseSyncStatus.mockReturnValue(makeIdleQuery() as ReturnType<typeof useSyncStatus>);
  mockedUseScoutStatus.mockReturnValue(makeIdleQuery() as ReturnType<typeof useScoutStatus>);
  mockedUseSkillMetrics.mockReturnValue(makeIdleQuery() as ReturnType<typeof useSkillMetrics>);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// Dynamic import to avoid issues with "use client" directive in test environment
const SettingsPage = (await import("@/app/settings/page")).default;

describe("SettingsPage — tentacle diagnostics card", () => {
  it("keeps diagnostics hooks disabled when the host lacks diagnostics support", () => {
    diagnosticsSupported = false;
    mockedUseTentacleStatus.mockReturnValue(makeTentacleQuery({}));

    render(<SettingsPage />);

    expect(
      screen.getAllByText("The selected host does not advertise diagnostics support.").length
    ).toBeGreaterThan(0);
    expect(mockedUseHealth.mock.calls.at(-1)?.[1]).toBe(false);
    expect(mockedUseSyncStatus.mock.calls.at(-1)?.[1]).toBe(false);
    expect(mockedUseScoutStatus.mock.calls.at(-1)?.[1]).toBe(false);
    expect(mockedUseTentacleStatus.mock.calls.at(-1)?.[1]).toBe(false);
    expect(mockedUseSkillMetrics.mock.calls.at(-1)?.[1]).toBe(false);
  });

  it("shows loading skeletons while tentacle status is loading", () => {
    mockedUseTentacleStatus.mockReturnValue(makeTentacleQuery({ isLoading: true }));

    render(<SettingsPage />);
    // Skeletons render when loading; check card title is present
    expect(screen.getByText("Tentacle runtime diagnostics")).toBeInTheDocument();
  });

  it("shows warning banner when tentacle status fetch fails", () => {
    mockedUseTentacleStatus.mockReturnValue(
      makeTentacleQuery({
        isError: true,
        error: new Error("Connection refused") as never,
      })
    );

    render(<SettingsPage />);
    expect(screen.getByText("Tentacle diagnostics unavailable")).toBeInTheDocument();
    expect(screen.getByText("Connection refused")).toBeInTheDocument();
  });

  it("renders tentacle summary metrics when data is available", () => {
    mockedUseTentacleStatus.mockReturnValue(
      makeTentacleQuery({ data: baseTentacleData, isSuccess: true })
    );

    render(<SettingsPage />);
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByText("0 / 2")).toBeInTheDocument();
  });

  it("does not render goal-loop diagnostics block when goal_aware_count is absent", () => {
    mockedUseTentacleStatus.mockReturnValue(
      makeTentacleQuery({ data: baseTentacleData, isSuccess: true })
    );

    render(<SettingsPage />);
    expect(screen.queryByText("Goal-loop diagnostics")).not.toBeInTheDocument();
  });

  it("does not render goal-loop diagnostics block when goal_aware_count is zero", () => {
    mockedUseTentacleStatus.mockReturnValue(
      makeTentacleQuery({
        data: { ...baseTentacleData, goal_aware_count: 0 },
        isSuccess: true,
      })
    );

    render(<SettingsPage />);
    expect(screen.queryByText("Goal-loop diagnostics")).not.toBeInTheDocument();
  });

  it("renders goal-loop diagnostics when tentacles have goal_id set", () => {
    mockedUseTentacleStatus.mockReturnValue(
      makeTentacleQuery({
        data: {
          ...baseTentacleData,
          goal_aware_count: 1,
          tentacles: [
            {
              name: "my-tentacle",
              tentacle_id: "t-001",
              status: "active",
              created_at: "2026-05-01T00:00:00Z",
              description: "Test",
              scope: [],
              skills: [],
              worktree: { prepared: false, path: "", stale: false },
              verification: { coverage_exists: false, total: 0, passed: 0, failed: 0 },
              goal_id: "goal-xyz",
              goal_name: "Ship faster search",
              goal_iteration: 1,
            },
          ],
        },
        isSuccess: true,
      })
    );

    render(<SettingsPage />);
    expect(screen.getByText("Goal-loop diagnostics")).toBeInTheDocument();
    expect(screen.getByText("Goal-linked tentacles:")).toBeInTheDocument();
    // Goal name appears in the diagnostics block and the tentacle registry entry
    expect(screen.getAllByText(/Ship faster search/).length).toBeGreaterThan(0);
  });

  it("shows goal_id as fallback when goal_name is absent", () => {
    mockedUseTentacleStatus.mockReturnValue(
      makeTentacleQuery({
        data: {
          ...baseTentacleData,
          goal_aware_count: 1,
          tentacles: [
            {
              name: "bare-tentacle",
              tentacle_id: "t-002",
              status: "idle",
              created_at: "2026-05-01T00:00:00Z",
              description: "",
              scope: [],
              skills: [],
              worktree: { prepared: false, path: "", stale: false },
              verification: { coverage_exists: false, total: 0, passed: 0, failed: 0 },
              goal_id: "goal-abc-123",
            },
          ],
        },
        isSuccess: true,
      })
    );

    render(<SettingsPage />);
    expect(screen.getByText("goal-abc-123")).toBeInTheDocument();
  });
});

describe("SettingsPage — hosted-safe diagnostics (no agent host)", () => {
  beforeEach(() => {
    // Simulate Firebase-hosted root: no /v2/ path, no remote host in localStorage
    window.history.pushState({}, "", "/settings");
    localStorage.clear();
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: false };
    mockedUseTentacleStatus.mockReturnValue(makeTentacleQuery({}));
    mockedUseHealth.mockReturnValue(makeIdleQuery() as ReturnType<typeof useHealth>);
    mockedUseSyncStatus.mockReturnValue(makeIdleQuery() as ReturnType<typeof useSyncStatus>);
    mockedUseScoutStatus.mockReturnValue(makeIdleQuery() as ReturnType<typeof useScoutStatus>);
    mockedUseSkillMetrics.mockReturnValue(makeIdleQuery() as ReturnType<typeof useSkillMetrics>);
  });

  it("shows idle guidance in the sync diagnostics card", () => {
    render(<SettingsPage />);
    expect(screen.getByTestId("sync-diagnostics-idle")).toBeInTheDocument();
  });

  it("shows idle guidance in the Trend Scout diagnostics card", () => {
    render(<SettingsPage />);
    expect(screen.getByTestId("scout-diagnostics-idle")).toBeInTheDocument();
  });

  it("shows idle guidance in the tentacle diagnostics card", () => {
    render(<SettingsPage />);
    expect(screen.getByTestId("tentacle-diagnostics-idle")).toBeInTheDocument();
  });

  it("shows idle guidance in the skill metrics card", () => {
    render(<SettingsPage />);
    expect(screen.getByTestId("skill-diagnostics-idle")).toBeInTheDocument();
  });

  it("shows idle guidance in the system health card", () => {
    render(<SettingsPage />);
    expect(screen.getByTestId("health-diagnostics-idle")).toBeInTheDocument();
  });

  it("does not render diagnostics loading skeletons when no host is configured", () => {
    render(<SettingsPage />);
    // Skeleton elements are only rendered inside the enabled branch, so they should not appear
    // when diagnostics are disabled (no Skeleton components rendered)
    expect(screen.queryAllByRole("status").length).toBe(0);
  });
});

describe("SettingsPage — Hosts & connections card", () => {
  beforeEach(() => {
    window.history.pushState({}, "", "/settings");
    localStorage.clear();
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: false };
    mockedUseTentacleStatus.mockReturnValue(makeTentacleQuery({}));
    mockedUseHealth.mockReturnValue(makeIdleQuery() as ReturnType<typeof useHealth>);
    mockedUseSyncStatus.mockReturnValue(makeIdleQuery() as ReturnType<typeof useSyncStatus>);
    mockedUseScoutStatus.mockReturnValue(makeIdleQuery() as ReturnType<typeof useScoutStatus>);
    mockedUseSkillMetrics.mockReturnValue(makeIdleQuery() as ReturnType<typeof useSkillMetrics>);
  });

  it("renders the Hosts & connections card heading", () => {
    render(<SettingsPage />);
    // The card title heading is unique; idle guidance also references "Hosts & connections"
    // but as inline text, not as a heading.
    const headings = screen.getAllByText("Hosts & connections");
    // At least one should exist (the card heading)
    expect(headings.length).toBeGreaterThanOrEqual(1);
  });

  it("shows the local host row by default", () => {
    render(<SettingsPage />);
    expect(screen.getByTestId("host-row-local")).toBeInTheDocument();
    expect(screen.getByText("Local (same-origin)")).toBeInTheDocument();
  });

  it("shows 'Same-origin default' subtitle for the local host row on local pages", () => {
    render(<SettingsPage />);
    expect(screen.getByText("Same-origin default")).toBeInTheDocument();
  });

  it("shows empty state when no remote hosts are saved", () => {
    render(<SettingsPage />);
    expect(screen.getByTestId("no-remote-hosts")).toBeInTheDocument();
  });

  it("shows the Add host button", () => {
    render(<SettingsPage />);
    expect(screen.getByRole("button", { name: "Add remote host" })).toBeInTheDocument();
  });

  it("opens the add host form when Add host is clicked", async () => {
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add remote host" }));
    await waitFor(() => {
      expect(screen.getByTestId("host-add-form")).toBeInTheDocument();
    });
    expect(screen.getByLabelText("Tunnel URL")).toBeInTheDocument();
    expect(screen.getByLabelText("Host label")).toBeInTheDocument();
    expect(screen.getByLabelText("Auth token")).toBeInTheDocument();
  });

  it("disables Save host button when URL is empty", async () => {
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add remote host" }));
    await waitFor(() => expect(screen.getByTestId("host-add-form")).toBeInTheDocument());
    const saveBtn = screen.getByTestId("save-host-btn");
    expect(saveBtn).toBeDisabled();
  });

  it("enables Save host button when URL is provided", async () => {
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add remote host" }));
    await waitFor(() => expect(screen.getByTestId("host-add-form")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://abc123.ngrok.io" },
    });
    expect(screen.getByTestId("save-host-btn")).not.toBeDisabled();
  });

  it("adds a new remote host and shows it in the list", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add remote host" }));
    await waitFor(() => expect(screen.getByTestId("host-add-form")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://myhost.ngrok.io" },
    });
    fireEvent.change(screen.getByLabelText("Host label"), {
      target: { value: "My Laptop" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));
    await waitFor(() => {
      expect(screen.getByText("My Laptop")).toBeInTheDocument();
    });
  });

  it("shows Restore local button when a remote host is active", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    render(<SettingsPage />);
    // Add and activate a remote host
    fireEvent.click(screen.getByRole("button", { name: "Add remote host" }));
    await waitFor(() => expect(screen.getByTestId("host-add-form")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "https://test.ngrok.io" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("restore-local-btn")).toBeInTheDocument();
    });
  });
});

describe("SettingsPage — mixed-content loopback guard", () => {
  beforeEach(() => {
    // Simulate a hosted HTTPS deployment
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "https://agents.example.com" },
      configurable: true,
    });
    localStorage.clear();
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: false };
    mockedUseTentacleStatus.mockReturnValue(makeTentacleQuery({}));
    mockedUseHealth.mockReturnValue(makeIdleQuery() as ReturnType<typeof useHealth>);
    mockedUseSyncStatus.mockReturnValue(makeIdleQuery() as ReturnType<typeof useSyncStatus>);
    mockedUseScoutStatus.mockReturnValue(makeIdleQuery() as ReturnType<typeof useScoutStatus>);
    mockedUseSkillMetrics.mockReturnValue(makeIdleQuery() as ReturnType<typeof useSkillMetrics>);
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      value: { ...window.location, origin: "http://localhost:3000" },
      configurable: true,
    });
  });

  it("shows a PNA informational note when adding a localhost URL from a hosted HTTPS page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("Failed to fetch");
      })
    );
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add remote host" }));
    await waitFor(() => expect(screen.getByTestId("host-add-form")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "http://localhost:3000" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));
    // pna-note shown first (synchronous), then validation-error from probe failure
    await waitFor(() => expect(screen.getByTestId("pna-note")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
  });

  it("shows 'Save anyway' for a loopback URL after probe failure (pna-required is compatible: true)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("Failed to fetch");
      })
    );
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add remote host" }));
    await waitFor(() => expect(screen.getByTestId("host-add-form")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Tunnel URL"), {
      target: { value: "http://127.0.0.1:8080" },
    });
    fireEvent.click(screen.getByTestId("save-host-btn"));
    await waitFor(() => expect(screen.getByTestId("validation-error")).toBeInTheDocument());
    // pna-required is compatible: true, so probe fires and fails → "Save anyway" is shown
    expect(screen.getByTestId("skip-validation-btn")).toBeInTheDocument();
  });

  it("shows 'Open the local browse app directly' for the local host row on hosted pages", () => {
    render(<SettingsPage />);
    expect(screen.getByText("Open the local browse app directly")).toBeInTheDocument();
  });
});

describe("SettingsPage — platform-aware shortcut display", () => {
  beforeEach(() => {
    mockedUseTentacleStatus.mockReturnValue(makeTentacleQuery({}));
  });

  it("shows ⌘/Ctrl+K and ⌘/Ctrl+B shortcut labels on unknown platform", () => {
    mockedUseKeyboardPlatform.mockReturnValue("unknown");
    render(<SettingsPage />);
    expect(screen.getByText("⌘/Ctrl+K")).toBeInTheDocument();
    expect(screen.getByText("⌘/Ctrl+B")).toBeInTheDocument();
  });

  it("shows ⌘K and ⌘B shortcut labels on mac platform", () => {
    mockedUseKeyboardPlatform.mockReturnValue("mac");
    render(<SettingsPage />);
    expect(screen.getByText("⌘K")).toBeInTheDocument();
    expect(screen.getByText("⌘B")).toBeInTheDocument();
  });

  it("shows Ctrl+K and Ctrl+B shortcut labels on win platform", () => {
    mockedUseKeyboardPlatform.mockReturnValue("win");
    render(<SettingsPage />);
    expect(screen.getByText("Ctrl+K")).toBeInTheDocument();
    expect(screen.getByText("Ctrl+B")).toBeInTheDocument();
  });

  it("leaves non-modifier shortcuts unchanged across platforms", () => {
    mockedUseKeyboardPlatform.mockReturnValue("mac");
    render(<SettingsPage />);
    expect(screen.getAllByText("Esc").length).toBeGreaterThan(0);
    expect(screen.getAllByText("G then S").length).toBeGreaterThan(0);
  });
});
