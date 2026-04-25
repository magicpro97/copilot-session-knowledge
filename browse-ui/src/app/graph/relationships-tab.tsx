"use client";

import { Search, RotateCcw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { GraphCanvas, type GraphCanvasHandle } from "@/components/data/graph-canvas";
import { NodeDetailCard } from "@/components/data/node-detail-card";
import { FilterSidebar } from "@/components/layout/filter-sidebar";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useGraph } from "@/lib/api/hooks";
import type { GraphEdge, GraphNode } from "@/lib/api/types";

const GRAPH_NODE_LIMIT = 500;

type RelationshipsTabProps = {
  active: boolean;
};

type OptionWithCount = {
  value: string;
  count: number;
};

function toggleValue(values: string[], value: string): string[] {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}

function optionCounts(nodes: GraphNode[], field: "wing" | "category"): OptionWithCount[] {
  const counts = new Map<string, number>();
  for (const node of nodes) {
    const value = (field === "wing" ? node.wing : node.category)?.trim();
    if (!value) continue;
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([value, count]) => ({ value, count }))
    .sort((a, b) => a.value.localeCompare(b.value));
}

function edgeNodeId(value: string | { id: string }): string {
  return typeof value === "string" ? value : value.id;
}

export function RelationshipsTab({ active }: RelationshipsTabProps) {
  const router = useRouter();
  const graphRef = useRef<GraphCanvasHandle | null>(null);

  const [labelQuery, setLabelQuery] = useState("");
  const [selectedWings, setSelectedWings] = useState<string[]>([]);
  const [selectedKinds, setSelectedKinds] = useState<string[]>([]);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  const graphQuery = useGraph({
    wing: selectedWings,
    kind: selectedKinds,
    limit: GRAPH_NODE_LIMIT,
  });
  const baseGraphQuery = useGraph({ limit: GRAPH_NODE_LIMIT });

  const graph = graphQuery.data;
  const allNodes = useMemo(() => graph?.nodes ?? [], [graph?.nodes]);
  const allEdges = useMemo(() => graph?.edges ?? [], [graph?.edges]);
  const optionSourceNodes = baseGraphQuery.data?.nodes ?? allNodes;

  const wingOptions = useMemo(() => optionCounts(optionSourceNodes, "wing"), [optionSourceNodes]);
  const kindOptions = useMemo(() => optionCounts(optionSourceNodes, "category"), [optionSourceNodes]);

  const normalizedLabelQuery = labelQuery.trim().toLowerCase();

  const visibleNodes = useMemo(() => {
    if (!normalizedLabelQuery) return allNodes;
    return allNodes.filter((node) => node.label.toLowerCase().includes(normalizedLabelQuery));
  }, [allNodes, normalizedLabelQuery]);

  const visibleEdges = useMemo<GraphEdge[]>(() => {
    const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));
    return allEdges.filter((edge) => {
      const sourceId = edgeNodeId(edge.source);
      const targetId = edgeNodeId(edge.target);
      return visibleNodeIds.has(sourceId) && visibleNodeIds.has(targetId);
    });
  }, [allEdges, visibleNodes]);

  useEffect(() => {
    if (!selectedNode) return;
    if (!visibleNodes.some((node) => node.id === selectedNode.id)) {
      setSelectedNode(null);
    }
  }, [selectedNode, visibleNodes]);

  const clearFilters = useCallback(() => {
    setSelectedWings([]);
    setSelectedKinds([]);
    setLabelQuery("");
    setSelectedNode(null);
  }, []);

  useEffect(() => {
    if (!active) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      const target = event.target as HTMLElement | null;
      const isTypingTarget =
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.isContentEditable;
      if (isTypingTarget) return;

      if (event.key.toLowerCase() === "f") {
        event.preventDefault();
        graphRef.current?.fitToScreen();
      } else if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        clearFilters();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active, clearFilters]);

  const openNodeInSearch = (node: GraphNode) => {
    const params = new URLSearchParams();
    params.set("q", node.label);
    params.set("src", "knowledge");
    if (node.category) params.set("kind", node.category);
    router.push(`/search?${params.toString()}`);
  };

  const hasServerFilters = selectedWings.length > 0 || selectedKinds.length > 0;
  const isClientLabelFiltered = normalizedLabelQuery.length > 0;

  const filterSections = [
    {
      id: "label",
      title: "Label search",
      defaultOpen: true,
      content: (
        <div className="space-y-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              value={labelQuery}
              onChange={(event) => setLabelQuery(event.target.value)}
              placeholder="Filter labels..."
              className="pl-7"
            />
          </div>
          <p className="text-xs text-muted-foreground">
            Label filtering is client-side over currently loaded nodes.
          </p>
        </div>
      ),
    },
    {
      id: "wing",
      title: "Wing",
      defaultOpen: true,
      content:
        wingOptions.length > 0 ? (
          <div className="space-y-2">
            {wingOptions.map((option) => (
              <label key={option.value} className="flex cursor-pointer items-center gap-2 text-sm">
                <Checkbox
                  checked={selectedWings.includes(option.value)}
                  onCheckedChange={() =>
                    setSelectedWings((prev) => toggleValue(prev, option.value))
                  }
                />
                <span className="flex-1 truncate">{option.value}</span>
                <span className="text-xs text-muted-foreground">{option.count}</span>
              </label>
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">No wings in current graph data.</p>
        ),
    },
    {
      id: "kind",
      title: "Category",
      defaultOpen: true,
      content:
        kindOptions.length > 0 ? (
          <div className="space-y-2">
            {kindOptions.map((option) => (
              <label key={option.value} className="flex cursor-pointer items-center gap-2 text-sm">
                <Checkbox
                  checked={selectedKinds.includes(option.value)}
                  onCheckedChange={() =>
                    setSelectedKinds((prev) => toggleValue(prev, option.value))
                  }
                />
                <span className="flex-1 truncate">{option.value}</span>
                <span className="text-xs text-muted-foreground">{option.count}</span>
              </label>
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">No categories in current graph data.</p>
        ),
    },
    {
      id: "actions",
      title: "Actions",
      defaultOpen: true,
      content: (
        <div className="space-y-2">
          <Button type="button" variant="outline" size="sm" className="w-full" onClick={clearFilters}>
            <RotateCcw className="size-3.5" />
            Reset (R)
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => graphRef.current?.fitToScreen()}
          >
            Fit graph (F)
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-3">
      {graph?.truncated ? (
        <Banner
          tone="warning"
          title="Graph results are truncated by the backend limit."
          description={
            hasServerFilters
              ? isClientLabelFiltered
                ? `This view is from /api/graph with server-side wing/category filters and limit=${GRAPH_NODE_LIMIT}. Label filtering is client-side on this capped result set.`
                : `This view is from /api/graph with server-side wing/category filters and limit=${GRAPH_NODE_LIMIT}.`
              : isClientLabelFiltered
                ? `This view is from /api/graph with limit=${GRAPH_NODE_LIMIT}. Apply wing/category filters to fetch a narrower subset. Label filtering is client-side on this capped result set.`
                : `This view is from /api/graph with limit=${GRAPH_NODE_LIMIT}. Apply wing/category filters to fetch a narrower subset.`
          }
        />
      ) : null}

      {graphQuery.error ? (
        <Banner
          tone="danger"
          title="Failed to load graph data"
          description={
            graphQuery.error instanceof Error
              ? graphQuery.error.message
              : "Unknown graph error."
          }
        />
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[18rem_1fr_20rem]">
        <FilterSidebar
          title="Filters"
          className="lg:sticky lg:top-4 lg:self-start"
          sections={filterSections}
        />

        <div className="space-y-3">
          {graphQuery.isLoading ? (
            <div className="flex h-[65vh] min-h-[22rem] items-center justify-center rounded-xl border bg-card text-sm text-muted-foreground">
              Loading graph…
            </div>
          ) : graphQuery.isSuccess && allNodes.length === 0 ? (
            <EmptyState
              title="No knowledge relations found"
              description="Add knowledge entries and relations, then refresh graph data."
              actionLabel="Reload"
              onAction={() => graphQuery.refetch()}
            />
          ) : graphQuery.isSuccess && visibleNodes.length === 0 ? (
            <EmptyState
              title="No nodes match current label search"
              description="Clear label search or reset filters to see more nodes."
              actionLabel="Clear label search"
              onAction={() => setLabelQuery("")}
            />
          ) : (
            <GraphCanvas
              ref={graphRef}
              nodes={visibleNodes}
              edges={visibleEdges}
              selectedNodeId={selectedNode?.id ?? null}
              onNodeSelect={setSelectedNode}
            />
          )}

          <p className="text-xs text-muted-foreground">
            Showing {visibleNodes.length} nodes and {visibleEdges.length} edges.
            {hasServerFilters ? " Wing/category filters are server-side." : " Wing/category filters are not applied."}
          </p>
        </div>

        <div className="space-y-3 lg:sticky lg:top-4 lg:self-start">
          {selectedNode ? (
            <NodeDetailCard node={selectedNode} onOpenSearch={openNodeInSearch} />
          ) : (
            <EmptyState
              title="Select a node"
              description="Click a graph node to inspect details and jump to search."
              className="min-h-[16rem]"
            />
          )}
        </div>
      </div>
    </div>
  );
}
