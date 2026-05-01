"use client";

import { useEffect, useState } from "react";

import { ClustersTab } from "./clusters-tab";
import { CommunitiesTab } from "./communities-tab";
import { InsightTab } from "./insight-tab";
import { RelationshipsTab } from "./relationships-tab";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type GraphTab = "insight" | "evidence" | "similarity" | "communities";

function hashToGraphTab(hash: string): GraphTab | null {
  const cleaned = hash.replace(/^#/, "").toLowerCase();
  if (cleaned === "insight") return "insight";
  if (cleaned === "evidence" || cleaned === "relationships") return "evidence";
  if (cleaned === "similarity" || cleaned === "clusters") return "similarity";
  if (cleaned === "communities") return "communities";
  return null;
}

export default function GraphPage() {
  // "insight" is the default — graph-specific summary surfaces first.
  const [activeTab, setActiveTab] = useState<GraphTab>("insight");

  useEffect(() => {
    if (typeof window === "undefined") return;
    const initial = hashToGraphTab(window.location.hash);
    if (initial) setActiveTab(initial);

    const onHashChange = () => {
      const next = hashToGraphTab(window.location.hash);
      if (next) setActiveTab(next);
    };

    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      const isTypingTarget =
        target?.tagName === "INPUT" || target?.tagName === "TEXTAREA" || target?.isContentEditable;
      if (isTypingTarget) return;

      // 1 = Insight (default/first), 2 = Evidence, 3 = Similarity, 4 = Communities
      if (event.key === "1") {
        event.preventDefault();
        setActiveTab("insight");
      } else if (event.key === "2") {
        event.preventDefault();
        setActiveTab("evidence");
      } else if (event.key === "3") {
        event.preventDefault();
        setActiveTab("similarity");
      } else if (event.key === "4") {
        event.preventDefault();
        setActiveTab("communities");
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.history.replaceState(null, "", `#${activeTab}`);
  }, [activeTab]);

  return (
    <Tabs
      orientation="vertical"
      value={activeTab}
      onValueChange={(value) => setActiveTab(value as GraphTab)}
      className="flex-col gap-4 md:flex-row md:items-start md:gap-6"
    >
      <TabsList variant="line" className="w-full shrink-0 md:w-56">
        <TabsTrigger value="insight">Insight</TabsTrigger>
        <TabsTrigger value="evidence">Evidence</TabsTrigger>
        <TabsTrigger value="similarity">Similarity</TabsTrigger>
        <TabsTrigger value="communities">Communities</TabsTrigger>
      </TabsList>

      <TabsContent value="insight" className="min-w-0">
        <InsightTab active={activeTab === "insight"} onNavigate={(tab) => setActiveTab(tab)} />
      </TabsContent>

      <TabsContent value="evidence" className="min-w-0">
        <RelationshipsTab active={activeTab === "evidence"} />
      </TabsContent>

      <TabsContent value="similarity" className="min-w-0">
        <ClustersTab active={activeTab === "similarity"} />
      </TabsContent>

      <TabsContent value="communities" className="min-w-0">
        <CommunitiesTab
          active={activeTab === "communities"}
          onDrillIn={(target) => {
            setActiveTab(target);
          }}
        />
      </TabsContent>
    </Tabs>
  );
}
