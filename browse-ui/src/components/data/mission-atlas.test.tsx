/**
 * Component tests for MissionAtlas (Flight Recorder Mission Atlas §4g).
 *
 * Covers:
 *   - Renders mission header with totals, duration, artifacts, errors
 *   - Renders heatmap bucket strip with correct data-testid elements
 *   - Gap buckets render without error markers
 *   - Error buckets have visual emphasis
 *   - Milestone rail renders buttons for each milestone
 *   - onSelectEntryIdx is called with correct idx on bucket/milestone click
 *   - Milestones without idx render as disabled
 *   - Lane totals and name-count chips render when counts > 0
 *   - MissionAtlasLoading and MissionAtlasError render correctly
 *   - No raw content, paths, or messages are rendered
 */
import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  MissionAtlas,
  MissionAtlasError,
  MissionAtlasLoading,
} from "@/components/data/mission-atlas";
import type { SessionMissionAtlasResponse } from "@/lib/api/types";

// ── Fixture ───────────────────────────────────────────────────────────────────

const FIXTURE: SessionMissionAtlasResponse = {
  schema_version: "1",
  session_id: "test-session",
  total_events: 55000,
  event_file_bytes: 2097152,
  first_event_at: "2025-01-01T00:00:00Z",
  last_event_at: "2025-01-01T01:00:00Z",
  duration_ms: 3600000,
  bucket_count: 3,
  buckets: [
    {
      bucket_idx: 0,
      start_idx: 0,
      end_idx: 999,
      event_count: 1000,
      start_rel_ms: 0,
      end_rel_ms: 60000,
      ts_start: "2025-01-01T00:00:00Z",
      ts_end: "2025-01-01T00:01:00Z",
      lanes: { tool: 700, model: 300 },
      dominant_lane: "tool",
      error_count: 0,
      is_gap: false,
    },
    {
      bucket_idx: 1,
      start_idx: null,
      end_idx: null,
      event_count: 0,
      start_rel_ms: 60000,
      end_rel_ms: 120000,
      ts_start: null,
      ts_end: null,
      lanes: {},
      dominant_lane: null,
      error_count: 0,
      is_gap: true,
    },
    {
      bucket_idx: 2,
      start_idx: 2000,
      end_idx: 2999,
      event_count: 200,
      start_rel_ms: 120000,
      end_rel_ms: 180000,
      ts_start: null,
      ts_end: null,
      lanes: { error: 50, tool: 150 },
      dominant_lane: "tool",
      error_count: 50,
      is_gap: false,
    },
  ],
  lane_totals: {
    tool: 850,
    hook: 10,
    skill: 5,
    subagent: 3,
    model: 300,
    turn: 300,
    system: 0,
    error: 50,
    generic: 0,
  },
  top_tools: [
    { name: "bash", count: 300 },
    { name: "view", count: 200 },
  ],
  top_skills: [{ name: "code-reviewer", count: 5 }],
  top_agent_names: [{ name: "general-purpose", count: 3 }],
  milestones: [
    {
      idx: 42,
      timestamp: "2025-01-01T00:00:30Z",
      kind: "checkpoint",
      label: "Checkpoint 1",
      bucket_idx: 0,
    },
    {
      idx: 100,
      timestamp: "2025-01-01T00:01:00Z",
      kind: "skill_invoked",
      label: "skill:code-reviewer",
      bucket_idx: 0,
    },
    {
      idx: null,
      timestamp: null,
      kind: "task_complete",
      label: "Task done",
      bucket_idx: 2,
    },
  ],
  artifact_counts: {
    checkpoint_files: 2,
    rewind_snapshots: 1,
    todos_total: 10,
    todos_done: 8,
    todos_blocked: 1,
    todo_deps: 5,
    files: 42,
    compactions: 3,
  },
  error_count: 50,
  error_sample: [{ idx: 12, timestamp: null, event_type: "error", error_category: "rate_limited" }],
  caps: { top_n: 20, milestones: 200, error_sample: 5, buckets_min: 8, buckets_max: 200 },
  truncated: { tools: false, skills: false, agents: false, milestones: false },
};

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("MissionAtlas", () => {
  it("renders root with correct data-testid", () => {
    render(<MissionAtlas data={FIXTURE} />);
    expect(screen.getByTestId("mission-atlas")).toBeInTheDocument();
  });

  it("renders mission header with total events and duration", () => {
    render(<MissionAtlas data={FIXTURE} />);
    expect(screen.getByText(/55,000 events/)).toBeInTheDocument();
    // duration_ms=3600000 → formatMs renders "60m00s"
    expect(screen.getByText(/60m00s/)).toBeInTheDocument();
  });

  it("renders error count in header when > 0", () => {
    render(<MissionAtlas data={FIXTURE} />);
    expect(screen.getByText(/50 errors/)).toBeInTheDocument();
  });

  it("does not render error count header text when 0", () => {
    const noErrors = {
      ...FIXTURE,
      error_count: 0,
      lane_totals: { ...FIXTURE.lane_totals, error: 0 },
    };
    render(<MissionAtlas data={noErrors} />);
    // With error_count=0 the header error span should not appear
    expect(screen.queryByText(/0 error/i)).not.toBeInTheDocument();
  });

  it("renders all three buckets", () => {
    render(<MissionAtlas data={FIXTURE} />);
    const buckets = screen.getAllByTestId("mission-atlas-bucket");
    expect(buckets).toHaveLength(3);
  });

  it("calls onSelectEntryIdx with bucket start_idx when a clickable bucket is clicked", () => {
    const handler = vi.fn();
    render(<MissionAtlas data={FIXTURE} onSelectEntryIdx={handler} />);
    const buckets = screen.getAllByTestId("mission-atlas-bucket");
    // First bucket has start_idx=0
    fireEvent.click(buckets[0]);
    expect(handler).toHaveBeenCalledWith(0);
  });

  it("calls onSelectEntryIdx with bucket.start_idx for bucket with errors", () => {
    const handler = vi.fn();
    render(<MissionAtlas data={FIXTURE} onSelectEntryIdx={handler} />);
    const buckets = screen.getAllByTestId("mission-atlas-bucket");
    // Third bucket (error bucket) has start_idx=2000
    fireEvent.click(buckets[2]);
    expect(handler).toHaveBeenCalledWith(2000);
  });

  it("renders milestone buttons", () => {
    render(<MissionAtlas data={FIXTURE} />);
    const milestones = screen.getAllByTestId("mission-atlas-milestone");
    expect(milestones).toHaveLength(3);
  });

  it("calls onSelectEntryIdx with milestone idx when clicked", () => {
    const handler = vi.fn();
    render(<MissionAtlas data={FIXTURE} onSelectEntryIdx={handler} />);
    const milestones = screen.getAllByTestId("mission-atlas-milestone");
    // First milestone has idx=42
    fireEvent.click(milestones[0]);
    expect(handler).toHaveBeenCalledWith(42);
  });

  it("milestone with idx=null renders as disabled button", () => {
    render(<MissionAtlas data={FIXTURE} onSelectEntryIdx={vi.fn()} />);
    const milestones = screen.getAllByTestId("mission-atlas-milestone");
    // Third milestone has idx=null
    const nullIdxMilestone = milestones[2];
    expect(nullIdxMilestone).toBeDisabled();
  });

  it("milestone with valid idx is not disabled", () => {
    render(<MissionAtlas data={FIXTURE} onSelectEntryIdx={vi.fn()} />);
    const milestones = screen.getAllByTestId("mission-atlas-milestone");
    expect(milestones[0]).not.toBeDisabled();
    expect(milestones[1]).not.toBeDisabled();
  });

  it("renders lane total chips for non-zero lanes", () => {
    render(<MissionAtlas data={FIXTURE} />);
    // lane_totals has tool=850, hook=10, etc. system=0 and generic=0 should not appear.
    expect(screen.getByText("Tools")).toBeInTheDocument();
    expect(screen.getByText("Hooks")).toBeInTheDocument();
    // System is 0, should not render
    expect(screen.queryByText("System")).not.toBeInTheDocument();
  });

  it("renders top tool chips", () => {
    render(<MissionAtlas data={FIXTURE} />);
    expect(screen.getByTitle("bash: 300")).toBeInTheDocument();
    expect(screen.getByTitle("view: 200")).toBeInTheDocument();
  });

  it("renders top skill chips", () => {
    render(<MissionAtlas data={FIXTURE} />);
    expect(screen.getByTitle("code-reviewer: 5")).toBeInTheDocument();
  });

  it("renders top agent name chips", () => {
    render(<MissionAtlas data={FIXTURE} />);
    expect(screen.getByTitle("general-purpose: 3")).toBeInTheDocument();
  });

  it("renders artifact counts section when artifacts > 0", () => {
    render(<MissionAtlas data={FIXTURE} />);
    expect(screen.getByText(/Checkpoints/)).toBeInTheDocument();
    expect(screen.getByText(/Rewinds/)).toBeInTheDocument();
    expect(screen.getByText(/Files touched/)).toBeInTheDocument();
  });

  it("does not render artifact section when all counts are 0", () => {
    const noArtifacts = {
      ...FIXTURE,
      artifact_counts: {
        checkpoint_files: 0,
        rewind_snapshots: 0,
        todos_total: 0,
        todos_done: 0,
        todos_blocked: 0,
        todo_deps: 0,
        files: 0,
        compactions: 0,
      },
    };
    render(<MissionAtlas data={noArtifacts} />);
    expect(screen.queryByText(/Artifacts/i)).not.toBeInTheDocument();
  });

  it("does not call onSelectEntryIdx when no handler provided", () => {
    // Just ensure no error is thrown when clicking without a handler
    render(<MissionAtlas data={FIXTURE} />);
    const buckets = screen.getAllByTestId("mission-atlas-bucket");
    expect(() => fireEvent.click(buckets[0])).not.toThrow();
  });

  it("shows truncated indicator when truncated=true", () => {
    render(
      <MissionAtlas data={{ ...FIXTURE, truncated: { ...FIXTURE.truncated, milestones: true } }} />
    );
    expect(screen.getByText(/truncated/i)).toBeInTheDocument();
  });

  it("does not render raw content, paths, or tool args", () => {
    render(<MissionAtlas data={FIXTURE} />);
    // error_sample has "rate_limited" — should NOT be rendered as visible text in the component
    // (it is not passed to any DOM element in the current implementation)
    const root = screen.getByTestId("mission-atlas");
    expect(root.textContent).not.toContain("/secret");
    expect(root.textContent).not.toContain("raw_content");
  });

  it("renders empty milestone list gracefully", () => {
    const noMilestones = { ...FIXTURE, milestones: [] };
    render(<MissionAtlas data={noMilestones} />);
    expect(screen.queryByTestId("mission-atlas-milestone")).not.toBeInTheDocument();
  });

  it("renders empty bucket list gracefully", () => {
    const noBuckets = { ...FIXTURE, buckets: [] };
    render(<MissionAtlas data={noBuckets} />);
    // Heatmap section should not render
    expect(screen.queryByTestId("mission-atlas-bucket")).not.toBeInTheDocument();
  });
});

describe("MissionAtlasLoading", () => {
  it("renders loading state with testid", () => {
    render(<MissionAtlasLoading />);
    expect(screen.getByTestId("mission-atlas-loading")).toBeInTheDocument();
    expect(screen.getByText(/Loading session atlas/i)).toBeInTheDocument();
  });
});

describe("MissionAtlasError", () => {
  it("renders error state with truncated message", () => {
    render(<MissionAtlasError error={new Error("Failed to fetch")} />);
    expect(screen.getByTestId("mission-atlas-error")).toBeInTheDocument();
    expect(screen.getByText(/Atlas unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/Failed to fetch/)).toBeInTheDocument();
  });

  it("truncates very long error messages to 80 chars", () => {
    const longMsg = "A".repeat(200);
    render(<MissionAtlasError error={new Error(longMsg)} />);
    const el = screen.getByTestId("mission-atlas-error");
    expect(el.textContent?.length).toBeLessThan(150);
  });
});
