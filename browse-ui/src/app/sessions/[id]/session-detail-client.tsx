"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Download, GitCompare, Loader2 } from "lucide-react";

import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { CompareSheet } from "@/components/data/compare-sheet";
import { SourceBadge, TimeRelative } from "@/components/data/session-badges";
import { Banner } from "@/components/data/banner";
import { OverviewTab } from "./overview-tab";
import { TimelineTab } from "./timeline-tab";
import { MindmapTab } from "./mindmap-tab";
import { CheckpointsTab } from "./checkpoints-tab";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { useSessionDetail } from "@/lib/api/hooks";
import { formatNumber, formatSessionIdBadgeText } from "@/lib/formatters";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";

type SessionTab = "overview" | "timeline" | "mindmap" | "checkpoints";

function hashToTab(hash: string): SessionTab | null {
  const cleaned = hash.replace(/^#/, "").toLowerCase();
  if (cleaned === "overview") return "overview";
  if (cleaned === "timeline") return "timeline";
  if (cleaned === "mindmap") return "mindmap";
  if (cleaned === "checkpoints") return "checkpoints";
  return null;
}

export function SessionDetailClient() {
  const params = useParams<{ id: string }>();
  const sessionId = params.id ?? "";

  const [activeTab, setActiveTab] = useState<SessionTab>("overview");
  const [compareOpen, setCompareOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const detailQuery = useSessionDetail(sessionId, Boolean(sessionId));
  const shortId = formatSessionIdBadgeText(sessionId);
  const exportHref = `/session/${encodeURIComponent(sessionId)}.md`;
  const exportFileName = `${sessionId || "session"}.md`;

  const handleExport = useCallback(async () => {
    if (!sessionId || typeof window === "undefined" || exporting) return;

    setExportError(null);
    setExporting(true);
    try {
      const response = await fetch(exportHref, {
        method: "GET",
        credentials: "same-origin",
      });
      if (!response.ok) {
        throw new Error(`Export failed (${response.status})`);
      }

      const blob = await response.blob();
      const objectUrl = window.URL.createObjectURL(blob);
      const anchor = window.document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = exportFileName;
      window.document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.URL.revokeObjectURL(objectUrl);
    } catch (error) {
      setExportError(error instanceof Error ? error.message : "Unknown export error");
    } finally {
      setExporting(false);
    }
  }, [exportFileName, exportHref, exporting, sessionId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const initial = hashToTab(window.location.hash);
    if (initial) setActiveTab(initial);

    const onHashChange = () => {
      const value = hashToTab(window.location.hash);
      if (value) setActiveTab(value);
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
      handler: () => setActiveTab("timeline"),
    },
    {
      key: "3",
      preventDefault: true,
      handler: () => setActiveTab("mindmap"),
    },
    {
      key: "4",
      preventDefault: true,
      handler: () => setActiveTab("checkpoints"),
    },
    {
      key: "e",
      preventDefault: true,
      handler: () => {
        if (!sessionId) return false;
        void handleExport();
        return true;
      },
    },
    {
      key: "c",
      preventDefault: true,
      handler: () => setCompareOpen(true),
    },
  ]);

  return (
    <div className="space-y-4">
      <Breadcrumbs
        items={[
          { label: "Sessions", href: "/sessions" },
          { label: shortId || "Session" },
        ]}
      />

      {detailQuery.error ? (
        <Banner
          tone="danger"
          title="Failed to load session detail"
          description={
            detailQuery.error instanceof Error ? detailQuery.error.message : "Unknown error"
          }
        />
      ) : null}
      {exportError ? <Banner tone="danger" title="Export failed" description={exportError} /> : null}

      <Card>
        <CardHeader className="space-y-2">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <CardTitle className="text-xl">
                {detailQuery.data?.meta.summary?.trim() || `Session ${shortId}`}
              </CardTitle>
              <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                <SourceBadge source={detailQuery.data?.meta.source} />
                <span>Events: {formatNumber(detailQuery.data?.meta.event_count_estimate)}</span>
                <TimeRelative value={detailQuery.data?.meta.fts_indexed_at} />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  void handleExport();
                }}
                disabled={exporting || !sessionId}
              >
                {exporting ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}
                Export .md
              </Button>
              <Button variant="outline" onClick={() => setCompareOpen(true)}>
                <GitCompare className="size-4" />
                Compare
              </Button>
            </div>
          </div>
        </CardHeader>
      </Card>

      <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as SessionTab)}>
        <TabsList variant="line">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="timeline">Timeline</TabsTrigger>
          <TabsTrigger value="mindmap">Mindmap</TabsTrigger>
          <TabsTrigger value="checkpoints">Checkpoints</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <OverviewTab meta={detailQuery.data?.meta ?? null} timeline={detailQuery.data?.timeline ?? []} />
        </TabsContent>
        <TabsContent value="timeline">
          <TimelineTab sessionId={sessionId} active={activeTab === "timeline"} />
        </TabsContent>
        <TabsContent value="mindmap">
          <MindmapTab sessionId={sessionId} />
        </TabsContent>
        <TabsContent value="checkpoints">
          <CheckpointsTab sessionId={sessionId} />
        </TabsContent>
      </Tabs>

      <CompareSheet open={compareOpen} onOpenChange={setCompareOpen} sessionId={sessionId} />
    </div>
  );
}
