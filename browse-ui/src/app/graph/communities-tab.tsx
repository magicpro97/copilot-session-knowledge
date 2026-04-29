"use client";

import { useMemo } from "react";

import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { relationTypeColor, relationTypeLabel } from "@/components/data/evidence-relations";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useCommunities } from "@/lib/api/hooks";
import type { CommunitySummary } from "@/lib/api/types";

type CommunitiesTabProps = {
  active: boolean;
  onDrillIn?: (target: "evidence" | "similarity") => void;
};

function formatTopCounts(items: Array<{ name: string; count: number }>): string {
  if (items.length === 0) return "None";
  return items.map((item) => `${item.name} (${item.count})`).join(", ");
}

function byDeterministicCommunityOrder(a: CommunitySummary, b: CommunitySummary): number {
  if (b.entry_count !== a.entry_count) return b.entry_count - a.entry_count;
  return a.id.localeCompare(b.id);
}

export function CommunitiesTab({ active, onDrillIn }: CommunitiesTabProps) {
  const communitiesQuery = useCommunities(active);

  const usefulCommunities = useMemo(
    () =>
      (communitiesQuery.data?.communities ?? [])
        .filter((community) => community.entry_count > 1)
        .sort(byDeterministicCommunityOrder),
    [communitiesQuery.data?.communities]
  );

  return (
    <div className="space-y-3">
      {communitiesQuery.error ? (
        <Banner
          tone="danger"
          title="Failed to load communities"
          description={
            communitiesQuery.error instanceof Error
              ? communitiesQuery.error.message
              : "Unknown communities error."
          }
        />
      ) : null}

      {communitiesQuery.isLoading ? (
        <Card>
          <CardContent className="text-muted-foreground py-10 text-center text-sm">
            Loading communities…
          </CardContent>
        </Card>
      ) : null}

      {!communitiesQuery.isLoading &&
      !communitiesQuery.isError &&
      usefulCommunities.length === 0 ? (
        <EmptyState
          title="No useful communities yet"
          description="Only singleton or disconnected groups were found, so this tab hides noise."
        />
      ) : null}

      {usefulCommunities.length > 0 ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {usefulCommunities.map((community) => (
            <Card key={community.id}>
              <CardHeader>
                <CardTitle className="text-sm">
                  Community {community.id} · {community.entry_count} entries
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <p>
                  <span className="font-medium">Top categories:</span>{" "}
                  {formatTopCounts(community.top_categories)}
                </p>
                <p>
                  <span className="font-medium">Top wings:</span>{" "}
                  {(community.wings ?? []).length > 0 ? community.wings?.join(", ") : "None"}
                </p>
                <p>
                  <span className="font-medium">Top relation types:</span>{" "}
                  {(community.top_relation_types ?? []).length > 0 ? (
                    <span className="inline-flex flex-wrap gap-x-3 gap-y-1">
                      {(community.top_relation_types ?? []).map((item) => (
                        <span
                          key={`${community.id}:${item.type}`}
                          className="inline-flex items-center gap-1"
                        >
                          <span
                            className="inline-block size-2 rounded-full"
                            style={{ backgroundColor: relationTypeColor(item.type) }}
                            aria-hidden
                          />
                          <span>
                            {relationTypeLabel(item.type)} ({item.count})
                          </span>
                        </span>
                      ))}
                    </span>
                  ) : (
                    "None"
                  )}
                </p>
                <div className="space-y-1">
                  <p className="font-medium">Representative entries</p>
                  <ul className="list-disc space-y-1 pl-5">
                    {community.representative_entries.map((entry) => (
                      <li key={`${community.id}-${entry.id}`}>
                        {entry.title} ({entry.category}) #{entry.id}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="flex flex-wrap gap-2 pt-1">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => onDrillIn?.("evidence")}
                  >
                    Open Evidence tab
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => onDrillIn?.("similarity")}
                  >
                    Open Similarity tab
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : null}
    </div>
  );
}
