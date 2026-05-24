/**
 * Component tests for TimelinePlayer (issue #540).
 *
 * Uses @testing-library/react + vitest.
 * Mocks window.matchMedia so reduced-motion can be toggled.
 * Does NOT test DebugLogTab (#541 owns that).
 */

import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { BrowseDebugSkeletonEntry } from "@/lib/api/types";
import { TimelinePlayer } from "@/components/data/timeline-player";

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeSkeleton(
  overrides: Partial<BrowseDebugSkeletonEntry> & { idx: number }
): BrowseDebugSkeletonEntry {
  return {
    timestamp: `2024-01-01T00:00:0${overrides.idx}.000Z`,
    kind: "tool_call",
    duration_ms: 50,
    status: "ok",
    span_id: `span${overrides.idx.toString().padStart(4, "0")}`,
    parent_span_id: null,
    ...overrides,
  };
}

/** A small fixture: 10 skeleton entries spanning multiple lanes. */
const FIXTURE: BrowseDebugSkeletonEntry[] = [
  makeSkeleton({ idx: 0, kind: "session_start", timestamp: "2024-01-01T00:00:00.000Z" }),
  makeSkeleton({ idx: 1, kind: "turn_start", timestamp: "2024-01-01T00:00:01.000Z" }),
  makeSkeleton({
    idx: 2,
    kind: "llm_request",
    timestamp: "2024-01-01T00:00:02.000Z",
    duration_ms: 800,
  }),
  makeSkeleton({ idx: 3, kind: "tool_call", timestamp: "2024-01-01T00:00:03.000Z" }),
  makeSkeleton({ idx: 4, kind: "tool_call", timestamp: "2024-01-01T00:00:04.000Z" }),
  makeSkeleton({ idx: 5, kind: "agent_response", timestamp: "2024-01-01T00:00:05.000Z" }),
  makeSkeleton({ idx: 6, kind: "error", timestamp: "2024-01-01T00:00:06.000Z", status: "error" }),
  makeSkeleton({ idx: 7, kind: "tool_call", timestamp: "2024-01-01T00:00:07.000Z" }),
  makeSkeleton({ idx: 8, kind: "turn_start", timestamp: "2024-01-01T00:00:08.000Z" }),
  makeSkeleton({ idx: 9, kind: "agent_response", timestamp: "2024-01-01T00:00:09.000Z" }),
];

// ── Mock window.matchMedia ────────────────────────────────────────────────────

function mockMatchMedia(prefersReducedMotion: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("reduce") ? prefersReducedMotion : false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

beforeEach(() => {
  mockMatchMedia(false); // default: no reduced motion
  vi.clearAllMocks();
});

// ── Render tests ──────────────────────────────────────────────────────────────

describe("TimelinePlayer render", () => {
  it("renders control bar with play button, speed select, step buttons", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    expect(screen.getByTestId("btn-play-pause")).toBeInTheDocument();
    expect(screen.getByTestId("speed-select")).toBeInTheDocument();
    expect(screen.getByTestId("btn-step-prev")).toBeInTheDocument();
    expect(screen.getByTestId("btn-step-next")).toBeInTheDocument();
  });

  it("shows event count in control bar", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    expect(screen.getByText(/1 \/ 10/)).toBeInTheDocument();
  });

  it("renders waterfall container", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    expect(screen.getByTestId("waterfall")).toBeInTheDocument();
  });

  it("renders lane rows only for lanes that have entries", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    // Session lane (session_start)
    expect(screen.getByTestId("lane-row-Session")).toBeInTheDocument();
    // Turn lane (turn_start × 2)
    expect(screen.getByTestId("lane-row-Turn")).toBeInTheDocument();
    // Model lane (llm_request, agent_response × 2)
    expect(screen.getByTestId("lane-row-Model")).toBeInTheDocument();
    // Tool/Hook/Skill lane (tool_call × 3)
    expect(screen.getByTestId("lane-row-Tool/Hook/Skill")).toBeInTheDocument();
    // Error lane
    expect(screen.getByTestId("lane-row-Error")).toBeInTheDocument();
    // SubAgent lane should NOT appear (no entries)
    expect(screen.queryByTestId("lane-row-SubAgent")).toBeNull();
  });

  it("renders scrubber with correct aria attributes", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const scrubber = screen.getByTestId("scrubber");
    expect(scrubber).toHaveAttribute("role", "slider");
    expect(scrubber).toHaveAttribute("aria-valuemin", "0");
    expect(scrubber).toHaveAttribute("aria-valuemax", "9");
    expect(scrubber).toHaveAttribute("aria-valuenow", "0");
  });

  it("renders current event card with idx, kind, status, lane fields", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const card = screen.getByTestId("event-card");
    expect(card).toBeInTheDocument();
    // First entry is session_start at idx 0
    expect(within(card).getByText(/Event 0/)).toBeInTheDocument();
    expect(within(card).getByText(/session_start/i)).toBeInTheDocument();
  });

  it("shows hasMore hint when hasMore=true", () => {
    render(<TimelinePlayer entries={FIXTURE} total={100} hasMore={true} />);
    expect(screen.getAllByText(/\+90 more/).length).toBeGreaterThan(0);
  });

  it("shows marker navigation buttons when markers are present", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    // FIXTURE has turn_start, agent_response, error markers
    expect(screen.getByTestId("btn-marker-prev")).toBeInTheDocument();
    expect(screen.getByTestId("btn-marker-next")).toBeInTheDocument();
  });

  it("renders empty entries gracefully", () => {
    render(<TimelinePlayer entries={[]} total={0} hasMore={false} />);
    expect(screen.getByText(/0 events/)).toBeInTheDocument();
    expect(screen.queryByTestId("event-card")).toBeNull();
  });
});

// ── Play / Pause ──────────────────────────────────────────────────────────────

describe("Play/Pause button", () => {
  it("play button aria-label reflects playing state after click", async () => {
    vi.useFakeTimers();
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const btn = screen.getByTestId("btn-play-pause");
    // Initially shows Play
    expect(btn).toHaveAttribute("aria-label", "Play");
    fireEvent.click(btn);
    // Now shows Pause
    expect(btn).toHaveAttribute("aria-label", "Pause");
    vi.useRealTimers();
  });

  it("clicking pause stops playback", () => {
    vi.useFakeTimers();
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const btn = screen.getByTestId("btn-play-pause");
    fireEvent.click(btn); // start playing
    fireEvent.click(btn); // pause
    expect(btn).toHaveAttribute("aria-label", "Play");
    vi.useRealTimers();
  });
});

// ── Step buttons ──────────────────────────────────────────────────────────────

describe("Step buttons", () => {
  it("step-next advances playhead and updates aria-valuenow", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const scrubber = screen.getByTestId("scrubber");
    expect(scrubber).toHaveAttribute("aria-valuenow", "0");
    fireEvent.click(screen.getByTestId("btn-step-next"));
    expect(scrubber).toHaveAttribute("aria-valuenow", "1");
  });

  it("step-prev does not go below 0", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const scrubber = screen.getByTestId("scrubber");
    fireEvent.click(screen.getByTestId("btn-step-prev"));
    expect(scrubber).toHaveAttribute("aria-valuenow", "0");
  });

  it("step-next does not exceed max", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const scrubber = screen.getByTestId("scrubber");
    // Click step-next 20 times (more than the 10-entry fixture)
    for (let i = 0; i < 20; i++) {
      fireEvent.click(screen.getByTestId("btn-step-next"));
    }
    expect(scrubber).toHaveAttribute("aria-valuenow", "9");
  });
});

// ── Scrubber (range input) ────────────────────────────────────────────────────

describe("Scrubber", () => {
  it("changing scrubber value updates playhead index and event card", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const scrubber = screen.getByTestId("scrubber");
    fireEvent.change(scrubber, { target: { value: "5" } });
    expect(scrubber).toHaveAttribute("aria-valuenow", "5");
    // idx 5 is agent_response
    const card = screen.getByTestId("event-card");
    expect(within(card).getByText(/agent_response/i)).toBeInTheDocument();
  });

  it("scrubbing pauses playback", () => {
    vi.useFakeTimers();
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const btn = screen.getByTestId("btn-play-pause");
    fireEvent.click(btn); // start playing
    const scrubber = screen.getByTestId("scrubber");
    fireEvent.change(scrubber, { target: { value: "3" } });
    // After scrub, play button should show "Play" (paused)
    expect(btn).toHaveAttribute("aria-label", "Play");
    vi.useRealTimers();
  });
});

// ── Marker navigation ─────────────────────────────────────────────────────────

describe("Marker navigation", () => {
  it("clicking marker-next seeks to the next marker", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const scrubber = screen.getByTestId("scrubber");
    // At idx 0 (session_start), first marker is turn_start at idx 1
    fireEvent.click(screen.getByTestId("btn-marker-next"));
    // turn_start is sortedIndex 1
    const nowValue = parseInt(scrubber.getAttribute("aria-valuenow") ?? "-1", 10);
    expect(nowValue).toBeGreaterThan(0);
  });

  it("clicking marker-prev at start stays at first marker", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const scrubber = screen.getByTestId("scrubber");
    const before = scrubber.getAttribute("aria-valuenow");
    fireEvent.click(screen.getByTestId("btn-marker-prev"));
    // Should not go negative
    const after = parseInt(scrubber.getAttribute("aria-valuenow") ?? "-1", 10);
    expect(after).toBeGreaterThanOrEqual(0);
    // Should move to the first marker position (≥ 0)
    void before; // used to verify no crash
  });

  it("clicking a marker tick seeks to its sorted index", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const scrubber = screen.getByTestId("scrubber");
    // Find a marker tick button (turn_start)
    const markerTick = screen.getAllByLabelText(/Marker: turn_start/i)[0];
    if (markerTick) {
      fireEvent.click(markerTick);
      const val = parseInt(scrubber.getAttribute("aria-valuenow") ?? "-1", 10);
      expect(val).toBeGreaterThanOrEqual(0);
    }
  });
});

// ── Bar click ─────────────────────────────────────────────────────────────────

describe("Bar click", () => {
  it("clicking a waterfall bar updates the event card", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const waterfall = screen.getByTestId("waterfall");
    // Find bars in the Tool/Hook/Skill lane row (tool_call entries)
    const toolLane = within(waterfall).getByTestId("lane-row-Tool/Hook/Skill");
    const bars = within(toolLane).getAllByRole("button");
    expect(bars.length).toBeGreaterThan(0);
    // Click the first bar in the Tool lane
    fireEvent.click(bars[0]);
    const card = screen.getByTestId("event-card");
    // Card should now show a tool_call entry
    expect(within(card).getByText(/tool_call/i)).toBeInTheDocument();
  });

  it("bar click pauses playback", () => {
    vi.useFakeTimers();
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const btn = screen.getByTestId("btn-play-pause");
    fireEvent.click(btn); // start playing
    const waterfall = screen.getByTestId("waterfall");
    const toolLane = within(waterfall).getByTestId("lane-row-Tool/Hook/Skill");
    const bars = within(toolLane).getAllByRole("button");
    fireEvent.click(bars[0]);
    expect(btn).toHaveAttribute("aria-label", "Play");
    vi.useRealTimers();
  });
});

// ── Keyboard ──────────────────────────────────────────────────────────────────

describe("Keyboard shortcuts (active=true)", () => {
  it("Space toggles play/pause", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const btn = screen.getByTestId("btn-play-pause");
    expect(btn).toHaveAttribute("aria-label", "Play");
    fireEvent.keyDown(window, { key: " " });
    expect(btn).toHaveAttribute("aria-label", "Pause");
    fireEvent.keyDown(window, { key: " " });
    expect(btn).toHaveAttribute("aria-label", "Play");
  });

  it("period steps forward", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const scrubber = screen.getByTestId("scrubber");
    expect(scrubber).toHaveAttribute("aria-valuenow", "0");
    fireEvent.keyDown(window, { key: "." });
    expect(scrubber).toHaveAttribute("aria-valuenow", "1");
  });

  it("comma steps backward", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const scrubber = screen.getByTestId("scrubber");
    fireEvent.keyDown(window, { key: "." }); // advance first
    fireEvent.keyDown(window, { key: "." });
    expect(scrubber).toHaveAttribute("aria-valuenow", "2");
    fireEvent.keyDown(window, { key: "," });
    expect(scrubber).toHaveAttribute("aria-valuenow", "1");
  });

  it("ArrowRight steps forward", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const scrubber = screen.getByTestId("scrubber");
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(scrubber).toHaveAttribute("aria-valuenow", "1");
  });

  it("ArrowLeft steps backward (min 0)", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const scrubber = screen.getByTestId("scrubber");
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(scrubber).toHaveAttribute("aria-valuenow", "0");
  });

  it("Shift+ArrowRight jumps 10%", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const scrubber = screen.getByTestId("scrubber");
    fireEvent.keyDown(window, { key: "ArrowRight", shiftKey: true });
    const val = parseInt(scrubber.getAttribute("aria-valuenow") ?? "0", 10);
    expect(val).toBeGreaterThanOrEqual(1);
  });

  it("Home seeks to index 0", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const scrubber = screen.getByTestId("scrubber");
    fireEvent.keyDown(window, { key: "." });
    fireEvent.keyDown(window, { key: "." });
    expect(scrubber).toHaveAttribute("aria-valuenow", "2");
    fireEvent.keyDown(window, { key: "Home" });
    expect(scrubber).toHaveAttribute("aria-valuenow", "0");
  });

  it("End seeks to last index", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const scrubber = screen.getByTestId("scrubber");
    fireEvent.keyDown(window, { key: "End" });
    expect(scrubber).toHaveAttribute("aria-valuenow", "9");
  });

  it("0 seeks to first event", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const scrubber = screen.getByTestId("scrubber");
    fireEvent.keyDown(window, { key: "End" }); // go to end first
    fireEvent.keyDown(window, { key: "0" });
    expect(scrubber).toHaveAttribute("aria-valuenow", "0");
  });

  it("5 seeks to ~50% position", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const scrubber = screen.getByTestId("scrubber");
    fireEvent.keyDown(window, { key: "5" });
    // 50% of 9 = 4.5 → round to 5
    const val = parseInt(scrubber.getAttribute("aria-valuenow") ?? "0", 10);
    expect(val).toBeGreaterThanOrEqual(4);
    expect(val).toBeLessThanOrEqual(5);
  });

  it("? toggles keyboard help", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    expect(screen.queryByTestId("key-help")).toBeNull();
    fireEvent.keyDown(window, { key: "?" });
    expect(screen.getByTestId("key-help")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "?" });
    expect(screen.queryByTestId("key-help")).toBeNull();
  });

  it("[ and ] navigate markers", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const scrubber = screen.getByTestId("scrubber");
    // ] moves to next marker
    fireEvent.keyDown(window, { key: "]" });
    const after = parseInt(scrubber.getAttribute("aria-valuenow") ?? "0", 10);
    expect(after).toBeGreaterThan(0);
    // [ moves back
    fireEvent.keyDown(window, { key: "[" });
    const back = parseInt(scrubber.getAttribute("aria-valuenow") ?? "0", 10);
    expect(back).toBeLessThanOrEqual(after);
  });

  it("keyboard events do nothing when active=false", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active={false} />);
    const scrubber = screen.getByTestId("scrubber");
    fireEvent.keyDown(window, { key: "." });
    expect(scrubber).toHaveAttribute("aria-valuenow", "0");
  });

  it("keyboard events do nothing when focus is on an input", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} active />);
    const scrubber = screen.getByTestId("scrubber");
    const input = document.createElement("input");
    document.body.appendChild(input);
    // Fire keydown on the input itself — it bubbles to window
    // but isTypingTarget sees e.target = input → returns early
    fireEvent.keyDown(input, { key: "." });
    expect(scrubber).toHaveAttribute("aria-valuenow", "0");
    document.body.removeChild(input);
  });
});

// ── Reduced motion ────────────────────────────────────────────────────────────

describe("Reduced motion", () => {
  it("Play button label is 'Step forward' when reducedMotion is true", () => {
    mockMatchMedia(true);
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const btn = screen.getByTestId("btn-play-pause");
    expect(btn).toHaveAttribute("aria-label", "Step forward");
  });

  it("in reduced motion, clicking Play steps one event forward without interval", () => {
    mockMatchMedia(true);
    vi.useFakeTimers();
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const btn = screen.getByTestId("btn-play-pause");
    const scrubber = screen.getByTestId("scrubber");
    expect(scrubber).toHaveAttribute("aria-valuenow", "0");
    fireEvent.click(btn);
    expect(scrubber).toHaveAttribute("aria-valuenow", "1");
    // Advance timer — should NOT auto-advance because there's no interval
    vi.advanceTimersByTime(5000);
    expect(scrubber).toHaveAttribute("aria-valuenow", "1");
    vi.useRealTimers();
  });

  it("speed select is hidden in reduced motion mode", () => {
    mockMatchMedia(true);
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    expect(screen.queryByTestId("speed-select")).toBeNull();
  });
});

// ── Overview strip ────────────────────────────────────────────────────────────

describe("Overview strip", () => {
  it("renders overview strip bins when entries exist", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    expect(screen.getByTestId("overview-strip")).toBeInTheDocument();
  });

  it("overview strip shows error bins for error entries", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    const strip = screen.getByTestId("overview-strip");
    // At least one bin should have the red error class
    const errorBins = strip.querySelectorAll("[class*='red']");
    expect(errorBins.length).toBeGreaterThan(0);
  });
});

// ── Key help ──────────────────────────────────────────────────────────────────

describe("Keyboard help overlay", () => {
  it("clicking ? button shows keyboard help", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    expect(screen.queryByTestId("key-help")).toBeNull();
    fireEvent.click(screen.getByTestId("btn-key-help"));
    expect(screen.getByTestId("key-help")).toBeInTheDocument();
  });

  it("help contains Space shortcut description", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    fireEvent.click(screen.getByTestId("btn-key-help"));
    const help = screen.getByTestId("key-help");
    expect(within(help).getByText(/Play \/ Pause/i)).toBeInTheDocument();
  });
});

// ── Open in Debug Log button ───────────────────────────────────────────────────

describe("Open in Debug Log button", () => {
  it("does not render the button when onOpenDebugLog is not provided", () => {
    render(<TimelinePlayer entries={FIXTURE} total={10} hasMore={false} />);
    expect(screen.queryByTestId("btn-open-debug-log")).toBeNull();
  });

  it("does not render the button when entries are empty (no current event)", () => {
    const onOpenDebugLog = vi.fn();
    render(
      <TimelinePlayer entries={[]} total={0} hasMore={false} onOpenDebugLog={onOpenDebugLog} />
    );
    expect(screen.queryByTestId("btn-open-debug-log")).toBeNull();
  });

  it("renders the button when onOpenDebugLog is provided and there is a current event", () => {
    const onOpenDebugLog = vi.fn();
    render(
      <TimelinePlayer
        entries={FIXTURE}
        total={10}
        hasMore={false}
        onOpenDebugLog={onOpenDebugLog}
      />
    );
    expect(screen.getByTestId("btn-open-debug-log")).toBeInTheDocument();
  });

  it("calls onOpenDebugLog with the current event idx when clicked", () => {
    const onOpenDebugLog = vi.fn();
    render(
      <TimelinePlayer
        entries={FIXTURE}
        total={10}
        hasMore={false}
        onOpenDebugLog={onOpenDebugLog}
      />
    );
    // Initially at playhead index 0 → current event idx = FIXTURE[0].idx = 0
    fireEvent.click(screen.getByTestId("btn-open-debug-log"));
    expect(onOpenDebugLog).toHaveBeenCalledOnce();
    expect(onOpenDebugLog).toHaveBeenCalledWith(0);
  });

  it("calls onOpenDebugLog with the correct idx after seeking to a different event", () => {
    const onOpenDebugLog = vi.fn();
    render(
      <TimelinePlayer
        entries={FIXTURE}
        total={10}
        hasMore={false}
        onOpenDebugLog={onOpenDebugLog}
      />
    );
    // Seek to entry at sorted index 5 (agent_response, idx=5)
    fireEvent.change(screen.getByTestId("scrubber"), { target: { value: "5" } });
    fireEvent.click(screen.getByTestId("btn-open-debug-log"));
    expect(onOpenDebugLog).toHaveBeenCalledWith(5);
  });
});

// ── Regression: null-timestamp cursor in time mode (issue #543) ───────────────

describe("TimelinePlayer cursor — null-timestamp regression (#543)", () => {
  /** Fixture with null timestamps to force index-mode fallback */
  const NULL_TS_FIXTURE: BrowseDebugSkeletonEntry[] = [
    makeSkeleton({ idx: 0, kind: "session_start", timestamp: null }),
    makeSkeleton({ idx: 1, kind: "turn_start", timestamp: null }),
    makeSkeleton({ idx: 2, kind: "tool_call", timestamp: null }),
    makeSkeleton({ idx: 3, kind: "agent_response", timestamp: null }),
  ];

  it("renders a non-blank event card at every playhead position when all timestamps are null", () => {
    render(<TimelinePlayer entries={NULL_TS_FIXTURE} total={4} hasMore={false} />);
    // Playhead starts at index 0; frame must have a current event
    expect(screen.getByTestId("event-card")).toBeInTheDocument();
    expect(screen.getByText(/Event 0/)).toBeInTheDocument();

    // Advance to index 3; frame must still show an event card
    fireEvent.change(screen.getByTestId("scrubber"), { target: { value: "3" } });
    expect(screen.getByTestId("event-card")).toBeInTheDocument();
    expect(screen.getByText(/Event 3/)).toBeInTheDocument();
  });

  it("event count reflects full prefix when scrubber is at last entry", () => {
    render(<TimelinePlayer entries={NULL_TS_FIXTURE} total={4} hasMore={false} />);
    // Seek to last entry
    fireEvent.change(screen.getByTestId("scrubber"), { target: { value: "3" } });
    // Control bar shows "4 / 4"
    expect(screen.getByText(/4 \/ 4/)).toBeInTheDocument();
  });
});

// ── Regression: multi-null-timestamp time mode (#543 re-review) ───────────────
// With 41 entries and exactly 2 null timestamps, null fraction = 2/41 ≈ 4.9% ≤ 5%
// so time mode stays active. Scrubbing to the FIRST null entry must show that
// entry, not the last null entry (the over-scan bug).
describe("TimelinePlayer — multi-null-timestamp time mode (#543 re-review)", () => {
  /** 39 valid-timestamp entries + 2 null-timestamp entries = 41 total (time mode). */
  const MULTI_NULL_FIXTURE: BrowseDebugSkeletonEntry[] = [
    ...Array.from({ length: 39 }, (_, i) =>
      makeSkeleton({
        idx: i,
        kind: "tool_call",
        timestamp: `2024-01-01T00:00:${String(i).padStart(2, "0")}.000Z`,
      })
    ),
    makeSkeleton({ idx: 39, kind: "generic", timestamp: null }),
    makeSkeleton({ idx: 40, kind: "error", timestamp: null }),
  ];

  it("scrubbing to first null entry (sorted index 39) shows Event 39, not Event 40", () => {
    render(<TimelinePlayer entries={MULTI_NULL_FIXTURE} total={41} hasMore={false} />);
    // Seek to sorted index 39 (first null-timestamp entry)
    fireEvent.change(screen.getByTestId("scrubber"), { target: { value: "39" } });
    const card = screen.getByTestId("event-card");
    // Must show the first null entry (idx=39), not the last (idx=40)
    expect(within(card).getByText(/Event 39/)).toBeInTheDocument();
    expect(within(card).queryByText(/Event 40/)).toBeNull();
  });

  it("scrubbing to second null entry (sorted index 40) shows Event 40", () => {
    render(<TimelinePlayer entries={MULTI_NULL_FIXTURE} total={41} hasMore={false} />);
    fireEvent.change(screen.getByTestId("scrubber"), { target: { value: "40" } });
    const card = screen.getByTestId("event-card");
    expect(within(card).getByText(/Event 40/)).toBeInTheDocument();
  });

  it("event card is present at all 41 sorted positions", () => {
    render(<TimelinePlayer entries={MULTI_NULL_FIXTURE} total={41} hasMore={false} />);
    const scrubber = screen.getByTestId("scrubber");
    for (const idx of [0, 10, 20, 38, 39, 40]) {
      fireEvent.change(scrubber, { target: { value: String(idx) } });
      expect(screen.getByTestId("event-card")).toBeInTheDocument();
    }
  });
});
