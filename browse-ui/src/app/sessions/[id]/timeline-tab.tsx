import { useMemo } from "react";

import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import { SubagentActivityPanel } from "@/components/data/subagent-activity-panel";
import { TimelinePlayer } from "@/components/data/timeline-player";
import {
  useSessionCheckpoints,
  useSessionDebugLogSkeleton,
  useSessionRewindSnapshots,
  useSubagentActivity,
} from "@/lib/api/hooks";
import type { HostProfile } from "@/lib/api/types";
import { deriveSubagentActivitySummary, deriveSubagentExecutions } from "@/lib/flight-recorder";

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
  // Flight Recorder v3: additive bounded routes. Hook failures are non-fatal
  // — when the response is unavailable the chapter rail falls back to
  // turn-derived chapters and ResumeDrawer is suppressed.
  const checkpointsQuery = useSessionCheckpoints(sessionId, host, Boolean(sessionId));
  const rewindQuery = useSessionRewindSnapshots(sessionId, host, Boolean(sessionId));

  // Sub-agent activity — dedicated bounded route; non-fatal if unavailable.
  const subagentQuery = useSubagentActivity(sessionId, Boolean(sessionId), host);
  const subagentExecutions = useMemo(
    () => deriveSubagentExecutions(subagentQuery.data),
    [subagentQuery.data]
  );
  const subagentSummary = useMemo(
    () => deriveSubagentActivitySummary(subagentQuery.data),
    [subagentQuery.data]
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
      <div className="space-y-3">
        <Banner
          tone="danger"
          title="Failed to load timeline"
          description={query.error instanceof Error ? query.error.message : "Unknown error"}
        />
        {/* Still show sub-agent panel if its dedicated route succeeded. */}
        {(subagentQuery.data || subagentQuery.isLoading) && (
          <SubagentActivityPanel
            executions={subagentExecutions}
            summary={subagentSummary}
            loading={subagentQuery.isLoading}
            error={subagentQuery.error instanceof Error ? subagentQuery.error : null}
            onOpenDebugLog={onOpenDebugLog}
          />
        )}
      </div>
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
    <div className="space-y-3">
      {/* Sub-agent activity panel — above the timeline player.
          The panel handles its own loading/error/empty states. */}
      <SubagentActivityPanel
        executions={subagentExecutions}
        summary={subagentSummary}
        loading={subagentQuery.isLoading}
        error={subagentQuery.error instanceof Error ? subagentQuery.error : null}
        onOpenDebugLog={onOpenDebugLog}
      />

      <TimelinePlayer
        entries={query.data.entries}
        total={query.data.total}
        hasMore={query.data.has_more}
        active={active}
        onOpenDebugLog={onOpenDebugLog}
        checkpoints={checkpointsQuery.data?.checkpoints}
        rewindSnapshots={rewindQuery.data?.snapshots}
      />
    </div>
  );
}
