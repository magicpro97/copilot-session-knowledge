"use client";

import { useMemo, useState } from "react";
import { Loader2, Scale } from "lucide-react";

import { Banner } from "@/components/data/banner";
import { SessionPicker } from "@/components/data/session-picker";
import { SourceBadge, TimeRelative } from "@/components/data/session-badges";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useCompare } from "@/lib/api/hooks";
import type { TimelineEntry } from "@/lib/api/types";
import { formatNumber, formatSessionIdBadgeText } from "@/lib/formatters";

type CompareSheetProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sessionId: string;
};

function buildTimelineSummary(timeline: TimelineEntry[]) {
  const sectionCount = new Set(
    timeline
      .map((entry) => entry.section_name?.trim())
      .filter((value): value is string => Boolean(value))
  ).size;
  const docTypeCount = new Set(
    timeline
      .map((entry) => entry.doc_type?.trim())
      .filter((value): value is string => Boolean(value))
  ).size;

  const first = timeline[0];
  const last = timeline[timeline.length - 1];

  return {
    rows: timeline.length,
    sectionCount,
    docTypeCount,
    firstTitle: first?.title?.trim() || "(empty)",
    lastTitle: last?.title?.trim() || "(empty)",
  };
}

export function CompareSheet({ open, onOpenChange, sessionId }: CompareSheetProps) {
  const [compareSessionId, setCompareSessionId] = useState("");
  const compareQuery = useCompare(sessionId, compareSessionId, open && Boolean(compareSessionId));

  const summaryA = useMemo(
    () => buildTimelineSummary(compareQuery.data?.a.timeline ?? []),
    [compareQuery.data?.a.timeline]
  );
  const summaryB = useMemo(
    () => buildTimelineSummary(compareQuery.data?.b.timeline ?? []),
    [compareQuery.data?.b.timeline]
  );
  const timelineDelta = summaryB.rows - summaryA.rows;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full gap-0 sm:max-w-4xl">
        <SheetHeader className="border-b">
          <SheetTitle>Compare sessions</SheetTitle>
          <SheetDescription>
            Side-by-side compare uses the available session metadata and timeline rows from{" "}
            <code>/api/compare?a=&amp;b=</code>.
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-4 p-4">
          <SessionPicker
            currentSessionId={sessionId}
            open={open}
            value={compareSessionId}
            onValueChange={setCompareSessionId}
          />

          {compareQuery.isLoading ? (
            <div className="text-muted-foreground flex items-center gap-2 text-sm">
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
            <>
              <Card size="sm">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-sm">
                    <Scale className="size-4" />
                    Timeline delta
                  </CardTitle>
                </CardHeader>
                <CardContent className="text-muted-foreground text-xs">
                  {timelineDelta === 0 ? (
                    <p>
                      Both sessions have the same timeline row count ({formatNumber(summaryA.rows)}
                      ).
                    </p>
                  ) : (
                    <p>
                      {timelineDelta > 0 ? "Compared session has" : "Current session has"}{" "}
                      {formatNumber(Math.abs(timelineDelta))} more timeline rows.
                    </p>
                  )}
                </CardContent>
              </Card>

              <div className="grid gap-3 md:grid-cols-2">
                <Card size="sm">
                  <CardHeader>
                    <CardTitle className="text-sm">
                      {formatSessionIdBadgeText(sessionId)}{" "}
                      <span className="text-muted-foreground">(current)</span>
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="text-muted-foreground space-y-1.5 text-xs">
                    <p className="text-foreground">
                      {compareQuery.data.a.session?.summary || "(no summary)"}
                    </p>
                    <div className="flex flex-wrap items-center gap-2">
                      <SourceBadge source={compareQuery.data.a.session?.source} />
                      <TimeRelative value={compareQuery.data.a.session?.fts_indexed_at} />
                    </div>
                    <p>Timeline rows: {formatNumber(summaryA.rows)}</p>
                    <p>Sections: {formatNumber(summaryA.sectionCount)}</p>
                    <p>Doc types: {formatNumber(summaryA.docTypeCount)}</p>
                    <p className="truncate">First: {summaryA.firstTitle}</p>
                    <p className="truncate">Last: {summaryA.lastTitle}</p>
                  </CardContent>
                </Card>

                <Card size="sm">
                  <CardHeader>
                    <CardTitle className="text-sm">
                      {formatSessionIdBadgeText(compareSessionId)}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="text-muted-foreground space-y-1.5 text-xs">
                    <p className="text-foreground">
                      {compareQuery.data.b.session?.summary || "(missing session)"}
                    </p>
                    <div className="flex flex-wrap items-center gap-2">
                      <SourceBadge source={compareQuery.data.b.session?.source} />
                      <TimeRelative value={compareQuery.data.b.session?.fts_indexed_at} />
                    </div>
                    <p>Timeline rows: {formatNumber(summaryB.rows)}</p>
                    <p>Sections: {formatNumber(summaryB.sectionCount)}</p>
                    <p>Doc types: {formatNumber(summaryB.docTypeCount)}</p>
                    <p className="truncate">First: {summaryB.firstTitle}</p>
                    <p className="truncate">Last: {summaryB.lastTitle}</p>
                  </CardContent>
                </Card>
              </div>
            </>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}
