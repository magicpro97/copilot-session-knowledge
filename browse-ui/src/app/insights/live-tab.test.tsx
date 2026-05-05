import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LiveTab } from "@/app/insights/live-tab";
import { InsightsTabContext } from "@/app/insights/insights-tab-context";
import { LOCAL_HOST } from "@/lib/host-profiles";

vi.mock("@/hooks/use-sse", () => ({
  useSSE: vi.fn(() => ({
    events: [],
    status: "closed" as const,
    paused: false,
    toggle: vi.fn(),
  })),
}));

vi.mock("@/lib/api/hooks", () => ({
  createLiveStreamUrl: vi.fn(() => "http://test-stream"),
}));

vi.mock("lucide-react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("lucide-react")>();
  return {
    ...actual,
    CircleDot: () => <span data-testid="icon-circle-dot" />,
  };
});

import { useSSE } from "@/hooks/use-sse";
const mockedUseSSE = vi.mocked(useSSE);

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
      <LiveTab />
    </InsightsTabContext.Provider>
  );
}

describe("LiveTab — idle states", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows 'No agent host selected' guidance when no host is configured", () => {
    renderWithCapabilityState("no-host");
    expect(screen.getByText(/no agent host selected/i)).toBeInTheDocument();
    expect(mockedUseSSE).not.toHaveBeenCalled();
  });

  it("shows 'Not supported by this host' when selected host lacks insights support", () => {
    renderWithCapabilityState("unsupported");
    expect(screen.getByText(/not supported by this host/i)).toBeInTheDocument();
    expect(mockedUseSSE).not.toHaveBeenCalled();
  });

  it("shows 'Checking host capabilities' while capability check is in progress", () => {
    renderWithCapabilityState("checking");
    expect(screen.getByText(/checking host capabilities/i)).toBeInTheDocument();
    expect(mockedUseSSE).not.toHaveBeenCalled();
  });
});
