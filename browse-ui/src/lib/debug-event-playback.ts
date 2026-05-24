/**
 * Pure playback model for timeline/debug-log playback (issue #539).
 *
 * Converts a flat list of debug entries (full BrowseDebugEntry or lightweight
 * BrowseDebugSkeletonEntry) into a frame-by-frame playback model suitable for
 * rendering a time-axis waterfall or span Gantt without touching React or the
 * DOM.
 *
 * Design choices documented here for audit:
 *
 * TIMESTAMP NORMALIZATION
 *   Entries are sorted stable by (timestampMs ASC, idx ASC). If >5% of entries
 *   have missing/invalid timestamps (or every timestamp is identical/missing),
 *   the model falls back to index-domain mode where cursor is treated as an
 *   absolute index into the sorted array. In index mode elapsedMs is always
 *   null. The threshold is 5% with a minimum of 2 invalid entries required to
 *   trigger fallback, so single-entry traces always use time mode when a valid
 *   timestamp is present.
 *
 * ACTIVE SPAN SEMANTICS
 *   A span is "active" at playhead P when:
 *     • duration_ms is non-null:   startMs <= P < startMs + duration_ms
 *     • duration_ms is null:       span is active only when it IS the current
 *       event (i.e., idx === currentEvent.idx). This zero-width treatment is
 *       safer than marking incomplete spans as "forever active". Documented so
 *       the renderer can distinguish "running now" from "known complete".
 *
 * LANES
 *   Seven grouped display lanes per spec:
 *     Session | Turn | Model | Tool/Hook/Skill | SubAgent |
 *     Notification/Compaction | Error | Generic (fallback)
 *   Full entries use deriveNodeCategory from debug-span-flow.ts.
 *   Skeleton entries (no attrs/tool_name) use kind/status-only fallback.
 *
 * MARKERS
 *   Navigation anchors are derived from kind (turn_start, agent_response,
 *   error) and status/kind (compaction). Useful for prev/next marker jump.
 *
 * AGGREGATION
 *   binEntriesByTime and binEntriesByCount are pure helpers for large-trace
 *   overview rendering — they never touch the DOM.
 */

import type { BrowseDebugEntry } from "@/lib/api/types";
import type { BrowseDebugSkeletonEntry } from "@/lib/api/types";
import type { FlowNodeCategory } from "@/lib/debug-span-flow";
import { deriveNodeCategory } from "@/lib/debug-span-flow";

// ── Shared minimal entry shape ────────────────────────────────────────────────

/**
 * Minimal common shape accepted by the playback model. Both BrowseDebugEntry
 * and BrowseDebugSkeletonEntry satisfy this interface so the model is
 * projection-agnostic.
 */
export interface PlaybackBaseEntry {
  idx: number;
  timestamp: string | null;
  kind: string;
  duration_ms: number | null;
  status: string | null;
  span_id: string | null;
  parent_span_id: string | null;
}

// ── Display lane types ────────────────────────────────────────────────────────

/**
 * Seven grouped display lanes.
 * "Generic" is an internal fallback lane for events that don't match any
 * known category — renderers may hide it or group it with Error.
 */
export type PlaybackLaneId =
  | "Session"
  | "Turn"
  | "Model"
  | "Tool/Hook/Skill"
  | "SubAgent"
  | "Notification/Compaction"
  | "Error"
  | "Generic";

/** Map from internal FlowNodeCategory to grouped display lane. */
export const PLAYBACK_LANE_MAP: Record<FlowNodeCategory, PlaybackLaneId> = {
  session: "Session",
  turn: "Turn",
  model: "Model",
  tool: "Tool/Hook/Skill",
  hook: "Tool/Hook/Skill",
  skill: "Tool/Hook/Skill",
  subagent: "SubAgent",
  notification: "Notification/Compaction",
  compaction: "Notification/Compaction",
  error: "Error",
  generic: "Generic",
};

/** All lane ids in canonical display order. */
export const PLAYBACK_LANE_ORDER: PlaybackLaneId[] = [
  "Session",
  "Turn",
  "Model",
  "Tool/Hook/Skill",
  "SubAgent",
  "Notification/Compaction",
  "Error",
  "Generic",
];

// ── Category derivation ───────────────────────────────────────────────────────

/**
 * Derive coarse category from a skeleton entry (kind and status only).
 * Used when attrs/tool_name/source are absent. Falls back to "generic"
 * for unrecognised kinds.
 *
 * Note: "notification" and "compaction" cannot be detected from kind alone
 * (they are signalled by attrs.notification_kind/compaction_kind in full
 * entries). Skeleton entries with those kinds will fall through to "generic"
 * unless status=error elevates them to "error". This is intentional — the
 * skeleton projection is designed for span-level timeline rendering and
 * coarse-grained categorisation is sufficient.
 */
export function deriveSkeletonCategory(kind: string, status: string | null): FlowNodeCategory {
  switch (kind) {
    case "tool_call":
      return "tool";
    case "hook":
      return "hook";
    case "subagent":
      return "subagent";
    case "agent_response":
    case "llm_request":
      return "model";
    case "session_start":
      return "session";
    case "turn_start":
      return "turn";
    case "error":
      return "error";
  }
  // Status-based error escalation for unknown kinds
  if (status === "error") return "error";
  return "generic";
}

/**
 * Derive display lane for a playback entry.
 * For full BrowseDebugEntry uses rich attrs-aware category derivation.
 * For skeleton entries (no attrs field) falls back to kind/status only.
 */
export function derivePlaybackLane(entry: PlaybackBaseEntry): PlaybackLaneId {
  const category = isFull(entry)
    ? deriveNodeCategory(entry)
    : deriveSkeletonCategory(entry.kind, entry.status);
  return PLAYBACK_LANE_MAP[category] ?? "Generic";
}

/** Type guard: true when the entry has the full BrowseDebugEntry shape. */
function isFull(entry: PlaybackBaseEntry): entry is BrowseDebugEntry {
  return "attrs" in entry || "message" in entry || "source" in entry;
}

// ── Marker types ──────────────────────────────────────────────────────────────

/** Kinds of playback navigation markers. */
export type PlaybackMarkerKind = "turn_start" | "agent_response" | "error" | "compaction";

/** A navigation anchor in the playback timeline. */
export interface PlaybackMarker {
  /** Index into the sorted normalized entry array. */
  sortedIndex: number;
  /** Original entry idx (stable, used for serialisation). */
  entryIdx: number;
  kind: PlaybackMarkerKind;
  /** ISO-8601 timestamp. null when absent. */
  timestamp: string | null;
  /** Parsed timestamp in ms. null when absent or invalid. */
  timestampMs: number | null;
}

// ── Normalized entry ──────────────────────────────────────────────────────────

/** Entry with parsed timestamp and sort key. */
export interface NormalizedEntry<T extends PlaybackBaseEntry = PlaybackBaseEntry> {
  entry: T;
  /** Parsed timestamp in ms since epoch. null when absent or unparseable. */
  timestampMs: number | null;
  /**
   * Sort key used for stable ordering:
   * - Time mode: timestampMs (with idx as secondary tiebreaker in the sort fn)
   * - Index mode: entry.idx
   */
  sortKey: number;
}

// ── Active span ───────────────────────────────────────────────────────────────

/** A span that is "active" at the current playhead position. */
export interface ActiveSpan {
  /** Stable 16-char hex span id (may be null for spanless events). */
  spanId: string | null;
  parentSpanId: string | null;
  /** Original entry idx. */
  entryIdx: number;
  /** Parsed start timestamp in ms. null in index mode or when timestamp missing. */
  startMs: number | null;
  durationMs: number | null;
  kind: string;
  lane: PlaybackLaneId;
  status: string | null;
  /**
   * True when duration_ms is null and the span is active only because it IS
   * the current event (zero-width treatment for incomplete spans).
   */
  isZeroWidth: boolean;
}

// ── Playback frame ────────────────────────────────────────────────────────────

/** Derived state for a single playhead position. */
export interface PlaybackFrame<T extends PlaybackBaseEntry = PlaybackBaseEntry> {
  /**
   * The entry at (or immediately before) the playhead.
   * null when entries is empty or playhead is before the first event.
   */
  currentEvent: T | null;
  /** Index into the sorted entries array of currentEvent. -1 when null. */
  currentIndex: number;
  /** All entries at or before the playhead (visible "prefix"). */
  prefix: T[];
  /** Spans whose active interval contains the playhead. */
  activeSpans: ActiveSpan[];
  /** prefix.length (convenience alias). */
  prefixCount: number;
  /**
   * Elapsed time from first event to playhead in ms.
   * null in index mode or when first event has no valid timestamp.
   */
  elapsedMs: number | null;
  /** Playback domain: "time" when timestamps are reliable, "index" as fallback. */
  mode: "time" | "index";
  /** All markers derived from the full sorted entry list. */
  markers: PlaybackMarker[];
  /**
   * Index into markers[] of the last marker at-or-before the playhead.
   * -1 when no such marker exists.
   */
  currentMarkerIndex: number;
}

// ── Aggregation / binning ─────────────────────────────────────────────────────

/** A time or count bin for overview rendering of large traces. */
export interface PlaybackBin {
  /** Bin start time in ms (or start index in index mode). */
  start: number;
  /** Bin end time in ms (or end index in index mode, exclusive). */
  end: number;
  /** Number of events in this bin. */
  count: number;
  /** Number of events with status=error or kind=error in this bin. */
  errorCount: number;
  /** Unique set of kinds seen in this bin (max 8 to bound memory). */
  kinds: string[];
  /** Unique set of lanes seen in this bin. */
  lanes: PlaybackLaneId[];
}

// ── Internal helpers ──────────────────────────────────────────────────────────

const INVALID_TIMESTAMP_THRESHOLD = 0.05; // 5%

function parseTimestampMs(ts: string | null): number | null {
  if (!ts) return null;
  const ms = Date.parse(ts);
  return Number.isFinite(ms) ? ms : null;
}

function collectKinds(kinds: Set<string>, kind: string): void {
  if (kinds.size < 8) kinds.add(kind);
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Normalize and sort entries into a stable playback order.
 *
 * Sort criteria (time mode): ascending timestampMs, with idx as stable
 * tiebreaker. Index-domain fallback triggers when the fraction of entries
 * missing valid timestamps exceeds 5% (minimum 2 entries must be invalid).
 *
 * @returns Object with normalized entries and the resolved playback mode.
 */
export function normalizeAndSortEntries<T extends PlaybackBaseEntry>(
  entries: T[]
): { normalized: NormalizedEntry<T>[]; mode: "time" | "index" } {
  if (entries.length === 0) return { normalized: [], mode: "time" };

  // Parse timestamps
  const withTs = entries.map((entry) => ({
    entry,
    timestampMs: parseTimestampMs(entry.timestamp),
  }));

  // Count invalid timestamps
  const invalidCount = withTs.filter((e) => e.timestampMs === null).length;
  const total = entries.length;

  // Determine mode: fallback to index when > threshold are missing
  // At least 2 entries must be invalid to avoid triggering on single-entry traces
  const fraction = invalidCount / total;
  const useIndexMode = invalidCount >= 2 && fraction > INVALID_TIMESTAMP_THRESHOLD;

  if (useIndexMode) {
    // Index mode: sort by idx, sortKey = idx
    const normalized: NormalizedEntry<T>[] = withTs
      .slice()
      .sort((a, b) => a.entry.idx - b.entry.idx)
      .map((e) => ({
        entry: e.entry,
        timestampMs: e.timestampMs,
        sortKey: e.entry.idx,
      }));
    return { normalized, mode: "index" };
  }

  // Time mode: sort by timestampMs ASC, then idx as tiebreaker
  // Entries with null timestamp are sorted last (treated as infinity)
  const normalized: NormalizedEntry<T>[] = withTs
    .slice()
    .sort((a, b) => {
      const tA = a.timestampMs ?? Number.MAX_SAFE_INTEGER;
      const tB = b.timestampMs ?? Number.MAX_SAFE_INTEGER;
      if (tA !== tB) return tA - tB;
      return a.entry.idx - b.entry.idx;
    })
    .map((e) => ({
      entry: e.entry,
      timestampMs: e.timestampMs,
      sortKey: e.timestampMs ?? Number.MAX_SAFE_INTEGER,
    }));

  return { normalized, mode: "time" };
}

/**
 * Derive navigation markers from a sorted normalized entry list.
 *
 * Markers are emitted for:
 *   - turn_start: kind === "turn_start"
 *   - agent_response: kind === "agent_response"
 *   - error: kind === "error" or status === "error"
 *   - compaction: kind === "generic" with compaction heuristic (full entries
 *     check attrs.compaction_kind; skeleton entries have no attrs so
 *     compaction markers are omitted unless the renderer supplies extra info)
 *
 * Full entries also emit "compaction" markers when attrs.compaction_kind is
 * present. Skeleton entries cannot emit compaction markers — this is a known
 * limitation of the skeleton projection.
 */
export function deriveMarkersFromEntries<T extends PlaybackBaseEntry>(
  normalized: NormalizedEntry<T>[]
): PlaybackMarker[] {
  const markers: PlaybackMarker[] = [];

  for (let i = 0; i < normalized.length; i++) {
    const { entry, timestampMs } = normalized[i];
    const kind = entry.kind;
    const status = entry.status;

    let markerKind: PlaybackMarkerKind | null = null;

    if (kind === "turn_start") {
      markerKind = "turn_start";
    } else if (kind === "agent_response") {
      markerKind = "agent_response";
    } else if (kind === "error" || status === "error") {
      markerKind = "error";
    } else if (isFull(entry)) {
      // Full entry: check for compaction attrs
      const full = entry as BrowseDebugEntry;
      const attrs = full.attrs as Record<string, unknown> | null;
      if (attrs && typeof attrs.compaction_kind === "string" && attrs.compaction_kind.length > 0) {
        markerKind = "compaction";
      }
    }

    if (markerKind !== null) {
      markers.push({
        sortedIndex: i,
        entryIdx: entry.idx,
        kind: markerKind,
        timestamp: entry.timestamp,
        timestampMs,
      });
    }
  }

  return markers;
}

/**
 * Navigate to the next marker index. Returns markers.length - 1 when already
 * at or past the last marker, and 0 when markers is empty.
 */
export function nextMarkerIndex(markers: PlaybackMarker[], currentMarkerIndex: number): number {
  if (markers.length === 0) return 0;
  return Math.min(currentMarkerIndex + 1, markers.length - 1);
}

/**
 * Navigate to the previous marker index. Returns 0 when at or before the
 * first marker, and 0 when markers is empty.
 */
export function prevMarkerIndex(markers: PlaybackMarker[], currentMarkerIndex: number): number {
  if (markers.length === 0) return 0;
  return Math.max(currentMarkerIndex - 1, 0);
}

/**
 * Resolve the marker index for a given playhead sort-key (time in ms or index).
 * Returns the index of the last marker whose sortKey <= playhead, or -1 if none.
 */
export function resolveMarkerIndex(
  markers: PlaybackMarker[],
  normalizedEntries: NormalizedEntry[],
  playhead: number
): number {
  let result = -1;
  for (let i = 0; i < markers.length; i++) {
    const sortedIdx = markers[i].sortedIndex;
    if (sortedIdx < normalizedEntries.length) {
      const key = normalizedEntries[sortedIdx].sortKey;
      if (key <= playhead) result = i;
      else break;
    }
  }
  return result;
}

/**
 * Derive the complete playback frame for a given cursor (playhead).
 *
 * @param entries  Raw entries (either BrowseDebugEntry[] or BrowseDebugSkeletonEntry[]).
 * @param cursor   Playhead value. In time mode: ms since epoch. In index mode: 0-based index into sorted entries. Pass Infinity to see all events.
 * @param forceMode  Optional override to force "time" or "index" domain regardless of timestamp validity.
 * @param exactSortedIndex  Optional exact sorted-array index to use as currentIndex in time mode.
 *   Pass this when the playhead is on a null-timestamp entry in time mode so the correct entry
 *   is selected, rather than over-scanning because all null entries share the same MAX_SAFE_INTEGER
 *   sort key (all ≤ Infinity). Has no effect in index mode (index mode always derives from cursor).
 *
 * The function is pure and referentially transparent — calling with the same
 * inputs always returns an equivalent frame.
 */
export function derivePlaybackFrame<T extends PlaybackBaseEntry>(
  entries: T[],
  cursor: number,
  forceMode?: "time" | "index",
  exactSortedIndex?: number
): PlaybackFrame<T> {
  const { normalized, mode: detectedMode } = normalizeAndSortEntries(entries);
  const mode = forceMode ?? detectedMode;

  const markers = deriveMarkersFromEntries(normalized);

  if (normalized.length === 0) {
    return {
      currentEvent: null,
      currentIndex: -1,
      prefix: [],
      activeSpans: [],
      prefixCount: 0,
      elapsedMs: null,
      mode,
      markers,
      currentMarkerIndex: -1,
    };
  }

  // Find the last entry at-or-before the cursor.
  // In time mode, exactSortedIndex overrides the scan when the playhead lands on a
  // null-timestamp entry: all null entries share sortKey=MAX_SAFE_INTEGER, so an
  // Infinity cursor would scan past the first null entry and select the last one.
  let currentIndex = -1;
  if (mode === "time" && exactSortedIndex !== undefined) {
    currentIndex = Math.min(Math.max(exactSortedIndex, 0), normalized.length - 1);
  } else {
    for (let i = 0; i < normalized.length; i++) {
      const key =
        mode === "index"
          ? normalized[i].entry.idx
          : (normalized[i].timestampMs ?? Number.MAX_SAFE_INTEGER);
      if (key <= cursor) {
        currentIndex = i;
      } else {
        break;
      }
    }
  }

  // In index mode, cursor IS the 0-based sorted index
  if (mode === "index") {
    currentIndex = Math.min(Math.floor(cursor), normalized.length - 1);
    if (cursor < 0) currentIndex = -1;
  }

  const currentEvent = currentIndex >= 0 ? normalized[currentIndex].entry : null;
  const prefix = currentIndex >= 0 ? normalized.slice(0, currentIndex + 1).map((n) => n.entry) : [];

  // Elapsed time: ms from first valid timestamp to playhead
  let elapsedMs: number | null = null;
  if (mode === "time" && currentEvent !== null) {
    const firstMs = normalized[0].timestampMs;
    const curMs = normalized[currentIndex].timestampMs;
    if (firstMs !== null && curMs !== null) {
      elapsedMs = curMs - firstMs;
    }
  }

  // Playhead in ms (for span interval check)
  const playheadMs =
    mode === "time"
      ? cursor
      : currentIndex >= 0
        ? (normalized[currentIndex].timestampMs ?? null)
        : null;

  // Derive active spans
  const activeSpans: ActiveSpan[] = [];
  for (let i = 0; i <= (currentIndex < 0 ? -1 : currentIndex); i++) {
    const { entry, timestampMs: startMs } = normalized[i];
    if (entry.span_id === null && entry.duration_ms === null) continue;

    const lane = derivePlaybackLane(entry);
    let isActive = false;
    let isZeroWidth = false;

    if (entry.duration_ms !== null) {
      // Known duration: active when start <= playhead < start + duration
      if (mode === "time" && startMs !== null && playheadMs !== null) {
        isActive = startMs <= playheadMs && playheadMs < startMs + entry.duration_ms;
      } else if (mode === "index") {
        // In index mode, treat "active" as: this entry's index == currentIndex
        // (duration-based active interval doesn't apply in index domain)
        isActive = i === currentIndex;
        isZeroWidth = false;
      }
    } else {
      // Unknown duration: active only when this IS the current event
      isActive = i === currentIndex;
      isZeroWidth = true;
    }

    if (isActive) {
      activeSpans.push({
        spanId: entry.span_id,
        parentSpanId: entry.parent_span_id,
        entryIdx: entry.idx,
        startMs,
        durationMs: entry.duration_ms,
        kind: entry.kind,
        lane,
        status: entry.status,
        isZeroWidth,
      });
    }
  }

  // Resolve current marker index.
  // In index mode the cursor is a 0-based sorted position, but resolveMarkerIndex
  // compares against sortKey (= entry.idx, potentially with gaps). Pass the
  // sortKey of the current entry so both sides are in the same idx domain.
  const markerPlayhead =
    mode === "index" ? (currentIndex >= 0 ? normalized[currentIndex].sortKey : -1) : cursor;
  const currentMarkerIndex = resolveMarkerIndex(markers, normalized, markerPlayhead);

  return {
    currentEvent,
    currentIndex,
    prefix,
    activeSpans,
    prefixCount: prefix.length,
    elapsedMs,
    mode,
    markers,
    currentMarkerIndex,
  };
}

// ── Aggregation helpers ───────────────────────────────────────────────────────

/**
 * Bin events by fixed time width (ms). For overview/scrubber rendering of large traces.
 * Pure — does not modify inputs. Only usable in time mode (returns empty array in index mode).
 *
 * @param normalized  Sorted normalized entries (from normalizeAndSortEntries).
 * @param binWidthMs  Width of each bin in milliseconds. Must be > 0.
 * @returns Array of bins sorted by start time. Empty when no valid timestamps.
 */
export function binEntriesByTime<T extends PlaybackBaseEntry>(
  normalized: NormalizedEntry<T>[],
  binWidthMs: number
): PlaybackBin[] {
  if (normalized.length === 0 || binWidthMs <= 0) return [];

  const firstMs = normalized[0].timestampMs;
  if (firstMs === null) return [];

  const bins = new Map<number, PlaybackBin>();

  for (const { entry, timestampMs } of normalized) {
    if (timestampMs === null) continue;
    const binStart = Math.floor((timestampMs - firstMs) / binWidthMs) * binWidthMs + firstMs;
    let bin = bins.get(binStart);
    if (!bin) {
      bin = {
        start: binStart,
        end: binStart + binWidthMs,
        count: 0,
        errorCount: 0,
        kinds: [],
        lanes: [],
      };
      bins.set(binStart, bin);
    }
    bin.count++;
    if (entry.kind === "error" || entry.status === "error") bin.errorCount++;
    const kindsSet = new Set(bin.kinds);
    collectKinds(kindsSet, entry.kind);
    bin.kinds = Array.from(kindsSet);
    const lanesSet = new Set(bin.lanes);
    lanesSet.add(derivePlaybackLane(entry));
    bin.lanes = Array.from(lanesSet);
  }

  return Array.from(bins.values()).sort((a, b) => a.start - b.start);
}

/**
 * Bin events by count (fixed number of events per bin). For proportional
 * overview rendering when timestamps are sparse or in index mode.
 *
 * @param normalized  Sorted normalized entries.
 * @param binSize     Number of events per bin. Must be > 0.
 * @returns Array of bins sorted by start index.
 */
export function binEntriesByCount<T extends PlaybackBaseEntry>(
  normalized: NormalizedEntry<T>[],
  binSize: number
): PlaybackBin[] {
  if (normalized.length === 0 || binSize <= 0) return [];

  const result: PlaybackBin[] = [];
  for (let i = 0; i < normalized.length; i += binSize) {
    const slice = normalized.slice(i, i + binSize);
    const startKey = slice[0].sortKey;
    const endKey = (slice[slice.length - 1].sortKey ?? startKey) + 1;
    let count = 0;
    let errorCount = 0;
    const kindsSet = new Set<string>();
    const lanesSet = new Set<PlaybackLaneId>();

    for (const { entry } of slice) {
      count++;
      if (entry.kind === "error" || entry.status === "error") errorCount++;
      collectKinds(kindsSet, entry.kind);
      lanesSet.add(derivePlaybackLane(entry));
    }

    result.push({
      start: startKey,
      end: endKey,
      count,
      errorCount,
      kinds: Array.from(kindsSet),
      lanes: Array.from(lanesSet),
    });
  }

  return result;
}

/**
 * Return the sorted entry indices for a given display lane.
 * Useful for lane-filtered rendering (only show events in one row).
 */
export function filterByLane<T extends PlaybackBaseEntry>(
  normalized: NormalizedEntry<T>[],
  lane: PlaybackLaneId
): NormalizedEntry<T>[] {
  return normalized.filter((n) => derivePlaybackLane(n.entry) === lane);
}
