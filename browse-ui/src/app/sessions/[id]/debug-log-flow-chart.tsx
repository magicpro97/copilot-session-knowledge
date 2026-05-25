"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { BrowseDebugEntry, SubagentInternalsResponse } from "@/lib/api/types";
import {
  computeFlowLayout,
  computeTraceLayout,
  deriveInspectorCard,
  laneIdSlug,
  type FlowNodeCategory,
  type FlowNodeStatus,
  type PlaybackLaneId,
  type TraceInspectorCard,
} from "@/lib/debug-span-flow";
import type { FlowAggregateModel } from "@/lib/debug-flow-aggregate";

/**
 * Flow-chart view of the debug log — modeled on VS Code's Agent Debug
 * "flow chart" view. Renders a top-down SVG tree built from span_id /
 * parent_span_id relationships using the pure render model produced by
 * `debug-span-flow.ts`. The renderer never inspects raw `attrs`; it
 * consumes only `node.render` + safe public `BrowseDebugEntry` fields.
 *
 * Pan via mouse drag, zoom via mouse wheel (native non-passive listener
 * so `preventDefault` is honoured in modern browsers) or toolbar
 * buttons. Click a node to open the existing DetailDrawer (the parent
 * owns selection state).
 */

const PADDING = 24;
// VS Code Agent Debug clamps and wheel factor (see chatDebugFlowChartView.ts).
const MIN_SCALE = 0.1;
const MAX_SCALE = 5;
const WHEEL_ZOOM_FACTOR = 0.002;
const ZOOM_STEP = 1.2;

/** Category → accent colour for the left gutter and category label. */
const CATEGORY_COLORS: Record<FlowNodeCategory, string> = {
  session: "#94a3b8",
  turn: "#fbbf24",
  model: "#22d3ee",
  tool: "#34d399",
  hook: "#fb923c",
  skill: "#c084fc",
  subagent: "#f472b6",
  notification: "#60a5fa",
  compaction: "#a3e635",
  error: "#ef4444",
  generic: "#9ca3af",
};

function categoryColor(c: FlowNodeCategory): string {
  return CATEGORY_COLORS[c] ?? "#9ca3af";
}

function truncate(s: string, n: number): string {
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

export type DebugLogFlowChartProps = {
  entries: BrowseDebugEntry[];
  selectedEntry: BrowseDebugEntry | null;
  onSelect: (entry: BrowseDebugEntry) => void;
  /** True when more events are available beyond the currently-loaded page. */
  hasMore?: boolean;
  /** Total number of events on the server (across all pages). */
  totalEvents?: number;
  /** Full-session safe internals summary for sub-agent flow context. */
  subagentInternals?: SubagentInternalsResponse | null;
  /** Full-session aggregate model from Mission Atlas + Subagent Activity. */
  aggregate?: FlowAggregateModel;
  /** True while the Mission Atlas query is loading. */
  atlasLoading?: boolean;
  /** True when the Mission Atlas query failed. */
  atlasError?: boolean;
  /** Currently-loaded page index (0-based). */
  currentPage?: number;
  /** Raw entry idx for the first entry on the current page. */
  currentPageStart?: number;
  /** Raw entry idx for the last entry on the current page. */
  currentPageEnd?: number;
  /** Called when the user clicks a bucket/milestone/subagent bar to navigate. */
  onNavigateToIdx?: (idx: number) => void;
  /** True while the current page-level debug entries are being fetched. */
  pageLoading?: boolean;
};

export function DebugLogFlowChart({
  entries,
  selectedEntry,
  onSelect,
  hasMore = false,
  totalEvents,
  subagentInternals,
  aggregate,
  atlasLoading = false,
  atlasError = false,
  currentPage = 0,
  currentPageStart,
  currentPageEnd,
  onNavigateToIdx,
  pageLoading = false,
}: DebugLogFlowChartProps) {
  // P4 (perf): memoize the heavy layout so it isn't rebuilt on every
  // pan/zoom/state change. Only the entry list drives geometry.
  const layout = useMemo(() => computeFlowLayout(entries), [entries]);
  const [scale, setScale] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  const contentWidth = layout.width + PADDING * 2;
  const contentHeight = layout.height + PADDING * 2;

  // Native non-passive wheel listener. Using React's onWheel attaches a
  // passive listener in modern browsers; calling `preventDefault()` then
  // logs "Unable to preventDefault inside passive event listener" and the
  // page scrolls instead of zooming. We add the listener ourselves with
  // `{ passive: false }` and clean it up on unmount.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = svg.getBoundingClientRect();
      const hasRect = rect.width > 0 && rect.height > 0;

      // Cursor position normalised to 0..1 inside the SVG viewport. When
      // the SVG has no laid-out box (e.g. jsdom in unit tests), default
      // to the top-left so zoom still applies.
      const ratioX = hasRect ? clamp((e.clientX - rect.left) / rect.width, 0, 1) : 0;
      const ratioY = hasRect ? clamp((e.clientY - rect.top) / rect.height, 0, 1) : 0;

      setScale((prevScale) => {
        const factor = Math.pow(2, -e.deltaY * WHEEL_ZOOM_FACTOR);
        const nextScale = clamp(prevScale * factor, MIN_SCALE, MAX_SCALE);
        if (nextScale === prevScale) return prevScale;

        if (hasRect) {
          // Keep the tree point under the cursor stable across the zoom step.
          const viewWidthPrev = contentWidth / prevScale;
          const viewHeightPrev = contentHeight / prevScale;
          const viewWidthNext = contentWidth / nextScale;
          const viewHeightNext = contentHeight / nextScale;
          setPan((prevPan) => {
            const pointX = prevPan.x + ratioX * viewWidthPrev;
            const pointY = prevPan.y + ratioY * viewHeightPrev;
            return {
              x: pointX - ratioX * viewWidthNext,
              y: pointY - ratioY * viewHeightNext,
            };
          });
        }
        return nextScale;
      });
    };
    svg.addEventListener("wheel", handleWheel, { passive: false });
    return () => {
      svg.removeEventListener("wheel", handleWheel);
    };
  }, [contentWidth, contentHeight]);

  if (layout.nodes.length === 0) {
    return (
      <div className="border-border rounded-xl border" data-testid="debug-log-flow-chart">
        <FlowSessionCanvas
          aggregate={aggregate}
          atlasLoading={atlasLoading}
          atlasError={atlasError}
          currentPage={currentPage}
          currentPageStart={currentPageStart}
          currentPageEnd={currentPageEnd}
          onNavigateToIdx={onNavigateToIdx}
        />
        <div
          className="text-muted-foreground py-8 text-center text-sm"
          data-testid={pageLoading ? "debug-log-flow-page-loading" : "debug-log-flow-chart-empty"}
        >
          {pageLoading ? "Loading page events…" : "No events match the current filters."}
        </div>
      </div>
    );
  }

  const viewWidth = contentWidth / scale;
  const viewHeight = contentHeight / scale;

  const handleMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    if ((e.target as Element).closest("[data-flow-node]")) return;
    dragRef.current = { x: e.clientX, y: e.clientY };
  };

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!dragRef.current) return;
    const dx = (e.clientX - dragRef.current.x) / scale;
    const dy = (e.clientY - dragRef.current.y) / scale;
    dragRef.current = { x: e.clientX, y: e.clientY };
    setPan((p) => ({ x: p.x - dx, y: p.y - dy }));
  };

  const endDrag = () => {
    dragRef.current = null;
  };

  const zoomIn = () => setScale((s) => clamp(s * ZOOM_STEP, MIN_SCALE, MAX_SCALE));
  const zoomOut = () => setScale((s) => clamp(s / ZOOM_STEP, MIN_SCALE, MAX_SCALE));
  const reset = () => {
    setScale(1);
    setPan({ x: 0, y: 0 });
  };

  // Group visible (non-synthetic) nodes by render.layer for subgraph
  // headers. Stable insertion order. A null layer means "no header".
  const layerGroups = new Map<string, number>();
  for (const node of layout.nodes) {
    if (node.isSynthetic) continue;
    const layer = node.render.layer;
    if (!layer) continue;
    layerGroups.set(layer, (layerGroups.get(layer) ?? 0) + 1);
  }
  const layerEntries = Array.from(layerGroups.entries());

  return (
    <div className="border-border rounded-xl border" data-testid="debug-log-flow-chart">
      <FlowSessionCanvas
        aggregate={aggregate}
        atlasLoading={atlasLoading}
        atlasError={atlasError}
        currentPage={currentPage}
        currentPageStart={currentPageStart}
        currentPageEnd={currentPageEnd}
        onNavigateToIdx={onNavigateToIdx}
      />
      <div className="border-border bg-muted/30 flex flex-wrap items-center justify-between gap-2 border-b px-3 py-1.5 text-xs">
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-muted-foreground" data-testid="debug-log-flow-counts">
            {layout.nodes.length} node{layout.nodes.length === 1 ? "" : "s"} · {layout.edges.length}{" "}
            edge{layout.edges.length === 1 ? "" : "s"}
          </p>
          {layerEntries.length > 0 && (
            <p
              className="text-muted-foreground"
              data-testid="debug-log-flow-layers"
              aria-label="Flow layers"
            >
              Layers: {layerEntries.map(([layer, count]) => `${layer} (${count})`).join(" · ")}
            </p>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={zoomOut}
            className="hover:bg-muted rounded px-2 py-0.5 font-mono"
            aria-label="Zoom out"
          >
            −
          </button>
          <span
            className="text-muted-foreground w-12 text-center font-mono"
            aria-label="Current zoom level"
            data-testid="debug-log-flow-zoom"
          >
            {Math.round(scale * 100)}%
          </span>
          <button
            type="button"
            onClick={zoomIn}
            className="hover:bg-muted rounded px-2 py-0.5 font-mono"
            aria-label="Zoom in"
          >
            +
          </button>
          <button
            type="button"
            onClick={reset}
            className="hover:bg-muted ml-2 rounded px-2 py-0.5"
            aria-label="Reset flow chart view"
          >
            Reset
          </button>
        </div>
      </div>
      <SubagentFlowInternalsStrip data={subagentInternals} />
      <TraceOverviewStrip entries={entries} selectedEntry={selectedEntry} onSelect={onSelect} />
      <TraceInspectorPanel selectedEntry={selectedEntry} entries={entries} />
      <svg
        ref={svgRef}
        role="img"
        aria-label="Debug log flow chart"
        className="block w-full cursor-grab select-none active:cursor-grabbing"
        style={{ height: 480 }}
        viewBox={`${pan.x} ${pan.y} ${viewWidth} ${viewHeight}`}
        preserveAspectRatio="xMidYMin meet"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
      >
        <g transform={`translate(${PADDING},${PADDING})`}>
          {/* Edges first so nodes paint on top */}
          {layout.edges.map((edge) => {
            const from = layout.nodes.find((n) => n.id === edge.fromId);
            const to = layout.nodes.find((n) => n.id === edge.toId);
            if (!from || !to) return null;
            const x1 = from.x + from.width / 2;
            const y1 = from.y + from.height;
            const x2 = to.x + to.width / 2;
            const y2 = to.y;
            const midY = (y1 + y2) / 2;
            return (
              <path
                key={`${edge.fromId}->${edge.toId}`}
                d={`M${x1},${y1} C${x1},${midY} ${x2},${midY} ${x2},${y2}`}
                className="stroke-border"
                fill="none"
                strokeWidth={1.5}
              />
            );
          })}

          {layout.nodes.map((node) => {
            const render = node.render;
            const isSelected = selectedEntry?.idx === node.entry.idx && !node.isSynthetic;
            const status: FlowNodeStatus = render.status;
            const isError = status === "error";
            const accent = categoryColor(render.category);
            const strokeColor = isError ? "#ef4444" : isSelected ? "#3b82f6" : accent;
            const strokeWidth = isSelected || isError ? 2.5 : 1.25;
            const clickable = !node.isSynthetic;

            const ariaLabel = node.isSynthetic
              ? "Orphans group"
              : `Debug event ${node.entry.idx}: ${render.label}${
                  render.sublabel ? ` — ${render.sublabel}` : ""
                }`;
            const tooltip = render.tooltipLines.join("\n");

            return (
              <g
                key={node.id}
                data-flow-node={node.id}
                data-testid={
                  node.isSynthetic
                    ? "debug-log-flow-node-orphan"
                    : `debug-log-flow-node-${node.entry.idx}`
                }
                data-flow-category={render.category}
                data-flow-status={status}
                data-flow-layer={render.layer ?? ""}
                transform={`translate(${node.x},${node.y})`}
                className={clickable ? "cursor-pointer" : "cursor-default opacity-70"}
                role={clickable ? "button" : "group"}
                tabIndex={clickable ? 0 : -1}
                aria-label={ariaLabel}
                onClick={(e) => {
                  e.stopPropagation();
                  if (clickable) onSelect(node.entry);
                }}
                onKeyDown={(e) => {
                  if (!clickable) return;
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(node.entry);
                  }
                }}
              >
                {/* Native SVG tooltip — shows all render.tooltipLines. */}
                <title>{tooltip}</title>
                <rect
                  width={node.width}
                  height={node.height}
                  rx={8}
                  ry={8}
                  className="fill-card"
                  stroke={strokeColor}
                  strokeWidth={strokeWidth}
                  strokeDasharray={node.isSynthetic ? "4 3" : undefined}
                />
                {/* Category accent gutter */}
                <rect width={4} height={node.height} fill={accent} rx={2} ry={2} />
                {/* Layer / category header */}
                <text
                  x={12}
                  y={14}
                  fontSize={9}
                  fontFamily="ui-monospace, SFMono-Regular, monospace"
                  className="fill-muted-foreground"
                  data-testid={
                    node.isSynthetic ? undefined : `debug-log-flow-node-${node.entry.idx}-layer`
                  }
                >
                  {truncate(render.layer ?? render.category, 28)}
                </text>
                {/* Primary label */}
                <text
                  x={12}
                  y={28}
                  fontSize={11}
                  fontWeight={600}
                  className="fill-foreground"
                  data-testid={
                    node.isSynthetic ? undefined : `debug-log-flow-node-${node.entry.idx}-label`
                  }
                >
                  {truncate(render.label, 28)}
                </text>
                {/* Sublabel (status / metrics / duration) */}
                {render.sublabel ? (
                  <text
                    x={12}
                    y={42}
                    fontSize={10}
                    className={isError ? "fill-red-500" : "fill-muted-foreground"}
                    data-testid={
                      node.isSynthetic
                        ? undefined
                        : `debug-log-flow-node-${node.entry.idx}-sublabel`
                    }
                  >
                    {truncate(render.sublabel, 32)}
                  </text>
                ) : null}
                {/* Timestamp (HH:MM:SS UTC) */}
                {render.timestampLabel ? (
                  <text
                    x={node.width - 8}
                    y={node.height - 8}
                    fontSize={9}
                    textAnchor="end"
                    className="fill-muted-foreground"
                    data-testid={
                      node.isSynthetic
                        ? undefined
                        : `debug-log-flow-node-${node.entry.idx}-timestamp`
                    }
                  >
                    {render.timestampLabel}
                  </text>
                ) : null}
                {/* Error dot top-right */}
                {isError ? (
                  <circle
                    cx={node.width - 10}
                    cy={10}
                    r={4}
                    fill="#ef4444"
                    data-testid={
                      node.isSynthetic ? undefined : `debug-log-flow-node-${node.entry.idx}-error`
                    }
                  />
                ) : null}
              </g>
            );
          })}
        </g>
      </svg>
      {hasMore ? (
        <div
          className="border-border text-muted-foreground border-t px-3 py-1.5 text-xs"
          data-testid="debug-log-flow-has-more"
        >
          {typeof totalEvents === "number"
            ? `More events available — showing ${layout.nodes.length} of ${totalEvents}. Use Next below to load more.`
            : "More events available — use Next below to load more."}
        </div>
      ) : null}
    </div>
  );
}

// ── Flow Session Canvas (full-session aggregate) ──────────────────────────────

/** Lane display names for the session canvas. */
const SESSION_LANE_LABELS: Record<string, string> = {
  turn: "Turn",
  model: "Model",
  tool: "Tool",
  hook: "Hook",
  skill: "Skill",
  subagent: "SubAgent",
  error: "Error",
  generic: "Generic",
  system: "System",
};

/** Lane accent colors — must match CATEGORY_COLORS above. */
const SESSION_LANE_COLORS: Record<string, string> = {
  turn: "#fbbf24",
  model: "#22d3ee",
  tool: "#34d399",
  hook: "#fb923c",
  skill: "#c084fc",
  subagent: "#f472b6",
  error: "#ef4444",
  generic: "#9ca3af",
  system: "#94a3b8",
};

/** Milestone kind → short glyph for the milestone rail. */
function milestoneGlyph(kind: string): string {
  switch (kind) {
    case "checkpoint":
      return "⚑";
    case "error":
      return "✕";
    case "skill":
      return "◈";
    case "subagent":
      return "⊕";
    case "rewind":
      return "↺";
    case "task_complete":
      return "✓";
    default:
      return "·";
  }
}

/** Format milliseconds as a human-readable duration string. */
function formatDurationMs(ms: number | null): string {
  if (ms === null) return "";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
}

const FLOW_LANE_DISPLAY_ORDER = [
  "turn",
  "model",
  "tool",
  "hook",
  "skill",
  "subagent",
  "error",
  "generic",
  "system",
] as const;

type FlowSessionCanvasProps = {
  aggregate?: FlowAggregateModel;
  atlasLoading?: boolean;
  atlasError?: boolean;
  currentPage?: number;
  currentPageStart?: number;
  currentPageEnd?: number;
  onNavigateToIdx?: (idx: number) => void;
};

function FlowSessionCanvas({
  aggregate,
  atlasLoading = false,
  atlasError = false,
  currentPage = 0,
  currentPageStart,
  currentPageEnd,
  onNavigateToIdx,
}: FlowSessionCanvasProps) {
  // Loading skeleton — shows while Mission Atlas is fetching.
  if (atlasLoading) {
    return (
      <div
        className="border-border bg-card/30 border-b px-3 py-2"
        data-testid="debug-log-flow-session-canvas-loading"
        aria-label="Loading session overview"
      >
        <div className="text-muted-foreground animate-pulse text-xs">
          Loading full-session overview…
        </div>
      </div>
    );
  }

  // Error or missing aggregate — graceful fallback; page-level trace still works.
  if (atlasError) {
    return (
      <div
        className="border-border bg-card/30 border-b px-3 py-1.5 text-xs"
        data-testid="debug-log-flow-session-canvas-error"
      >
        <span className="text-muted-foreground">
          Session overview unavailable — showing current page only.
        </span>
      </div>
    );
  }

  // No aggregate or empty session — render nothing; page canvas operates alone.
  if (!aggregate || aggregate.totalEvents === 0) return null;

  const {
    totalEvents,
    durationMs,
    errorCount,
    buckets,
    milestones,
    subagentBars,
    legend,
    truncationWarnings,
  } = aggregate;

  // Determine which lanes are visible (non-zero total events).
  const visibleLanes = FLOW_LANE_DISPLAY_ORDER.filter(
    (lane) => (legend.laneTotals[lane as keyof typeof legend.laneTotals] ?? 0) > 0
  );

  // Current page bucket highlight: buckets whose [start_idx, end_idx] overlaps with
  // [currentPageStart, currentPageEnd].
  const currentPageStartEff = currentPageStart ?? 0;
  const currentPageEndEff = currentPageEnd ?? currentPageStartEff + 99;

  // Compute page window position as a ratio [0, 1] of the full session.
  const pageWindowLeft =
    totalEvents > 0 && currentPageStart !== undefined
      ? clamp(currentPageStart / totalEvents, 0, 1)
      : null;
  const pageWindowWidth =
    totalEvents > 0
      ? clamp((currentPageEndEff - currentPageStartEff + 1) / totalEvents, 0.005, 1)
      : null;

  // Map a bucket to its horizontal position as a ratio [0, 1].
  const bucketCount = buckets.length;
  function bucketLeft(bIdx: number): number {
    return bucketCount > 0 ? bIdx / bucketCount : 0;
  }
  function bucketWidth(): number {
    return bucketCount > 0 ? 1 / bucketCount : 1;
  }

  return (
    <div
      className="border-border bg-card/30 border-b"
      data-testid="debug-log-flow-session-canvas"
      data-flow-total-events={totalEvents}
      aria-label="Full-session flow overview"
      role="region"
    >
      {/* ── Session header ─────────────────────────────────────────────── */}
      <div className="border-border flex flex-wrap items-center gap-3 border-b px-3 py-1.5 text-xs">
        <span className="text-foreground font-medium" data-testid="debug-log-flow-session-total">
          {totalEvents.toLocaleString()} events
        </span>
        {durationMs !== null ? (
          <span className="text-muted-foreground">{formatDurationMs(durationMs)}</span>
        ) : null}
        {errorCount > 0 ? (
          <span
            className="text-red-600 dark:text-red-400"
            data-testid="debug-log-flow-session-errors"
          >
            {errorCount} error{errorCount === 1 ? "" : "s"}
          </span>
        ) : null}
        <span className="text-muted-foreground ml-auto" data-testid="debug-log-flow-session-window">
          {currentPage !== undefined
            ? `Page ${currentPage + 1} · events ${currentPageStartEff}–${currentPageEndEff}`
            : null}
        </span>
      </div>

      {/* ── Swimlane density chart ─────────────────────────────────────── */}
      {visibleLanes.length > 0 ? (
        <div className="px-3 py-2" aria-label="Lane density chart">
          {visibleLanes.map((lane) => (
            <div key={lane} className="mb-0.5 flex items-center gap-1.5">
              {/* Lane label */}
              <span
                className="w-20 shrink-0 text-right font-mono text-[10px]"
                style={{ color: SESSION_LANE_COLORS[lane] ?? "#9ca3af" }}
                aria-label={SESSION_LANE_LABELS[lane] ?? lane}
              >
                {SESSION_LANE_LABELS[lane] ?? lane}
              </span>
              {/* Bucket density bar */}
              <div
                className="relative flex h-3 flex-1 overflow-hidden rounded"
                style={{ background: "rgba(128,128,128,0.08)" }}
              >
                {/* Page window indicator overlay */}
                {pageWindowLeft !== null && pageWindowWidth !== null ? (
                  <div
                    className="border-primary absolute top-0 h-full rounded border"
                    style={{
                      left: `${pageWindowLeft * 100}%`,
                      width: `${pageWindowWidth * 100}%`,
                      background: "rgba(59,130,246,0.12)",
                      zIndex: 1,
                    }}
                    aria-hidden="true"
                  />
                ) : null}
                {/* Bucket cells */}
                {buckets.map((bucket) => {
                  const intensity = bucket.laneIntensities[lane] ?? 0;
                  if (intensity === 0 && !bucket.is_gap) return null;
                  const left = bucketLeft(bucket.bucket_idx) * 100;
                  const width = bucketWidth() * 100;
                  const hasIdx = bucket.start_idx !== null;
                  const color = SESSION_LANE_COLORS[lane] ?? "#9ca3af";
                  const isInCurrentPage =
                    bucket.start_idx !== null &&
                    bucket.end_idx !== null &&
                    bucket.start_idx <= currentPageEndEff &&
                    bucket.end_idx >= currentPageStartEff;
                  return (
                    <div
                      key={`${lane}-${bucket.bucket_idx}`}
                      className={hasIdx ? "cursor-pointer" : "cursor-default"}
                      style={{
                        position: "absolute",
                        top: 0,
                        left: `${left}%`,
                        width: `${Math.max(width, 0.3)}%`,
                        height: "100%",
                        background: bucket.is_gap
                          ? "repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(128,128,128,0.12) 2px, rgba(128,128,128,0.12) 4px)"
                          : color,
                        opacity: bucket.is_gap ? 0.4 : intensity,
                        outline: isInCurrentPage ? `1px solid ${color}` : undefined,
                        zIndex: isInCurrentPage ? 2 : undefined,
                      }}
                      role={hasIdx ? "button" : undefined}
                      tabIndex={hasIdx ? 0 : undefined}
                      aria-label={
                        hasIdx
                          ? `${SESSION_LANE_LABELS[lane] ?? lane} bucket ${bucket.bucket_idx}: ${bucket.event_count} events`
                          : undefined
                      }
                      data-testid={`debug-log-flow-session-bucket-${lane}-${bucket.bucket_idx}`}
                      onClick={
                        hasIdx && onNavigateToIdx
                          ? (e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              onNavigateToIdx(bucket.start_idx!);
                            }
                          : undefined
                      }
                      onKeyDown={
                        hasIdx && onNavigateToIdx
                          ? (e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                onNavigateToIdx(bucket.start_idx!);
                              }
                            }
                          : undefined
                      }
                    />
                  );
                })}
              </div>
              {/* Lane total count */}
              <span className="text-muted-foreground w-10 text-right font-mono text-[10px]">
                {(legend.laneTotals[lane as keyof typeof legend.laneTotals] ?? 0).toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      ) : null}

      {/* ── Milestone rail ────────────────────────────────────────────── */}
      {milestones.length > 0 ? (
        <div
          className="border-border border-t px-3 py-1"
          aria-label="Milestone rail"
          data-testid="debug-log-flow-session-milestones"
        >
          <div className="relative flex h-4 flex-1 overflow-hidden">
            {milestones.map((m, i) => {
              // Position by bucket_idx if available, else by idx ratio.
              let leftRatio = 0;
              if (m.bucket_idx !== null && bucketCount > 0) {
                leftRatio = m.bucket_idx / bucketCount;
              } else if (m.idx !== null && totalEvents > 0) {
                leftRatio = m.idx / totalEvents;
              }
              const hasIdx = m.idx !== null;
              const glyph = milestoneGlyph(m.kind);
              return (
                <span
                  key={`ms-${i}`}
                  className={`absolute text-[10px] leading-none ${
                    m.kind === "error"
                      ? "text-red-500"
                      : m.kind === "task_complete"
                        ? "text-green-500"
                        : "text-muted-foreground"
                  } ${hasIdx ? "hover:text-foreground cursor-pointer" : ""}`}
                  style={{ left: `${clamp(leftRatio, 0, 0.99) * 100}%`, top: 2 }}
                  title={`${m.kind}: ${m.label}`}
                  role={hasIdx ? "button" : undefined}
                  tabIndex={hasIdx ? 0 : undefined}
                  aria-label={hasIdx ? `${m.kind}: ${m.label}` : undefined}
                  data-testid={`debug-log-flow-session-milestone-${i}`}
                  onClick={
                    hasIdx && onNavigateToIdx
                      ? (e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          onNavigateToIdx(m.idx!);
                        }
                      : undefined
                  }
                  onKeyDown={
                    hasIdx && onNavigateToIdx
                      ? (e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            onNavigateToIdx(m.idx!);
                          }
                        }
                      : undefined
                  }
                >
                  {glyph}
                </span>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* ── Subagent bars ─────────────────────────────────────────────── */}
      {subagentBars.length > 0 ? (
        <div
          className="border-border border-t px-3 py-1.5"
          aria-label="Sub-agent activity bars"
          data-testid="debug-log-flow-session-subagents"
        >
          <div className="text-muted-foreground mb-1 text-[10px] font-medium">Sub-agents</div>
          {subagentBars.slice(0, 8).map((bar) => {
            const left =
              bar.start_idx !== null && totalEvents > 0
                ? clamp(bar.start_idx / totalEvents, 0, 1) * 100
                : 0;
            const right =
              bar.end_idx !== null && totalEvents > 0
                ? clamp(bar.end_idx / totalEvents, 0, 1) * 100
                : left + 1;
            const widthPct = Math.max(right - left, 0.5);
            const hasIdx = bar.start_idx !== null;
            return (
              <div key={bar.key} className="mb-0.5 flex items-center gap-1.5">
                <span
                  className="w-20 shrink-0 truncate text-right font-mono text-[10px]"
                  title={bar.label}
                  style={{ color: "#f472b6" }}
                >
                  {bar.label.length > 10 ? `${bar.label.slice(0, 10)}…` : bar.label}
                </span>
                <div
                  className="relative flex h-2.5 flex-1 overflow-hidden rounded"
                  style={{ background: "rgba(128,128,128,0.08)" }}
                >
                  <div
                    className={`absolute h-full rounded ${
                      bar.status === "failed"
                        ? "bg-red-500/70"
                        : bar.status === "completed"
                          ? "bg-pink-400/70"
                          : "bg-pink-300/50"
                    } ${hasIdx ? "cursor-pointer" : ""}`}
                    style={{ left: `${left}%`, width: `${widthPct}%` }}
                    role={hasIdx ? "button" : undefined}
                    tabIndex={hasIdx ? 0 : undefined}
                    aria-label={
                      hasIdx
                        ? `Sub-agent ${bar.label}: ${bar.status}${bar.durationMs !== null ? ` · ${formatDurationMs(bar.durationMs)}` : ""}`
                        : undefined
                    }
                    data-testid={`debug-log-flow-session-subagent-${bar.key}`}
                    onClick={
                      hasIdx && onNavigateToIdx
                        ? (e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            onNavigateToIdx(bar.start_idx!);
                          }
                        : undefined
                    }
                    onKeyDown={
                      hasIdx && onNavigateToIdx
                        ? (e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              onNavigateToIdx(bar.start_idx!);
                            }
                          }
                        : undefined
                    }
                  />
                </div>
                <span
                  className={`w-12 text-right font-mono text-[10px] ${
                    bar.status === "failed" ? "text-red-500" : "text-muted-foreground"
                  }`}
                >
                  {bar.status}
                </span>
              </div>
            );
          })}
        </div>
      ) : null}

      {/* ── Top entities (chip rows) ───────────────────────────────────── */}
      {legend.topTools.length > 0 || legend.topSkills.length > 0 || legend.topAgents.length > 0 ? (
        <div
          className="border-border border-t px-3 py-1.5"
          aria-label="Top tools, skills, and agents"
          data-testid="debug-log-flow-session-legend"
        >
          {legend.topTools.length > 0 ? (
            <div className="mb-1 flex flex-wrap items-center gap-1">
              <span className="text-muted-foreground mr-1 text-[10px]">Tools:</span>
              {legend.topTools.map((t) => (
                <span
                  key={t.name}
                  className="border-border bg-background rounded border px-1 py-0.5 font-mono text-[10px]"
                  style={{ borderColor: "#34d399" }}
                  data-testid={`debug-log-flow-session-tool-chip-${t.name}`}
                >
                  {t.name} ·{" "}
                  <span className="text-muted-foreground">{t.count.toLocaleString()}</span>
                </span>
              ))}
            </div>
          ) : null}
          {legend.topSkills.length > 0 ? (
            <div className="mb-1 flex flex-wrap items-center gap-1">
              <span className="text-muted-foreground mr-1 text-[10px]">Skills:</span>
              {legend.topSkills.map((s) => (
                <span
                  key={s.name}
                  className="border-border bg-background rounded border px-1 py-0.5 font-mono text-[10px]"
                  style={{ borderColor: "#c084fc" }}
                  data-testid={`debug-log-flow-session-skill-chip-${s.name}`}
                >
                  {s.name} ·{" "}
                  <span className="text-muted-foreground">{s.count.toLocaleString()}</span>
                </span>
              ))}
            </div>
          ) : null}
          {legend.topAgents.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-muted-foreground mr-1 text-[10px]">Agents:</span>
              {legend.topAgents.map((a) => (
                <span
                  key={a.name}
                  className="border-border bg-background rounded border px-1 py-0.5 font-mono text-[10px]"
                  style={{ borderColor: "#f472b6" }}
                  data-testid={`debug-log-flow-session-agent-chip-${a.name}`}
                >
                  {a.name} ·{" "}
                  <span className="text-muted-foreground">{a.count.toLocaleString()}</span>
                </span>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}

      {/* ── Truncation warning ─────────────────────────────────────────── */}
      {truncationWarnings.length > 0 ? (
        <div
          className="border-border text-muted-foreground border-t px-3 py-1 text-[10px]"
          data-testid="debug-log-flow-session-truncation"
          aria-live="polite"
        >
          ⚠ {truncationWarnings.join(" · ")}
        </div>
      ) : null}
    </div>
  );
}

// ── Sub-agent internals strip ─────────────────────────────────────────────────

function SubagentFlowInternalsStrip({ data }: { data?: SubagentInternalsResponse | null }) {
  if (!data || data.entries.length === 0) return null;

  const top = data.entries
    .slice()
    .sort((a, b) => b.internals.internal_event_count - a.internals.internal_event_count)
    .slice(0, 6);

  return (
    <div
      className="border-border bg-card/40 border-b px-3 py-2 text-xs"
      data-testid="debug-log-flow-subagent-internals"
      aria-label="Sub-agent internals overview"
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="font-medium">Sub-agent internals</span>
        <span className="text-muted-foreground">
          {data.returned} agent{data.returned === 1 ? "" : "s"} ·{" "}
          {data.entries.reduce((sum, entry) => sum + entry.internals.tool_call_count, 0)} tools ·{" "}
          {data.entries.reduce((sum, entry) => sum + entry.internals.llm_turn_count, 0)} model
          messages
        </span>
        {!data.skill_correlation_supported && data.uncorrelated_skill_invocations > 0 ? (
          <span className="text-muted-foreground" data-testid="debug-log-flow-skills-note">
            Skills are session-level only ({data.uncorrelated_skill_invocations} loads)
          </span>
        ) : null}
      </div>
      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        {top.map((entry) => {
          const label = entry.agent_display_name ?? entry.agent_name ?? "Sub-agent";
          const toolNames = entry.internals.tool_names.slice(0, 5);
          return (
            <div
              key={entry.agent_key_hash}
              className="border-border bg-background/70 rounded border px-2 py-1.5"
              data-testid={`debug-log-flow-subagent-${entry.agent_key_hash}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{truncate(label, 34)}</span>
                <span
                  className={
                    entry.status === "failed"
                      ? "text-red-600 dark:text-red-400"
                      : "text-muted-foreground"
                  }
                >
                  {entry.status}
                </span>
              </div>
              <div className="text-muted-foreground mt-1">
                {entry.internals.tool_call_count} tools · {entry.internals.llm_turn_count} model ·{" "}
                {entry.internals.output_tokens_total.toLocaleString()} tok
              </div>
              {toolNames.length > 0 ? (
                <div className="mt-1 flex flex-wrap gap-1">
                  {toolNames.map((tool) => (
                    <span key={tool} className="border-border rounded border px-1 py-0.5">
                      {tool}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
      {data.session_skill_names.length > 0 ? (
        <p className="text-muted-foreground mt-2" data-testid="debug-log-flow-session-skills">
          Session skills: {data.session_skill_names.join(", ")}
        </p>
      ) : null}
    </div>
  );
}

// ── Trace overview strip (v2) ────────────────────────────────────────────────

const TRACE_STRIP_VIEW_WIDTH = 1000;
const TRACE_LANE_LABEL_WIDTH = 160;
const TRACE_LANE_HEIGHT = 20;
const TRACE_BAR_HEIGHT = 12;
const TRACE_MIN_BAR_PX = 2;

const LANE_LABELS: Record<PlaybackLaneId, string> = {
  Session: "Session",
  Turn: "Turn",
  Model: "Model",
  "Tool/Hook/Skill": "Tool/Hook/Skill",
  SubAgent: "SubAgent",
  "Notification/Compaction": "Notification/Compaction",
  Error: "Error",
  Generic: "Generic",
};

type TraceOverviewStripProps = {
  entries: BrowseDebugEntry[];
  selectedEntry: BrowseDebugEntry | null;
  onSelect: (entry: BrowseDebugEntry) => void;
};

function TraceOverviewStrip({ entries, selectedEntry, onSelect }: TraceOverviewStripProps) {
  const trace = computeTraceLayout(entries);
  if (trace.lanes.length === 0) return null;

  const trackWidth = TRACE_STRIP_VIEW_WIDTH - TRACE_LANE_LABEL_WIDTH;
  const totalHeight = 26 + trace.lanes.length * TRACE_LANE_HEIGHT;

  return (
    <div
      className="border-border bg-card/40 border-b px-3 py-2"
      data-testid="debug-log-flow-trace"
      data-flow-mode={trace.range.mode}
      role="group"
      aria-label="Debug log trace overview"
    >
      <svg
        className="block w-full"
        viewBox={`0 0 ${TRACE_STRIP_VIEW_WIDTH} ${totalHeight}`}
        preserveAspectRatio="none"
        style={{ height: totalHeight }}
      >
        {/* Time ruler row */}
        <g
          data-testid="debug-log-flow-time-ruler"
          transform={`translate(${TRACE_LANE_LABEL_WIDTH},0)`}
        >
          <line x1={0} y1={20} x2={trackWidth} y2={20} className="stroke-border" strokeWidth={1} />
          {trace.range.ticks.map((tick, i) => {
            const x = tick.ratio * trackWidth;
            return (
              <g
                key={`tick-${i}`}
                data-testid={`debug-log-flow-tick-${i}`}
                transform={`translate(${x},0)`}
              >
                <line y1={14} y2={20} className="stroke-border" strokeWidth={1} />
                <text
                  y={11}
                  x={i === trace.range.ticks.length - 1 ? -4 : 2}
                  textAnchor={i === trace.range.ticks.length - 1 ? "end" : "start"}
                  fontSize={9}
                  fontFamily="ui-monospace, SFMono-Regular, monospace"
                  className="fill-muted-foreground"
                >
                  {tick.label}
                </text>
              </g>
            );
          })}
        </g>

        {/* Lane rows */}
        {trace.lanes.map((lane, laneIdx) => {
          const slug = laneIdSlug(lane.id);
          const yTop = 26 + laneIdx * TRACE_LANE_HEIGHT;
          const yMid = yTop + TRACE_LANE_HEIGHT / 2 - TRACE_BAR_HEIGHT / 2;
          return (
            <g
              key={lane.id}
              data-testid={`debug-log-flow-lane-${slug}`}
              data-flow-lane-count={lane.count}
              data-flow-lane-errors={lane.errorCount}
            >
              <text
                x={4}
                y={yTop + TRACE_LANE_HEIGHT / 2 + 3}
                fontSize={10}
                className="fill-foreground"
                fontFamily="ui-monospace, SFMono-Regular, monospace"
              >
                {`${LANE_LABELS[lane.id] ?? lane.id} · ${lane.count}`}
              </text>
              <line
                x1={TRACE_LANE_LABEL_WIDTH}
                y1={yTop + TRACE_LANE_HEIGHT - 1}
                x2={TRACE_STRIP_VIEW_WIDTH}
                y2={yTop + TRACE_LANE_HEIGHT - 1}
                className="stroke-border/40"
                strokeWidth={0.5}
              />
              {lane.bars.map((bar) => {
                const entry = entries.find((e) => e.idx === bar.entryIdx);
                if (!entry) return null;
                const x = TRACE_LANE_LABEL_WIDTH + bar.startRatio * trackWidth;
                const w = Math.max(TRACE_MIN_BAR_PX, bar.widthRatio * trackWidth);
                const isSelected = selectedEntry?.idx === bar.entryIdx;
                const isError = bar.status === "error";
                const fill = categoryColor(bar.category);
                const stroke = isSelected ? "#3b82f6" : isError ? "#ef4444" : fill;
                const strokeWidth = isSelected || isError ? 2 : 0;
                const tooltip = `${LANE_LABELS[lane.id] ?? lane.id} · ${bar.label}`;
                return (
                  <g
                    key={`bar-${bar.entryIdx}`}
                    data-testid={`debug-log-flow-bar-${bar.entryIdx}`}
                    data-flow-category={bar.category}
                    data-flow-status={bar.status}
                    data-flow-lane={lane.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`Debug event ${bar.entryIdx}: ${bar.label}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect(entry);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelect(entry);
                      }
                    }}
                    className="cursor-pointer focus:outline-none"
                  >
                    <title>{tooltip}</title>
                    <rect
                      x={x}
                      y={yMid}
                      width={w}
                      height={TRACE_BAR_HEIGHT}
                      rx={2}
                      ry={2}
                      fill={fill}
                      stroke={stroke}
                      strokeWidth={strokeWidth}
                      opacity={bar.isZeroWidth ? 0.55 : 0.95}
                    />
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ── Inspector card (v2) ──────────────────────────────────────────────────────

type TraceInspectorPanelProps = {
  selectedEntry: BrowseDebugEntry | null;
  entries: BrowseDebugEntry[];
};

function TraceInspectorPanel({ selectedEntry, entries }: TraceInspectorPanelProps) {
  // Local memory: once a user has selected anything in this view session,
  // never revert to the empty placeholder (design rule on inspector card).
  const [lastCard, setLastCard] = useState<TraceInspectorCard | null>(null);

  useEffect(() => {
    if (!selectedEntry) return;
    // Confirm the selected entry is still present in the current page.
    const present = entries.some((e) => e.idx === selectedEntry.idx);
    if (!present) return;
    setLastCard(deriveInspectorCard(selectedEntry));
  }, [selectedEntry, entries]);

  if (!lastCard) {
    return (
      <div
        className="border-border bg-muted/10 text-muted-foreground border-b px-3 py-3 text-xs"
        data-testid="debug-log-flow-inspector-empty"
      >
        Click a bar or node to inspect.
      </div>
    );
  }

  const card = lastCard;
  const isError = card.status === "error";

  return (
    <div
      className="border-border bg-card/30 border-b px-3 py-2"
      data-testid="debug-log-flow-inspector"
      role="region"
      aria-label="Selected debug event"
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span
          className="border-border rounded border px-1.5 py-0.5 font-mono"
          style={{ borderColor: categoryColor(card.category), color: categoryColor(card.category) }}
          data-testid="debug-log-flow-inspector-category"
        >
          {card.category}
        </span>
        <span className="text-foreground font-medium">{card.label}</span>
        {card.timestampLabel ? (
          <span
            className="text-muted-foreground font-mono"
            data-testid="debug-log-flow-inspector-timestamp"
          >
            {card.timestampLabel}
          </span>
        ) : null}
        <span
          className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
            isError ? "bg-red-100 text-red-700" : "bg-muted text-muted-foreground"
          }`}
          data-testid="debug-log-flow-inspector-status"
        >
          {card.status}
        </span>
        {card.redacted ? (
          <span
            className="rounded bg-amber-100 px-1.5 py-0.5 font-mono text-[10px] text-amber-700"
            data-testid="debug-log-flow-inspector-redacted"
          >
            redacted
          </span>
        ) : null}
      </div>
      {card.sublabel ? (
        <p
          className="text-muted-foreground mt-1 text-xs"
          data-testid="debug-log-flow-inspector-sublabel"
        >
          {card.sublabel}
        </p>
      ) : null}
      {card.rows.length > 0 ? (
        <dl className="mt-2 grid grid-cols-1 gap-x-3 gap-y-0.5 text-xs sm:grid-cols-2 md:grid-cols-3">
          {card.rows.map((row) => (
            <div
              key={row.key}
              className="flex gap-1"
              data-testid={`debug-log-flow-inspector-row-${row.key}`}
            >
              <dt className="text-muted-foreground font-mono">{row.key}:</dt>
              <dd className="text-foreground truncate font-mono" title={row.value}>
                {row.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {card.messagePreview ? (
        <p
          className="text-foreground/80 mt-2 line-clamp-3 font-mono text-xs"
          data-testid="debug-log-flow-inspector-message"
        >
          {card.messagePreview}
        </p>
      ) : null}
    </div>
  );
}
