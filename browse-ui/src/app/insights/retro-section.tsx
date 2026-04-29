"use client";

import { Banner } from "@/components/data/banner";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useRetro } from "@/lib/api/hooks";
import { formatNumber } from "@/lib/formatters";

const SECTION_LABELS: Record<string, string> = {
  knowledge: "Knowledge",
  skills: "Skills",
  hooks: "Hooks",
  git: "Git",
};

function scoreBadgeVariant(score: number): "outline" | "secondary" | "destructive" {
  if (score >= 80) return "outline";
  if (score >= 50) return "secondary";
  return "destructive";
}

export function RetroSection() {
  const retro = useRetro("repo");

  if (retro.isSuccess && !retro.data) {
    return null;
  }

  return (
    <details className="bg-card rounded-xl border p-4">
      <summary className="cursor-pointer list-none text-sm font-medium">
        <span className="inline-flex items-center gap-2">
          Retrospective
          {retro.isSuccess && retro.data ? (
            <Badge variant={scoreBadgeVariant(retro.data.retro_score)}>
              {retro.data.grade_emoji} {retro.data.grade} ({formatNumber(retro.data.retro_score)})
            </Badge>
          ) : null}
        </span>
      </summary>

      <div className="mt-4 space-y-4">
        {retro.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-6 w-36" />
            <Skeleton className="h-6 w-40" />
          </div>
        ) : retro.isError ? (
          <Banner
            tone="warning"
            title="Retrospective unavailable"
            description={
              retro.error instanceof Error
                ? retro.error.message
                : "Could not load /api/retro/summary."
            }
          />
        ) : retro.data ? (
          <>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {retro.data.available_sections.map((section) => {
                const score = retro.data!.subscores[section];
                return (
                  <div key={section} className="rounded-lg border px-3 py-2">
                    <p className="text-muted-foreground text-xs">
                      {SECTION_LABELS[section] ?? section}
                    </p>
                    <p className="mt-1 text-lg font-semibold">
                      {score != null ? formatNumber(score) : "–"}
                    </p>
                  </div>
                );
              })}
            </div>

            <p className="text-muted-foreground text-xs">
              mode: {retro.data.mode} · generated {retro.data.generated_at}
            </p>
          </>
        ) : null}
      </div>
    </details>
  );
}
