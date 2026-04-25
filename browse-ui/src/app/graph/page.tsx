"use client";

import { useEffect, useState } from "react";

import { ClustersTab } from "./clusters-tab";
import { CommunitiesTab } from "./communities-tab";
import { RelationshipsTab } from "./relationships-tab";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type GraphTab = "evidence" | "similarity" | "communities";

function hashToGraphTab(hash: string): GraphTab | null {
  const cleaned = hash.replace(/^#/, "").toLowerCase();
  if (cleaned === "evidence" || cleaned === "relationships") return "evidence";
  if (cleaned === "similarity" || cleaned === "clusters") return "similarity";
  if (cleaned === "communities") return "communities";
  return null;
}

export default function GraphPage() {
  const [activeTab, setActiveTab] = useState<GraphTab>("evidence");

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
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.isContentEditable;
      if (isTypingTarget) return;

      if (event.key === "1") {
        event.preventDefault();
        setActiveTab("evidence");
      } else if (event.key === "2") {
        event.preventDefault();
        setActiveTab("similarity");
      } else if (event.key === "3") {
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
    <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as GraphTab)}>
      <TabsList variant="line">
        <TabsTrigger value="evidence">Evidence</TabsTrigger>
        <TabsTrigger value="similarity">Similarity</TabsTrigger>
        <TabsTrigger value="communities">Communities</TabsTrigger>
      </TabsList>

      <TabsContent value="evidence">
        <RelationshipsTab active={activeTab === "evidence"} />
      </TabsContent>

      <TabsContent value="similarity">
        <ClustersTab active={activeTab === "similarity"} />
      </TabsContent>

      <TabsContent value="communities">
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
