"use client";

import { useEffect, useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ClustersTab } from "./clusters-tab";
import { RelationshipsTab } from "./relationships-tab";

type GraphTab = "relationships" | "clusters";

function hashToGraphTab(hash: string): GraphTab | null {
  const cleaned = hash.replace(/^#/, "").toLowerCase();
  if (cleaned === "relationships") return "relationships";
  if (cleaned === "clusters") return "clusters";
  return null;
}

export default function GraphPage() {
  const [activeTab, setActiveTab] = useState<GraphTab>("relationships");

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
    window.history.replaceState(null, "", `#${activeTab}`);
  }, [activeTab]);

  return (
    <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as GraphTab)}>
      <TabsList variant="line">
        <TabsTrigger value="relationships">Relationships</TabsTrigger>
        <TabsTrigger value="clusters">Clusters</TabsTrigger>
      </TabsList>

      <TabsContent value="relationships">
        <RelationshipsTab active={activeTab === "relationships"} />
      </TabsContent>

      <TabsContent value="clusters">
        <ClustersTab active={activeTab === "clusters"} />
      </TabsContent>
    </Tabs>
  );
}
