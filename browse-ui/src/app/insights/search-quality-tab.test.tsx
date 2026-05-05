import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SearchQualityTab } from "@/app/insights/search-quality-tab";
import { InsightsTabContext } from "@/app/insights/insights-tab-context";
import { LOCAL_HOST } from "@/lib/host-profiles";

vi.mock("@/lib/api/hooks", () => ({
  useEval: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    isError: false,
    isSuccess: false,
    error: null,
  })),
}));

vi.mock("@/app/insights/eval-section", () => ({
  EvalBody: () => <div data-testid="eval-body" />,
}));

import { useEval } from "@/lib/api/hooks";
const mockedUseEval = vi.mocked(useEval);

type CapabilityState = "ready" | "no-host" | "checking" | "unsupported";

function renderWithCapabilityState(capabilityState: CapabilityState) {
  return render(
    <InsightsTabContext.Provider
      value={{
        setActiveTab: vi.fn(),
        diagnosticsEnabled: false,
        capabilityState,
        host: LOCAL_HOST,
      }}
    >
      <SearchQualityTab />
    </InsightsTabContext.Provider>
  );
}

describe("SearchQualityTab — idle states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows 'No agent host selected' guidance when no host is configured", () => {
    renderWithCapabilityState("no-host");
    expect(screen.getByText(/no agent host selected/i)).toBeInTheDocument();
    expect(mockedUseEval).not.toHaveBeenCalled();
  });

  it("shows 'Not supported by this host' when selected host lacks insights support", () => {
    renderWithCapabilityState("unsupported");
    expect(screen.getByText(/not supported by this host/i)).toBeInTheDocument();
    expect(mockedUseEval).not.toHaveBeenCalled();
  });

  it("shows 'Checking host capabilities' while capability check is in progress", () => {
    renderWithCapabilityState("checking");
    expect(screen.getByText(/checking host capabilities/i)).toBeInTheDocument();
    expect(mockedUseEval).not.toHaveBeenCalled();
  });
});
