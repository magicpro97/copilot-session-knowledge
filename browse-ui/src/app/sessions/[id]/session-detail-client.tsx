"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, usePathname, useRouter } from "next/navigation";
import { Download, GitCompare, Loader2, ServerCog, Terminal } from "lucide-react";

import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { CompareSheet } from "@/components/data/compare-sheet";
import { SourceBadge, TimeRelative } from "@/components/data/session-badges";
import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { OverviewTab } from "./overview-tab";
import { TimelineTab } from "./timeline-tab";
import { MindmapTab } from "./mindmap-tab";
import { CheckpointsTab } from "./checkpoints-tab";
import { DebugLogTab } from "./debug-log-tab";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { hostRequest } from "@/lib/api/client";
import {
  useSessionDetail,
  useOperatorRuns,
  useCliSession,
  useAdoptCliSession,
} from "@/lib/api/hooks";
import { formatNumber, formatSessionIdBadgeText } from "@/lib/formatters";
import { useHostFeature } from "@/lib/hosts";
import { LOCAL_HOST_ID } from "@/lib/host-profiles";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";
import { useHostState } from "@/providers/host-provider";

type SessionTab = "overview" | "timeline" | "mindmap" | "checkpoints" | "debug-log";
const PLACEHOLDER_SESSION_ID = "_placeholder";

function hashToTab(hash: string): SessionTab | null {
  const cleaned = hash.replace(/^#/, "").toLowerCase();
  if (cleaned === "overview") return "overview";
  if (cleaned === "timeline") return "timeline";
  if (cleaned === "mindmap") return "mindmap";
  if (cleaned === "checkpoints") return "checkpoints";
  if (cleaned === "debug-log") return "debug-log";
  return null;
}

function safeDecodeSegment(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function sessionIdFromPathname(pathname: string | null): string {
  if (!pathname) return "";
  const parts = pathname.split("/").filter(Boolean);
  const sessionsIndex = parts.lastIndexOf("sessions");
  if (sessionsIndex < 0) return "";
  const rawId = parts[sessionsIndex + 1];
  return rawId ? safeDecodeSegment(rawId) : "";
}

function sessionIdFromHref(href: string | null): string {
  if (!href) return "";
  try {
    const url = new URL(href, "http://localhost");
    return sessionIdFromPathname(url.pathname);
  } catch {
    return "";
  }
}

export function SessionDetailClient() {
  const params = useParams<{ id: string }>();
  const pathname = usePathname();
  const router = useRouter();
  const { host, diagnosticsEnabled } = useHostState();
  const { supported: sessionsSupported, loading: sessionsCapabilityLoading } = useHostFeature(
    host,
    "sessions",
    diagnosticsEnabled
  );
  const sessionsEnabled = diagnosticsEnabled && sessionsSupported;
  const [sessionId, setSessionId] = useState("");

  const [activeTab, setActiveTab] = useState<SessionTab>("overview");
  const [compareOpen, setCompareOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [adoptError, setAdoptError] = useState<string | null>(null);

  // CLI adopt feature — only probe if sessions capability is available.
  const { supported: cliAdoptSupported } = useHostFeature(host, "cli_adopt", sessionsEnabled);
  // Fetch single CLI session to check adoptability.
  // SECURITY: cli_session_id from this query must only ever be used in POST body.
  const cliSessionQuery = useCliSession(sessionId, host, cliAdoptSupported && Boolean(sessionId));
  const adoptMutation = useAdoptCliSession(host);
  const isAdoptable = cliAdoptSupported && Boolean(cliSessionQuery.data);

  const handleAdoptFromDetail = useCallback(() => {
    const cliData = cliSessionQuery.data;
    if (!cliData || adoptMutation.isPending) return;
    setAdoptError(null);
    // SECURITY: cli_session_id goes into POST JSON body only; never into the URL.
    adoptMutation.mutate(
      { payload: { cli_session_id: cliData.cli_session_id }, host },
      {
        onSuccess: (operatorSession) => {
          // Navigate using operator session id only — CLI UUID is not in the URL.
          const params = new URLSearchParams();
          params.set("s", operatorSession.id);
          if (host.id !== LOCAL_HOST_ID) {
            params.set("h", host.id);
          }
          router.push(`/chat?${params.toString()}`);
        },
        onError: (err) => {
          setAdoptError(err instanceof Error ? err.message : "Adopt failed");
        },
      }
    );
  }, [cliSessionQuery.data, adoptMutation, host, router]);

  const detailQuery = useSessionDetail(sessionId, sessionsEnabled && Boolean(sessionId), host);
  // Only fetch operator runs when (a) Debug Log tab is active AND (b) the
  // backend reports `has_operator_runs: true` on the session detail. The
  // endpoint targets operator sessions (JSON-backed) only; knowledge sessions
  // (SQLite/CLI) have no operator record and would otherwise emit a visible
  // 404 in Chrome Network when Debug Log opens (issue #518).
  const hasOperatorRuns = detailQuery.data?.has_operator_runs === true;
  const runsQuery = useOperatorRuns(
    sessionId,
    sessionsEnabled && Boolean(sessionId) && activeTab === "debug-log" && hasOperatorRuns,
    host
  );
  const runsLoading = activeTab === "debug-log" && runsQuery.isLoading;
  // Use the latest run ID (last item in runs list). Gracefully null when no runs.
  const latestRunId = runsQuery.data?.runs[runsQuery.data.runs.length - 1]?.id ?? null;
  const shortId = formatSessionIdBadgeText(sessionId);
  const exportFileName = `${sessionId || "session"}.md`;

  useEffect(() => {
    const routeSessionId = safeDecodeSegment(params.id ?? "");
    const browserSessionId =
      typeof window === "undefined" ? "" : sessionIdFromHref(window.location.href);
    const browserPathname = typeof window === "undefined" ? null : window.location.pathname;
    const pathnameSessionId = sessionIdFromPathname(browserPathname ?? pathname);
    const nextSessionId =
      [browserSessionId, pathnameSessionId, routeSessionId].find(
        (value) => value && value !== PLACEHOLDER_SESSION_ID
      ) || "";
    setSessionId(nextSessionId);
  }, [params.id, pathname]);

  const handleExport = useCallback(async () => {
    if (!sessionId || !sessionsEnabled || typeof window === "undefined" || exporting) return;

    setExportError(null);
    setExporting(true);
    try {
      const response = await hostRequest(
        `/api/session/${encodeURIComponent(sessionId)}/export`,
        host,
        {
          method: "GET",
        }
      );
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
  }, [exportFileName, exporting, host, sessionId, sessionsEnabled]);

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
      key: "5",
      preventDefault: true,
      handler: () => setActiveTab("debug-log"),
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

  if (!diagnosticsEnabled) {
    return (
      <div className="space-y-4">
        <Breadcrumbs
          items={[{ label: "Sessions", href: "/sessions" }, { label: shortId || "Session" }]}
        />
        <EmptyState
          icon={<ServerCog className="size-5" />}
          title="No agent host selected"
          description="Run the browse server locally or select a remote agent host in the header to load session detail."
        />
      </div>
    );
  }

  if (sessionsCapabilityLoading) {
    return (
      <div className="space-y-4">
        <Breadcrumbs
          items={[{ label: "Sessions", href: "/sessions" }, { label: shortId || "Session" }]}
        />
        <EmptyState
          icon={<ServerCog className="size-5" />}
          title="Checking host capabilities"
          description="Waiting for the selected agent host to report session support."
        />
      </div>
    );
  }

  if (!sessionsSupported) {
    return (
      <div className="space-y-4">
        <Breadcrumbs
          items={[{ label: "Sessions", href: "/sessions" }, { label: shortId || "Session" }]}
        />
        <EmptyState
          icon={<ServerCog className="size-5" />}
          title="Not supported by this host"
          description="The connected agent does not advertise session detail support."
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Breadcrumbs
        items={[{ label: "Sessions", href: "/sessions" }, { label: shortId || "Session" }]}
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
      {exportError ? (
        <Banner tone="danger" title="Export failed" description={exportError} />
      ) : null}
      {adoptError ? <Banner tone="danger" title="Adopt failed" description={adoptError} /> : null}

      <Card>
        <CardHeader className="space-y-2">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1 space-y-1">
              <CardTitle className="line-clamp-2 text-xl">
                {detailQuery.data?.meta.summary?.trim() || `Session ${shortId}`}
              </CardTitle>
              <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-sm">
                <SourceBadge source={detailQuery.data?.meta.source} />
                <span>Events: {formatNumber(detailQuery.data?.meta.event_count_estimate)}</span>
                <TimeRelative value={detailQuery.data?.meta.fts_indexed_at} />
              </div>
            </div>
            <div className="flex items-center gap-2">
              {isAdoptable ? (
                <Button
                  variant="outline"
                  onClick={handleAdoptFromDetail}
                  disabled={adoptMutation.isPending}
                  data-testid="adopt-in-chat-btn"
                  title="Adopt this CLI session in Chat"
                >
                  {adoptMutation.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Terminal className="size-4" />
                  )}
                  Adopt in Chat
                </Button>
              ) : null}
              <Button
                variant="outline"
                onClick={() => {
                  void handleExport();
                }}
                disabled={exporting || !sessionId}
              >
                {exporting ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Download className="size-4" />
                )}
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
        <div className="bg-background/95 supports-[backdrop-filter]:bg-background/80 sticky top-0 z-10 -mx-1 overflow-x-auto px-1 pt-1 pb-px backdrop-blur">
          <TabsList variant="line">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="timeline">Timeline</TabsTrigger>
            <TabsTrigger value="mindmap">Mindmap</TabsTrigger>
            <TabsTrigger value="checkpoints">Checkpoints</TabsTrigger>
            <TabsTrigger value="debug-log">Debug Log</TabsTrigger>
          </TabsList>
          <div className="border-border -mx-1 border-b" />
        </div>

        <TabsContent value="overview">
          <OverviewTab
            meta={detailQuery.data?.meta ?? null}
            timeline={detailQuery.data?.timeline ?? []}
          />
        </TabsContent>
        <TabsContent value="timeline">
          <TimelineTab sessionId={sessionId} active={activeTab === "timeline"} host={host} />
        </TabsContent>
        <TabsContent value="mindmap">
          <MindmapTab sessionId={sessionId} active={activeTab === "mindmap"} host={host} />
        </TabsContent>
        <TabsContent value="checkpoints">
          <CheckpointsTab sessionId={sessionId} host={host} />
        </TabsContent>
        <TabsContent value="debug-log">
          <DebugLogTab
            sessionId={sessionId}
            runId={latestRunId}
            runsLoading={runsLoading}
            host={host}
          />
        </TabsContent>
      </Tabs>

      <CompareSheet
        open={compareOpen}
        onOpenChange={setCompareOpen}
        sessionId={sessionId}
        host={host}
      />
    </div>
  );
}
