import { describe, expect, it } from "vitest";

import type { BrowseDebugEntry } from "@/lib/api/types";
import {
  computeFlowLayout,
  FLOW_HORIZONTAL_GAP,
  FLOW_NODE_HEIGHT,
  FLOW_NODE_WIDTH,
  FLOW_VERTICAL_GAP,
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
});
