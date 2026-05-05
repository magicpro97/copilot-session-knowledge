import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeTab } from "@/app/insights/knowledge-tab";
import { InsightsTabContext } from "@/app/insights/insights-tab-context";
import { LOCAL_HOST } from "@/lib/host-profiles";

vi.mock("@/lib/api/hooks", () => ({
  useKnowledgeInsights: vi.fn(() => ({
    data: undefined,
    isLoading: false,
    isError: false,
    isSuccess: false,
    error: null,
  })),
}));

vi.mock("@/app/insights/knowledge-insights-section", () => ({
  KnowledgeInsightsBody: () => <div data-testid="knowledge-insights-body" />,
}));

import { useKnowledgeInsights } from "@/lib/api/hooks";
const mockedUseKnowledgeInsights = vi.mocked(useKnowledgeInsights);

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
      <KnowledgeTab />
    </InsightsTabContext.Provider>
  );
}

describe("KnowledgeTab — idle states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows 'No agent host selected' guidance when no host is configured", () => {
    renderWithCapabilityState("no-host");
    expect(screen.getByText(/no agent host selected/i)).toBeInTheDocument();
    expect(mockedUseKnowledgeInsights).not.toHaveBeenCalled();
  });

  it("shows 'Not supported by this host' when selected host lacks insights support", () => {
    renderWithCapabilityState("unsupported");
    expect(screen.getByText(/not supported by this host/i)).toBeInTheDocument();
    expect(mockedUseKnowledgeInsights).not.toHaveBeenCalled();
  });

  it("shows 'Checking host capabilities' while capability check is in progress", () => {
    renderWithCapabilityState("checking");
    expect(screen.getByText(/checking host capabilities/i)).toBeInTheDocument();
    expect(mockedUseKnowledgeInsights).not.toHaveBeenCalled();
  });

  it("does not fire useKnowledgeInsights in any idle state", () => {
    for (const state of ["no-host", "unsupported", "checking"] as CapabilityState[]) {
      vi.clearAllMocks();
      renderWithCapabilityState(state);
      expect(mockedUseKnowledgeInsights).not.toHaveBeenCalled();
    }
  });
});
