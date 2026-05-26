import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LiveTab } from "@/app/insights/live-tab";
import { InsightsTabContext } from "@/app/insights/insights-tab-context";
import { LOCAL_HOST } from "@/lib/host-profiles";

type SseReturn = {
  events: never[];
  status: "closed" | "open" | "connecting";
  paused: boolean;
  toggle: () => void;
  dropped: number;
  capped: boolean;
  bufferLimit: number;
  clear: () => void;
};

const sseDefault: SseReturn = {
  events: [],
  status: "closed",
  paused: false,
  toggle: vi.fn(),
  dropped: 0,
  capped: false,
  bufferLimit: 200,
  clear: vi.fn(),
};

vi.mock("@/hooks/use-sse", () => ({
  useSSE: vi.fn(() => sseDefault),
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

// ── Buffer-cap indicator (#566) ───────────────────────────────────────────────

function renderActive() {
  return render(
    <InsightsTabContext.Provider
      value={{
        setActiveTab: vi.fn(),
        diagnosticsEnabled: true,
        capabilityState: "ready",
        host: LOCAL_HOST,
      }}
    >
      <LiveTab />
    </InsightsTabContext.Provider>
  );
}

describe("LiveTab — buffer cap indicator", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("hides the dropped badge when dropped count is zero", () => {
    mockedUseSSE.mockReturnValueOnce({ ...sseDefault, status: "open", dropped: 0, capped: false });
    renderActive();
    expect(screen.queryByTestId("live-buffer-dropped-badge")).not.toBeInTheDocument();
  });

  it("shows an amber 'N events dropped' badge when capped", () => {
    mockedUseSSE.mockReturnValueOnce({
      ...sseDefault,
      status: "open",
      dropped: 12,
      capped: true,
      bufferLimit: 200,
    });
    renderActive();
    const badge = screen.getByTestId("live-buffer-dropped-badge");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("12 events dropped");
    expect(badge.className).toMatch(/amber/);
    expect(badge).toHaveAttribute(
      "aria-label",
      "12 live events dropped due to 200-event buffer cap"
    );
    // No payload, ids, or titles must appear in the indicator
    expect(badge.textContent).not.toMatch(/session|id|payload/i);
  });

  it("Clear buffer button invokes clear() without reconnecting", () => {
    const clear = vi.fn();
    mockedUseSSE.mockReturnValueOnce({
      ...sseDefault,
      status: "open",
      dropped: 3,
      capped: true,
      clear,
    });
    renderActive();
    fireEvent.click(screen.getByTestId("live-clear-buffer"));
    expect(clear).toHaveBeenCalledTimes(1);
  });
});
