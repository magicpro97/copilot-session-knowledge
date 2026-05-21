import type { BrowseDebugEntry } from "@/lib/api/types";

/**
 * A node in the derived span tree.
 *
 * `isRoot`:  true when `parent_span_id` is null (or when `span_id` is null).
 * `isOrphan`: true when `parent_span_id` is set but no matching span exists in the
 *             input set; such nodes are collected under a synthetic "(orphans)" root.
 * `rollupDurationMs`: non-null only when the parent has NO own `duration_ms` and at
 *             least one descendant has a measurable duration; used to surface total
 *             wall-clock time for the subtree.
 */
export type SpanTreeNode = {
  entry: BrowseDebugEntry;
  children: SpanTreeNode[];
  depth: number;
  isOrphan: boolean;
  isRoot: boolean;
  rollupDurationMs: number | null;
};

/**
 * Derives a span tree from a flat list of debug log entries.
 *
 * Rules:
 * - Root nodes: `parent_span_id === null` (incl. entries where `span_id` is also null).
 * - Orphan nodes: `parent_span_id` is set but no matching `span_id` exists in the set.
 *   Orphans are collected under a synthetic "(orphans)" root (entry.idx = -1).
 * - Duration rollup: when a node has no own `duration_ms`, its `rollupDurationMs` is
 *   set to the sum of its children's effective durations.
 * - Multiple entries may share the same `span_id` (paired tool start / completion).
 *   The first occurrence is the canonical parent for child lookup; later occurrences
 *   with the same `parent_span_id = null` are independent roots.
 */
export function deriveSpanTree(entries: BrowseDebugEntry[]): SpanTreeNode[] {
  if (entries.length === 0) return [];

  // ── 1. Build span_id → first entry (canonical parent lookup) ─────────────────
  const spanMap = new Map<string, BrowseDebugEntry>();
  for (const entry of entries) {
    if (entry.span_id !== null && !spanMap.has(entry.span_id)) {
      spanMap.set(entry.span_id, entry);
    }
  }

  // ── 2. Create a SpanTreeNode for every entry ─────────────────────────────────
  const nodeMap = new Map<number, SpanTreeNode>();
  for (const entry of entries) {
    nodeMap.set(entry.idx, {
      entry,
      children: [],
      depth: 0,
      isOrphan: false,
      isRoot: entry.parent_span_id === null,
      rollupDurationMs: null,
    });
  }

  const regularRoots: SpanTreeNode[] = [];
  const orphanRoots: SpanTreeNode[] = [];

  // ── 3. Wire up parent-child relationships and classify each node ──────────────
  for (const entry of entries) {
    const node = nodeMap.get(entry.idx)!;

    if (entry.parent_span_id === null) {
      // Root (covers span_id === null as well)
      node.isRoot = true;
      regularRoots.push(node);
      continue;
    }

    const parentEntry = spanMap.get(entry.parent_span_id);
    if (!parentEntry) {
      // Orphan: parent_span_id is set but no matching parent in this set
      node.isOrphan = true;
      node.isRoot = false;
      orphanRoots.push(node);
      continue;
    }

    // Guard against self-referential cycles
    if (parentEntry.idx === entry.idx) {
      node.isRoot = true;
      regularRoots.push(node);
      continue;
    }

    const parentNode = nodeMap.get(parentEntry.idx)!;
    parentNode.children.push(node);
    node.isRoot = false;
  }

  // ── 4. Assemble final root list ───────────────────────────────────────────────
  const allRoots: SpanTreeNode[] = [...regularRoots];

  if (orphanRoots.length > 0) {
    const syntheticEntry: BrowseDebugEntry = {
      idx: -1,
      timestamp: null,
      kind: "generic",
      level: null,
      source: "synthetic",
      message: "(orphans)",
      tool_name: null,
      duration_ms: null,
      span_id: null,
      parent_span_id: null,
      status: null,
      attrs: null,
      redacted: false,
    };

    allRoots.push({
      entry: syntheticEntry,
      children: orphanRoots,
      depth: 0,
      isOrphan: false,
      isRoot: true,
      rollupDurationMs: null,
    });
  }

  // ── 5. Assign depths (DFS) ────────────────────────────────────────────────────
  function assignDepths(nodes: SpanTreeNode[], depth: number): void {
    for (const node of nodes) {
      node.depth = depth;
      assignDepths(node.children, depth + 1);
    }
  }
  assignDepths(allRoots, 0);

  // ── 6. Compute duration rollups (post-order DFS) ─────────────────────────────
  function computeRollup(node: SpanTreeNode): number | null {
    if (node.children.length === 0) {
      return node.entry.duration_ms;
    }

    let childSum = 0;
    let hasAny = false;
    for (const child of node.children) {
      const dur = computeRollup(child);
      if (dur !== null) {
        childSum += dur;
        hasAny = true;
      }
    }

    if (node.entry.duration_ms !== null) {
      // Node has its own duration; no rollup badge needed
      node.rollupDurationMs = null;
      return node.entry.duration_ms;
    }

    if (hasAny) {
      node.rollupDurationMs = childSum;
      return childSum;
    }

    node.rollupDurationMs = null;
    return null;
  }

  for (const root of allRoots) {
    computeRollup(root);
  }

  return allRoots;
}
