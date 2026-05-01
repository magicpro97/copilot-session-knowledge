import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RetroBody } from "@/app/insights/retro-section";
import { useRetro } from "@/lib/api/hooks";
import type { RetroResponse } from "@/lib/api/types";

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
});
