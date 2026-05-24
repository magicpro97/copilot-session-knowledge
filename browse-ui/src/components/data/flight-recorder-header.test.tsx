/**
 * Component tests for FlightRecorderHeader (Flight Recorder v3 §4d).
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { BrowseDebugSkeletonEntry } from "@/lib/api/types";
import { FlightRecorderHeader } from "@/components/data/flight-recorder-header";

function mk(
  overrides: Partial<BrowseDebugSkeletonEntry> & { idx: number }
): BrowseDebugSkeletonEntry {
  return {
    timestamp: `2024-01-01T00:00:0${overrides.idx % 10}.000Z`,
    kind: "tool_call",
    duration_ms: 50,
    status: "ok",
    span_id: `s${String(overrides.idx).padStart(4, "0")}`,
    parent_span_id: null,
    ...overrides,
  };
}

const HEALTHY: BrowseDebugSkeletonEntry[] = [
  mk({ idx: 0, kind: "session_start" }),
  mk({ idx: 1, kind: "turn_start" }),
  mk({ idx: 2, kind: "tool_call" }),
  mk({ idx: 3, kind: "agent_response" }),
];

const WITH_ERROR: BrowseDebugSkeletonEntry[] = [
  mk({ idx: 0, kind: "session_start" }),
  mk({ idx: 1, kind: "turn_start" }),
  mk({ idx: 2, kind: "error", status: "error" }),
  mk({ idx: 3, kind: "tool_call" }),
  mk({ idx: 4, kind: "agent_response" }),
];

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((q: string) => ({
      matches: false,
      media: q,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

describe("FlightRecorderHeader", () => {
  it("renders all required selectors with healthy data", () => {
    render(<FlightRecorderHeader entries={HEALTHY} total={4} hasMore={false} />);
    expect(screen.getByTestId("flight-recorder-header")).toBeInTheDocument();
    expect(screen.getByTestId("flight-recorder-status")).toHaveTextContent("OK");
    expect(screen.getByTestId("flight-recorder-duration")).toBeInTheDocument();
    expect(screen.getByTestId("flight-recorder-event-count")).toHaveTextContent(/events?/);
    expect(screen.getByTestId("flight-recorder-turn-count")).toHaveTextContent(/Turn|turns/);
  });

  it("returns null when there are no entries", () => {
    const { container } = render(<FlightRecorderHeader entries={[]} total={0} hasMore={false} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows ERROR (N) and exposes jump-error button when errors are present", () => {
    const onErr = vi.fn();
    render(
      <FlightRecorderHeader entries={WITH_ERROR} total={5} hasMore={false} onJumpToError={onErr} />
    );
    expect(screen.getByTestId("flight-recorder-status").textContent).toMatch(/ERROR \(1\)/);
    const btn = screen.getByTestId("flight-recorder-jump-error");
    fireEvent.click(btn);
    expect(onErr).toHaveBeenCalledTimes(1);
    expect(onErr.mock.calls[0][0]).toBeTypeOf("number");
  });

  it("invokes jump-last-response with the sorted index of the last agent_response", () => {
    const onResp = vi.fn();
    render(
      <FlightRecorderHeader
        entries={HEALTHY}
        total={4}
        hasMore={false}
        onJumpToLastResponse={onResp}
      />
    );
    const btn = screen.getByTestId("flight-recorder-jump-last-response");
    fireEvent.click(btn);
    expect(onResp).toHaveBeenCalledTimes(1);
    // Last agent_response is at idx 3 → sortedIndex 3 in the trivial fixture.
    expect(onResp.mock.calls[0][0]).toBe(3);
  });

  it("renders truncated chip when hasMore and total exceeds entries", () => {
    render(<FlightRecorderHeader entries={HEALTHY} total={42} hasMore={true} />);
    expect(screen.getByTestId("flight-recorder-truncated")).toBeInTheDocument();
  });

  it("does NOT render jump-error when there are no error events", () => {
    render(<FlightRecorderHeader entries={HEALTHY} total={4} hasMore={false} />);
    expect(screen.queryByTestId("flight-recorder-jump-error")).toBeNull();
  });
});
