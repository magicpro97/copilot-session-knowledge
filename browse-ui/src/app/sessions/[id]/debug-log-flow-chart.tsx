"use client";

import { useRef, useState } from "react";

import type { BrowseDebugEntry } from "@/lib/api/types";
import { computeFlowLayout } from "@/lib/debug-span-flow";

/**
 * Flow-chart view of the debug log — modeled on VS Code's Agent Debug
 * "flow chart" (visual event hierarchy). Renders a top-down SVG tree built
 * from span_id / parent_span_id relationships. Pan via mouse drag, zoom via
 * mouse wheel or the toolbar buttons. Click a node to open the existing
 * DetailDrawer (the parent owns selection state).
 */

const PADDING = 24;
const MIN_SCALE = 0.25;
const MAX_SCALE = 4;
const ZOOM_STEP = 1.2;

/** Kind → accent colour for the left node border and kind label. */
const KIND_COLORS: Record<string, string> = {
  user_message: "#60a5fa",
  agent_response: "#a78bfa",
  tool_call: "#34d399",
  turn_start: "#fbbf24",
  subagent: "#f472b6",
  hook: "#fb923c",
  llm_request: "#22d3ee",
  session_start: "#94a3b8",
  error: "#ef4444",
  generic: "#9ca3af",
  raw: "#9ca3af",
};

function kindColor(kind: string): string {
  return KIND_COLORS[kind] ?? "#9ca3af";
}

function truncate(s: string, n: number): string {
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

function formatMs(ms: number): string {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(2)}s`;
}

export type DebugLogFlowChartProps = {
  entries: BrowseDebugEntry[];
  selectedEntry: BrowseDebugEntry | null;
  onSelect: (entry: BrowseDebugEntry) => void;
};

export function DebugLogFlowChart({ entries, selectedEntry, onSelect }: DebugLogFlowChartProps) {
  const layout = computeFlowLayout(entries);
  const [scale, setScale] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ x: number; y: number } | null>(null);

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

  const contentWidth = layout.width + PADDING * 2;
  const contentHeight = layout.height + PADDING * 2;
  const viewWidth = contentWidth / scale;
  const viewHeight = contentHeight / scale;

  const handleWheel = (e: React.WheelEvent<SVGSVGElement>) => {
    // Use passive=false implicit; React provides cancelable wheel event.
    e.preventDefault();
    const factor = e.deltaY > 0 ? 1 / ZOOM_STEP : ZOOM_STEP;
    setScale((s) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, s * factor)));
  };

  const handleMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    // Ignore drags that start on a node — those are clicks.
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

  const zoomIn = () => setScale((s) => Math.min(MAX_SCALE, s * ZOOM_STEP));
  const zoomOut = () => setScale((s) => Math.max(MIN_SCALE, s / ZOOM_STEP));
  const reset = () => {
    setScale(1);
    setPan({ x: 0, y: 0 });
  };

  return (
    <div className="border-border rounded-xl border" data-testid="debug-log-flow-chart">
      <div className="border-border bg-muted/30 flex items-center justify-between border-b px-3 py-1.5 text-xs">
        <p className="text-muted-foreground">
          {layout.nodes.length} node{layout.nodes.length === 1 ? "" : "s"} · {layout.edges.length}{" "}
          edge{layout.edges.length === 1 ? "" : "s"}
        </p>
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
        role="img"
        aria-label="Debug log flow chart"
        className="block w-full cursor-grab select-none active:cursor-grabbing"
        style={{ height: 480 }}
        viewBox={`${pan.x} ${pan.y} ${viewWidth} ${viewHeight}`}
        preserveAspectRatio="xMidYMin meet"
        onWheel={handleWheel}
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
            const isSelected = selectedEntry?.idx === node.entry.idx;
            const isError = node.entry.status === "error" || node.entry.kind === "error";
            const accent = kindColor(String(node.entry.kind));
            const strokeColor = isError ? "#ef4444" : isSelected ? "#3b82f6" : accent;
            const strokeWidth = isSelected || isError ? 2.5 : 1.25;
            const ariaLabel = node.isSynthetic
              ? "Orphans group"
              : `Debug event ${node.entry.idx}: ${node.entry.kind} from ${node.entry.source}`;

            const clickable = !node.isSynthetic;

            return (
              <g
                key={node.id}
                data-flow-node={node.id}
                data-testid={
                  node.isSynthetic
                    ? "debug-log-flow-node-orphan"
                    : `debug-log-flow-node-${node.entry.idx}`
                }
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
                {/* Kind accent stripe */}
                <rect width={4} height={node.height} fill={accent} rx={2} ry={2} />
                <text
                  x={12}
                  y={16}
                  fontSize={10}
                  fontFamily="ui-monospace, SFMono-Regular, monospace"
                  className="fill-muted-foreground"
                >
                  {String(node.entry.kind)}
                </text>
                <text x={12} y={34} fontSize={11} className="fill-foreground">
                  {truncate(node.entry.message, 28)}
                </text>
                <text x={12} y={50} fontSize={9} className="fill-muted-foreground">
                  {[
                    node.entry.source,
                    node.entry.tool_name ?? null,
                    node.entry.duration_ms !== null ? formatMs(node.entry.duration_ms) : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </text>
                {isError ? <circle cx={node.width - 10} cy={10} r={4} fill="#ef4444" /> : null}
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}
