"use client";

import { ExternalLink } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { CATEGORY_COLORS, ScatterCanvas } from "@/components/data/scatter-canvas";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useEmbeddings, useSimilarity } from "@/lib/api/hooks";
import type { EmbeddingPoint } from "@/lib/api/types";

type ClustersTabProps = {
  active: boolean;
};

type CategoryOption = {
  value: string;
  count: number;
};

const ALL_CATEGORIES = "all";
const DEFAULT_SIMILARITY_K = 8;
const COLD_START_LIMIT = 8;

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

function extractMetaBoolean(meta: Record<string, unknown> | undefined, key: string): boolean {
  return meta?.[key] === true;
}

function extractMetaNumber(meta: Record<string, unknown> | undefined, key: string): number | null {
  const value = meta?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function extractMetaEntryIds(meta: Record<string, unknown> | undefined, key: string): number[] {
  const value = meta?.[key];
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => Number(item))
    .filter((item) => Number.isFinite(item))
    .map((item) => Math.trunc(item));
}

function formatScore(score: number): string {
  if (!Number.isFinite(score)) return "0.000";
  return score.toFixed(3);
}

export function ClustersTab({ active }: ClustersTabProps) {
  const router = useRouter();
  const embeddingsQuery = useEmbeddings();

  const [selectedCategory, setSelectedCategory] = useState<string>(ALL_CATEGORIES);
  const [selectedPointId, setSelectedPointId] = useState<number | null>(null);
  const [selectionQuery, setSelectionQuery] = useState("");

  const points = useMemo(() => embeddingsQuery.data?.points ?? [], [embeddingsQuery.data?.points]);

  const similarityEnabled = selectedPointId !== null;
  const similarityQuery = useSimilarity(
    similarityEnabled
      ? {
          entry_id: [selectedPointId],
          k: DEFAULT_SIMILARITY_K,
        }
      : {},
    similarityEnabled
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

  const normalizedSelectionQuery = selectionQuery.trim().toLowerCase();
  const coldStartMatches = useMemo<EmbeddingPoint[]>(() => {
    const sorted = [...visiblePoints].sort((a, b) => a.title.localeCompare(b.title));
    if (!normalizedSelectionQuery) {
      return sorted.slice(0, COLD_START_LIMIT);
    }
    return sorted
      .filter((point) => {
        const searchBlob = `${point.title} ${point.category} ${point.id}`.toLowerCase();
        return searchBlob.includes(normalizedSelectionQuery);
      })
      .slice(0, COLD_START_LIMIT);
  }, [normalizedSelectionQuery, visiblePoints]);

  useEffect(() => {
    if (!active) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      const isTypingTarget =
        target?.tagName === "INPUT" || target?.tagName === "TEXTAREA" || target?.isContentEditable;
      if (isTypingTarget) return;

      if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        setSelectedCategory(ALL_CATEGORIES);
        setSelectedPointId(null);
        setSelectionQuery("");
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

  const selectedPoint = points.find((point) => point.id === selectedPointId) ?? null;
  const selectedSimilarity = useMemo(
    () =>
      similarityQuery.data?.results.find((result) => result.entry_id === selectedPointId) ?? null,
    [selectedPointId, similarityQuery.data?.results]
  );
  const neighbors = selectedSimilarity?.neighbors ?? [];
  const similarityMeta = (similarityQuery.data?.meta ?? undefined) as
    | Record<string, unknown>
    | undefined;
  const degraded = extractMetaBoolean(similarityMeta, "degraded");
  const skippedEntryIds = extractMetaEntryIds(similarityMeta, "skipped_entry_ids");
  const selectedSkipped =
    selectedPointId !== null && skippedEntryIds.some((entryId) => entryId === selectedPointId);
  const similarityCached = extractMetaBoolean(similarityMeta, "cached");
  const embeddingCount = extractMetaNumber(similarityMeta, "embedding_count");

  const statusCode = statusCodeFromError(embeddingsQuery.error);
  const isUnavailable = statusCode === 503;
  const hasProjectionData = Boolean(embeddingsQuery.data);
  const totalCount = embeddingsQuery.data?.count ?? points.length;
  const loadedCount = points.length;

  const openInSearch = (title: string, category?: string) => {
    const params = new URLSearchParams();
    params.set("q", title);
    params.set("src", "knowledge");
    if (category) params.set("kind", category);
    router.push(`/search?${params.toString()}`);
  };

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

      {similarityQuery.error ? (
        <Banner
          tone="danger"
          title="Failed to load similarity neighbors"
          description={
            similarityQuery.error instanceof Error
              ? similarityQuery.error.message
              : "Unknown similarity error."
          }
        />
      ) : null}

      {degraded ? (
        <Banner
          tone="warning"
          title="Similarity results are partially degraded"
          description={
            skippedEntryIds.length > 0
              ? `The backend returned partial neighbors. Skipped entry IDs: ${skippedEntryIds.join(", ")}.`
              : "The backend marked this response as degraded. Neighbor coverage may be incomplete."
          }
        />
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div className="space-y-3">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Select entry</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <label className="text-sm font-medium" htmlFor="similarity-category-filter">
                  Category
                </label>
                <Select
                  value={selectedCategory}
                  onValueChange={(value) => {
                    setSelectedCategory(value ?? ALL_CATEGORIES);
                    setSelectedPointId(null);
                  }}
                >
                  <SelectTrigger id="similarity-category-filter" className="w-56">
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
                <p className="text-muted-foreground text-sm">
                  {hasProjectionData
                    ? `Loaded ${loadedCount}${
                        totalCount > loadedCount ? ` of ${totalCount}` : ""
                      } projection points.${embeddingsQuery.data?.cached ? " Cached." : " Fresh."}`
                    : embeddingsQuery.isLoading
                      ? "Loading projection..."
                      : "No projection loaded."}
                </p>
              </div>

              <div className="space-y-2">
                <label htmlFor="similarity-entry-search" className="text-sm font-medium">
                  Entry search
                </label>
                <Input
                  id="similarity-entry-search"
                  type="search"
                  value={selectionQuery}
                  onChange={(event) => setSelectionQuery(event.target.value)}
                  placeholder="Search by title, category, or ID"
                />
                <p className="text-muted-foreground text-xs">
                  Choose an entry first to load nearest neighbors. Map click is optional.
                </p>
              </div>

              <div className="space-y-2">
                {coldStartMatches.length > 0 ? (
                  <div className="grid gap-2 sm:grid-cols-2">
                    {coldStartMatches.map((point) => (
                      <Button
                        key={point.id}
                        type="button"
                        variant={point.id === selectedPointId ? "default" : "outline"}
                        className="h-auto justify-start py-2 text-left"
                        onClick={() => setSelectedPointId(point.id)}
                      >
                        <span className="min-w-0">
                          <span className="block truncate">{point.title}</span>
                          <span className="text-muted-foreground block text-xs">
                            {point.category} · #{point.id}
                          </span>
                        </span>
                      </Button>
                    ))}
                  </div>
                ) : (
                  <p className="text-muted-foreground text-sm">
                    No entries match your current filters.
                  </p>
                )}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Nearest neighbors</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {!selectedPoint ? (
                <EmptyState
                  title="Select an entry to explore neighbors"
                  description="Use entry search above or click a point on the orientation map."
                />
              ) : similarityQuery.isLoading ? (
                <div className="bg-muted/20 text-muted-foreground flex min-h-48 items-center justify-center rounded-lg border text-sm">
                  Loading neighbors…
                </div>
              ) : similarityQuery.isError ? (
                <EmptyState
                  title="Neighbors unavailable"
                  description="Could not load neighbors for the selected entry."
                  actionLabel="Retry"
                  onAction={() => similarityQuery.refetch()}
                />
              ) : selectedSkipped ? (
                <EmptyState
                  title="Selected entry was skipped"
                  description="The backend returned a partial similarity response and skipped this entry for now."
                />
              ) : neighbors.length === 0 ? (
                <EmptyState
                  title="No similar entries found"
                  description="This entry has no close semantic neighbors in the current embedding space."
                />
              ) : (
                <div className="space-y-2">
                  <p className="text-muted-foreground text-sm">
                    Source:{" "}
                    <span className="text-foreground font-medium">{selectedPoint.title}</span> (#
                    {selectedPoint.id})
                    {embeddingCount !== null ? ` · ${embeddingCount} embeddings` : ""}
                    {similarityCached ? " · cached" : ""}
                  </p>
                  <ul className="space-y-2">
                    {neighbors.map((neighbor) => (
                      <li
                        key={`${selectedPoint.id}:${neighbor.id}`}
                        className="flex flex-wrap items-center gap-2 rounded-md border p-2"
                      >
                        <div className="min-w-0 flex-1">
                          <Button
                            type="button"
                            variant="link"
                            className="h-auto p-0 text-left"
                            onClick={() => setSelectedPointId(neighbor.id)}
                          >
                            {neighbor.title}
                          </Button>
                          <p className="text-muted-foreground text-xs">
                            {neighbor.category} · #{neighbor.id}
                          </p>
                        </div>
                        <p className="text-muted-foreground font-mono text-xs">
                          score {formatScore(neighbor.score)}
                        </p>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => openInSearch(neighbor.title, neighbor.category)}
                        >
                          <ExternalLink className="size-3.5" />
                          Search
                        </Button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Orientation map (secondary)</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-muted-foreground text-sm">
                Projection map from /api/embeddings/points for orientation only. Proximity on this
                map is not proof of relatedness.
              </p>
              {embeddingsQuery.isLoading ? (
                <div className="bg-card text-muted-foreground flex h-[50vh] min-h-[18rem] items-center justify-center rounded-xl border text-sm">
                  Loading projection…
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
                  className="h-[50vh] min-h-[18rem]"
                  points={visiblePoints}
                  selectedPointId={selectedPointId}
                  onPointSelect={(point) => setSelectedPointId(point?.id ?? null)}
                />
              )}
            </CardContent>
          </Card>
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
                    <span className="text-muted-foreground text-xs">{option.count}</span>
                  </div>
                ))
              ) : (
                <p className="text-muted-foreground text-sm">No categories to show.</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Selected entry</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {selectedPoint ? (
                <>
                  <p className="font-medium">{selectedPoint.title}</p>
                  <p className="text-muted-foreground">Category: {selectedPoint.category}</p>
                  <p className="text-muted-foreground">ID: {selectedPoint.id}</p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => openInSearch(selectedPoint.title, selectedPoint.category)}
                  >
                    <ExternalLink className="size-3.5" />
                    Open in Search
                  </Button>
                  <p className="text-muted-foreground text-xs">
                    {neighbors.length > 0
                      ? `${neighbors.length} neighbors listed in primary view.`
                      : "No neighbors listed yet for this entry."}
                  </p>
                </>
              ) : (
                <p className="text-muted-foreground">Select an entry using search or map.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
