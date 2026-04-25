"use client";

import { useEffect, useMemo, useState } from "react";

import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { CATEGORY_COLORS, ScatterCanvas } from "@/components/data/scatter-canvas";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useEmbeddings } from "@/lib/api/hooks";
import type { EmbeddingPoint } from "@/lib/api/types";

type ClustersTabProps = {
  active: boolean;
};

type CategoryOption = {
  value: string;
  count: number;
};

const ALL_CATEGORIES = "all";

function normalizeCategory(category: string): string {
  return category.trim() || "unknown";
}

function statusCodeFromError(error: unknown): number | null {
  if (!(error instanceof Error)) return null;
  const match = error.message.match(/^API\s+(\d+):/);
  if (!match) return null;
  const parsed = Number(match[1]);
  return Number.isFinite(parsed) ? parsed : null;
}

export function ClustersTab({ active }: ClustersTabProps) {
  const embeddingsQuery = useEmbeddings();

  const [selectedCategory, setSelectedCategory] = useState<string>(ALL_CATEGORIES);
  const [selectedPointId, setSelectedPointId] = useState<number | null>(null);

  const points = useMemo(
    () => embeddingsQuery.data?.points ?? [],
    [embeddingsQuery.data?.points]
  );

  const categoryOptions = useMemo<CategoryOption[]>(() => {
    const counts = new Map<string, number>();
    for (const point of points) {
      const category = normalizeCategory(point.category);
      counts.set(category, (counts.get(category) ?? 0) + 1);
    }
    return [...counts.entries()]
      .map(([value, count]) => ({ value, count }))
      .sort((a, b) => a.value.localeCompare(b.value));
  }, [points]);

  const visiblePoints = useMemo<EmbeddingPoint[]>(() => {
    if (selectedCategory === ALL_CATEGORIES) return points;
    return points.filter((point) => normalizeCategory(point.category) === selectedCategory);
  }, [points, selectedCategory]);

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

      if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        setSelectedCategory(ALL_CATEGORIES);
        setSelectedPointId(null);
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active]);

  useEffect(() => {
    if (selectedPointId === null) return;
    if (!visiblePoints.some((point) => point.id === selectedPointId)) {
      setSelectedPointId(null);
    }
  }, [selectedPointId, visiblePoints]);

  const selectedPoint = visiblePoints.find((point) => point.id === selectedPointId) ?? null;
  const statusCode = statusCodeFromError(embeddingsQuery.error);
  const isUnavailable = statusCode === 503;
  const hasProjectionData = Boolean(embeddingsQuery.data);
  const totalCount = embeddingsQuery.data?.count ?? points.length;
  const loadedCount = points.length;

  return (
    <div className="space-y-3">
      {isUnavailable ? (
        <Banner
          tone="warning"
          title="Embeddings projection unavailable"
          description="The embeddings projection endpoint is currently unavailable. Try again later."
        />
      ) : null}

      {embeddingsQuery.error && !isUnavailable ? (
        <Banner
          tone="danger"
          title="Failed to load embeddings"
          description={
            embeddingsQuery.error instanceof Error
              ? embeddingsQuery.error.message
              : "Unknown embeddings error."
          }
        />
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div className="space-y-3">
          <Card>
            <CardContent className="flex flex-wrap items-center gap-3 pt-6">
              <label className="text-sm font-medium" htmlFor="clusters-category-filter">
                Category
              </label>
              <Select
                value={selectedCategory}
                onValueChange={(value) => {
                  setSelectedCategory(value ?? ALL_CATEGORIES);
                  setSelectedPointId(null);
                }}
              >
                <SelectTrigger id="clusters-category-filter" className="w-56">
                  <SelectValue placeholder="All categories" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_CATEGORIES}>All categories</SelectItem>
                  {categoryOptions.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.value} ({option.count})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-sm text-muted-foreground">
                {hasProjectionData
                  ? `Showing ${visiblePoints.length} / ${loadedCount} loaded points${
                      totalCount > loadedCount ? ` (from ${totalCount} embeddings)` : ""
                    }.${embeddingsQuery.data?.cached ? " Cached projection." : " Fresh projection."}`
                  : embeddingsQuery.isLoading
                    ? "Loading projection..."
                    : "No projection loaded."}
              </p>
            </CardContent>
          </Card>

          {embeddingsQuery.isLoading ? (
            <div className="flex h-[65vh] min-h-[22rem] items-center justify-center rounded-xl border bg-card text-sm text-muted-foreground">
              Loading clusters…
            </div>
          ) : embeddingsQuery.isError ? (
            <EmptyState
              title="Projection unavailable"
              description="Could not load the embeddings projection. Try again later."
              actionLabel="Retry"
              onAction={() => embeddingsQuery.refetch()}
            />
          ) : embeddingsQuery.isSuccess && loadedCount === 0 ? (
            <EmptyState
              title="No embedding points available"
              description="Generate embeddings data, then reload this tab."
              actionLabel="Reload"
              onAction={() => embeddingsQuery.refetch()}
            />
          ) : embeddingsQuery.isSuccess && visiblePoints.length === 0 ? (
            <EmptyState
              title="No points match this category"
              description="Choose a different category or reset filters."
              actionLabel="Show all categories"
              onAction={() => setSelectedCategory(ALL_CATEGORIES)}
            />
          ) : (
            <ScatterCanvas
              points={visiblePoints}
              selectedPointId={selectedPointId}
              onPointSelect={(point) => setSelectedPointId(point?.id ?? null)}
            />
          )}
        </div>

        <div className="space-y-3 lg:sticky lg:top-4 lg:self-start">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Legend</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {categoryOptions.length > 0 ? (
                categoryOptions.map((option) => (
                  <div key={option.value} className="flex items-center gap-2 text-sm">
                    <span
                      className="inline-block size-2.5 rounded-full"
                      style={{ backgroundColor: CATEGORY_COLORS[option.value] ?? "#9ca3af" }}
                    />
                    <span className="flex-1 truncate">{option.value}</span>
                    <span className="text-xs text-muted-foreground">{option.count}</span>
                  </div>
                ))
              ) : (
                <p className="text-sm text-muted-foreground">No categories to show.</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Selected point</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-sm">
              {selectedPoint ? (
                <>
                  <p className="font-medium">{selectedPoint.title}</p>
                  <p className="text-muted-foreground">Category: {selectedPoint.category}</p>
                  <p className="text-muted-foreground">ID: {selectedPoint.id}</p>
                </>
              ) : (
                <p className="text-muted-foreground">Click a point in the scatter plot.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
