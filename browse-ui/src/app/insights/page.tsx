"use client";

import { OverviewTab } from "@/app/insights/overview-tab";
import { useInsightsTab } from "@/app/insights/insights-tab-context";

export default function InsightsPage() {
  const { setActiveTab } = useInsightsTab();
  return <OverviewTab onNavigate={setActiveTab} />;
}
