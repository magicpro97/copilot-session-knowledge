"use client";

import { Badge } from "@/components/ui/badge";
import { useKnowledgeInsights } from "@/lib/api/hooks";
import { formatNumber } from "@/lib/formatters";
import { KnowledgeInsightsBody } from "./knowledge-insights-section";

export function KnowledgeTab() {
  const insights = useKnowledgeInsights();

  const alertCount = insights.data?.quality_alerts.length ?? 0;
  const criticalCount =
    insights.data?.quality_alerts.filter((a) => a.severity === "critical").length ?? 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-medium">Knowledge Insights</h2>
        {insights.isSuccess && insights.data ? (
          <Badge variant="outline">
            health score {formatNumber(insights.data.overview.health_score)}
          </Badge>
        ) : null}
        {criticalCount > 0 ? (
          <Badge variant="destructive">{criticalCount} critical</Badge>
        ) : alertCount > 0 ? (
          <Badge variant="secondary">{alertCount} alerts</Badge>
        ) : null}
      </div>

      <KnowledgeInsightsBody insights={insights} />
    </div>
  );
}
