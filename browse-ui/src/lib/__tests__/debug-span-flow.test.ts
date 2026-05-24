import { describe, expect, it } from "vitest";

import type { BrowseDebugEntry, DebugSafeAttrs } from "@/lib/api/types";
import {
  computeFlowLayout,
  deriveGroupKey,
  deriveNodeCategory,
  deriveNodeRender,
  deriveNodeStatus,
  FLOW_HORIZONTAL_GAP,
  FLOW_NODE_HEIGHT,
  FLOW_NODE_WIDTH,
  FLOW_VERTICAL_GAP,
  formatDurationLabel,
  formatTimestampLabel,
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

  it("places sibling roots side-by-side with horizontal gap", () => {
    const layout = computeFlowLayout([
      makeEntry({ idx: 0, span_id: "a", parent_span_id: null }),
      makeEntry({ idx: 1, span_id: "b", parent_span_id: null }),
    ]);
    const a = layout.nodes.find((n) => n.id === "node-0")!;
    const b = layout.nodes.find((n) => n.id === "node-1")!;
    expect(b.x - (a.x + a.width)).toBe(FLOW_HORIZONTAL_GAP);
    expect(a.y).toBe(b.y);
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
