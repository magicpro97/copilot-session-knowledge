import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ResearchPackSection } from "@/app/insights/research-pack-section";
import { InsightsTabContext } from "@/app/insights/insights-tab-context";
import { useReloadScoutResearchPack, useScoutResearchPack } from "@/lib/api/hooks";
import { LOCAL_HOST } from "@/lib/host-profiles";

vi.mock("@/lib/api/hooks", () => ({
  useReloadScoutResearchPack: vi.fn(),
  useScoutResearchPack: vi.fn(),
}));

type ScoutPackQuery = ReturnType<typeof useScoutResearchPack>;
type ReloadPackMutation = ReturnType<typeof useReloadScoutResearchPack>;

const mockedUseScoutResearchPack = vi.mocked(useScoutResearchPack);
const mockedUseReloadScoutResearchPack = vi.mocked(useReloadScoutResearchPack);
const REMOTE_HOST = {
  id: "remote-h1",
  label: "Remote Host",
  base_url: "https://remote.example.com",
  token: "tok-remote",
  cli_kind: "copilot" as const,
  is_default: false,
};

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

/** Render inside a context that enables diagnostics (simulates local/remote-host mode). */
function renderEnabled(ui: React.ReactElement) {
  return render(
    <InsightsTabContext.Provider
      value={{ setActiveTab: vi.fn(), diagnosticsEnabled: true, host: LOCAL_HOST }}
    >
      {ui}
    </InsightsTabContext.Provider>
  );
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

    renderEnabled(<ResearchPackSection />);

    expect(screen.getByText("Research pack reload failed")).toBeInTheDocument();
    expect(screen.getByText("API 500: boom")).toBeInTheDocument();
  });

  it("renders null when diagnosticsEnabled is false (hosted static mode)", () => {
    mockedUseScoutResearchPack.mockReturnValue(
      makePackQuery({ isSuccess: true, data: basePackData })
    );
    mockedUseReloadScoutResearchPack.mockReturnValue(makeReloadMutation({}));

    const { container } = render(
      <InsightsTabContext.Provider
        value={{ setActiveTab: vi.fn(), diagnosticsEnabled: false, host: LOCAL_HOST }}
      >
        <ResearchPackSection />
      </InsightsTabContext.Provider>
    );

    expect(container.firstChild).toBeNull();
  });

  it("passes the selected host into research pack hooks when diagnostics are enabled", () => {
    mockedUseScoutResearchPack.mockReturnValue(
      makePackQuery({ isSuccess: true, data: basePackData })
    );
    mockedUseReloadScoutResearchPack.mockReturnValue(makeReloadMutation({}));

    render(
      <InsightsTabContext.Provider
        value={{ setActiveTab: vi.fn(), diagnosticsEnabled: true, host: REMOTE_HOST }}
      >
        <ResearchPackSection />
      </InsightsTabContext.Provider>
    );

    expect(mockedUseScoutResearchPack).toHaveBeenCalledWith(REMOTE_HOST, true);
    expect(mockedUseReloadScoutResearchPack).toHaveBeenCalledWith(REMOTE_HOST);
  });
});
