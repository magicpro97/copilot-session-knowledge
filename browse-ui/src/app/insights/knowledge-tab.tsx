"use client";

import { Badge } from "@/components/ui/badge";
import { useKnowledgeInsights } from "@/lib/api/hooks";
import { formatNumber } from "@/lib/formatters";
import { HostedIdleGuidance } from "./overview-tab";
import { useInsightsTab } from "./insights-tab-context";
import { KnowledgeInsightsBody } from "./knowledge-insights-section";

function KnowledgeTabContent() {
  const { host, diagnosticsEnabled } = useInsightsTab();
  const insights = useKnowledgeInsights(host, diagnosticsEnabled);

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

export function KnowledgeTab() {
  const { diagnosticsEnabled } = useInsightsTab();
  if (!diagnosticsEnabled) {
    return <HostedIdleGuidance />;
  }
  return <KnowledgeTabContent />;
}
