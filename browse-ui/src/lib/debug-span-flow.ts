import type { BrowseDebugEntry } from "@/lib/api/types";

import { deriveSpanTree, type SpanTreeNode } from "./debug-span-tree";

/**
 * Flow-chart layout for debug-log span trees.
 *
 * Mirrors the VS Code Agent Debug "flow chart" view (visual event hierarchy)
 * rather than a time-axis Gantt: parent-child edges from `span_id` /
 * `parent_span_id`, top-down vertical layout, deterministic placement.
 *
 * The layout is computed in tree-units (px before any SVG scaling). Pan/zoom
 * is the consumer's responsibility (handled by `viewBox` in the renderer).
 */

export type FlowNode = {
  /** Stable id used for keys, edge endpoints, and test selectors. */
  id: string;
  entry: BrowseDebugEntry;
  /** Top-left x in tree-units. */
  x: number;
  /** Top-left y in tree-units. */
  y: number;
  width: number;
  height: number;
  depth: number;
  /** True for the synthetic "(orphans)" grouping node. Not clickable. */
  isSynthetic: boolean;
};

export type FlowEdge = {
  fromId: string;
  toId: string;
};

export type FlowLayout = {
  nodes: FlowNode[];
  edges: FlowEdge[];
  /** Total width of the layout in tree-units. */
  width: number;
  /** Total height of the layout in tree-units. */
  height: number;
};

export const FLOW_NODE_WIDTH = 200;
export const FLOW_NODE_HEIGHT = 60;
export const FLOW_HORIZONTAL_GAP = 24;
export const FLOW_VERTICAL_GAP = 36;

function keyOf(node: SpanTreeNode): string {
  return node.entry.idx === -1 ? "synthetic-orphan" : `node-${node.entry.idx}`;
}

/**
 * Compute a deterministic top-down tree layout from a flat list of debug
 * entries. Sibling roots are laid out left-to-right; each subtree is centered
 * over its children. Missing parent_span_id values are handled by
 * `deriveSpanTree` (collected under a synthetic orphans root).
 */
export function computeFlowLayout(entries: BrowseDebugEntry[]): FlowLayout {
  const roots = deriveSpanTree(entries);
  const nodes: FlowNode[] = [];
  const edges: FlowEdge[] = [];

  if (roots.length === 0) {
    return { nodes, edges, width: 0, height: 0 };
  }

  let cursorX = 0;

  function layout(node: SpanTreeNode): { left: number; right: number; center: number } {
    const y = node.depth * (FLOW_NODE_HEIGHT + FLOW_VERTICAL_GAP);
    const isSynthetic = node.entry.idx === -1;

    if (node.children.length === 0) {
      const left = cursorX;
      const right = left + FLOW_NODE_WIDTH;
      const center = left + FLOW_NODE_WIDTH / 2;
      nodes.push({
        id: keyOf(node),
        entry: node.entry,
        x: left,
        y,
        width: FLOW_NODE_WIDTH,
        height: FLOW_NODE_HEIGHT,
        depth: node.depth,
        isSynthetic,
      });
      cursorX = right + FLOW_HORIZONTAL_GAP;
      return { left, right, center };
    }

    const childExtents = node.children.map((c) => layout(c));
    const left = childExtents[0].left;
    const right = childExtents[childExtents.length - 1].right;
    const center = (left + right) / 2;
    const nodeX = center - FLOW_NODE_WIDTH / 2;

    nodes.push({
      id: keyOf(node),
      entry: node.entry,
      x: nodeX,
      y,
      width: FLOW_NODE_WIDTH,
      height: FLOW_NODE_HEIGHT,
      depth: node.depth,
      isSynthetic,
    });

    for (const child of node.children) {
      edges.push({ fromId: keyOf(node), toId: keyOf(child) });
    }

    return { left, right, center };
  }

  for (const root of roots) {
    layout(root);
  }

  const width = nodes.reduce((m, n) => Math.max(m, n.x + n.width), 0);
  const height = nodes.reduce((m, n) => Math.max(m, n.y + n.height), 0);

  return { nodes, edges, width, height };
}
