/**
 * Flight Recorder v3 — Header strip (synthesis §4d/§4e).
 *
 * Additive overlay above the existing TimelinePlayer control bar. Surfaces:
 *   - status pill ("OK" | "ERROR (N)" | "INCOMPLETE")
 *   - total duration
 *   - event count + lane breakdown
 *   - turn count
 *   - jump-to-first-error / jump-to-last-response buttons
 *   - truncated indicator when `hasMore` is true
 *
 * Reads only safe `PlaybackBaseEntry`-shaped fields plus the derived
 * `PlaybackMarker[]` list — never raw `attrs`, `userMessage`, or message
 * bodies. Renders all text as React text nodes (no `dangerouslySetInnerHTML`).
 */
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import {
  deriveMarkersFromEntries,
  normalizeAndSortEntries,
  type PlaybackBaseEntry,
  type PlaybackLaneId,
  type PlaybackMarker,
} from "@/lib/debug-event-playback";
import { computeTraceLayout } from "@/lib/debug-span-flow";
import type { BrowseDebugEntry } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export type FlightRecorderHeaderProps = {
  entries: PlaybackBaseEntry[];
  /** Total events on the server (may exceed `entries.length` if truncated). */
  total: number;
  /** True when the backend signaled more events past the loaded window. */
  hasMore: boolean;
  /** Optional: called with the sorted index of the first `error` marker. */
  onJumpToError?: (sortedIndex: number) => void;
  /** Optional: called with the sorted index of the last `agent_response` marker. */
  onJumpToLastResponse?: (sortedIndex: number) => void;
};

type HeaderStatus = "ok" | "error" | "incomplete";

type LaneCountSummary = {
  id: PlaybackLaneId;
  count: number;
  errorCount: number;
};

function formatDurationMs(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);
  return `${minutes}m${seconds.toString().padStart(2, "0")}s`;
}

const LANE_SHORT_LABEL: Record<PlaybackLaneId, string> = {
  Session: "session",
  Turn: "turn",
  Model: "model",
  "Tool/Hook/Skill": "tool",
  SubAgent: "subagent",
  "Notification/Compaction": "notif",
  Error: "error",
  Generic: "generic",
};

function buildLaneSummary(
  laneTotals: { id: PlaybackLaneId; count: number; errorCount: number }[]
): string {
  // Pick lanes that we want to surface in the breakdown.  Always show counts
  // for: tool/hook/skill, hook (collapsed under same lane), errors when > 0.
  const parts: string[] = [];
  for (const lane of laneTotals) {
    if (lane.count <= 0) continue;
    if (lane.id === "Generic" || lane.id === "Session") continue;
    parts.push(`${lane.count} ${LANE_SHORT_LABEL[lane.id]}`);
  }
  return parts.join(" · ");
}

function pluralize(n: number, label: string): string {
  return `${n.toLocaleString()} ${label}${n === 1 ? "" : "s"}`;
}

export function FlightRecorderHeader({
  entries,
  total,
  hasMore,
  onJumpToError,
  onJumpToLastResponse,
}: FlightRecorderHeaderProps) {
  const { normalized } = useMemo(() => normalizeAndSortEntries(entries), [entries]);

  const markers = useMemo<PlaybackMarker[]>(
    () => deriveMarkersFromEntries(normalized),
    [normalized]
  );

  /**
   * Lane breakdown + duration come from `computeTraceLayout`, which is safe
   * (it operates on `BrowseDebugEntry`-shaped fields only). For skeleton
   * entries the cast is still valid: `computeTraceLayout` reads
   * `idx/timestamp/kind/duration_ms/status/span_id/parent_span_id` only.
   */
  const trace = useMemo(() => computeTraceLayout(entries as BrowseDebugEntry[]), [entries]);

  const errorCount = trace.summary.errorCount;
  const turnCount = markers.filter((m) => m.kind === "turn_start").length;

  const status: HeaderStatus = useMemo(() => {
    if (errorCount > 0) return "error";
    if (normalized.length === 0) return "ok";
    const last = normalized[normalized.length - 1].entry;
    const terminal = last.status === "ok" || last.status === "error" || last.status === "cancelled";
    if (!terminal && last.kind !== "agent_response") return "incomplete";
    return "ok";
  }, [errorCount, normalized]);

  const firstErrorMarker = useMemo(
    () => markers.find((m) => m.kind === "error") ?? null,
    [markers]
  );
  const lastResponseMarker = useMemo(() => {
    for (let i = markers.length - 1; i >= 0; i--) {
      if (markers[i].kind === "agent_response") return markers[i];
    }
    return null;
  }, [markers]);

  const laneSummary = useMemo<string>(
    () => buildLaneSummary(trace.summary.laneTotals as LaneCountSummary[]),
    [trace]
  );

  if (entries.length === 0) return null;

  const statusLabel =
    status === "ok" ? "OK" : status === "error" ? `ERROR (${errorCount})` : "INCOMPLETE";
  const statusToneCls =
    status === "ok"
      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40"
      : status === "error"
        ? "bg-red-500/15 text-red-300 border-red-500/40"
        : "bg-amber-500/15 text-amber-300 border-amber-500/40";

  const durationLabel = formatDurationMs(trace.summary.totalDurationMs);
  const eventLabel = laneSummary
    ? `${pluralize(normalized.length, "event")} · ${laneSummary}`
    : pluralize(normalized.length, "event");

  return (
    <div
      role="region"
      aria-label="Flight recorder summary"
      data-testid="flight-recorder-header"
      className="border-border bg-card flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-xs"
    >
      <span
        data-testid="flight-recorder-status"
        className={cn(
          "inline-flex items-center rounded-full border px-2 py-0.5 font-medium",
          statusToneCls
        )}
      >
        {statusLabel}
      </span>

      <span data-testid="flight-recorder-duration" className="text-muted-foreground">
        {durationLabel}
      </span>

      <span data-testid="flight-recorder-event-count" className="text-muted-foreground">
        {eventLabel}
      </span>

      <span data-testid="flight-recorder-turn-count" className="text-muted-foreground">
        {turnCount === 1 ? "Turn 1" : `${turnCount} turns`}
      </span>

      {hasMore && total > normalized.length && (
        <span
          data-testid="flight-recorder-truncated"
          className="text-amber-400/90"
          title={`Showing first ${normalized.length} of ${total} events`}
        >
          +{(total - normalized.length).toLocaleString()} more
        </span>
      )}

      <div className="ml-auto flex items-center gap-1">
        {firstErrorMarker && (
          <Button
            type="button"
            variant="outline"
            size="xs"
            data-testid="flight-recorder-jump-error"
            aria-label="Jump to first error"
            onClick={() => onJumpToError?.(firstErrorMarker.sortedIndex)}
          >
            Jump to first error
          </Button>
        )}
        {lastResponseMarker && (
          <Button
            type="button"
            variant="outline"
            size="xs"
            data-testid="flight-recorder-jump-last-response"
            aria-label="Jump to last response"
            onClick={() => onJumpToLastResponse?.(lastResponseMarker.sortedIndex)}
          >
            Jump to last response
          </Button>
        )}
      </div>
    </div>
  );
}
