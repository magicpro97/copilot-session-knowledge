"use client";

import { Activity } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";
import { useHealth } from "@/lib/api/hooks";
import { formatNumber } from "@/lib/formatters";
import { KnowledgeTab } from "./knowledge-tab";
import { LiveTab } from "./live-tab";
import { RetroTab } from "./retro-tab";
import { SearchQualityTab } from "./search-quality-tab";
import { WorkflowTab } from "./workflow-tab";
import type { InsightsTabKey } from "./overview-tab";
import { InsightsTabContext } from "./insights-tab-context";

type InsightsLayoutProps = {
  children: ReactNode;
};

function getHealthTone(status: string | undefined): string {
  if (!status) return "text-muted-foreground";
  const normalized = status.toLowerCase();
  if (normalized.includes("ok") || normalized.includes("healthy")) {
    return "text-[hsl(142_72%_38%)]";
  }
  return "text-[hsl(12_76%_46%)]";
}

function hashToInsightsTab(hash: string): InsightsTabKey | null {
  const cleaned = hash.replace(/^#/, "").toLowerCase();
  if (cleaned === "overview") return "overview";
  if (cleaned === "knowledge") return "knowledge";
  if (cleaned === "retro") return "retro";
  if (cleaned === "search-quality") return "search-quality";
  if (cleaned === "live") return "live";
  if (cleaned === "workflow") return "workflow";
  return null;
}

export default function InsightsLayout({ children }: InsightsLayoutProps) {
  const health = useHealth();
  const [activeTab, setActiveTab] = useState<InsightsTabKey>(() => {
    if (typeof window === "undefined") return "overview";
    return hashToInsightsTab(window.location.hash) ?? "overview";
  });

  useEffect(() => {
    if (typeof window === "undefined") return;

    const onHashChange = () => {
      const next = hashToInsightsTab(window.location.hash);
      if (next) setActiveTab(next);
    };

    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.history.replaceState(null, "", `#${activeTab}`);
  }, [activeTab]);

  useKeyboardShortcuts([
    {
      key: "1",
      preventDefault: true,
      handler: () => setActiveTab("overview"),
    },
    {
      key: "2",
      preventDefault: true,
      handler: () => setActiveTab("knowledge"),
    },
    {
      key: "3",
      preventDefault: true,
      handler: () => setActiveTab("retro"),
    },
    {
      key: "4",
      preventDefault: true,
      handler: () => setActiveTab("search-quality"),
    },
    {
      key: "5",
      preventDefault: true,
      handler: () => setActiveTab("live"),
    },
    {
      key: "6",
      preventDefault: true,
      handler: () => setActiveTab("workflow"),
    },
  ]);

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <h1 className="text-2xl font-semibold tracking-tight">Insights</h1>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="gap-1.5">
            <Activity className={`size-3.5 ${getHealthTone(health.data?.status)}`} />
            {health.isLoading ? (
              <span>Health: loading…</span>
            ) : health.isError ? (
              <span>Health: unavailable</span>
            ) : (
              <span>
                {health.data?.status} · schema v{health.data?.schema_version} ·{" "}
                {formatNumber(health.data?.sessions)} sessions
              </span>
            )}
          </Badge>
        </div>
      </div>

      <Tabs
        orientation="vertical"
        value={activeTab}
        onValueChange={(value) => setActiveTab(value as InsightsTabKey)}
        className="flex-col gap-4 md:flex-row md:items-start md:gap-6"
      >
        <TabsList variant="line" className="w-full shrink-0 md:w-56">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="knowledge">Knowledge</TabsTrigger>
          <TabsTrigger value="retro">Retro</TabsTrigger>
          <TabsTrigger value="search-quality">Search Quality</TabsTrigger>
          <TabsTrigger value="live">Live feed</TabsTrigger>
          <TabsTrigger value="workflow">Workflow</TabsTrigger>
        </TabsList>
        <InsightsTabContext.Provider value={{ setActiveTab }}>
          <TabsContent value="overview" className="min-w-0">
            {children}
          </TabsContent>
        </InsightsTabContext.Provider>
        <TabsContent value="knowledge" className="min-w-0">
          <KnowledgeTab />
        </TabsContent>
        <TabsContent value="retro" className="min-w-0">
          <RetroTab />
        </TabsContent>
        <TabsContent value="search-quality" className="min-w-0">
          <SearchQualityTab />
        </TabsContent>
        <TabsContent value="live" className="min-w-0">
          <LiveTab active={activeTab === "live"} />
        </TabsContent>
        <TabsContent value="workflow" className="min-w-0">
          <WorkflowTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
