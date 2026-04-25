"use client";

import dynamic from "next/dynamic";
import {
  type ComponentType,
  type Ref,
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";

import type { GraphEdge, GraphNode } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type ForceGraphRef = {
  zoomToFit?: (durationMs?: number, padding?: number) => void;
  d3ReheatSimulation?: () => void;
};

type ForceGraphNode = GraphNode & { x?: number; y?: number };
type ForceGraphEdge = GraphEdge & {
  source: string | ForceGraphNode;
  target: string | ForceGraphNode;
};

type ForceGraphData = {
  nodes: ForceGraphNode[];
  links: ForceGraphEdge[];
};

type ForceGraph2DProps = {
  graphData: ForceGraphData;
  width: number;
  height: number;
  nodeRelSize?: number;
  nodeColor?: (node: object) => string;
  nodeVal?: (node: object) => number;
  linkColor?: (link: object) => string;
  linkWidth?: (link: object) => number;
  nodeLabel?: (node: object) => string;
  onNodeClick?: (node: object) => void;
  cooldownTicks?: number;
  onEngineStop?: () => void;
};

const ForceGraph2D = dynamic(
  () => import("react-force-graph-2d").then((module) => module.default),
  { ssr: false }
) as unknown as ComponentType<ForceGraph2DProps & { ref?: Ref<ForceGraphRef> }>;

export type GraphCanvasHandle = {
  fitToScreen: () => void;
};

type GraphCanvasProps = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedNodeId?: string | null;
  className?: string;
  onNodeSelect?: (node: GraphNode) => void;
};

function resolveNodeId(nodeOrId: string | ForceGraphNode): string {
  return typeof nodeOrId === "string" ? nodeOrId : nodeOrId.id;
}

export const GraphCanvas = forwardRef<GraphCanvasHandle, GraphCanvasProps>(
  function GraphCanvas({ nodes, edges, selectedNodeId, className, onNodeSelect }, ref) {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const forceGraphRef = useRef<ForceGraphRef | null>(null);
    const [size, setSize] = useState({ width: 0, height: 0 });

    const graphData = useMemo<ForceGraphData>(
      () => ({
        nodes: [...nodes],
        links: edges.map((edge) => ({ ...edge })),
      }),
      [edges, nodes]
    );

    const selectedNeighbors = useMemo(() => {
      if (!selectedNodeId) return new Set<string>();
      const next = new Set<string>([selectedNodeId]);
      for (const edge of edges) {
        if (edge.source === selectedNodeId) next.add(edge.target);
        if (edge.target === selectedNodeId) next.add(edge.source);
      }
      return next;
    }, [edges, selectedNodeId]);

    useEffect(() => {
      if (!containerRef.current) return;
      const node = containerRef.current;
      const observer = new ResizeObserver((entries) => {
        const entry = entries[0];
        if (!entry) return;
        setSize({
          width: Math.max(240, Math.floor(entry.contentRect.width)),
          height: Math.max(320, Math.floor(entry.contentRect.height)),
        });
      });

      observer.observe(node);
      return () => observer.disconnect();
    }, []);

    const fitToScreen = useCallback(() => {
      forceGraphRef.current?.zoomToFit?.(500, 56);
    }, []);

    useImperativeHandle(
      ref,
      () => ({
        fitToScreen,
      }),
      [fitToScreen]
    );

    useEffect(() => {
      if (nodes.length === 0) return;
      const timeout = window.setTimeout(() => {
        forceGraphRef.current?.d3ReheatSimulation?.();
        fitToScreen();
      }, 180);
      return () => window.clearTimeout(timeout);
    }, [fitToScreen, nodes.length]);

    return (
      <div
        ref={containerRef}
        className={cn("h-[65vh] min-h-[22rem] w-full rounded-xl border bg-card", className)}
      >
        {size.width > 0 && size.height > 0 ? (
          <ForceGraph2D
            ref={forceGraphRef}
            graphData={graphData}
            width={size.width}
            height={size.height}
            cooldownTicks={140}
            nodeRelSize={5}
            nodeColor={(node) => (node as GraphNode).color}
            nodeVal={(node) => {
              const typed = node as GraphNode;
              if (typed.id === selectedNodeId) return 6;
              if (selectedNodeId && selectedNeighbors.has(typed.id)) return 3;
              return 2;
            }}
            linkColor={(link) => {
              const typed = link as ForceGraphEdge;
              if (!selectedNodeId) return "rgba(148, 163, 184, 0.45)";
              const sourceId = resolveNodeId(typed.source);
              const targetId = resolveNodeId(typed.target);
              if (sourceId === selectedNodeId || targetId === selectedNodeId) {
                return "rgba(99, 102, 241, 0.75)";
              }
              return "rgba(148, 163, 184, 0.2)";
            }}
            linkWidth={(link) => {
              const typed = link as ForceGraphEdge;
              if (!selectedNodeId) return 1;
              const sourceId = resolveNodeId(typed.source);
              const targetId = resolveNodeId(typed.target);
              return sourceId === selectedNodeId || targetId === selectedNodeId ? 2 : 0.8;
            }}
            nodeLabel={(node) => {
              const typed = node as GraphNode;
              const parts = [typed.label];
              if (typed.category) parts.push(`category: ${typed.category}`);
              if (typed.wing) parts.push(`wing: ${typed.wing}`);
              return parts.join("\n");
            }}
            onNodeClick={(node) => onNodeSelect?.(node as GraphNode)}
          />
        ) : null}
      </div>
    );
  }
);
