import { describe, expect, it } from "vitest";

import type { BrowseDebugEntry } from "@/lib/api/types";
import { deriveSpanTree } from "@/lib/debug-span-tree";

// ── Fixtures ──────────────────────────────────────────────────────────────────

let _idx = 0;

function makeEntry(overrides: Partial<BrowseDebugEntry> = {}): BrowseDebugEntry {
  return {
    idx: _idx++,
    timestamp: "2024-01-01T12:00:00.000Z",
    kind: "tool_call",
    level: "info",
    source: "operator_console",
    message: "Test event",
    tool_name: "bash",
    duration_ms: null,
    span_id: null,
    parent_span_id: null,
    status: "ok",
    attrs: null,
    redacted: false,
    ...overrides,
  };
}

// Reset counter before each test via beforeEach — but since we assign idx in overrides
// most of the time we just pass idx explicitly. Let's keep _idx incrementing for
// fixtures that don't specify idx.

// ── Empty input ───────────────────────────────────────────────────────────────

describe("deriveSpanTree – empty input", () => {
  it("returns empty array for empty input", () => {
    expect(deriveSpanTree([])).toEqual([]);
  });
});

// ── Single root ───────────────────────────────────────────────────────────────

describe("deriveSpanTree – single root", () => {
  it("wraps a span-less entry as a root node with depth 0", () => {
    const entry = makeEntry({ idx: 0, span_id: null, parent_span_id: null });
    const roots = deriveSpanTree([entry]);

    expect(roots).toHaveLength(1);
    const node = roots[0];
    expect(node.entry).toBe(entry);
    expect(node.depth).toBe(0);
    expect(node.isRoot).toBe(true);
    expect(node.isOrphan).toBe(false);
    expect(node.children).toHaveLength(0);
    expect(node.rollupDurationMs).toBeNull();
  });

  it("wraps a span-bearing root entry correctly", () => {
    const entry = makeEntry({ idx: 0, span_id: "aabbccddeeff0011", parent_span_id: null });
    const [root] = deriveSpanTree([entry]);

    expect(root.isRoot).toBe(true);
    expect(root.isOrphan).toBe(false);
    expect(root.depth).toBe(0);
  });
});

// ── Native parent-child spans ─────────────────────────────────────────────────

describe("deriveSpanTree – native spans", () => {
  it("wires parent-child when parent_span_id matches a span_id", () => {
    const parent = makeEntry({ idx: 0, span_id: "parent0000000000", parent_span_id: null });
    const child = makeEntry({
      idx: 1,
      span_id: "child00000000000",
      parent_span_id: "parent0000000000",
    });

    const roots = deriveSpanTree([parent, child]);

    expect(roots).toHaveLength(1);
    const parentNode = roots[0];
    expect(parentNode.entry).toBe(parent);
    expect(parentNode.children).toHaveLength(1);
    expect(parentNode.children[0].entry).toBe(child);
  });

  it("assigns correct depths to parent and child", () => {
    const parent = makeEntry({ idx: 0, span_id: "aaaa000000000000", parent_span_id: null });
    const child = makeEntry({
      idx: 1,
      span_id: "bbbb000000000000",
      parent_span_id: "aaaa000000000000",
    });

    const [parentNode] = deriveSpanTree([parent, child]);

    expect(parentNode.depth).toBe(0);
    expect(parentNode.children[0].depth).toBe(1);
  });

  it("child node has isRoot=false and isOrphan=false", () => {
    const parent = makeEntry({ idx: 0, span_id: "pp00000000000000", parent_span_id: null });
    const child = makeEntry({
      idx: 1,
      span_id: "cc00000000000000",
      parent_span_id: "pp00000000000000",
    });

    const [parentNode] = deriveSpanTree([parent, child]);
    const childNode = parentNode.children[0];

    expect(childNode.isRoot).toBe(false);
    expect(childNode.isOrphan).toBe(false);
  });
});

// ── Parallel siblings ─────────────────────────────────────────────────────────

describe("deriveSpanTree – parallel siblings", () => {
  it("places multiple entries with same parent under that parent's children", () => {
    const root = makeEntry({ idx: 0, span_id: "root000000000000", parent_span_id: null });
    const sib1 = makeEntry({
      idx: 1,
      span_id: "sib1000000000000",
      parent_span_id: "root000000000000",
    });
    const sib2 = makeEntry({
      idx: 2,
      span_id: "sib2000000000000",
      parent_span_id: "root000000000000",
    });

    const roots = deriveSpanTree([root, sib1, sib2]);

    expect(roots).toHaveLength(1);
    expect(roots[0].children).toHaveLength(2);
    const childEntries = roots[0].children.map((c) => c.entry);
    expect(childEntries).toContain(sib1);
    expect(childEntries).toContain(sib2);
  });

  it("sibling depths are both 1", () => {
    const root = makeEntry({ idx: 0, span_id: "root100000000000", parent_span_id: null });
    const sib1 = makeEntry({
      idx: 1,
      span_id: "sib1100000000000",
      parent_span_id: "root100000000000",
    });
    const sib2 = makeEntry({
      idx: 2,
      span_id: "sib2100000000000",
      parent_span_id: "root100000000000",
    });

    const [rootNode] = deriveSpanTree([root, sib1, sib2]);

    expect(rootNode.children[0].depth).toBe(1);
    expect(rootNode.children[1].depth).toBe(1);
  });
});

// ── Missing parent (orphan) ───────────────────────────────────────────────────

describe("deriveSpanTree – orphan entries", () => {
  it("collects entries with unresolvable parent under a synthetic (orphans) root", () => {
    const orphan = makeEntry({
      idx: 0,
      span_id: "orphan0000000000",
      parent_span_id: "doesnotexist0000",
    });

    const roots = deriveSpanTree([orphan]);

    // Should have one root — the synthetic orphan group
    expect(roots).toHaveLength(1);
    const syntheticRoot = roots[0];
    expect(syntheticRoot.entry.idx).toBe(-1);
    expect(syntheticRoot.entry.message).toBe("(orphans)");
    expect(syntheticRoot.children).toHaveLength(1);
    expect(syntheticRoot.children[0].entry).toBe(orphan);
  });

  it("marks orphan nodes with isOrphan=true", () => {
    const orphan = makeEntry({
      idx: 0,
      span_id: "orp00000000000a0",
      parent_span_id: "missing00000000a",
    });

    const roots = deriveSpanTree([orphan]);
    const orphanNode = roots[0].children[0];
    expect(orphanNode.isOrphan).toBe(true);
  });

  it("synthetic root is isRoot=true and isOrphan=false", () => {
    const orphan = makeEntry({
      idx: 0,
      span_id: "orp00000000000b0",
      parent_span_id: "missing00000000b",
    });

    const [syntheticRoot] = deriveSpanTree([orphan]);
    expect(syntheticRoot.isRoot).toBe(true);
    expect(syntheticRoot.isOrphan).toBe(false);
  });

  it("orphan node is placed at depth 1 under synthetic root at depth 0", () => {
    const orphan = makeEntry({
      idx: 0,
      span_id: "orp00000000000c0",
      parent_span_id: "missing00000000c",
    });

    const [syntheticRoot] = deriveSpanTree([orphan]);
    expect(syntheticRoot.depth).toBe(0);
    expect(syntheticRoot.children[0].depth).toBe(1);
  });

  it("mixes regular roots and orphans correctly", () => {
    const regular = makeEntry({ idx: 0, span_id: "reg00000000000d0", parent_span_id: null });
    const orphan = makeEntry({
      idx: 1,
      span_id: "orp00000000000d0",
      parent_span_id: "missing00000000d",
    });

    const roots = deriveSpanTree([regular, orphan]);

    // Two roots: the regular one and the synthetic orphan group
    expect(roots).toHaveLength(2);
    const entryIdxSet = new Set(roots.map((r) => r.entry.idx));
    expect(entryIdxSet.has(0)).toBe(true);
    expect(entryIdxSet.has(-1)).toBe(true);
  });
});

// ── Repeated tool calls with same span_id ─────────────────────────────────────

describe("deriveSpanTree – repeated span_id (paired start/completion)", () => {
  it("treats both entries as independent roots when both have parent_span_id=null", () => {
    const start = makeEntry({
      idx: 0,
      span_id: "sharedspan000000",
      parent_span_id: null,
      tool_name: "bash",
    });
    const complete = makeEntry({
      idx: 1,
      span_id: "sharedspan000000", // same span_id
      parent_span_id: null,
      duration_ms: 120,
      tool_name: "bash",
    });

    const roots = deriveSpanTree([start, complete]);
    expect(roots).toHaveLength(2);
    expect(roots[0].entry).toBe(start);
    expect(roots[1].entry).toBe(complete);
  });

  it("children pointing to shared span_id attach to the first occurrence", () => {
    const start = makeEntry({ idx: 0, span_id: "shared0000000000", parent_span_id: null });
    const complete = makeEntry({ idx: 1, span_id: "shared0000000000", parent_span_id: null });
    const child = makeEntry({
      idx: 2,
      span_id: "child00000000001",
      parent_span_id: "shared0000000000",
    });

    const roots = deriveSpanTree([start, complete, child]);

    // start (idx=0) is the canonical parent because it's the first with span_id "shared…"
    const startNode = roots.find((r) => r.entry.idx === 0)!;
    expect(startNode.children).toHaveLength(1);
    expect(startNode.children[0].entry).toBe(child);

    // complete (idx=1) has no children
    const completeNode = roots.find((r) => r.entry.idx === 1)!;
    expect(completeNode.children).toHaveLength(0);
  });
});

// ── Deeply nested (5+ levels) ────────────────────────────────────────────────

describe("deriveSpanTree – deeply nested tree", () => {
  it("assigns correct depths for a 6-level chain (depth 0 … 5)", () => {
    const ids = [
      "a000000000000000",
      "b000000000000000",
      "c000000000000000",
      "d000000000000000",
      "e000000000000000",
      "f000000000000000",
    ];

    const entries: BrowseDebugEntry[] = ids.map((span_id, i) =>
      makeEntry({
        idx: i,
        span_id,
        parent_span_id: i === 0 ? null : ids[i - 1],
      })
    );

    const roots = deriveSpanTree(entries);

    expect(roots).toHaveLength(1);

    // Walk the chain and check depths
    let current = roots[0];
    for (let depth = 0; depth < 6; depth++) {
      expect(current.depth).toBe(depth);
      if (depth < 5) {
        expect(current.children).toHaveLength(1);
        current = current.children[0];
      } else {
        expect(current.children).toHaveLength(0);
      }
    }
  });
});

// ── Duration rollup ───────────────────────────────────────────────────────────

describe("deriveSpanTree – duration rollup", () => {
  it("sets rollupDurationMs on parent when parent has no own duration", () => {
    const parent = makeEntry({
      idx: 0,
      span_id: "p000000000000000",
      parent_span_id: null,
      duration_ms: null,
    });
    const child1 = makeEntry({
      idx: 1,
      span_id: "c100000000000000",
      parent_span_id: "p000000000000000",
      duration_ms: 100,
    });
    const child2 = makeEntry({
      idx: 2,
      span_id: "c200000000000000",
      parent_span_id: "p000000000000000",
      duration_ms: 50,
    });

    const [parentNode] = deriveSpanTree([parent, child1, child2]);

    expect(parentNode.rollupDurationMs).toBe(150);
  });

  it("does NOT set rollupDurationMs when parent already has own duration", () => {
    const parent = makeEntry({
      idx: 0,
      span_id: "p100000000000000",
      parent_span_id: null,
      duration_ms: 200,
    });
    const child = makeEntry({
      idx: 1,
      span_id: "c300000000000000",
      parent_span_id: "p100000000000000",
      duration_ms: 100,
    });

    const [parentNode] = deriveSpanTree([parent, child]);

    expect(parentNode.rollupDurationMs).toBeNull();
  });

  it("returns null rollupDurationMs when no child has a duration", () => {
    const parent = makeEntry({
      idx: 0,
      span_id: "p200000000000000",
      parent_span_id: null,
      duration_ms: null,
    });
    const child = makeEntry({
      idx: 1,
      span_id: "c400000000000000",
      parent_span_id: "p200000000000000",
      duration_ms: null,
    });

    const [parentNode] = deriveSpanTree([parent, child]);

    expect(parentNode.rollupDurationMs).toBeNull();
  });

  it("leaf node always has rollupDurationMs = null", () => {
    const entry = makeEntry({
      idx: 0,
      span_id: "leaf000000000000",
      parent_span_id: null,
      duration_ms: 42,
    });

    const [node] = deriveSpanTree([entry]);

    expect(node.rollupDurationMs).toBeNull();
    expect(node.entry.duration_ms).toBe(42);
  });
});
