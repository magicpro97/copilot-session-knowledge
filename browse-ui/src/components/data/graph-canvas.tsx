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

import type { GraphNode } from "@/lib/api/types";
import { edgeColor, type GraphRelationEdge } from "@/components/data/evidence-relations";
import { cn } from "@/lib/utils";

type ForceGraphRef = {
  zoomToFit?: (durationMs?: number, padding?: number) => void;
  d3ReheatSimulation?: () => void;
};

type ForceGraphNode = GraphNode & { x?: number; y?: number };
type ForceGraphEdge = GraphRelationEdge & {
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
  edges: GraphRelationEdge[];
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
        const sourceId = resolveNodeId(edge.source as string | ForceGraphNode);
        const targetId = resolveNodeId(edge.target as string | ForceGraphNode);
        if (sourceId === selectedNodeId) next.add(targetId);
        if (targetId === selectedNodeId) next.add(sourceId);
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
              const sourceId = resolveNodeId(typed.source);
              const targetId = resolveNodeId(typed.target);
              const isConnected =
                Boolean(selectedNodeId) &&
                (sourceId === selectedNodeId || targetId === selectedNodeId);

              if (!selectedNodeId) return edgeColor(typed, 0.45);
              return edgeColor(typed, isConnected ? 0.85 : 0.18, "rgba(148, 163, 184, 0.2)");
            }}
            linkWidth={(link) => {
              const typed = link as ForceGraphEdge;
              const sourceId = resolveNodeId(typed.source);
              const targetId = resolveNodeId(typed.target);
              const isConnected =
                Boolean(selectedNodeId) &&
                (sourceId === selectedNodeId || targetId === selectedNodeId);
              const confidence = "confidence" in typed ? typed.confidence : 0;
              const baseWidth = 1 + confidence;
              if (!selectedNodeId) return baseWidth;
              return isConnected ? baseWidth + 0.8 : 0.8;
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
