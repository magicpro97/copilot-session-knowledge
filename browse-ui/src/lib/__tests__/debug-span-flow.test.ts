import { describe, expect, it } from "vitest";

import type { BrowseDebugEntry, DebugSafeAttrs } from "@/lib/api/types";
import {
  computeFlowLayout,
  computeTraceLayout,
  deriveGroupKey,
  deriveInspectorCard,
  deriveNodeCategory,
  deriveNodeRender,
  deriveNodeStatus,
  FLOW_HORIZONTAL_GAP,
  FLOW_INDENT_PER_LEVEL,
  FLOW_NODE_HEIGHT,
  FLOW_NODE_WIDTH,
  FLOW_VERTICAL_GAP,
  formatDurationLabel,
  formatTimestampLabel,
  laneIdSlug,
  MIN_BAR_RATIO,
  readSafeAttrs,
} from "@/lib/debug-span-flow";

function makeEntry(overrides: Partial<BrowseDebugEntry> & { idx: number }): BrowseDebugEntry {
  return {
    idx: overrides.idx,
    timestamp: overrides.timestamp ?? "2024-01-01T12:00:00.000Z",
    kind: overrides.kind ?? "tool_call",
    level: overrides.level ?? "info",
    source: overrides.source ?? "operator_console",
    message: overrides.message ?? "msg",
    tool_name: overrides.tool_name ?? null,
    duration_ms: overrides.duration_ms ?? null,
    span_id: overrides.span_id ?? null,
    parent_span_id: overrides.parent_span_id ?? null,
    status: overrides.status ?? null,
    attrs: overrides.attrs ?? null,
    redacted: overrides.redacted ?? false,
  };
}

function withAttrs(
  overrides: Partial<BrowseDebugEntry> & { idx: number },
  attrs: DebugSafeAttrs
): BrowseDebugEntry {
  return makeEntry({ ...overrides, attrs: attrs as Record<string, unknown> });
}

describe("computeFlowLayout", () => {
  it("returns an empty layout for an empty entry list", () => {
    const layout = computeFlowLayout([]);
    expect(layout.nodes).toHaveLength(0);
    expect(layout.edges).toHaveLength(0);
    expect(layout.width).toBe(0);
    expect(layout.height).toBe(0);
  });

  it("places a single root with no children at depth 0", () => {
    const layout = computeFlowLayout([makeEntry({ idx: 0, span_id: "a", parent_span_id: null })]);
    expect(layout.nodes).toHaveLength(1);
    expect(layout.edges).toHaveLength(0);
    expect(layout.nodes[0].depth).toBe(0);
    expect(layout.nodes[0].y).toBe(0);
    expect(layout.nodes[0].width).toBe(FLOW_NODE_WIDTH);
    expect(layout.nodes[0].height).toBe(FLOW_NODE_HEIGHT);
  });

  it("connects parent to children via edges derived from parent_span_id", () => {
    const layout = computeFlowLayout([
      makeEntry({ idx: 0, span_id: "root", parent_span_id: null }),
      makeEntry({ idx: 1, span_id: "child1", parent_span_id: "root" }),
      makeEntry({ idx: 2, span_id: "child2", parent_span_id: "root" }),
    ]);
    expect(layout.edges).toHaveLength(2);
    expect(layout.edges.map((e) => e.fromId).every((f) => f === "node-0")).toBe(true);
    expect(layout.edges.map((e) => e.toId).sort()).toEqual(["node-1", "node-2"]);
  });

  it("places children below their parent (greater y)", () => {
    const layout = computeFlowLayout([
      makeEntry({ idx: 0, span_id: "root", parent_span_id: null }),
      makeEntry({ idx: 1, span_id: "child", parent_span_id: "root" }),
    ]);
    const root = layout.nodes.find((n) => n.id === "node-0")!;
    const child = layout.nodes.find((n) => n.id === "node-1")!;
    expect(child.y).toBeGreaterThan(root.y);
    expect(child.y - root.y).toBe(FLOW_NODE_HEIGHT + FLOW_VERTICAL_GAP);
  });

  it("places sibling roots vertically in DFS pre-order (not side-by-side)", () => {
    const layout = computeFlowLayout([
      makeEntry({ idx: 0, span_id: "a", parent_span_id: null }),
      makeEntry({ idx: 1, span_id: "b", parent_span_id: null }),
    ]);
    const a = layout.nodes.find((n) => n.id === "node-0")!;
    const b = layout.nodes.find((n) => n.id === "node-1")!;
    // Both roots at depth 0 → x = 0
    expect(a.x).toBe(0);
    expect(b.x).toBe(0);
    // b is placed one row below a
    expect(b.y - a.y).toBe(FLOW_NODE_HEIGHT + FLOW_VERTICAL_GAP);
    expect(a.y).toBeLessThan(b.y);
    // FLOW_HORIZONTAL_GAP is exported for back-compat but not used for y-layout
    expect(FLOW_HORIZONTAL_GAP).toBeGreaterThan(0);
  });

  it("indents children by FLOW_INDENT_PER_LEVEL × depth", () => {
    const layout = computeFlowLayout([
      makeEntry({ idx: 0, span_id: "root", parent_span_id: null }),
      makeEntry({ idx: 1, span_id: "child", parent_span_id: "root" }),
    ]);
    const root = layout.nodes.find((n) => n.id === "node-0")!;
    const child = layout.nodes.find((n) => n.id === "node-1")!;
    expect(root.x).toBe(0);
    expect(child.x).toBe(FLOW_INDENT_PER_LEVEL);
  });

  it("places nodes in DFS pre-order: parent before its children, first subtree before sibling subtrees", () => {
    // Tree:  root → [childA → grandchild, childB]
    const layout = computeFlowLayout([
      makeEntry({ idx: 0, span_id: "root", parent_span_id: null }),
      makeEntry({ idx: 1, span_id: "childA", parent_span_id: "root" }),
      makeEntry({ idx: 2, span_id: "grand", parent_span_id: "childA" }),
      makeEntry({ idx: 3, span_id: "childB", parent_span_id: "root" }),
    ]);
    const positions = [0, 1, 2, 3].map((i) => layout.nodes.find((n) => n.id === `node-${i}`)!.y);
    // DFS pre-order: root(0) < childA(1) < grand(2) < childB(3)
    expect(positions[0]).toBeLessThan(positions[1]);
    expect(positions[1]).toBeLessThan(positions[2]);
    expect(positions[2]).toBeLessThan(positions[3]);
  });

  it("collects orphans under a synthetic node and exposes it as a node", () => {
    const layout = computeFlowLayout([
      makeEntry({ idx: 7, span_id: "x", parent_span_id: "missing-parent" }),
    ]);
    const synthetic = layout.nodes.find((n) => n.id === "synthetic-orphan");
    expect(synthetic).toBeDefined();
    expect(synthetic?.isSynthetic).toBe(true);
    // Orphan child wired under synthetic root via an edge
    expect(layout.edges).toContainEqual({ fromId: "synthetic-orphan", toId: "node-7" });
  });

  it("does not crash when entries have null timestamps, durations, or span_ids", () => {
    expect(() =>
      computeFlowLayout([
        makeEntry({ idx: 0, timestamp: null, duration_ms: null, span_id: null }),
        makeEntry({ idx: 1, timestamp: null, duration_ms: null, span_id: null }),
      ])
    ).not.toThrow();
  });

  it("preserves entry references on each node (used for click → detail drawer)", () => {
    const entry = makeEntry({ idx: 0, message: "hello flow" });
    const layout = computeFlowLayout([entry]);
    expect(layout.nodes[0].entry).toBe(entry);
  });

  it("attaches a derived render model to every node, including the synthetic orphan", () => {
    const layout = computeFlowLayout([
      makeEntry({ idx: 0, span_id: "root", parent_span_id: null, kind: "session_start" }),
      makeEntry({ idx: 1, span_id: "x", parent_span_id: "missing" }),
    ]);
    const root = layout.nodes.find((n) => n.id === "node-0")!;
    const orphanGroup = layout.nodes.find((n) => n.id === "synthetic-orphan")!;
    expect(root.render.category).toBe("session");
    expect(root.render.tooltipLines.length).toBeGreaterThan(0);
    expect(orphanGroup.render.label).toBe("(orphans)");
    expect(orphanGroup.render.category).toBe("generic");
    expect(orphanGroup.render.status).toBe("unknown");
  });
});

describe("formatDurationLabel", () => {
  it("formats sub-second durations in ms", () => {
    expect(formatDurationLabel(0)).toBe("0ms");
    expect(formatDurationLabel(42)).toBe("42ms");
    expect(formatDurationLabel(999)).toBe("999ms");
  });

  it("formats >=1s durations as seconds with 2 decimals", () => {
    expect(formatDurationLabel(1000)).toBe("1.00s");
    expect(formatDurationLabel(1234)).toBe("1.23s");
  });

  it("returns null for null/undefined/negative/non-finite", () => {
    expect(formatDurationLabel(null)).toBeNull();
    expect(formatDurationLabel(undefined)).toBeNull();
    expect(formatDurationLabel(-1)).toBeNull();
    expect(formatDurationLabel(Number.NaN)).toBeNull();
    expect(formatDurationLabel(Number.POSITIVE_INFINITY)).toBeNull();
  });
});

describe("formatTimestampLabel", () => {
  it("formats valid ISO timestamps as HH:MM:SS in UTC", () => {
    expect(formatTimestampLabel("2024-01-01T12:34:56.789Z")).toBe("12:34:56");
    expect(formatTimestampLabel("2024-06-01T00:00:00Z")).toBe("00:00:00");
  });

  it("returns null for null/empty/unparseable timestamps", () => {
    expect(formatTimestampLabel(null)).toBeNull();
    expect(formatTimestampLabel(undefined)).toBeNull();
    expect(formatTimestampLabel("")).toBeNull();
    expect(formatTimestampLabel("not-a-date")).toBeNull();
  });
});

describe("readSafeAttrs", () => {
  it("returns an empty object for null attrs", () => {
    expect(readSafeAttrs(makeEntry({ idx: 0, attrs: null }))).toEqual({});
  });

  it("returns the attrs object when present", () => {
    const entry = withAttrs({ idx: 0 }, { tool_status: "ok", output_tokens: 17 });
    expect(readSafeAttrs(entry).tool_status).toBe("ok");
    expect(readSafeAttrs(entry).output_tokens).toBe(17);
  });
});

describe("deriveNodeCategory", () => {
  it("categorises tool calls by kind or tool_name", () => {
    expect(deriveNodeCategory(makeEntry({ idx: 0, kind: "tool_call", tool_name: "Read" }))).toBe(
      "tool"
    );
    expect(deriveNodeCategory(makeEntry({ idx: 0, kind: "generic", tool_name: "Bash" }))).toBe(
      "tool"
    );
  });

  it("categorises hook/skill/notification/compaction from safe attrs", () => {
    expect(
      deriveNodeCategory(withAttrs({ idx: 0, kind: "hook" }, { hook_type: "preToolUse" }))
    ).toBe("hook");
    expect(
      deriveNodeCategory(withAttrs({ idx: 0, kind: "generic" }, { skill_name: "code-reviewer" }))
    ).toBe("skill");
    expect(
      deriveNodeCategory(
        withAttrs({ idx: 0, kind: "generic" }, { notification_kind: "agent_completed" })
      )
    ).toBe("notification");
    expect(
      deriveNodeCategory(withAttrs({ idx: 0, kind: "generic" }, { compaction_kind: "auto" }))
    ).toBe("compaction");
  });

  it("categorises assistant/llm/subagent/session/turn from kind", () => {
    expect(deriveNodeCategory(makeEntry({ idx: 0, kind: "agent_response" }))).toBe("model");
    expect(deriveNodeCategory(makeEntry({ idx: 0, kind: "llm_request" }))).toBe("model");
    expect(deriveNodeCategory(makeEntry({ idx: 0, kind: "subagent" }))).toBe("subagent");
    expect(deriveNodeCategory(makeEntry({ idx: 0, kind: "session_start" }))).toBe("session");
    expect(deriveNodeCategory(makeEntry({ idx: 0, kind: "turn_start" }))).toBe("turn");
    expect(deriveNodeCategory(makeEntry({ idx: 0, kind: "error" }))).toBe("error");
    expect(deriveNodeCategory(makeEntry({ idx: 0, kind: "generic" }))).toBe("generic");
  });
});

describe("deriveNodeStatus", () => {
  it("returns the explicit terminal status when set", () => {
    expect(deriveNodeStatus(makeEntry({ idx: 0, status: "ok" }))).toBe("ok");
    expect(deriveNodeStatus(makeEntry({ idx: 0, status: "error" }))).toBe("error");
    expect(deriveNodeStatus(makeEntry({ idx: 0, status: "cancelled" }))).toBe("cancelled");
  });

  it("collapses tool_success=false and hook_status=error to error", () => {
    expect(
      deriveNodeStatus(withAttrs({ idx: 0, kind: "tool_call" }, { tool_success: false }))
    ).toBe("error");
    expect(deriveNodeStatus(withAttrs({ idx: 0, kind: "hook" }, { hook_status: "error" }))).toBe(
      "error"
    );
  });

  it("treats kind === 'error' as error even without status", () => {
    expect(deriveNodeStatus(makeEntry({ idx: 0, kind: "error", status: null }))).toBe("error");
  });

  it("falls back to attrs.tool_status when entry.status is null", () => {
    expect(
      deriveNodeStatus(withAttrs({ idx: 0, kind: "tool_call" }, { tool_status: "cancelled" }))
    ).toBe("cancelled");
  });

  it("assumes ok when duration_ms is set and no error signal is present", () => {
    expect(deriveNodeStatus(makeEntry({ idx: 0, duration_ms: 12 }))).toBe("ok");
  });

  it("falls back to unknown when nothing is known", () => {
    expect(deriveNodeStatus(makeEntry({ idx: 0, status: null, duration_ms: null }))).toBe(
      "unknown"
    );
  });
});

describe("deriveGroupKey (same-tool merge)", () => {
  it("returns a stable key for tool_call entries with a span_id", () => {
    const a = makeEntry({ idx: 0, kind: "tool_call", tool_name: "Read", span_id: "abc123" });
    const b = makeEntry({ idx: 1, kind: "tool_call", tool_name: "Read", span_id: "abc123" });
    expect(deriveGroupKey(a)).toBe(deriveGroupKey(b));
    expect(deriveGroupKey(a)).toContain("Read");
  });

  it("returns null when span_id is missing", () => {
    expect(
      deriveGroupKey(makeEntry({ idx: 0, kind: "tool_call", tool_name: "Read", span_id: null }))
    ).toBeNull();
  });

  it("returns null for non-tool entries", () => {
    expect(
      deriveGroupKey(makeEntry({ idx: 0, kind: "agent_response", span_id: "abc", tool_name: null }))
    ).toBeNull();
  });

  it("does not collide across different tools on the same span_id", () => {
    const a = makeEntry({ idx: 0, kind: "tool_call", tool_name: "Read", span_id: "x" });
    const b = makeEntry({ idx: 1, kind: "tool_call", tool_name: "Bash", span_id: "x" });
    expect(deriveGroupKey(a)).not.toBe(deriveGroupKey(b));
  });
});

describe("deriveNodeRender — label/sublabel/tooltip", () => {
  it("renders tool entries from tool_name + safe attrs", () => {
    const r = deriveNodeRender(
      withAttrs(
        { idx: 0, kind: "tool_call", tool_name: "Read", duration_ms: 250 },
        { tool_status: "ok", tool_result_type: "string", tool_metric_input_bytes: 42 }
      )
    );
    expect(r.category).toBe("tool");
    expect(r.label).toBe("Read");
    expect(r.sublabel).toContain("ok");
    expect(r.sublabel).toContain("string");
    expect(r.sublabel).toContain("250ms");
    expect(r.status).toBe("ok");
    expect(r.tooltipLines.some((l) => l.startsWith("tool: Read"))).toBe(true);
    expect(r.tooltipLines.some((l) => l.startsWith("input: 42B"))).toBe(true);
  });

  it("renders hook entries with hook_type + event_phase", () => {
    const r = deriveNodeRender(
      withAttrs(
        { idx: 0, kind: "hook", source: "hook_runner" },
        {
          hook_type: "preToolUse",
          hook_status: "ok",
          event_phase: "start",
          event_type: "hook.start",
        }
      )
    );
    expect(r.category).toBe("hook");
    expect(r.label).toBe("preToolUse");
    expect(r.sublabel).toContain("ok");
    expect(r.sublabel).toContain("start");
    expect(r.status).toBe("ok");
  });

  it("renders skill entries with skill_name + path_category + bytes", () => {
    const r = deriveNodeRender(
      withAttrs(
        { idx: 0, kind: "generic" },
        {
          skill_name: "code-reviewer",
          skill_path_category: "skill_pkg",
          skill_content_bytes: 1024,
        }
      )
    );
    expect(r.category).toBe("skill");
    expect(r.label).toBe("code-reviewer");
    expect(r.sublabel).toContain("skill_pkg");
    expect(r.sublabel).toContain("1024B");
  });

  it("renders assistant/model entries with output_tokens and tool_request_count", () => {
    const r = deriveNodeRender(
      withAttrs(
        { idx: 0, kind: "agent_response", duration_ms: 1200 },
        { output_tokens: 350, tool_request_count: 2 }
      )
    );
    expect(r.category).toBe("model");
    expect(r.label).toBe("agent_response");
    expect(r.sublabel).toContain("350 tok");
    expect(r.sublabel).toContain("2 tool req");
    expect(r.sublabel).toContain("1.20s");
  });

  it("renders subagent entries with a layer derived from mode", () => {
    const r = deriveNodeRender(
      withAttrs({ idx: 0, kind: "subagent", message: "review code" }, { mode: "review" })
    );
    expect(r.category).toBe("subagent");
    expect(r.label).toBe("review code");
    expect(r.layer).toBe("review");
  });

  it("falls back to entry.source for layer when mode is absent", () => {
    const r = deriveNodeRender(
      makeEntry({ idx: 0, kind: "agent_response", source: "operator_console" })
    );
    expect(r.layer).toBe("operator_console");
  });

  it("does not derive a layer for synthetic source", () => {
    const r = deriveNodeRender(makeEntry({ idx: 0, kind: "generic", source: "synthetic" }));
    expect(r.layer).toBeNull();
  });

  it("populates timestampLabel and tooltipLines from safe data; never from raw payloads", () => {
    const r = deriveNodeRender(
      withAttrs(
        {
          idx: 0,
          kind: "tool_call",
          tool_name: "Read",
          timestamp: "2024-06-01T05:06:07.000Z",
          redacted: true,
        },
        { tool_status: "error", event_type: "tool.end" }
      )
    );
    expect(r.timestampLabel).toBe("05:06:07");
    expect(r.tooltipLines).toContain("kind: tool_call");
    expect(r.tooltipLines).toContain("event: tool.end");
    expect(r.tooltipLines).toContain("status: error");
    expect(r.tooltipLines).toContain("redacted: true");
  });

  it("gracefully degrades when attrs is null and only top-level fields are set", () => {
    const r = deriveNodeRender(
      makeEntry({ idx: 0, kind: "tool_call", tool_name: "Bash", attrs: null })
    );
    expect(r.category).toBe("tool");
    expect(r.label).toBe("Bash");
    // No safe attrs, no duration → no sublabel parts at all
    expect(r.sublabel).toBeNull();
    expect(r.status).toBe("unknown");
    expect(r.tooltipLines[0]).toBe("kind: tool_call");
  });

  it("renders notification entries with kind + status + exit code", () => {
    const r = deriveNodeRender(
      withAttrs(
        { idx: 0, kind: "generic", source: "operator_console" },
        {
          notification_kind: "shell_completed",
          notification_status: "ok",
          notification_exit_code: 0,
        }
      )
    );
    expect(r.category).toBe("notification");
    expect(r.label).toBe("shell_completed");
    expect(r.sublabel).toContain("ok");
    expect(r.sublabel).toContain("exit 0");
  });

  it("renders error entries with error_category + exit_code", () => {
    const r = deriveNodeRender(
      withAttrs({ idx: 0, kind: "error" }, { error_category: "nonzero_exit", exit_code: 2 })
    );
    expect(r.category).toBe("error");
    expect(r.label).toBe("nonzero_exit");
    expect(r.sublabel).toContain("exit 2");
    expect(r.status).toBe("error");
  });
});

// ── Trace inspector model (v2) ───────────────────────────────────────────────

describe("computeTraceLayout", () => {
  it("returns an empty layout for an empty entry list", () => {
    const layout = computeTraceLayout([]);
    expect(layout.lanes).toEqual([]);
    expect(layout.range).toEqual({ startMs: 0, endMs: 0, ticks: [], mode: "time" });
    expect(layout.summary).toEqual({
      totalCount: 0,
      errorCount: 0,
      totalDurationMs: null,
      laneTotals: [],
    });
  });

  it("emits lanes in PLAYBACK_LANE_ORDER and drops empty lanes", () => {
    const entries = [
      makeEntry({
        idx: 0,
        kind: "turn_start",
        timestamp: "2024-01-01T12:00:00.000Z",
        span_id: "t",
      }),
      withAttrs(
        {
          idx: 1,
          kind: "tool_call",
          tool_name: "bash",
          duration_ms: 100,
          timestamp: "2024-01-01T12:00:01.000Z",
        },
        { tool_status: "ok" }
      ),
      withAttrs(
        {
          idx: 2,
          kind: "hook",
          timestamp: "2024-01-01T12:00:02.000Z",
        },
        { hook_type: "preToolUse", hook_status: "ok" }
      ),
      withAttrs(
        {
          idx: 3,
          kind: "generic",
          timestamp: "2024-01-01T12:00:03.000Z",
        },
        { skill_name: "code-reviewer" }
      ),
      makeEntry({
        idx: 4,
        kind: "agent_response",
        timestamp: "2024-01-01T12:00:04.000Z",
      }),
      makeEntry({
        idx: 5,
        kind: "error",
        timestamp: "2024-01-01T12:00:05.000Z",
        status: "error",
      }),
    ];
    const layout = computeTraceLayout(entries);
    const laneIds = layout.lanes.map((l) => l.id);
    // Turn, Model, Tool/Hook/Skill, Error must appear; SubAgent/Notification absent.
    expect(laneIds).toEqual(["Turn", "Model", "Tool/Hook/Skill", "Error"]);
    expect(layout.summary.errorCount).toBe(1);
    expect(layout.summary.totalCount).toBe(6);
    // Tool/Hook/Skill lane combines tool+hook+skill entries.
    const ths = layout.lanes.find((l) => l.id === "Tool/Hook/Skill")!;
    expect(ths.count).toBe(3);
  });

  it("falls back to index mode when >5% of entries have null timestamps", () => {
    const entries: BrowseDebugEntry[] = [];
    for (let i = 0; i < 10; i++) {
      const entry = makeEntry({ idx: i });
      // 3 of 10 have null timestamp (30% — well above 5% threshold).
      // makeEntry uses `??` so we have to overwrite after construction.
      if (i < 3) entry.timestamp = null;
      entries.push(entry);
    }
    const layout = computeTraceLayout(entries);
    expect(layout.range.mode).toBe("index");
    expect(layout.range.startMs).toBe(0);
    expect(layout.range.endMs).toBeGreaterThanOrEqual(1);
    // All ticks labelled with the "#" prefix.
    expect(layout.range.ticks.every((t) => t.label.startsWith("#"))).toBe(true);
    expect(layout.summary.totalDurationMs).toBeNull();
  });

  it("forces endMs > startMs when only a single timestamp is present", () => {
    const layout = computeTraceLayout([
      makeEntry({ idx: 0, timestamp: "2024-01-01T12:00:00.000Z", duration_ms: null }),
    ]);
    expect(layout.range.mode).toBe("time");
    expect(layout.range.endMs).toBeGreaterThan(layout.range.startMs);
    // Ticks must always be 5 entries with "+" prefix in time mode.
    expect(layout.range.ticks).toHaveLength(5);
    expect(layout.range.ticks[0].label.startsWith("+")).toBe(true);
  });

  it("assigns MIN_BAR_RATIO width and isZeroWidth=true for null duration", () => {
    const layout = computeTraceLayout([
      makeEntry({
        idx: 0,
        kind: "tool_call",
        tool_name: "bash",
        timestamp: "2024-01-01T12:00:00.000Z",
        duration_ms: null,
        span_id: "x",
      }),
      makeEntry({
        idx: 1,
        kind: "tool_call",
        tool_name: "bash",
        timestamp: "2024-01-01T12:00:05.000Z",
        duration_ms: 1000,
        span_id: "y",
      }),
    ]);
    const bars = layout.lanes.find((l) => l.id === "Tool/Hook/Skill")!.bars;
    const zero = bars.find((b) => b.entryIdx === 0)!;
    expect(zero.isZeroWidth).toBe(true);
    expect(zero.widthRatio).toBeCloseTo(MIN_BAR_RATIO, 6);
    const real = bars.find((b) => b.entryIdx === 1)!;
    expect(real.isZeroWidth).toBe(false);
    expect(real.widthRatio).toBeGreaterThan(MIN_BAR_RATIO);
  });

  it("sorts bars within a lane by startRatio ASC then entryIdx ASC", () => {
    const layout = computeTraceLayout([
      makeEntry({
        idx: 5,
        kind: "tool_call",
        tool_name: "bash",
        timestamp: "2024-01-01T12:00:02.000Z",
        span_id: "a",
      }),
      makeEntry({
        idx: 2,
        kind: "tool_call",
        tool_name: "bash",
        timestamp: "2024-01-01T12:00:01.000Z",
        span_id: "b",
      }),
      makeEntry({
        idx: 3,
        kind: "tool_call",
        tool_name: "bash",
        timestamp: "2024-01-01T12:00:01.000Z",
        span_id: "c",
      }),
    ]);
    const bars = layout.lanes.find((l) => l.id === "Tool/Hook/Skill")!.bars;
    expect(bars.map((b) => b.entryIdx)).toEqual([2, 3, 5]);
  });

  it("skips synthetic orphan entries (idx === -1)", () => {
    const orphan = makeEntry({
      idx: -1,
      kind: "generic",
      timestamp: null,
      source: "synthetic",
    });
    const real = makeEntry({
      idx: 0,
      kind: "tool_call",
      tool_name: "bash",
      timestamp: "2024-01-01T12:00:00.000Z",
      span_id: "a",
    });
    const layout = computeTraceLayout([orphan, real]);
    const allBars = layout.lanes.flatMap((l) => l.bars);
    expect(allBars.find((b) => b.entryIdx === -1)).toBeUndefined();
    expect(allBars).toHaveLength(1);
  });

  it("does not throw and stays bounded for large pages (200 entries)", () => {
    const entries: BrowseDebugEntry[] = [];
    for (let i = 0; i < 200; i++) {
      entries.push(
        makeEntry({
          idx: i,
          kind: i % 3 === 0 ? "tool_call" : "agent_response",
          tool_name: i % 3 === 0 ? "bash" : null,
          timestamp: new Date(2024, 0, 1, 12, 0, 0, i * 50).toISOString(),
          duration_ms: 30,
          span_id: `s${i}`,
        })
      );
    }
    const layout = computeTraceLayout(entries);
    expect(layout.summary.totalCount).toBe(200);
    expect(layout.lanes.length).toBeGreaterThan(0);
    for (const lane of layout.lanes) {
      for (const bar of lane.bars) {
        expect(bar.startRatio).toBeGreaterThanOrEqual(0);
        expect(bar.startRatio).toBeLessThanOrEqual(1);
        expect(bar.widthRatio).toBeGreaterThanOrEqual(MIN_BAR_RATIO);
      }
    }
  });

  it("counts errors per lane and in the summary", () => {
    const layout = computeTraceLayout([
      withAttrs(
        {
          idx: 0,
          kind: "tool_call",
          tool_name: "bash",
          timestamp: "2024-01-01T12:00:00.000Z",
          duration_ms: 50,
        },
        { tool_success: false }
      ),
      makeEntry({
        idx: 1,
        kind: "error",
        timestamp: "2024-01-01T12:00:01.000Z",
        status: "error",
      }),
    ]);
    expect(layout.summary.errorCount).toBe(2);
    const ths = layout.lanes.find((l) => l.id === "Tool/Hook/Skill")!;
    expect(ths.errorCount).toBe(1);
    const errLane = layout.lanes.find((l) => l.id === "Error")!;
    expect(errLane.errorCount).toBe(1);
  });
});

describe("deriveInspectorCard", () => {
  it("returns safe rows derived from tooltipLines (no raw attrs leakage)", () => {
    const entry = withAttrs(
      {
        idx: 1,
        kind: "tool_call",
        tool_name: "bash",
        duration_ms: 250,
        timestamp: "2024-01-01T12:00:05.000Z",
        message: "ran bash",
        redacted: true,
      },
      {
        tool_status: "ok",
        tool_result_type: "stdout",
        tool_metric_input_bytes: 16,
        tool_metric_output_bytes: 128,
        output_tokens: 42,
        tool_request_count: 2,
        event_type: "tool.end",
      }
    );
    const card = deriveInspectorCard(entry);
    expect(card.entryIdx).toBe(1);
    expect(card.label).toBe("bash");
    expect(card.category).toBe("tool");
    expect(card.status).toBe("ok");
    expect(card.timestampLabel).toBe("12:00:05");
    expect(card.redacted).toBe(true);
    const keys = card.rows.map((r) => r.key);
    expect(keys).toContain("kind");
    expect(keys).toContain("tool");
    expect(keys).toContain("duration");
    expect(keys).toContain("status");
    expect(keys).toContain("input");
    expect(keys).toContain("output");
    expect(keys).toContain("tokens");
    expect(keys).toContain("tool_requests");
    // Bound to 12 rows max.
    expect(card.rows.length).toBeLessThanOrEqual(12);
    // No unknown keys (anything not derived from DebugSafeAttrs/top-level fields).
    const allowed = new Set([
      "kind",
      "event",
      "source",
      "tool",
      "hook",
      "skill",
      "path",
      "status",
      "duration",
      "input",
      "output",
      "tokens",
      "tool_requests",
      "redacted",
      "time",
      "exit",
      "exit_code",
      "error_category",
    ]);
    for (const r of card.rows) {
      expect(allowed.has(r.key)).toBe(true);
    }
    // Message preview surfaces last (non `key: value` line).
    expect(card.messagePreview).toBe("ran bash");
  });

  it("returns null messagePreview when entry.message is empty", () => {
    const card = deriveInspectorCard(
      makeEntry({ idx: 0, kind: "tool_call", tool_name: "bash", message: "" })
    );
    expect(card.messagePreview).toBeNull();
  });

  it("preserves skill bytes / output_tokens / exit_code metadata", () => {
    const skillCard = deriveInspectorCard(
      withAttrs(
        { idx: 0, kind: "generic" },
        {
          skill_name: "code-reviewer",
          skill_path_category: "skill_pkg",
          skill_content_bytes: 1024,
        }
      )
    );
    expect(skillCard.category).toBe("skill");
    expect(skillCard.rows.find((r) => r.key === "skill")?.value).toBe("code-reviewer");
    expect(skillCard.rows.find((r) => r.key === "path")?.value).toBe("skill_pkg");
  });

  it.each([
    ["Error: file not found", "Error"],
    ["bash: command not found", "bash"],
    ["TypeError: cannot read properties of undefined", "TypeError"],
  ])("keeps free-form colon message %j as messagePreview, not a row", (message, forbiddenKey) => {
    const card = deriveInspectorCard(
      makeEntry({ idx: 0, kind: "error", status: "error", message })
    );
    expect(card.messagePreview).toBe(message);
    expect(card.rows.map((r) => r.key)).not.toContain(forbiddenKey);
  });

  it("only emits rows whose keys are in the inspector allowlist", () => {
    const allowed = new Set([
      "kind",
      "event",
      "source",
      "tool",
      "hook",
      "skill",
      "path",
      "status",
      "duration",
      "input",
      "output",
      "tokens",
      "tool_requests",
      "redacted",
      "time",
      "exit",
      "exit_code",
      "error_category",
    ]);
    const card = deriveInspectorCard(
      withAttrs(
        {
          idx: 0,
          kind: "tool_call",
          tool_name: "bash",
          duration_ms: 25,
          timestamp: "2024-01-01T12:00:00.000Z",
          message: "ok",
        },
        { tool_status: "ok" }
      )
    );
    for (const row of card.rows) {
      expect(allowed.has(row.key)).toBe(true);
    }
  });
});

describe("computeTraceLayout — boundary visibility", () => {
  it("keeps index-mode last entry inside [0, 1] (startRatio + widthRatio <= 1)", () => {
    const entries: BrowseDebugEntry[] = [];
    for (let i = 0; i < 5; i++) {
      const entry = makeEntry({
        idx: i,
        kind: "tool_call",
        tool_name: "bash",
        span_id: `s${i}`,
      });
      // Force index mode by nulling timestamps for >5% of the page.
      entry.timestamp = null;
      entries.push(entry);
    }
    const layout = computeTraceLayout(entries);
    expect(layout.range.mode).toBe("index");
    const allBars = layout.lanes.flatMap((l) => l.bars);
    expect(allBars.length).toBeGreaterThan(0);
    for (const bar of allBars) {
      expect(bar.startRatio).toBeGreaterThanOrEqual(0);
      expect(bar.startRatio + bar.widthRatio).toBeLessThanOrEqual(1 + 1e-9);
    }
    const last = allBars.find((b) => b.entryIdx === 4)!;
    expect(last.startRatio + last.widthRatio).toBeLessThanOrEqual(1 + 1e-9);
    // The last bar must still be visible (not clipped against the right edge).
    expect(last.startRatio).toBeLessThanOrEqual(1 - MIN_BAR_RATIO + 1e-9);
  });

  it("keeps a time-mode trailing zero-duration entry at the max timestamp visible", () => {
    const layout = computeTraceLayout([
      makeEntry({
        idx: 0,
        kind: "tool_call",
        tool_name: "bash",
        timestamp: "2024-01-01T12:00:00.000Z",
        duration_ms: 1000,
        span_id: "a",
      }),
      makeEntry({
        idx: 1,
        kind: "tool_call",
        tool_name: "bash",
        timestamp: "2024-01-01T12:00:01.000Z",
        duration_ms: null,
        span_id: "b",
      }),
    ]);
    expect(layout.range.mode).toBe("time");
    const bars = layout.lanes.find((l) => l.id === "Tool/Hook/Skill")!.bars;
    const trailing = bars.find((b) => b.entryIdx === 1)!;
    expect(trailing.isZeroWidth).toBe(true);
    expect(trailing.widthRatio).toBeCloseTo(MIN_BAR_RATIO, 6);
    expect(trailing.startRatio + trailing.widthRatio).toBeLessThanOrEqual(1 + 1e-9);
    // Must remain inside the viewport, not parked at the far-right edge.
    expect(trailing.startRatio).toBeLessThanOrEqual(1 - MIN_BAR_RATIO + 1e-9);
  });

  it("keeps a time-mode null-timestamp parked stub inside the viewport", () => {
    const real = makeEntry({
      idx: 0,
      kind: "tool_call",
      tool_name: "bash",
      timestamp: "2024-01-01T12:00:00.000Z",
      duration_ms: 1000,
      span_id: "a",
    });
    const stub = makeEntry({
      idx: 1,
      kind: "tool_call",
      tool_name: "bash",
      duration_ms: null,
      span_id: "b",
    });
    stub.timestamp = null;
    const layout = computeTraceLayout([real, stub]);
    expect(layout.range.mode).toBe("time");
    const bars = layout.lanes.find((l) => l.id === "Tool/Hook/Skill")!.bars;
    const parked = bars.find((b) => b.entryIdx === 1)!;
    expect(parked.isZeroWidth).toBe(true);
    expect(parked.startRatio + parked.widthRatio).toBeLessThanOrEqual(1 + 1e-9);
  });
});

describe("laneIdSlug", () => {
  it("lowercases and replaces slashes with hyphens", () => {
    expect(laneIdSlug("Tool/Hook/Skill")).toBe("tool-hook-skill");
    expect(laneIdSlug("Notification/Compaction")).toBe("notification-compaction");
    expect(laneIdSlug("Model")).toBe("model");
  });
});
