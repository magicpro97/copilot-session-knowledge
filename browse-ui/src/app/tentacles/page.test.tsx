import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

import { LOCAL_HOST } from "@/lib/host-profiles";
import type { HostState } from "@/providers/host-provider";
import type { TentacleStatusResponse } from "@/lib/api/types";

// ── Mocks ─────────────────────────────────────────────────────────────────────

vi.mock("@/lib/api/hooks", () => ({
  useTentacleStatus: vi.fn(),
}));

let hostStateMock: HostState = { host: LOCAL_HOST, diagnosticsEnabled: true };
vi.mock("@/providers/host-provider", () => ({
  useHostState: vi.fn(() => hostStateMock),
}));

vi.mock("lucide-react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("lucide-react")>();
  return {
    ...actual,
    GitBranch: () => <span data-testid="icon-git-branch" />,
    RefreshCw: ({ className }: { className?: string }) => (
      <span data-testid="icon-refresh" className={className} />
    ),
    ChevronDown: () => <span data-testid="icon-chevron-down" />,
    ChevronRight: () => <span data-testid="icon-chevron-right" />,
  };
});

import { useTentacleStatus } from "@/lib/api/hooks";
const mockedUseTentacleStatus = vi.mocked(useTentacleStatus);

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makeEntry(overrides: Partial<TentacleStatusResponse["tentacles"][number]> = {}) {
  return {
    name: "fix-search",
    tentacle_id: "t-001",
    status: "active",
    created_at: "2024-01-01T00:00:00Z",
    description: "Fix the search bug",
    scope: ["src/search.py"],
    skills: ["python"],
    worktree: { prepared: true, path: "/worktrees/fix-search", stale: false },
    verification: { coverage_exists: true, total: 4, passed: 3, failed: 1 },
    has_handoff: false,
    terminal_status: undefined,
    ...overrides,
  } satisfies TentacleStatusResponse["tentacles"][number];
}

function makeStatusResponse(
  overrides: Partial<TentacleStatusResponse> = {}
): TentacleStatusResponse {
  return {
    status: "active",
    configured: true,
    active_count: 1,
    total_count: 1,
    worktrees_prepared: 1,
    verification_covered: 1,
    goal_aware_count: 0,
    marker: { active: false, path: "", age_hours: null, stale: false },
    tentacles: [makeEntry()],
    audit: {
      summary: { ok: true, total_checks: 1, warning_checks: 0 },
      checks: [{ id: "check-1", title: "Registry present", status: "ok", detail: "ok" }],
    },
    operator_actions: [],
    runtime: { generated_at: "2024-01-01T00:00:00Z" },
    ...overrides,
  };
}

type QueryResult = ReturnType<typeof useTentacleStatus>;

function makeQueryResult(overrides: Partial<QueryResult> = {}): QueryResult {
  return {
    data: makeStatusResponse(),
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  } as unknown as QueryResult;
}

// ── Lazy import after mocks ───────────────────────────────────────────────────

const TentaclesPage = (await import("@/app/tentacles/page")).default;

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("TentaclesPage", () => {
  beforeEach(() => {
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: true };
    mockedUseTentacleStatus.mockReturnValue(makeQueryResult());
  });

  it("renders the page heading", () => {
    render(<TentaclesPage />);
    expect(screen.getByText("Tentacle Management")).toBeInTheDocument();
  });

  it("renders a tentacle row with name", () => {
    render(<TentaclesPage />);
    expect(screen.getByText("fix-search")).toBeInTheDocument();
  });

  it("shows no-host state when diagnosticsEnabled is false", () => {
    hostStateMock = { host: LOCAL_HOST, diagnosticsEnabled: false };
    render(<TentaclesPage />);
    expect(screen.getByText("No agent host selected")).toBeInTheDocument();
    expect(screen.queryByTestId("tentacle-list")).not.toBeInTheDocument();
  });

  it("renders loading state while fetching", () => {
    mockedUseTentacleStatus.mockReturnValue(makeQueryResult({ isLoading: true, data: undefined }));
    render(<TentaclesPage />);
    expect(screen.getByTestId("loading-state")).toBeInTheDocument();
  });

  it("renders error state on fetch failure", () => {
    mockedUseTentacleStatus.mockReturnValue(
      makeQueryResult({ isError: true, isLoading: false, data: undefined })
    );
    render(<TentaclesPage />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/Failed to load/)).toBeInTheDocument();
  });

  it("renders empty state when no tentacles exist", () => {
    mockedUseTentacleStatus.mockReturnValue(
      makeQueryResult({
        data: makeStatusResponse({ tentacles: [], total_count: 0, active_count: 0 }),
      })
    );
    render(<TentaclesPage />);
    expect(screen.getByTestId("empty-state")).toBeInTheDocument();
  });

  it("renders filter buttons", () => {
    render(<TentaclesPage />);
    expect(screen.getByTestId("filter-all")).toBeInTheDocument();
    expect(screen.getByTestId("filter-active")).toBeInTheDocument();
    expect(screen.getByTestId("filter-completed")).toBeInTheDocument();
    expect(screen.getByTestId("filter-failed")).toBeInTheDocument();
  });

  it("filters to show only completed tentacles", () => {
    const doneTentacle = makeEntry({
      name: "done-tentacle",
      tentacle_id: "t-done",
      status: "idle",
      terminal_status: "DONE",
    });
    const activeTentacle = makeEntry({ name: "active-tentacle", tentacle_id: "t-active" });
    mockedUseTentacleStatus.mockReturnValue(
      makeQueryResult({
        data: makeStatusResponse({
          tentacles: [doneTentacle, activeTentacle],
          total_count: 2,
        }),
      })
    );
    render(<TentaclesPage />);

    // Both visible initially (all filter)
    expect(screen.getByText("done-tentacle")).toBeInTheDocument();
    expect(screen.getByText("active-tentacle")).toBeInTheDocument();

    // Click "completed" filter
    fireEvent.click(screen.getByTestId("filter-completed"));

    // Only done tentacle visible
    expect(screen.getByText("done-tentacle")).toBeInTheDocument();
    expect(screen.queryByText("active-tentacle")).not.toBeInTheDocument();
  });

  it("filters to show only active tentacles", () => {
    const activeTentacle = makeEntry({
      name: "active-tentacle",
      tentacle_id: "t-active",
      status: "running",
    });
    const idleTentacle = makeEntry({
      name: "idle-tentacle",
      tentacle_id: "t-idle",
      status: "idle",
      terminal_status: "DONE",
    });
    mockedUseTentacleStatus.mockReturnValue(
      makeQueryResult({
        data: makeStatusResponse({ tentacles: [activeTentacle, idleTentacle], total_count: 2 }),
      })
    );
    render(<TentaclesPage />);

    fireEvent.click(screen.getByTestId("filter-active"));

    expect(screen.getByText("active-tentacle")).toBeInTheDocument();
    expect(screen.queryByText("idle-tentacle")).not.toBeInTheDocument();
  });

  it("filters to show only failed tentacles", () => {
    const blockedTentacle = makeEntry({
      name: "blocked-tentacle",
      tentacle_id: "t-blocked",
      status: "idle",
      terminal_status: "BLOCKED",
    });
    const doneTentacle = makeEntry({
      name: "done-tentacle",
      tentacle_id: "t-done",
      status: "idle",
      terminal_status: "DONE",
    });
    mockedUseTentacleStatus.mockReturnValue(
      makeQueryResult({
        data: makeStatusResponse({ tentacles: [blockedTentacle, doneTentacle], total_count: 2 }),
      })
    );
    render(<TentaclesPage />);

    fireEvent.click(screen.getByTestId("filter-failed"));

    expect(screen.getByText("blocked-tentacle")).toBeInTheDocument();
    expect(screen.queryByText("done-tentacle")).not.toBeInTheDocument();
  });

  it("expands a tentacle row to show detail on click", () => {
    render(<TentaclesPage />);

    // Detail not visible initially
    expect(screen.queryByTestId("tentacle-detail")).not.toBeInTheDocument();

    // Click to expand
    fireEvent.click(screen.getByTestId("tentacle-row-toggle"));

    // Detail now visible
    expect(screen.getByTestId("tentacle-detail")).toBeInTheDocument();
    expect(screen.getAllByText("src/search.py").length).toBeGreaterThan(0);
    expect(screen.getByText("python")).toBeInTheDocument();
  });

  it("shows progress percentage derived from verification", () => {
    // 3/4 passed = 75%
    render(<TentaclesPage />);
    expect(screen.getByText("75%")).toBeInTheDocument();
  });

  it("passes host and diagnosticsEnabled to useTentacleStatus", () => {
    render(<TentaclesPage />);
    const calls = (mockedUseTentacleStatus as Mock).mock.calls;
    expect(calls.at(-1)).toEqual([LOCAL_HOST, true]);
  });
});
