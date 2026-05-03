import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useHealth,
  useScoutStatus,
  useSkillMetrics,
  useSyncStatus,
  useTentacleStatus,
} from "@/lib/api/hooks";
import type { TentacleStatusResponse } from "@/lib/api/types";

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
  mockedUseHealth.mockReturnValue(makeIdleQuery() as ReturnType<typeof useHealth>);
  mockedUseSyncStatus.mockReturnValue(makeIdleQuery() as ReturnType<typeof useSyncStatus>);
  mockedUseScoutStatus.mockReturnValue(makeIdleQuery() as ReturnType<typeof useScoutStatus>);
  mockedUseSkillMetrics.mockReturnValue(makeIdleQuery() as ReturnType<typeof useSkillMetrics>);
});

// Dynamic import to avoid issues with "use client" directive in test environment
const SettingsPage = (await import("@/app/settings/page")).default;

describe("SettingsPage — tentacle diagnostics card", () => {
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
