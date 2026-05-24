import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { TimelinePlayer } from "@/components/data/timeline-player";
import { useSessionDebugLogSkeleton } from "@/lib/api/hooks";
import type { HostProfile } from "@/lib/api/types";

type TimelineTabProps = {
  sessionId: string;
  active: boolean;
  host: HostProfile;
  onOpenDebugLog?: (entryIdx: number) => void;
};

export function TimelineTab({ sessionId, active, host, onOpenDebugLog }: TimelineTabProps) {
  const query = useSessionDebugLogSkeleton(
    sessionId,
    { from: 0, limit: 5000 },
    Boolean(sessionId),
    host
  );

  if (query.isLoading) {
    return (
      <div className="border-border text-muted-foreground rounded-xl border p-4 text-sm">
        Loading timeline…
      </div>
    );
  }

  if (query.error) {
    return (
      <Banner
        tone="danger"
        title="Failed to load timeline"
        description={query.error instanceof Error ? query.error.message : "Unknown error"}
      />
    );
  }

  if (!query.data || query.data.entries.length === 0) {
    return (
      <EmptyState
        title="No timeline data"
        description="No debug timeline entries were found for this session. The session may not have recorded structured events yet."
      />
    );
  }

  return (
    <TimelinePlayer
      entries={query.data.entries}
      total={query.data.total}
      hasMore={query.data.has_more}
      active={active}
      onOpenDebugLog={onOpenDebugLog}
    />
  );
}
