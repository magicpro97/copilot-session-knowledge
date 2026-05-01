import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ResearchPackSection } from "@/app/insights/research-pack-section";
import { useReloadScoutResearchPack, useScoutResearchPack } from "@/lib/api/hooks";

vi.mock("@/lib/api/hooks", () => ({
  useReloadScoutResearchPack: vi.fn(),
  useScoutResearchPack: vi.fn(),
}));

type ScoutPackQuery = ReturnType<typeof useScoutResearchPack>;
type ReloadPackMutation = ReturnType<typeof useReloadScoutResearchPack>;

const mockedUseScoutResearchPack = vi.mocked(useScoutResearchPack);
const mockedUseReloadScoutResearchPack = vi.mocked(useReloadScoutResearchPack);

const basePackData = {
  available: true,
  generated_at: "2026-01-01T00:00:00Z",
  repo_count: 0,
  repos: [],
  error: null,
  run_skipped: false,
  skip_reason: null,
};

function makePackQuery(overrides: Partial<ScoutPackQuery>): ScoutPackQuery {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    isSuccess: false,
    error: null,
    ...overrides,
  } as ScoutPackQuery;
}

function makeReloadMutation(overrides: Partial<ReloadPackMutation>): ReloadPackMutation {
  return {
    data: undefined,
    error: null,
    isError: false,
    isPending: false,
    mutate: vi.fn(),
    ...overrides,
  } as ReloadPackMutation;
}

describe("ResearchPackSection", () => {
  it("shows a warning banner when the reload mutation fails before returning JSON", () => {
    mockedUseScoutResearchPack.mockReturnValue(
      makePackQuery({
        data: basePackData,
        isSuccess: true,
      })
    );
    mockedUseReloadScoutResearchPack.mockReturnValue(
      makeReloadMutation({
        isError: true,
        error: new Error("API 500: boom"),
      })
    );

    render(<ResearchPackSection />);

    expect(screen.getByText("Research pack reload failed")).toBeInTheDocument();
    expect(screen.getByText("API 500: boom")).toBeInTheDocument();
  });
});
