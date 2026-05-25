import { describe, expect, it } from "vitest";

import type {
  MissionAtlasBucket,
  MissionAtlasLaneTotals,
  MissionAtlasMilestone,
  SessionMissionAtlasResponse,
  SubagentActivityEntry,
  SubagentActivityResponse,
} from "@/lib/api/types";
import { buildFlowAggregate, pageForIdx } from "@/lib/debug-flow-aggregate";

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makeLaneTotals(overrides?: Partial<MissionAtlasLaneTotals>): MissionAtlasLaneTotals {
  return {
    tool: 0,
    hook: 0,
    skill: 0,
    subagent: 0,
    model: 0,
    turn: 0,
    system: 0,
    error: 0,
    generic: 0,
    ...overrides,
  };
}

function makeBucket(overrides: Partial<MissionAtlasBucket> = {}): MissionAtlasBucket {
  return {
    bucket_idx: 0,
    start_idx: 0,
    end_idx: 99,
    event_count: 10,
    start_rel_ms: 0,
    end_rel_ms: 1000,
    ts_start: "2024-01-01T00:00:00.000Z",
    ts_end: "2024-01-01T00:00:01.000Z",
    lanes: { tool: 5, model: 3, turn: 2 },
    dominant_lane: "tool",
    error_count: 0,
    is_gap: false,
    ...overrides,
  };
}

function makeMilestone(overrides: Partial<MissionAtlasMilestone> = {}): MissionAtlasMilestone {
  return {
    idx: 42,
    timestamp: "2024-01-01T00:00:00.500Z",
    kind: "checkpoint",
    label: "Checkpoint",
    bucket_idx: 0,
    ...overrides,
  };
}

function makeAtlas(
  overrides: Partial<SessionMissionAtlasResponse> = {}
): SessionMissionAtlasResponse {
  return {
    schema_version: "1",
    session_id: "sess-1",
    total_events: 500,
    event_file_bytes: null,
    first_event_at: "2024-01-01T00:00:00.000Z",
    last_event_at: "2024-01-01T01:00:00.000Z",
    duration_ms: 3600000,
    bucket_count: 2,
    buckets: [makeBucket(), makeBucket({ bucket_idx: 1, start_idx: 100, end_idx: 199 })],
    lane_totals: makeLaneTotals({ tool: 50, model: 30, turn: 20 }),
    top_tools: [
      { name: "bash", count: 30 },
      { name: "view", count: 20 },
    ],
    top_skills: [{ name: "my-skill", count: 5 }],
    top_agent_names: [{ name: "MyAgent", count: 2 }],
    milestones: [makeMilestone()],
    artifact_counts: {
      checkpoint_files: 1,
      rewind_snapshots: 0,
      todos_total: 0,
      todos_done: 0,
      todos_blocked: 0,
      todo_deps: 0,
      files: 0,
      compactions: 0,
    },
    error_count: 0,
    error_sample: [],
    caps: { top_n: 20, milestones: 200, error_sample: 5, buckets_min: 10, buckets_max: 200 },
    truncated: { tools: false, skills: false, agents: false, milestones: false },
    ...overrides,
  };
}

function makeActivity(
  entries: SubagentActivityEntry[] = [],
  overrides: Partial<SubagentActivityResponse> = {}
): SubagentActivityResponse {
  return {
    schema_version: "1",
    session_id: "sess-1",
    total_subagents_seen: entries.length,
    returned: entries.length,
    cap: 1000,
    truncated: false,
    dropped_pending_starts: 0,
    entries,
    ...overrides,
  };
}

function makeSubagentEntry(overrides: Partial<SubagentActivityEntry> = {}): SubagentActivityEntry {
  return {
    span_id: "span-abc",
    agent_name: "MyAgent",
    agent_display_name: "My Agent",
    model: "claude-3",
    status: "completed",
    started_at: "2024-01-01T00:01:00.000Z",
    ended_at: "2024-01-01T00:02:00.000Z",
    duration_ms: 60000,
    total_tool_calls: 5,
    total_tokens: 1000,
    error_category: null,
    error_preview: null,
    start_idx: 100,
    end_idx: 200,
    redacted: false,
    ...overrides,
  };
}

// ── pageForIdx ────────────────────────────────────────────────────────────────

describe("pageForIdx", () => {
  it("returns 0 for idx=0", () => {
    expect(pageForIdx(0, 100)).toBe(0);
  });

  it("returns 0 for idx=99 (last item on first page)", () => {
    expect(pageForIdx(99, 100)).toBe(0);
  });

  it("returns 1 for idx=100 (first item on second page)", () => {
    expect(pageForIdx(100, 100)).toBe(1);
  });

  it("returns correct page for large idx (idx=54999, pageSize=100)", () => {
    expect(pageForIdx(54999, 100)).toBe(549);
  });

  it("works with custom pageSize", () => {
    expect(pageForIdx(250, 50)).toBe(5);
  });

  it("returns 0 for idx=0 with any pageSize", () => {
    expect(pageForIdx(0, 200)).toBe(0);
  });
});

// ── buildFlowAggregate — null/empty input ─────────────────────────────────────

describe("buildFlowAggregate — null/undefined atlas", () => {
  it("returns empty model when atlas is null", () => {
    const model = buildFlowAggregate(null, null);
    expect(model.totalEvents).toBe(0);
    expect(model.buckets).toHaveLength(0);
    expect(model.subagentBars).toHaveLength(0);
    expect(model.milestones).toHaveLength(0);
    expect(model.truncationWarnings).toHaveLength(0);
    expect(model.hasTruncation).toBe(false);
  });

  it("returns empty model when atlas is undefined", () => {
    const model = buildFlowAggregate(undefined, undefined);
    expect(model.totalEvents).toBe(0);
    expect(model.buckets).toHaveLength(0);
  });

  it("includes zeroed lane totals in empty model", () => {
    const model = buildFlowAggregate(null, null);
    expect(model.legend.laneTotals.tool).toBe(0);
    expect(model.legend.laneTotals.model).toBe(0);
  });
});

describe("buildFlowAggregate — basic atlas", () => {
  it("maps totalEvents from atlas.total_events", () => {
    const model = buildFlowAggregate(makeAtlas({ total_events: 55000 }), null);
    expect(model.totalEvents).toBe(55000);
  });

  it("maps durationMs from atlas.duration_ms", () => {
    const model = buildFlowAggregate(makeAtlas({ duration_ms: 3600000 }), null);
    expect(model.durationMs).toBe(3600000);
  });

  it("handles null duration_ms", () => {
    const model = buildFlowAggregate(makeAtlas({ duration_ms: null }), null);
    expect(model.durationMs).toBeNull();
  });

  it("maps errorCount from atlas.error_count", () => {
    const model = buildFlowAggregate(makeAtlas({ error_count: 7 }), null);
    expect(model.errorCount).toBe(7);
  });

  it("maps firstEventAt and lastEventAt", () => {
    const model = buildFlowAggregate(
      makeAtlas({ first_event_at: "2024-01-01T00:00:00Z", last_event_at: "2024-01-01T01:00:00Z" }),
      null
    );
    expect(model.firstEventAt).toBe("2024-01-01T00:00:00Z");
    expect(model.lastEventAt).toBe("2024-01-01T01:00:00Z");
  });

  it("handles null first/last event timestamps", () => {
    const model = buildFlowAggregate(
      makeAtlas({ first_event_at: null, last_event_at: null }),
      null
    );
    expect(model.firstEventAt).toBeNull();
    expect(model.lastEventAt).toBeNull();
  });
});

// ── buildFlowAggregate — buckets ──────────────────────────────────────────────

describe("buildFlowAggregate — bucket mapping", () => {
  it("produces one FlowBucket per atlas bucket", () => {
    const atlas = makeAtlas({
      buckets: [makeBucket({ bucket_idx: 0 }), makeBucket({ bucket_idx: 1 })],
    });
    const model = buildFlowAggregate(atlas, null);
    expect(model.buckets).toHaveLength(2);
    expect(model.buckets[0].bucket_idx).toBe(0);
    expect(model.buckets[1].bucket_idx).toBe(1);
  });

  it("maps start_idx and end_idx from bucket", () => {
    const atlas = makeAtlas({
      buckets: [makeBucket({ start_idx: 200, end_idx: 299 })],
    });
    const model = buildFlowAggregate(atlas, null);
    expect(model.buckets[0].start_idx).toBe(200);
    expect(model.buckets[0].end_idx).toBe(299);
  });

  it("maps null start_idx gracefully", () => {
    const atlas = makeAtlas({
      buckets: [makeBucket({ start_idx: null, end_idx: null })],
    });
    const model = buildFlowAggregate(atlas, null);
    expect(model.buckets[0].start_idx).toBeNull();
    expect(model.buckets[0].end_idx).toBeNull();
  });

  it("marks is_gap buckets correctly", () => {
    const atlas = makeAtlas({
      buckets: [makeBucket({ is_gap: true, event_count: 0, lanes: {} })],
    });
    const model = buildFlowAggregate(atlas, null);
    expect(model.buckets[0].is_gap).toBe(true);
  });

  it("sets all laneIntensities to 0 for gap buckets (not counted in max)", () => {
    const atlas = makeAtlas({
      buckets: [makeBucket({ is_gap: true, event_count: 0, lanes: { tool: 5 } })],
    });
    const model = buildFlowAggregate(atlas, null);
    // Gap buckets are excluded from max computation, so max=0, intensity=0
    expect(model.buckets[0].laneIntensities["tool"]).toBe(0);
  });

  it("normalises lane intensities correctly (dominant lane gets 1.0)", () => {
    const atlas = makeAtlas({
      buckets: [makeBucket({ lanes: { tool: 10, model: 5 } })],
    });
    const model = buildFlowAggregate(atlas, null);
    expect(model.buckets[0].laneIntensities["tool"]).toBe(1.0);
    expect(model.buckets[0].laneIntensities["model"]).toBe(0.5);
  });

  it("clamps non-zero intensities to >= 0.15", () => {
    const atlas = makeAtlas({
      buckets: [
        makeBucket({ bucket_idx: 0, lanes: { tool: 1 } }),
        makeBucket({ bucket_idx: 1, lanes: { tool: 100 } }),
      ],
    });
    const model = buildFlowAggregate(atlas, null);
    // tool count=1, max=100 → ratio=0.01 → clamped to 0.15
    expect(model.buckets[0].laneIntensities["tool"]).toBeGreaterThanOrEqual(0.15);
  });

  it("sets intensity=0 for lanes absent from a bucket", () => {
    const atlas = makeAtlas({
      buckets: [makeBucket({ lanes: { tool: 5 } })],
    });
    const model = buildFlowAggregate(atlas, null);
    expect(model.buckets[0].laneIntensities["model"]).toBe(0);
    expect(model.buckets[0].laneIntensities["skill"]).toBe(0);
  });
});

// ── buildFlowAggregate — milestones ───────────────────────────────────────────

describe("buildFlowAggregate — milestones", () => {
  it("maps milestones from atlas", () => {
    const atlas = makeAtlas({
      milestones: [
        makeMilestone({ idx: 42, kind: "checkpoint", label: "CP1" }),
        makeMilestone({ idx: 100, kind: "skill", label: "skill-load" }),
      ],
    });
    const model = buildFlowAggregate(atlas, null);
    expect(model.milestones).toHaveLength(2);
    expect(model.milestones[0].idx).toBe(42);
    expect(model.milestones[0].kind).toBe("checkpoint");
    expect(model.milestones[0].label).toBe("CP1");
  });

  it("handles null milestone idx gracefully", () => {
    const atlas = makeAtlas({
      milestones: [makeMilestone({ idx: null })],
    });
    const model = buildFlowAggregate(atlas, null);
    expect(model.milestones[0].idx).toBeNull();
  });

  it("handles empty milestones array", () => {
    const model = buildFlowAggregate(makeAtlas({ milestones: [] }), null);
    expect(model.milestones).toHaveLength(0);
  });
});

// ── buildFlowAggregate — subagent bars ────────────────────────────────────────

describe("buildFlowAggregate — subagent bars", () => {
  it("returns empty subagentBars when activity is null", () => {
    const model = buildFlowAggregate(makeAtlas(), null);
    expect(model.subagentBars).toHaveLength(0);
  });

  it("maps one FlowSubagentBar per activity entry", () => {
    const activity = makeActivity([makeSubagentEntry(), makeSubagentEntry({ span_id: "span-2" })]);
    const model = buildFlowAggregate(makeAtlas(), activity);
    expect(model.subagentBars).toHaveLength(2);
  });

  it("prefers agent_display_name over agent_name for label", () => {
    const activity = makeActivity([
      makeSubagentEntry({ agent_display_name: "My Agent", agent_name: "raw-agent" }),
    ]);
    const model = buildFlowAggregate(makeAtlas(), activity);
    expect(model.subagentBars[0].label).toBe("My Agent");
  });

  it("falls back to agent_name when display_name is null", () => {
    const activity = makeActivity([
      makeSubagentEntry({ agent_display_name: null, agent_name: "raw-agent" }),
    ]);
    const model = buildFlowAggregate(makeAtlas(), activity);
    expect(model.subagentBars[0].label).toBe("raw-agent");
  });

  it("falls back to 'Sub-agent' when both name fields are null", () => {
    const activity = makeActivity([
      makeSubagentEntry({ agent_display_name: null, agent_name: null }),
    ]);
    const model = buildFlowAggregate(makeAtlas(), activity);
    expect(model.subagentBars[0].label).toBe("Sub-agent");
  });

  it("maps start_idx and end_idx for drilldown", () => {
    const activity = makeActivity([makeSubagentEntry({ start_idx: 150, end_idx: 250 })]);
    const model = buildFlowAggregate(makeAtlas(), activity);
    expect(model.subagentBars[0].start_idx).toBe(150);
    expect(model.subagentBars[0].end_idx).toBe(250);
  });

  it("handles null start_idx/end_idx gracefully", () => {
    const activity = makeActivity([makeSubagentEntry({ start_idx: null, end_idx: null })]);
    const model = buildFlowAggregate(makeAtlas(), activity);
    expect(model.subagentBars[0].start_idx).toBeNull();
    expect(model.subagentBars[0].end_idx).toBeNull();
  });

  it("does NOT include error_preview in any bar field", () => {
    const activity = makeActivity([makeSubagentEntry({ error_preview: "secret error text" })]);
    const model = buildFlowAggregate(makeAtlas(), activity);
    const bar = model.subagentBars[0];
    // Verify no bar field contains the raw error preview
    const barValues = JSON.stringify(bar);
    expect(barValues).not.toContain("secret error text");
  });
});

// ── buildFlowAggregate — truncation warnings ──────────────────────────────────

describe("buildFlowAggregate — truncation warnings", () => {
  it("produces no warnings when nothing is truncated", () => {
    const model = buildFlowAggregate(makeAtlas(), null);
    expect(model.truncationWarnings).toHaveLength(0);
    expect(model.hasTruncation).toBe(false);
  });

  it("adds warning for truncated tools", () => {
    const atlas = makeAtlas({
      truncated: { tools: true, skills: false, agents: false, milestones: false },
    });
    const model = buildFlowAggregate(atlas, null);
    expect(model.truncationWarnings.some((w) => w.includes("tool"))).toBe(true);
    expect(model.hasTruncation).toBe(true);
  });

  it("adds warning for truncated skills", () => {
    const atlas = makeAtlas({
      truncated: { tools: false, skills: true, agents: false, milestones: false },
    });
    const model = buildFlowAggregate(atlas, null);
    expect(model.truncationWarnings.some((w) => w.includes("skill"))).toBe(true);
  });

  it("adds warning for truncated agents", () => {
    const atlas = makeAtlas({
      truncated: { tools: false, skills: false, agents: true, milestones: false },
    });
    const model = buildFlowAggregate(atlas, null);
    expect(model.truncationWarnings.some((w) => w.includes("agent"))).toBe(true);
  });

  it("adds warning for truncated milestones", () => {
    const atlas = makeAtlas({
      truncated: { tools: false, skills: false, agents: false, milestones: true },
    });
    const model = buildFlowAggregate(atlas, null);
    expect(model.truncationWarnings.some((w) => w.includes("milestone"))).toBe(true);
  });

  it("adds warning for truncated subagent activity", () => {
    const activity = makeActivity([], { truncated: true });
    const model = buildFlowAggregate(makeAtlas(), activity);
    expect(model.truncationWarnings.some((w) => w.includes("sub-agent"))).toBe(true);
    expect(model.hasTruncation).toBe(true);
  });

  it("accumulates multiple warnings", () => {
    const atlas = makeAtlas({
      truncated: { tools: true, skills: true, agents: true, milestones: false },
    });
    const model = buildFlowAggregate(atlas, makeActivity([], { truncated: true }));
    expect(model.truncationWarnings.length).toBeGreaterThanOrEqual(4);
  });
});

// ── buildFlowAggregate — legend ───────────────────────────────────────────────

describe("buildFlowAggregate — legend", () => {
  it("includes top tools (up to 5)", () => {
    const tools = Array.from({ length: 10 }, (_, i) => ({ name: `tool-${i}`, count: 10 - i }));
    const model = buildFlowAggregate(makeAtlas({ top_tools: tools }), null);
    expect(model.legend.topTools).toHaveLength(5);
    expect(model.legend.topTools[0].name).toBe("tool-0");
  });

  it("includes top skills (up to 5)", () => {
    const skills = Array.from({ length: 6 }, (_, i) => ({ name: `skill-${i}`, count: 6 - i }));
    const model = buildFlowAggregate(makeAtlas({ top_skills: skills }), null);
    expect(model.legend.topSkills).toHaveLength(5);
  });

  it("includes top agents (up to 5)", () => {
    const agents = Array.from({ length: 8 }, (_, i) => ({ name: `agent-${i}`, count: 8 - i }));
    const model = buildFlowAggregate(makeAtlas({ top_agent_names: agents }), null);
    expect(model.legend.topAgents).toHaveLength(5);
  });

  it("maps lane totals from atlas", () => {
    const atlas = makeAtlas({ lane_totals: makeLaneTotals({ tool: 100, model: 50 }) });
    const model = buildFlowAggregate(atlas, null);
    expect(model.legend.laneTotals.tool).toBe(100);
    expect(model.legend.laneTotals.model).toBe(50);
  });
});

// ── idx coordinate stability ──────────────────────────────────────────────────

describe("pageForIdx — idx coordinate boundary cases", () => {
  const PAGE_SIZE = 100;

  it("idx=0 → page 0", () => expect(pageForIdx(0, PAGE_SIZE)).toBe(0));
  it("idx=99 → page 0 (last on first page)", () => expect(pageForIdx(99, PAGE_SIZE)).toBe(0));
  it("idx=100 → page 1 (first on second page)", () => expect(pageForIdx(100, PAGE_SIZE)).toBe(1));
  it("idx=54999 → page 549", () => expect(pageForIdx(54999, PAGE_SIZE)).toBe(549));
  it("idx=55000 → page 550", () => expect(pageForIdx(55000, PAGE_SIZE)).toBe(550));
});
