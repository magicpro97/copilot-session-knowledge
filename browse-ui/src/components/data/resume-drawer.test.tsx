/**
 * Component tests for ResumeDrawer (Flight Recorder v3 §4d).
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ResumeDrawer } from "@/components/data/resume-drawer";
import type { ResumeAnchor } from "@/lib/flight-recorder";

function anchor(overrides: Partial<ResumeAnchor> & { snapshotId: string }): ResumeAnchor {
  return {
    timestamp: "2024-01-01T00:00:00.000Z",
    timestampMs: 0,
    gitCommit: "abc1234abc1234abc1234abc1234abc1234abcd",
    gitBranch: "main",
    fileCount: 3,
    userMessagePresent: true,
    userMessageByteSize: 42,
    eventSpanId: "00112233abcdef00",
    taskCompleteEntryIdx: 7,
    taskCompleteSpanId: "00112233abcdef00",
    association: "exact",
    associationDistanceMs: null,
    ...overrides,
  };
}

describe("ResumeDrawer", () => {
  it("renders closed by default and exposes aria-expanded=false", () => {
    render(<ResumeDrawer anchors={[anchor({ snapshotId: "snap-1" })]} />);
    expect(screen.getByTestId("resume-drawer-toggle")).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("resume-anchor-snap-1")).toBeNull();
  });

  it("expands and exposes data-git-commit + data-event-span-id", () => {
    render(<ResumeDrawer anchors={[anchor({ snapshotId: "snap-1" })]} />);
    fireEvent.click(screen.getByTestId("resume-drawer-toggle"));
    const row = screen.getByTestId("resume-anchor-snap-1");
    expect(row).toHaveAttribute("data-git-commit", "abc1234abc1234abc1234abc1234abc1234abcd");
    expect(row).toHaveAttribute("data-event-span-id", "00112233abcdef00");
    expect(row).toHaveAttribute("data-association", "exact");
  });

  it("calls onSeek with the anchor when Jump is clicked (exact association)", () => {
    const onSeek = vi.fn();
    render(
      <ResumeDrawer
        anchors={[anchor({ snapshotId: "snap-1" })]}
        defaultOpen={true}
        onSeek={onSeek}
      />
    );
    const btn = screen.getByRole("button", { name: /jump to resume point snap-1.*exact match/i });
    fireEvent.click(btn);
    expect(onSeek).toHaveBeenCalledTimes(1);
    expect(onSeek.mock.calls[0][0].snapshotId).toBe("snap-1");
  });

  it("disables Jump only when association is 'unavailable'", () => {
    render(
      <ResumeDrawer
        anchors={[
          anchor({
            snapshotId: "snap-2",
            taskCompleteEntryIdx: null,
            taskCompleteSpanId: null,
            association: "unavailable",
            associationDistanceMs: null,
          }),
        ]}
        defaultOpen={true}
      />
    );
    const btn = screen.getByRole("button", { name: /jump unavailable for resume point snap-2/i });
    expect(btn).toBeDisabled();
  });

  it("keeps Jump enabled for 'nearest' association and shows the gap label", () => {
    const onSeek = vi.fn();
    render(
      <ResumeDrawer
        anchors={[
          anchor({
            snapshotId: "snap-near",
            association: "nearest",
            associationDistanceMs: 2500,
          }),
        ]}
        defaultOpen={true}
        onSeek={onSeek}
      />
    );
    const row = screen.getByTestId("resume-anchor-snap-near");
    expect(row).toHaveAttribute("data-association", "nearest");
    expect(row).toHaveAttribute("data-association-distance-ms", "2500");
    const btn = screen.getByRole("button", { name: /jump to resume point snap-near.*nearest/i });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(onSeek).toHaveBeenCalledTimes(1);
  });

  it("shows empty state when no anchors and drawer open", () => {
    render(<ResumeDrawer anchors={[]} defaultOpen={true} />);
    expect(screen.getByText(/no resume points/i)).toBeInTheDocument();
  });
});
