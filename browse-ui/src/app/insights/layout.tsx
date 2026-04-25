"use client";

import { Activity } from "lucide-react";
import { type ReactNode, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";
import { useHealth } from "@/lib/api/hooks";
import { formatNumber } from "@/lib/formatters";
import { LiveTab } from "./live-tab";

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

export default function InsightsLayout({ children }: InsightsLayoutProps) {
  const health = useHealth();
  const [activeTab, setActiveTab] = useState<"dashboard" | "live">("dashboard");

  useKeyboardShortcuts([
    {
      key: "1",
      preventDefault: true,
      handler: () => setActiveTab("dashboard"),
    },
    {
      key: "2",
      preventDefault: true,
      handler: () => setActiveTab("live"),
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
        value={activeTab}
        onValueChange={(value) => setActiveTab(value as "dashboard" | "live")}
        className="space-y-4"
      >
        <TabsList variant="line">
          <TabsTrigger value="dashboard">Dashboard</TabsTrigger>
          <TabsTrigger value="live">Live feed</TabsTrigger>
        </TabsList>
        <TabsContent value="dashboard">{children}</TabsContent>
        <TabsContent value="live">
          <LiveTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
