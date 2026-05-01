import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RetroBody } from "@/app/insights/retro-section";
import { useRetro } from "@/lib/api/hooks";
import type { RetroResponse, RetroScout } from "@/lib/api/types";

vi.mock("@/lib/api/hooks", () => ({
  useRetro: vi.fn(),
}));

type RetroQuery = ReturnType<typeof useRetro>;

function makeQuery(overrides: Partial<RetroQuery>): RetroQuery {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    isSuccess: false,
    error: null,
    ...overrides,
  } as RetroQuery;
}

function makeRetroData(overrides: Partial<RetroResponse> = {}): RetroResponse {
  return {
    retro_score: 70,
    grade: "Good",
    grade_emoji: "✅",
    mode: "repo",
    generated_at: "2026-01-01T00:00:00Z",
    available_sections: ["git"],
    weights: { git: 1.0 },
    subscores: { knowledge: 0, skills: 0, hooks: 0, git: 70 },
    knowledge: { available: false },
    skills: { available: false },
    hooks: { available: false },
    git: { available: true },
    scout: { available: false },
    improvement_actions: [],
    ...overrides,
  } as RetroResponse;
}

function makeScout(overrides: Partial<RetroScout> = {}): RetroScout {
  return {
    available: true,
    configured: true,
    script_exists: true,
    config_path: "/tmp/trend-scout.json",
    target_repo: "magicpro97/copilot-session-knowledge",
    issue_label: "trend-scout",
    grace_window_hours: 20,
    state_file: "/tmp/trend-scout-state.json",
    state_file_exists: false,
    last_run_utc: null,
    elapsed_hours: null,
    remaining_hours: null,
    would_skip_without_force: false,
    ...overrides,
  };
}

describe("BehaviorMetricsGrid via RetroBody", () => {
  it("renders all 4 behavior metrics when retro.data.behavior is defined", () => {
    const data = makeRetroData({
      behavior: {
        completion_rate: 0.75,
        knowledge_yield: 2.5,
        efficiency_ratio: 0.4,
        one_shot_rate: 0.6,
        session_count: 10,
        sessions_with_checkpoints: 8,
      },
    });

    render(<RetroBody retro={makeQuery({ isSuccess: true, data })} />);

    expect(screen.getByText("Completion Rate")).toBeInTheDocument();
    expect(screen.getByText("Knowledge Yield")).toBeInTheDocument();
    expect(screen.getByText("Efficiency Ratio")).toBeInTheDocument();
    expect(screen.getByText("One-Shot Rate")).toBeInTheDocument();

    // completion_rate=0.75 → "75.0%"
    expect(screen.getByText("75.0")).toBeInTheDocument();
    // efficiency_ratio=0.4 → "40.0%"
    expect(screen.getByText("40.0")).toBeInTheDocument();
  });

  it("does not render BehaviorMetricsGrid when behavior is undefined", () => {
    const data = makeRetroData({ behavior: undefined });

    render(<RetroBody retro={makeQuery({ isSuccess: true, data })} />);

    expect(screen.queryByText("Completion Rate")).not.toBeInTheDocument();
    expect(screen.queryByText("Knowledge Yield")).not.toBeInTheDocument();
    expect(screen.queryByText("Session Behavior")).not.toBeInTheDocument();
  });

  it("shows session count and correct one_shot_rate percentage", () => {
    const data = makeRetroData({
      behavior: {
        completion_rate: 1.0,
        knowledge_yield: 1.0,
        efficiency_ratio: 1.0,
        one_shot_rate: 0.5,
        session_count: 20,
        sessions_with_checkpoints: 20,
      },
    });

    render(<RetroBody retro={makeQuery({ isSuccess: true, data })} />);

    // one_shot_rate=0.5 → "50.0%"
    expect(screen.getByText("One-Shot Rate")).toBeInTheDocument();
    expect(screen.getByText("50.0")).toBeInTheDocument();
  });

  it("clarifies when Trend Scout has never run because no state file exists", () => {
    const data = makeRetroData({
      scout: makeScout({
        state_file_exists: false,
        last_run_utc: null,
      }),
    });

    render(<RetroBody retro={makeQuery({ isSuccess: true, data })} />);

    expect(screen.getByText(/Last run:/)).toHaveTextContent(
      "Last run: never run yet (no state file found)"
    );
  });

  it("clarifies when Trend Scout state exists but the last-run timestamp is missing", () => {
    const data = makeRetroData({
      scout: makeScout({
        state_file_exists: true,
        last_run_utc: null,
      }),
    });

    render(<RetroBody retro={makeQuery({ isSuccess: true, data })} />);

    expect(screen.getByText(/Last run:/)).toHaveTextContent(
      "Last run: unknown (state file exists, but no last-run timestamp)"
    );
  });
});
