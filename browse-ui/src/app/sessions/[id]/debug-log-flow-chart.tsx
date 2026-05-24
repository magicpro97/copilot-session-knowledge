"use client";

import { useEffect, useRef, useState } from "react";

import type { BrowseDebugEntry } from "@/lib/api/types";
import {
  computeFlowLayout,
  type FlowNodeCategory,
  type FlowNodeStatus,
} from "@/lib/debug-span-flow";

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
};

export function DebugLogFlowChart({
  entries,
  selectedEntry,
  onSelect,
  hasMore = false,
  totalEvents,
}: DebugLogFlowChartProps) {
  const layout = computeFlowLayout(entries);
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
      <div
        className="text-muted-foreground py-8 text-center text-sm"
        data-testid="debug-log-flow-chart-empty"
      >
        No events match the current filters.
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
