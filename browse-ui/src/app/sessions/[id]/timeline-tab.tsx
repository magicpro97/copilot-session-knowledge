import { useMemo, useState } from "react";

import { Banner } from "@/components/data/banner";
import { EmptyState } from "@/components/data/empty-state";
import {
  MissionAtlas,
  MissionAtlasError,
  MissionAtlasLoading,
} from "@/components/data/mission-atlas";
import { SubagentActivityPanel } from "@/components/data/subagent-activity-panel";
import { TimelinePlayer } from "@/components/data/timeline-player";
import {
  useSessionCheckpoints,
  useSessionDebugLogSkeleton,
  useSessionMissionAtlas,
  useSessionRewindSnapshots,
  useSubagentActivity,
  useSubagentInternals,
} from "@/lib/api/hooks";
import type { HostProfile } from "@/lib/api/types";
import {
  deriveSubagentActivitySummary,
  deriveSubagentExecutions,
  deriveSubagentInternalsBySpanId,
  deriveSubagentInternalsSummary,
} from "@/lib/flight-recorder";

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

  // Mission Atlas — full-session aggregate. Non-fatal; TimelinePlayer still
  // renders if this query fails or the endpoint is not yet deployed.
  const atlasQuery = useSessionMissionAtlas(sessionId, Boolean(sessionId), host);

  // Atlas seek target: seq increments so repeated clicks on the same bucket
  // still trigger TimelinePlayer's seek effect.
  const [atlasSeek, setAtlasSeek] = useState<{ idx: number; seq: number } | null>(null);

  /** Combined handler: opens debug log tab and seeks the timeline player. */
  const handleAtlasSelect = (idx: number) => {
    setAtlasSeek((prev) => ({ idx, seq: (prev?.seq ?? 0) + 1 }));
    onOpenDebugLog?.(idx);
  };

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
  const subagentInternalsQuery = useSubagentInternals(sessionId, Boolean(sessionId), host);
  const subagentInternalsBySpanId = useMemo(
    () => deriveSubagentInternalsBySpanId(subagentInternalsQuery.data),
    [subagentInternalsQuery.data]
  );
  const subagentInternalsSummary = useMemo(
    () => deriveSubagentInternalsSummary(subagentInternalsQuery.data),
    [subagentInternalsQuery.data]
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
            internalsBySpanId={subagentInternalsBySpanId}
            internalsSummary={subagentInternalsSummary}
            internalsLoading={subagentInternalsQuery.isLoading}
            internalsError={
              subagentInternalsQuery.error instanceof Error ? subagentInternalsQuery.error : null
            }
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
        internalsBySpanId={subagentInternalsBySpanId}
        internalsSummary={subagentInternalsSummary}
        internalsLoading={subagentInternalsQuery.isLoading}
        internalsError={
          subagentInternalsQuery.error instanceof Error ? subagentInternalsQuery.error : null
        }
      />

      {/* Mission Atlas — full-session aggregate visual.
          Graceful degradation: loading/error states are non-blocking; the
          TimelinePlayer renders regardless of atlas availability. */}
      {atlasQuery.isLoading && <MissionAtlasLoading />}
      {atlasQuery.error && !atlasQuery.isLoading && (
        <MissionAtlasError
          error={atlasQuery.error instanceof Error ? atlasQuery.error : new Error("Unknown error")}
        />
      )}
      {atlasQuery.data && (
        <MissionAtlas data={atlasQuery.data} onSelectEntryIdx={handleAtlasSelect} />
      )}

      <TimelinePlayer
        entries={query.data.entries}
        total={query.data.total}
        hasMore={query.data.has_more}
        active={active}
        onOpenDebugLog={onOpenDebugLog}
        checkpoints={checkpointsQuery.data?.checkpoints}
        rewindSnapshots={rewindQuery.data?.snapshots}
        seekToEntryIdx={atlasSeek?.idx ?? null}
        seekToEntrySeq={atlasSeek?.seq ?? 0}
      />
    </div>
  );
}
