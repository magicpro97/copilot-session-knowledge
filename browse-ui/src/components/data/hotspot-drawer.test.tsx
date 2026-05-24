/**
 * Component tests for HotspotDrawer (Flight Recorder v3 §4d).
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HotspotDrawer } from "@/components/data/hotspot-drawer";
import type { Hotspots } from "@/lib/flight-recorder";

const EMPTY: Hotspots = { errors: [], slowest: [], gaps: [] };

const SAMPLE: Hotspots = {
  errors: [
    { sortedIndex: 5, entryIdx: 12, timestamp: "2024-01-01T00:00:05.000Z", timestampMs: 5_000 },
  ],
  slowest: [
    { sortedIndex: 9, entryIdx: 24, durationMs: 8_200 },
    { sortedIndex: 3, entryIdx: 7, durationMs: 1_100 },
  ],
  gaps: [
    {
      startSortedIndex: 2,
      endSortedIndex: 3,
      startMs: 1_000,
      endMs: 1_000 + 90_000,
      gapMs: 90_000,
    },
  ],
};

describe("HotspotDrawer", () => {
  it("renders closed by default with aria-expanded=false", () => {
    render(<HotspotDrawer hotspots={SAMPLE} />);
    const toggle = screen.getByTestId("hotspot-drawer-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("hotspot-error-5")).toBeNull();
  });

  it("expands on click and exposes each row test id", () => {
    render(<HotspotDrawer hotspots={SAMPLE} />);
    fireEvent.click(screen.getByTestId("hotspot-drawer-toggle"));
    expect(screen.getByTestId("hotspot-drawer-toggle")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("hotspot-error-5")).toBeInTheDocument();
    expect(screen.getByTestId("hotspot-slow-9")).toBeInTheDocument();
    expect(screen.getByTestId("hotspot-slow-3")).toBeInTheDocument();
    expect(screen.getByTestId("hotspot-gap-2")).toBeInTheDocument();
  });

  it("calls onSeek with the row's sortedIndex when Go-to is clicked", () => {
    const onSeek = vi.fn();
    render(<HotspotDrawer hotspots={SAMPLE} onSeek={onSeek} defaultOpen={true} />);
    const errRow = screen.getByTestId("hotspot-error-5");
    fireEvent.click(errRow.querySelector("button")!);
    expect(onSeek).toHaveBeenCalledWith(5);
  });

  it("shows 'No hotspots detected' when all lists empty and drawer open", () => {
    render(<HotspotDrawer hotspots={EMPTY} defaultOpen={true} />);
    expect(screen.getByText(/no hotspots detected/i)).toBeInTheDocument();
  });
});
