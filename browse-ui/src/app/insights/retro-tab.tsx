"use client";

import { Badge } from "@/components/ui/badge";
import { useRetro } from "@/lib/api/hooks";
import { formatNumber } from "@/lib/formatters";
import { RetroBody } from "./retro-section";

function scoreBadgeVariant(score: number): "outline" | "secondary" | "destructive" {
  if (score >= 80) return "outline";
  if (score >= 50) return "secondary";
  return "destructive";
}

function confidenceBadgeVariant(
  confidence: "low" | "medium" | "high"
): "outline" | "secondary" | "destructive" {
  if (confidence === "high") return "outline";
  if (confidence === "medium") return "secondary";
  return "destructive";
}

export function RetroTab() {
  const retro = useRetro("repo");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-medium">Retrospective</h2>
        {retro.isSuccess && retro.data ? (
          <Badge variant={scoreBadgeVariant(retro.data.retro_score)}>
            {retro.data.grade_emoji} {retro.data.grade} ({formatNumber(retro.data.retro_score)})
          </Badge>
        ) : null}
        {retro.isSuccess && retro.data?.score_confidence ? (
          <Badge variant={confidenceBadgeVariant(retro.data.score_confidence)}>
            confidence: {retro.data.score_confidence}
          </Badge>
        ) : null}
      </div>

      <RetroBody retro={retro} />
    </div>
  );
}
