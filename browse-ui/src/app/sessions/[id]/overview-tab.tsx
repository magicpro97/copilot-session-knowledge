import type { SessionMeta, TimelineEntry } from "@/lib/api/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ToolUsageBar } from "@/components/data/tool-usage-bar";
import { EmptyState } from "@/components/data/empty-state";
import { formatNumber } from "@/lib/formatters";

type OverviewTabProps = {
  meta: SessionMeta | null;
  timeline: TimelineEntry[];
};

function timelineSections(timeline: TimelineEntry[]) {
  return timeline
    .map((entry, index) => ({
      key: `${entry.seq}-${index}`,
      name: entry.section_name || entry.title || `Section ${index + 1}`,
      docType: entry.doc_type || "unknown",
      content: (entry.content || "").trim(),
      length: (entry.content || "").length,
    }))
    .filter((section) => section.name || section.content);
}

export function OverviewTab({ meta, timeline }: OverviewTabProps) {
  const sections = timelineSections(timeline);

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Summary</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm leading-relaxed text-muted-foreground">
              {meta?.summary?.trim() || "No summary available for this session."}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Metadata</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 text-sm">
            <div className="flex items-start justify-between gap-4">
              <span className="text-muted-foreground">Source</span>
              <span className="text-right">{meta?.source || "unknown"}</span>
            </div>
            <div className="flex items-start justify-between gap-4">
              <span className="text-muted-foreground">Events</span>
              <span>{formatNumber(meta?.event_count_estimate ?? null)}</span>
            </div>
            <div className="flex items-start justify-between gap-4">
              <span className="text-muted-foreground">Path</span>
              <span className="max-w-[75%] break-all text-right text-xs">{meta?.path || "—"}</span>
            </div>
            <div className="flex items-start justify-between gap-4">
              <span className="text-muted-foreground">Indexed</span>
              <span>{meta?.fts_indexed_at || "—"}</span>
            </div>
          </CardContent>
        </Card>
      </div>

      <ToolUsageBar timeline={timeline} />

      <Card>
        <CardHeader>
          <CardTitle>Sections</CardTitle>
        </CardHeader>
        <CardContent>
          {sections.length === 0 ? (
            <EmptyState
              title="No timeline sections"
              description="This session does not have indexed section content yet."
              className="min-h-32"
            />
          ) : (
            <div className="space-y-2">
              {sections.map((section) => (
                <details key={section.key} className="rounded-lg border border-border px-3 py-2">
                  <summary className="cursor-pointer list-none text-sm font-medium">
                    <span>{section.name}</span>
                    <span className="ml-2 text-xs text-muted-foreground">
                      ({section.docType} · {formatNumber(section.length)} chars)
                    </span>
                  </summary>
                  <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs text-muted-foreground">
                    {section.content.slice(0, 500) || "(empty section content)"}
                  </pre>
                </details>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
