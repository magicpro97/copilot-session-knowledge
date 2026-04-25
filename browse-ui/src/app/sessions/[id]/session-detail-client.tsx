"use client";

import { useMemo, useState, useEffect } from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Download, GitCompare, Loader2 } from "lucide-react";

import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { SourceBadge, TimeRelative } from "@/components/data/session-badges";
import { Banner } from "@/components/data/banner";
import { OverviewTab } from "./overview-tab";
import { TimelineTab } from "./timeline-tab";
import { MindmapTab } from "./mindmap-tab";
import { CheckpointsTab } from "./checkpoints-tab";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useCompare, useSessionDetail } from "@/lib/api/hooks";
import { apiFetch } from "@/lib/api/client";
import { sessionListResponseSchema } from "@/lib/api/schemas";
import { formatNumber, formatSessionIdBadgeText } from "@/lib/formatters";

type SessionTab = "overview" | "timeline" | "mindmap" | "checkpoints";

const TAB_BY_KEY: Record<string, SessionTab> = {
  "1": "overview",
  "2": "timeline",
  "3": "mindmap",
  "4": "checkpoints",
};

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
  const [compareSessionId, setCompareSessionId] = useState("");

  const detailQuery = useSessionDetail(sessionId, Boolean(sessionId));
  const sessionListQuery = useQuery({
    queryKey: ["session-compare-picker", 1, 100],
    enabled: Boolean(sessionId),
    queryFn: async () => {
      const data = await apiFetch("/api/sessions?page=1&page_size=100");
      return sessionListResponseSchema.parse(data);
    },
  });
  const compareQuery = useCompare(
    sessionId,
    compareSessionId,
    compareOpen && Boolean(compareSessionId)
  );

  const compareCandidates = useMemo(
    () => (sessionListQuery.data?.items ?? []).filter((item) => item.id !== sessionId),
    [sessionListQuery.data?.items, sessionId]
  );

  const shortId = formatSessionIdBadgeText(sessionId);
  const exportHref = `/session/${encodeURIComponent(sessionId)}.md`;

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

  useEffect(() => {
    if (!compareOpen || compareSessionId || compareCandidates.length === 0) return;
    setCompareSessionId(compareCandidates[0].id);
  }, [compareOpen, compareSessionId, compareCandidates]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      const target = event.target as HTMLElement | null;
      const tagName = target?.tagName?.toLowerCase();
      if (
        target?.isContentEditable ||
        tagName === "input" ||
        tagName === "textarea" ||
        tagName === "select"
      ) {
        return;
      }

      if (TAB_BY_KEY[event.key]) {
        event.preventDefault();
        setActiveTab(TAB_BY_KEY[event.key]);
        return;
      }
      if (event.key.toLowerCase() === "e" && sessionId) {
        event.preventDefault();
        window.open(exportHref, "_blank", "noopener,noreferrer");
        return;
      }
      if (event.key.toLowerCase() === "c") {
        event.preventDefault();
        setCompareOpen(true);
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [exportHref, sessionId]);

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
                onClick={() => window.open(exportHref, "_blank", "noopener,noreferrer")}
              >
                <Download className="size-4" />
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

      <Sheet open={compareOpen} onOpenChange={setCompareOpen}>
        <SheetContent side="right" className="w-full gap-0 sm:max-w-2xl">
          <SheetHeader className="border-b">
            <SheetTitle>Compare sessions</SheetTitle>
            <SheetDescription>
              Compare this session against another session in context.
            </SheetDescription>
          </SheetHeader>

          <div className="space-y-3 p-4">
            <label className="text-sm font-medium" htmlFor="compare-session">
              Compare with
            </label>
            <select
              id="compare-session"
              className="h-8 w-full rounded-lg border border-input bg-background px-2.5 text-sm"
              value={compareSessionId}
              onChange={(event) => setCompareSessionId(event.target.value)}
            >
              {compareCandidates.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {formatSessionIdBadgeText(candidate.id)} — {candidate.summary || "(no summary)"}
                </option>
              ))}
            </select>

            {compareQuery.isLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" />
                Loading compare data...
              </div>
            ) : null}

            {compareQuery.error ? (
              <Banner
                tone="danger"
                title="Compare failed"
                description={
                  compareQuery.error instanceof Error
                    ? compareQuery.error.message
                    : "Unknown compare error"
                }
              />
            ) : null}

            {compareQuery.data ? (
              <div className="grid gap-3 md:grid-cols-2">
                <Card size="sm">
                  <CardHeader>
                    <CardTitle className="text-sm">
                      {formatSessionIdBadgeText(sessionId)} (current)
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-1 text-xs text-muted-foreground">
                    <p>{compareQuery.data.a.session?.summary || "(no summary)"}</p>
                    <p>Timeline rows: {formatNumber(compareQuery.data.a.timeline.length)}</p>
                  </CardContent>
                </Card>

                <Card size="sm">
                  <CardHeader>
                    <CardTitle className="text-sm">
                      {formatSessionIdBadgeText(compareSessionId)}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-1 text-xs text-muted-foreground">
                    <p>{compareQuery.data.b.session?.summary || "(missing session)"}</p>
                    <p>Timeline rows: {formatNumber(compareQuery.data.b.timeline.length)}</p>
                  </CardContent>
                </Card>
              </div>
            ) : null}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
