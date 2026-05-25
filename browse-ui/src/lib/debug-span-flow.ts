import type { BrowseDebugEntry, DebugSafeAttrs } from "@/lib/api/types";

import {
  derivePlaybackLane,
  normalizeAndSortEntries,
  PLAYBACK_LANE_ORDER,
  type PlaybackLaneId,
} from "./debug-event-playback";
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

/** Coarse-grained node category used for colour/icon selection. */
export type FlowNodeCategory =
  | "session"
  | "turn"
  | "model"
  | "tool"
  | "hook"
  | "skill"
  | "subagent"
  | "notification"
  | "compaction"
  | "error"
  | "generic";

/** Normalised status used for stroke colour and accessibility. */
export type FlowNodeStatus = "ok" | "error" | "cancelled" | "running" | "unknown";

/**
 * Pure, presentation-ready model derived from a single `BrowseDebugEntry`.
 *
 * The renderer should never inspect raw `attrs` — it uses these fields
 * exclusively. Every field degrades to `null`/empty when the underlying
 * safe metadata is absent (default-deny contract).
 */
export type FlowNodeRender = {
  /** Primary one-line label (e.g. tool name, hook type, skill name). */
  label: string;
  /** Secondary one-line label (e.g. result type, status, duration). null when nothing safe to show. */
  sublabel: string | null;
  /** Short clock label `HH:MM:SS` derived from `entry.timestamp`. null when absent or unparseable. */
  timestampLabel: string | null;
  /** Multi-line tooltip body. Always at least one line (kind). */
  tooltipLines: string[];
  /** Coarse-grained category — drives colour swatches. */
  category: FlowNodeCategory;
  /**
   * Optional layer label (e.g. `mode` or `source`). null when no safe
   * indicator is available. Never set from raw `agentId`.
   */
  layer: string | null;
  /** Normalised status — collapses backend `status`, hook_status, tool_success, kind === "error". */
  status: FlowNodeStatus;
  /**
   * Grouping key for same-tool merging. Sibling nodes with the same
   * non-null groupKey can be collapsed into a single visual card. null
   * when the entry is not part of a mergeable group.
   */
  groupKey: string | null;
};

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
  /** Presentation-ready render model. */
  render: FlowNodeRender;
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
/** Horizontal indent (in tree-units) per depth level in the vertical DFS layout. */
export const FLOW_INDENT_PER_LEVEL = 24;

// ── Pure helpers (exported for testability) ──────────────────────────────────

/**
 * Read `BrowseDebugEntry.attrs` as a typed view of the safe rich-metadata
 * contract. Returns an empty object when `attrs` is null. Unknown extra
 * keys are preserved (passthrough) but only documented keys are typed.
 *
 * Pure / defensive: never throws on malformed shapes; the caller falls
 * back to top-level entry fields.
 */
export function readSafeAttrs(entry: BrowseDebugEntry): DebugSafeAttrs {
  const raw = entry.attrs;
  if (!raw || typeof raw !== "object") return {};
  return raw as DebugSafeAttrs;
}

/** True when `s` is a non-empty string. */
function isStr(s: unknown): s is string {
  return typeof s === "string" && s.length > 0;
}

/** True when `n` is a finite number ≥ 0. */
function isNonNegNumber(n: unknown): n is number {
  return typeof n === "number" && Number.isFinite(n) && n >= 0;
}

/** Format a millisecond duration as `123ms` or `1.23s`. null when absent. */
export function formatDurationLabel(ms: number | null | undefined): string | null {
  if (!isNonNegNumber(ms)) return null;
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(2)}s`;
}

/**
 * Format an ISO-8601 timestamp as `HH:MM:SS` in UTC. null when the input
 * is null, empty, or unparseable. UTC keeps tests deterministic across
 * environments.
 */
export function formatTimestampLabel(ts: string | null | undefined): string | null {
  if (!isStr(ts)) return null;
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return null;
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  const ss = String(d.getUTCSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

/** Categorise an entry into a coarse-grained `FlowNodeCategory`. */
export function deriveNodeCategory(entry: BrowseDebugEntry): FlowNodeCategory {
  const attrs = readSafeAttrs(entry);
  const kind = String(entry.kind);

  if (isStr(attrs.notification_kind)) return "notification";
  if (isStr(attrs.compaction_kind)) return "compaction";
  if (
    isStr(attrs.skill_name) ||
    (isStr(attrs.event_type) && attrs.event_type.startsWith("skill"))
  ) {
    return "skill";
  }
  if (kind === "tool_call" || isStr(entry.tool_name)) return "tool";
  if (kind === "hook" || isStr(attrs.hook_type)) return "hook";
  if (kind === "subagent") return "subagent";
  if (kind === "agent_response" || kind === "llm_request") return "model";
  if (kind === "session_start") return "session";
  if (kind === "turn_start") return "turn";
  if (kind === "error") return "error";
  return "generic";
}

/**
 * Normalise the entry into a `FlowNodeStatus`. Order of precedence:
 *  1. `entry.status` (terminal status set by the backend)
 *  2. `attrs.tool_success === false` or `attrs.hook_status === "error"`
 *  3. `kind === "error"`
 *  4. `entry.duration_ms != null` → assume `ok` (the span completed)
 *  5. fallback `unknown` (treated as running by the renderer)
 */
export function deriveNodeStatus(entry: BrowseDebugEntry): FlowNodeStatus {
  if (entry.status === "ok" || entry.status === "error" || entry.status === "cancelled") {
    return entry.status as FlowNodeStatus;
  }
  const attrs = readSafeAttrs(entry);
  if (attrs.tool_success === false || attrs.hook_status === "error") return "error";
  if (String(entry.kind) === "error") return "error";
  if (
    attrs.tool_status === "ok" ||
    attrs.tool_status === "error" ||
    attrs.tool_status === "cancelled"
  ) {
    return attrs.tool_status;
  }
  if (attrs.hook_status === "ok") return "ok";
  if (entry.duration_ms !== null) return "ok";
  return "unknown";
}

/** Pick a stable, human-readable label for the entry. */
function deriveLabel(entry: BrowseDebugEntry, category: FlowNodeCategory): string {
  const attrs = readSafeAttrs(entry);
  switch (category) {
    case "tool":
      return entry.tool_name ?? attrs.tool_result_type ?? "tool";
    case "hook":
      return attrs.hook_type ?? "hook";
    case "skill":
      return attrs.skill_name ?? "skill";
    case "notification":
      return attrs.notification_kind ?? "notification";
    case "compaction":
      return attrs.compaction_kind ?? "compaction";
    case "model":
      return entry.kind === "llm_request" ? "llm_request" : "agent_response";
    case "subagent":
      return entry.message?.trim() || "subagent";
    case "session":
      return "session_start";
    case "turn":
      return "turn_start";
    case "error":
      return attrs.error_category ?? "error";
    default:
      return entry.message?.trim() || String(entry.kind);
  }
}

/** Build a concise sublabel from safe attrs + entry.duration_ms. */
function deriveSublabel(entry: BrowseDebugEntry, category: FlowNodeCategory): string | null {
  const attrs = readSafeAttrs(entry);
  const duration = formatDurationLabel(entry.duration_ms ?? attrs.tool_metric_duration_ms ?? null);
  const parts: string[] = [];

  switch (category) {
    case "tool": {
      const status =
        attrs.tool_status ??
        (attrs.tool_success === true ? "ok" : attrs.tool_success === false ? "error" : null);
      if (status) parts.push(status);
      if (isStr(attrs.tool_result_type)) parts.push(attrs.tool_result_type);
      if (duration) parts.push(duration);
      break;
    }
    case "hook": {
      if (attrs.hook_status) parts.push(attrs.hook_status);
      if (isStr(attrs.event_phase)) parts.push(attrs.event_phase);
      if (duration) parts.push(duration);
      break;
    }
    case "skill": {
      if (isStr(attrs.skill_path_category)) parts.push(attrs.skill_path_category);
      if (isNonNegNumber(attrs.skill_content_bytes)) parts.push(`${attrs.skill_content_bytes}B`);
      break;
    }
    case "model": {
      if (isNonNegNumber(attrs.output_tokens)) parts.push(`${attrs.output_tokens} tok`);
      if (isNonNegNumber(attrs.tool_request_count)) {
        parts.push(`${attrs.tool_request_count} tool req`);
      }
      if (duration) parts.push(duration);
      break;
    }
    case "notification": {
      if (isStr(attrs.notification_status)) parts.push(attrs.notification_status);
      if (typeof attrs.notification_exit_code === "number") {
        parts.push(`exit ${attrs.notification_exit_code}`);
      }
      break;
    }
    case "compaction": {
      if (isStr(attrs.mode)) parts.push(attrs.mode);
      if (duration) parts.push(duration);
      break;
    }
    case "error": {
      if (typeof attrs.exit_code === "number") parts.push(`exit ${attrs.exit_code}`);
      if (isStr(attrs.error_category)) parts.push(attrs.error_category);
      break;
    }
    default: {
      if (duration) parts.push(duration);
      break;
    }
  }

  return parts.length === 0 ? null : parts.join(" · ");
}

/** Build the full tooltip body. Always at least one line. */
function deriveTooltipLines(
  entry: BrowseDebugEntry,
  category: FlowNodeCategory,
  status: FlowNodeStatus
): string[] {
  const attrs = readSafeAttrs(entry);
  const lines: string[] = [];

  lines.push(`kind: ${entry.kind}`);
  if (isStr(attrs.event_type)) lines.push(`event: ${attrs.event_type}`);
  if (isStr(entry.source)) lines.push(`source: ${entry.source}`);
  if (isStr(entry.tool_name)) lines.push(`tool: ${entry.tool_name}`);
  if (category === "hook" && isStr(attrs.hook_type)) lines.push(`hook: ${attrs.hook_type}`);
  if (category === "skill" && isStr(attrs.skill_name)) lines.push(`skill: ${attrs.skill_name}`);
  if (category === "skill" && isStr(attrs.skill_path_category)) {
    lines.push(`path: ${attrs.skill_path_category}`);
  }
  if (status !== "unknown") lines.push(`status: ${status}`);

  const duration = formatDurationLabel(entry.duration_ms ?? attrs.tool_metric_duration_ms ?? null);
  if (duration) lines.push(`duration: ${duration}`);

  if (isNonNegNumber(attrs.tool_metric_input_bytes)) {
    lines.push(`input: ${attrs.tool_metric_input_bytes}B`);
  }
  if (isNonNegNumber(attrs.tool_metric_output_bytes)) {
    lines.push(`output: ${attrs.tool_metric_output_bytes}B`);
  }
  if (isNonNegNumber(attrs.output_tokens)) lines.push(`tokens: ${attrs.output_tokens}`);
  if (isNonNegNumber(attrs.tool_request_count)) {
    lines.push(`tool_requests: ${attrs.tool_request_count}`);
  }

  if (entry.redacted) lines.push("redacted: true");

  const ts = formatTimestampLabel(entry.timestamp);
  if (ts) lines.push(`time: ${ts}`);

  // Always include the truncated message preview, but never raw payloads
  // (the backend already capped this to 200 chars).
  if (isStr(entry.message)) lines.push(entry.message);

  return lines;
}

/**
 * Derive the layer label used for grouping/subgraph headers. Prefers
 * the safe `mode` attribute when set; otherwise falls back to the
 * non-empty `entry.source`. Never reads raw `agentId`.
 */
function deriveLayer(entry: BrowseDebugEntry): string | null {
  const attrs = readSafeAttrs(entry);
  if (isStr(attrs.mode)) return attrs.mode;
  if (isStr(entry.source) && entry.source !== "synthetic") return entry.source;
  return null;
}

/**
 * Compute a same-tool merge key. Adjacent sibling nodes with the same
 * groupKey can be visually merged into a single card (start+end pair).
 *
 * The key uses `span_id` (the backend's Synthetic Span-ID Rule already
 * pairs start/end events on the same span) plus `tool_name` to defend
 * against an unlikely id collision across different tools. null for
 * non-tool entries or entries with no span_id.
 */
export function deriveGroupKey(entry: BrowseDebugEntry): string | null {
  if (!isStr(entry.span_id)) return null;
  if (entry.kind !== "tool_call" && !isStr(entry.tool_name)) return null;
  const tool = entry.tool_name ?? "tool";
  return `tool:${tool}:${entry.span_id}`;
}

/**
 * Derive the complete pure render model for a single entry. Synthetic
 * orphans get a stable placeholder model (no real metadata to surface).
 */
export function deriveNodeRender(entry: BrowseDebugEntry): FlowNodeRender {
  if (entry.idx === -1) {
    return {
      label: "(orphans)",
      sublabel: null,
      timestampLabel: null,
      tooltipLines: ["Synthetic group for entries whose parent_span_id is unknown."],
      category: "generic",
      layer: null,
      status: "unknown",
      groupKey: null,
    };
  }

  const category = deriveNodeCategory(entry);
  const status = deriveNodeStatus(entry);
  return {
    label: deriveLabel(entry, category),
    sublabel: deriveSublabel(entry, category),
    timestampLabel: formatTimestampLabel(entry.timestamp),
    tooltipLines: deriveTooltipLines(entry, category, status),
    category,
    layer: deriveLayer(entry),
    status,
    groupKey: deriveGroupKey(entry),
  };
}

// ── Layout ───────────────────────────────────────────────────────────────────

function keyOf(node: SpanTreeNode): string {
  return node.entry.idx === -1 ? "synthetic-orphan" : `node-${node.entry.idx}`;
}

/**
 * Compute a deterministic top-down tree layout from a flat list of debug
 * entries using DFS pre-order vertical stacking.
 *
 * Each node is placed in DFS pre-order:
 *   - `y` = sequential row index × (FLOW_NODE_HEIGHT + FLOW_VERTICAL_GAP)
 *   - `x` = node.depth × FLOW_INDENT_PER_LEVEL
 *
 * This produces a compact, readable vertical outline that mirrors the
 * VS Code Agent Debug "flow chart" view. Siblings are stacked vertically
 * rather than spread horizontally, so deep trees remain visible without
 * requiring horizontal scrolling on typical viewport widths.
 *
 * Width  = max(x + FLOW_NODE_WIDTH) across all placed nodes.
 * Height = max(y + FLOW_NODE_HEIGHT) across all placed nodes.
 *
 * Missing parent_span_id values are handled by `deriveSpanTree`
 * (collected under a synthetic orphans root).
 */
export function computeFlowLayout(entries: BrowseDebugEntry[]): FlowLayout {
  const roots = deriveSpanTree(entries);
  const nodes: FlowNode[] = [];
  const edges: FlowEdge[] = [];

  if (roots.length === 0) {
    return { nodes, edges, width: 0, height: 0 };
  }

  // Incremented in DFS pre-order; each visit consumes one vertical slot.
  let rowIndex = 0;

  function layout(node: SpanTreeNode): void {
    const x = node.depth * FLOW_INDENT_PER_LEVEL;
    const y = rowIndex * (FLOW_NODE_HEIGHT + FLOW_VERTICAL_GAP);
    const isSynthetic = node.entry.idx === -1;

    nodes.push({
      id: keyOf(node),
      entry: node.entry,
      x,
      y,
      width: FLOW_NODE_WIDTH,
      height: FLOW_NODE_HEIGHT,
      depth: node.depth,
      isSynthetic,
      render: deriveNodeRender(node.entry),
    });

    rowIndex += 1;

    for (const child of node.children) {
      edges.push({ fromId: keyOf(node), toId: keyOf(child) });
      layout(child);
    }
  }

  for (const root of roots) {
    layout(root);
  }

  const width = nodes.reduce((m, n) => Math.max(m, n.x + n.width), 0);
  const height = nodes.reduce((m, n) => Math.max(m, n.y + n.height), 0);

  return { nodes, edges, width, height };
}

// ── Trace inspector model (v2) ───────────────────────────────────────────────

/** Domain used to render the trace overview ruler/lanes. */
export type TraceTimeMode = "time" | "index";

/** Minimum fractional width so zero-duration bars stay clickable. */
export const MIN_BAR_RATIO = 0.004;

/** A single per-event bar placed in a lane row. */
export interface TraceLaneBar {
  /** Stable selector — matches `BrowseDebugEntry.idx`. */
  entryIdx: number;
  laneId: PlaybackLaneId;
  category: FlowNodeCategory;
  status: FlowNodeStatus;
  /** Fractional [0..1] start within range. */
  startRatio: number;
  /** Fractional [0..1] width within range; always >= MIN_BAR_RATIO. */
  widthRatio: number;
  /** True when no real duration was available (rendered as a minimum-width stub). */
  isZeroWidth: boolean;
  /** Short label (tool / hook / skill / etc.) reused from `deriveNodeRender`. */
  label: string;
}

/** One lane row in the trace overview (PlaybackLaneId scoped). */
export interface TraceLane {
  id: PlaybackLaneId;
  count: number;
  errorCount: number;
  bars: TraceLaneBar[];
}

/** Time/index axis range and tick labels for the overview ruler. */
export interface TraceTimeRange {
  /** ms since epoch in time mode; 0-based index in index mode. */
  startMs: number;
  endMs: number;
  /** Tick positions as ratios [0..1]; 4–6 ticks. */
  ticks: { ratio: number; label: string }[];
  mode: TraceTimeMode;
}

/** Aggregate header counts for the trace overview strip. */
export interface TraceSummary {
  totalCount: number;
  errorCount: number;
  /** Wall-clock duration in ms (endMs − startMs); null in index mode. */
  totalDurationMs: number | null;
  /** Per-lane totals in `PLAYBACK_LANE_ORDER`. */
  laneTotals: { id: PlaybackLaneId; count: number; errorCount: number }[];
}

/** Pure layout consumed by the trace-overview UI. */
export interface TraceLayout {
  range: TraceTimeRange;
  /** Lanes with `count > 0`, sorted by `PLAYBACK_LANE_ORDER`. */
  lanes: TraceLane[];
  summary: TraceSummary;
}

/** One safe key/value row rendered in the inspector card grid. */
export interface TraceInspectorRow {
  key: string;
  value: string;
}

/** Pure model for the local inspector card above the SVG tree. */
export interface TraceInspectorCard {
  entryIdx: number;
  label: string;
  sublabel: string | null;
  category: FlowNodeCategory;
  status: FlowNodeStatus;
  layer: string | null;
  timestampLabel: string | null;
  /** Up to 12 safe key/value rows. Order is fixed; absent rows are omitted. */
  rows: TraceInspectorRow[];
  /** Truncated message preview (backend already capped to 200 chars). */
  messagePreview: string | null;
  /** True when the underlying entry had any redacted field. */
  redacted: boolean;
}

function emptyTraceLayout(): TraceLayout {
  return {
    range: { startMs: 0, endMs: 0, ticks: [], mode: "time" },
    lanes: [],
    summary: { totalCount: 0, errorCount: 0, totalDurationMs: null, laneTotals: [] },
  };
}

function makeTicks(mode: TraceTimeMode, startMs: number, endMs: number) {
  const span = Math.max(1, endMs - startMs);
  const ratios = [0, 0.25, 0.5, 0.75, 1];
  return ratios.map((ratio) => {
    if (mode === "index") {
      const idx = Math.round(ratio * span);
      return { ratio, label: `#${idx}` };
    }
    const deltaMs = Math.round(ratio * span);
    const label = formatDurationLabel(deltaMs) ?? "0ms";
    return { ratio, label: `+${label}` };
  });
}

function laneCategoryFor(entry: BrowseDebugEntry): FlowNodeCategory {
  return entry.idx === -1 ? "generic" : deriveNodeCategory(entry);
}

/**
 * Compute a pure trace-inspector layout (ruler + lanes + bars + summary)
 * for a flat entry list. Reuses `normalizeAndSortEntries` and
 * `derivePlaybackLane` from `debug-event-playback.ts` — no new data
 * plumbing, no raw `attrs` access beyond the existing safe contract.
 */
export function computeTraceLayout(entries: BrowseDebugEntry[]): TraceLayout {
  if (entries.length === 0) return emptyTraceLayout();

  // Exclude synthetic orphans from the time axis (R10).
  const real = entries.filter((e) => e.idx !== -1);
  if (real.length === 0) return emptyTraceLayout();

  const { normalized, mode } = normalizeAndSortEntries(real);

  let startMs = 0;
  let endMs = 0;
  if (mode === "index") {
    startMs = 0;
    endMs = Math.max(1, normalized.length - 1);
  } else {
    let min = Number.POSITIVE_INFINITY;
    let max = Number.NEGATIVE_INFINITY;
    for (const n of normalized) {
      if (n.timestampMs === null) continue;
      const d = n.entry.duration_ms ?? 0;
      const safeDuration = typeof d === "number" && Number.isFinite(d) && d >= 0 ? d : 0;
      if (n.timestampMs < min) min = n.timestampMs;
      if (n.timestampMs + safeDuration > max) max = n.timestampMs + safeDuration;
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      // No timestamps at all in time mode (shouldn't happen because that would
      // trigger index mode), degrade to a single-point window.
      startMs = 0;
      endMs = 1;
    } else {
      startMs = min;
      endMs = max <= min ? min + 1 : max;
    }
  }
  const totalSpan = Math.max(1, endMs - startMs);

  // Build a stable per-entry idx → normalized position for index-mode ratios.
  const positionByIdx = new Map<number, number>();
  normalized.forEach((n, i) => positionByIdx.set(n.entry.idx, i));

  // Group bars by lane.
  const laneBars = new Map<PlaybackLaneId, TraceLaneBar[]>();
  let errorCount = 0;
  const laneErrors = new Map<PlaybackLaneId, number>();

  for (const n of normalized) {
    const entry = n.entry;
    const laneId = derivePlaybackLane(entry);
    const category = laneCategoryFor(entry);
    const status = deriveNodeStatus(entry);
    if (status === "error") {
      errorCount += 1;
      laneErrors.set(laneId, (laneErrors.get(laneId) ?? 0) + 1);
    }

    let startRatio: number;
    let widthRatio: number;
    let isZeroWidth = false;

    if (mode === "index") {
      const pos = positionByIdx.get(entry.idx) ?? 0;
      startRatio = pos / totalSpan;
      widthRatio = MIN_BAR_RATIO;
      isZeroWidth = true;
    } else {
      if (n.timestampMs === null) {
        // Time mode with missing timestamp: park the bar at the end as
        // a zero-width marker so it's not lost but is visually de-emphasised.
        startRatio = 1;
        widthRatio = MIN_BAR_RATIO;
        isZeroWidth = true;
      } else {
        startRatio = (n.timestampMs - startMs) / totalSpan;
        const d = entry.duration_ms;
        if (typeof d === "number" && Number.isFinite(d) && d > 0) {
          widthRatio = Math.max(MIN_BAR_RATIO, d / totalSpan);
        } else {
          widthRatio = MIN_BAR_RATIO;
          isZeroWidth = true;
        }
      }
    }

    // Clamp to [0, 1] for safety.
    if (startRatio < 0) startRatio = 0;
    if (startRatio > 1) startRatio = 1;
    if (widthRatio < MIN_BAR_RATIO) widthRatio = MIN_BAR_RATIO;
    if (widthRatio > 1) widthRatio = 1;
    // Ensure the bar fits inside [0, 1]: shift startRatio left so that
    // `startRatio + widthRatio <= 1`. Previously we shrank widthRatio when
    // it overflowed, but a boundary-hugging bar (startRatio === 1) ended up
    // rendered at the far-right edge and clipped out of the viewport. The
    // index-mode last entry and time-mode trailing zero/null-duration events
    // at the max timestamp both fall into this case.
    if (startRatio + widthRatio > 1) {
      startRatio = Math.max(0, 1 - widthRatio);
    }

    const label = deriveNodeRender(entry).label;

    const bar: TraceLaneBar = {
      entryIdx: entry.idx,
      laneId,
      category,
      status,
      startRatio,
      widthRatio,
      isZeroWidth,
      label,
    };
    const arr = laneBars.get(laneId);
    if (arr) arr.push(bar);
    else laneBars.set(laneId, [bar]);
  }

  // Sort bars per lane (R6).
  for (const bars of laneBars.values()) {
    bars.sort((a, b) => {
      if (a.startRatio !== b.startRatio) return a.startRatio - b.startRatio;
      return a.entryIdx - b.entryIdx;
    });
  }

  // Emit lanes in PLAYBACK_LANE_ORDER; drop empty lanes (R5).
  const lanes: TraceLane[] = [];
  const laneTotals: TraceSummary["laneTotals"] = [];
  for (const id of PLAYBACK_LANE_ORDER) {
    const bars = laneBars.get(id);
    if (!bars || bars.length === 0) continue;
    const laneErrorCount = laneErrors.get(id) ?? 0;
    lanes.push({ id, count: bars.length, errorCount: laneErrorCount, bars });
    laneTotals.push({ id, count: bars.length, errorCount: laneErrorCount });
  }

  const ticks = makeTicks(mode, startMs, endMs);

  return {
    range: { startMs, endMs, ticks, mode },
    lanes,
    summary: {
      totalCount: normalized.length,
      errorCount,
      totalDurationMs: mode === "time" ? endMs - startMs : null,
      laneTotals,
    },
  };
}

/**
 * Allowlist of tooltip-line keys that `deriveTooltipLines` is permitted to
 * emit. Restricting structured rows to this set prevents free-form messages
 * such as `"Error: file not found"` or `"bash: command not found"` (which
 * happen to contain `": "`) from being misclassified as `key: value` rows.
 */
const ALLOWED_INSPECTOR_KEYS: ReadonlySet<string> = new Set([
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

/**
 * Map a `tooltipLines`-style key (`"kind: tool_call"`) into a structured
 * inspector row. Only keys in `ALLOWED_INSPECTOR_KEYS` are accepted so
 * free-form messages containing `": "` cannot synthesise arbitrary rows.
 * Returns null for lines that are not safe `key: value` pairs.
 */
function parseTooltipLineToRow(line: string): TraceInspectorRow | null {
  const idx = line.indexOf(": ");
  if (idx <= 0) return null;
  const key = line.slice(0, idx).trim();
  const value = line.slice(idx + 2).trim();
  if (!key || !value) return null;
  if (!ALLOWED_INSPECTOR_KEYS.has(key)) return null;
  return { key, value };
}

/**
 * Build the inspector card payload for a single entry. Reuses the safe
 * `tooltipLines` view (default-deny over safe attrs) so the renderer
 * never reads raw `attrs` beyond the existing DebugSafeAttrs contract.
 *
 * `deriveTooltipLines` always appends `entry.message` (when non-empty)
 * as the final line. We consume that trailing line as `messagePreview`
 * up front and only parse earlier lines into structured rows. This keeps
 * free-form messages like `"Error: file not found"` or
 * `"TypeError: cannot read properties"` out of the row grid.
 */
export function deriveInspectorCard(entry: BrowseDebugEntry): TraceInspectorCard {
  const render = deriveNodeRender(entry);
  const lines = render.tooltipLines.slice();
  let messagePreview: string | null = null;

  if (
    typeof entry.message === "string" &&
    entry.message.length > 0 &&
    lines.length > 0 &&
    lines[lines.length - 1] === entry.message
  ) {
    messagePreview = lines.pop() ?? null;
  }

  const rows: TraceInspectorRow[] = [];
  for (const line of lines) {
    const row = parseTooltipLineToRow(line);
    if (row && rows.length < 12) rows.push(row);
  }

  return {
    entryIdx: entry.idx,
    label: render.label,
    sublabel: render.sublabel,
    category: render.category,
    status: render.status,
    layer: render.layer,
    timestampLabel: render.timestampLabel,
    rows,
    messagePreview,
    redacted: entry.redacted === true,
  };
}

/**
 * Stable test-id slug for a `PlaybackLaneId` (lowercase, `/` → `-`).
 * Exported so component/playwright tests can derive selectors without
 * duplicating the mapping. `Tool/Hook/Skill` → `tool-hook-skill`.
 */
export function laneIdSlug(id: PlaybackLaneId): string {
  return id.toLowerCase().replace(/\//g, "-");
}

/** Re-export so consumers don't need a second import. */
export { type PlaybackLaneId };
