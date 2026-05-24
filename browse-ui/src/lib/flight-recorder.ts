/**
 * Flight Recorder v3 — pure, DOM-free model helpers.
 *
 * Synthesis contract: docs §4c. Helpers must:
 *   - Stay DOM-free and deterministic.
 *   - Read only `BrowseDebugSkeletonEntry`-shaped fields (`PlaybackBaseEntry`)
 *     or the safe outputs of `deriveNodeRender(...)` for full entries.
 *   - Never include free-text from `userMessage` or raw `attrs.message`.
 *   - Clamp `duration_ms` to ≥0 and finite (non-finite ⇒ `null`).
 *   - Reject unknown attr keys (default-deny — we never read `attrs` directly here).
 */
import type {
  BrowseCheckpointSummary,
  BrowseDebugEntry,
  BrowseRewindSnapshotSummary,
  SubagentActivityEntry,
  SubagentActivityResponse,
  SubagentErrorCategory,
  SubagentInternalsEntry,
  SubagentInternalsResponse,
} from "@/lib/api/types";
import {
  normalizeAndSortEntries,
  type NormalizedEntry,
  type PlaybackBaseEntry,
} from "@/lib/debug-event-playback";
import { deriveNodeRender, type FlowNodeCategory } from "@/lib/debug-span-flow";

// ── Public types ──────────────────────────────────────────────────────────────

export type ChapterMode = "checkpoint" | "turn" | "single";
export type ChapterStatus = "ok" | "error" | "cancelled" | "incomplete";

export interface Chapter {
  /** Stable id used for keys and selectors (`chapter-rail-chapter-<seq>` etc). */
  id: string;
  mode: ChapterMode;
  label: string;
  status: ChapterStatus;
  /** Inclusive index into the normalized sorted entries array. */
  startSortedIndex: number;
  /** Inclusive index into the normalized sorted entries array. */
  endSortedIndex: number;
  /** ms since epoch for the first event in the chapter. null when unknown. */
  startMs: number | null;
  /** Clamped, finite, non-negative duration in ms. null when unknown. */
  durationMs: number | null;
  /** Count of normalized entries within the chapter's inclusive range. */
  eventCount: number;
  /** Checkpoint seq when `mode === "checkpoint"`. */
  checkpointSeq?: number;
  /** Redaction-safe checkpoint title when `mode === "checkpoint"`. */
  checkpointTitle?: string;
  /** Sorted indexes of task_complete events whose start sortedIndex falls in the span. */
  taskCompleteSortedIndexes: number[];
}

export interface HotspotError {
  sortedIndex: number;
  entryIdx: number;
  timestamp: string | null;
  timestampMs: number | null;
}

export interface HotspotSlow {
  sortedIndex: number;
  entryIdx: number;
  durationMs: number;
}

export interface HotspotGap {
  startSortedIndex: number;
  endSortedIndex: number;
  startMs: number;
  endMs: number;
  gapMs: number;
}

export interface Hotspots {
  errors: HotspotError[];
  slowest: HotspotSlow[];
  gaps: HotspotGap[];
}

export interface MissionChip {
  /** Safe label sourced from `deriveNodeRender(entry).label`. */
  name: string;
  count: number;
}

export interface MissionRollup {
  tools: MissionChip[];
  hooks: MissionChip[];
  skills: MissionChip[];
  subagents: MissionChip[];
  /** Count only — per security §4f, never the raw `compaction_kind`. */
  compactions: number;
  errorCount: number;
}

export interface ResumeAnchor {
  snapshotId: string;
  timestamp: string | null;
  timestampMs: number | null;
  gitCommit: string | null;
  gitBranch: string | null;
  fileCount: number;
  /** Presence flag only — raw userMessage text is never on the wire. */
  userMessagePresent: boolean;
  userMessageByteSize: number;
  eventSpanId: string | null;
  /** Entry idx of the associated task_complete event (span-id match preferred). */
  taskCompleteEntryIdx: number | null;
  /** span_id of the associated task_complete event. */
  taskCompleteSpanId: string | null;
  /**
   * How the resume anchor was associated to a task_complete event:
   *   - "exact":       event_span_id matched a task_complete span_id.
   *   - "nearest":     no span match; nearest task_complete by timestamp.
   *   - "unavailable": no association possible (no spans, no timestamps).
   * Used by the drawer to render a label and gate Jump.
   */
  association: "exact" | "nearest" | "unavailable";
  /**
   * For "nearest" associations, the millisecond gap between the snapshot
   * timestamp and the chosen task_complete event. null otherwise.
   */
  associationDistanceMs: number | null;
}

// ── Tunables ──────────────────────────────────────────────────────────────────

export const MAX_HOTSPOT_ERRORS = 50;
export const MAX_HOTSPOT_SLOWEST = 5;
export const MAX_HOTSPOT_GAPS = 5;
/** Minimum inter-event interval (ms) considered a "long gap". */
export const HOTSPOT_GAP_THRESHOLD_MS = 60_000;

// ── Internal helpers ──────────────────────────────────────────────────────────

/**
 * Clamp a raw `duration_ms` value:
 *   - `null`/`undefined`/non-number ⇒ `null`
 *   - `NaN`/`Infinity`/`-Infinity` ⇒ `null`
 *   - negative finite ⇒ `0`
 *   - otherwise ⇒ unchanged
 */
export function clampDurationMs(d: number | null | undefined): number | null {
  if (typeof d !== "number") return null;
  if (!Number.isFinite(d)) return null;
  return d < 0 ? 0 : d;
}

function parseMs(ts: string | null): number | null {
  if (typeof ts !== "string" || ts.length === 0) return null;
  const v = Date.parse(ts);
  return Number.isFinite(v) ? v : null;
}

function isError<T extends PlaybackBaseEntry>(e: T): boolean {
  return e.kind === "error" || e.status === "error";
}

function isCancelled<T extends PlaybackBaseEntry>(e: T): boolean {
  return e.status === "cancelled";
}

function isTerminal<T extends PlaybackBaseEntry>(e: T): boolean {
  const s = e.status;
  return s === "ok" || s === "error" || s === "cancelled";
}

/**
 * Build a map from `entry.idx` → sortedIndex in the normalized list.
 * Stable for any normalized output of `normalizeAndSortEntries`.
 */
function buildIdxToSorted<T extends PlaybackBaseEntry>(
  normalized: NormalizedEntry<T>[]
): Map<number, number> {
  const m = new Map<number, number>();
  for (let i = 0; i < normalized.length; i++) {
    m.set(normalized[i].entry.idx, i);
  }
  return m;
}

/** Safe sanitized leaf label — strips control chars and clamps length. */
function safeLabel(s: string | undefined | null, fallback: string): string {
  if (typeof s !== "string") return fallback;
  const clean = s.replace(/[\u0000-\u001f\u007f]/g, "").trim();
  if (clean.length === 0) return fallback;
  return clean.length > 120 ? clean.slice(0, 120) : clean;
}

// ── deriveChapters ────────────────────────────────────────────────────────────

export interface DeriveChaptersOptions<T extends PlaybackBaseEntry = PlaybackBaseEntry> {
  checkpoints?: BrowseCheckpointSummary[];
  /** Sub-list of entries previously identified by the caller as task_complete markers. */
  taskCompletes: T[];
  entries: T[];
}

/**
 * Derive chapter rail data with three precedence modes:
 *   1. "checkpoint" — at least one checkpoint summary AND a mappable boundary.
 *   2. "turn"       — fallback when `turn_start` entries are present.
 *   3. "single"     — fallback when no markers exist at all.
 */
export function deriveChapters<T extends PlaybackBaseEntry>(
  opts: DeriveChaptersOptions<T>
): Chapter[] {
  const { entries, taskCompletes, checkpoints } = opts;
  const { normalized } = normalizeAndSortEntries(entries);
  const total = normalized.length;
  if (total === 0) return [];

  const idxToSorted = buildIdxToSorted(normalized);

  // Collect sorted-index list of task_complete markers (dedup, in order).
  const taskCompleteSorted: number[] = [];
  for (const tc of taskCompletes) {
    const s = idxToSorted.get(tc.idx);
    if (typeof s === "number") taskCompleteSorted.push(s);
  }
  taskCompleteSorted.sort((a, b) => a - b);

  const hasCheckpoints =
    Array.isArray(checkpoints) &&
    checkpoints.length > 0 &&
    // Gate the checkpoint mode on at least one parseable, in-range mtime.
    // Without timestamps we cannot honestly anchor checkpoint boundaries to
    // events (the previous even-tiling was the P1 lie); fall back to turn /
    // single in that case.
    canAnchorCheckpoints(checkpoints, normalized);

  if (hasCheckpoints) {
    const cpChapters = buildCheckpointChapters(checkpoints!, normalized, taskCompleteSorted);
    // `buildCheckpointChapters` filters out checkpoints whose mtime sits
    // outside the visible event window; if nothing usable remains, fall
    // through to turn/single mode rather than emit a deceptive chapter.
    if (cpChapters.length > 0) return cpChapters;
  }

  // Locate turn_start markers.
  const turnSorted: number[] = [];
  for (let i = 0; i < normalized.length; i++) {
    if (normalized[i].entry.kind === "turn_start") turnSorted.push(i);
  }

  if (turnSorted.length > 0) {
    return buildTurnChapters(normalized, turnSorted, taskCompleteSorted);
  }

  return [buildSingleChapter(normalized, taskCompleteSorted)];
}

function canAnchorCheckpoints<T extends PlaybackBaseEntry>(
  checkpoints: BrowseCheckpointSummary[],
  normalized: NormalizedEntry<T>[]
): boolean {
  if (normalized.length === 0) return false;
  const firstMs = normalized[0].timestampMs;
  const lastMs = normalized[normalized.length - 1].timestampMs;
  if (firstMs === null || lastMs === null) return false;
  for (const cp of checkpoints) {
    const ms = parseMs(cp.mtime_iso ?? null);
    if (ms === null) continue;
    // At least one checkpoint must fall within the event window; otherwise
    // anchoring would produce degenerate boundaries.
    if (ms >= firstMs && ms <= lastMs) return true;
  }
  return false;
}

function buildCheckpointChapters<T extends PlaybackBaseEntry>(
  checkpoints: BrowseCheckpointSummary[],
  normalized: NormalizedEntry<T>[],
  taskCompleteSorted: number[]
): Chapter[] {
  const total = normalized.length;
  if (total === 0) return [];

  // Determine the visible event window using the first and last *valid*
  // (non-null) entry timestamps. Checkpoints whose mtime falls outside this
  // window cannot honestly claim a visible event span — a pre-window
  // checkpoint would otherwise grab the first event (raw=-1 ⇒ end=0) and a
  // post-window checkpoint would absorb trailing events via the final
  // clamp to `total - 1`. Filter them out so only in-window checkpoints
  // anchor chapters; the caller falls back to turn/single mode if nothing
  // remains.
  let firstValidMs: number | null = null;
  let lastValidMs: number | null = null;
  for (const n of normalized) {
    if (n.timestampMs !== null) {
      if (firstValidMs === null) firstValidMs = n.timestampMs;
      lastValidMs = n.timestampMs;
    }
  }
  if (firstValidMs === null || lastValidMs === null) return [];

  // Sort checkpoints by mtime so boundaries are monotonic. Checkpoints
  // without a parseable mtime or with an mtime outside the visible event
  // window are dropped — they cannot truthfully anchor a chapter onto a
  // visible event span.
  const annotated = checkpoints
    .map((cp, originalIdx) => ({ cp, originalIdx, ms: parseMs(cp.mtime_iso ?? null) }))
    .filter(
      (x): x is { cp: BrowseCheckpointSummary; originalIdx: number; ms: number } =>
        x.ms !== null && x.ms >= (firstValidMs as number) && x.ms <= (lastValidMs as number)
    );
  if (annotated.length === 0) return [];
  annotated.sort((a, b) => a.ms - b.ms || a.originalIdx - b.originalIdx);

  // Map each checkpoint mtime to the sortedIndex of the latest event whose
  // timestamp is ≤ mtime. The checkpoint covers events up to and including
  // that point (it was written AFTER those events). Use binary search.
  const tsArr = normalized.map((n) => n.timestampMs);

  const upperBoundIdx = (ms: number): number => {
    // Largest i such that tsArr[i] !== null && tsArr[i] <= ms.
    // Skip null timestamps in the comparison by treating them as -Infinity.
    let lo = 0;
    let hi = tsArr.length - 1;
    let best = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >>> 1;
      const t = tsArr[mid];
      if (t !== null && t <= ms) {
        best = mid;
        lo = mid + 1;
      } else if (t === null) {
        // Step both directions; cheapest path is to scan right.
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return best;
  };

  // Compute each checkpoint's "end" sortedIndex from its mtime, with a
  // monotonic clamp so a later checkpoint never ends before an earlier one.
  const ends: number[] = [];
  let prevEnd = -1;
  for (let i = 0; i < annotated.length; i++) {
    const raw = upperBoundIdx(annotated[i].ms);
    // If the mtime is before any event we have, place this checkpoint at the
    // start. If it's after all events, clamp to the final index. Then enforce
    // monotonicity vs. the previous checkpoint's end.
    let end: number;
    if (raw < 0) {
      end = Math.max(prevEnd + 1, 0);
    } else {
      end = Math.max(raw, prevEnd + 1);
    }
    if (end > total - 1) end = total - 1;
    ends.push(end);
    prevEnd = end;
  }

  // Force the final chapter to cover through the last event so no events are
  // orphaned past the last checkpoint.
  if (ends.length > 0) ends[ends.length - 1] = total - 1;

  // Build chapter spans: [start, end] where start = prevEnd + 1 (or 0 first).
  const out: Chapter[] = [];
  let start = 0;
  for (let i = 0; i < annotated.length; i++) {
    const end = ends[i];
    if (end < start) {
      // Degenerate / overlapping: skip emitting an empty chapter — but keep
      // start pinned so the next chapter still picks up correctly.
      continue;
    }
    const cp = annotated[i].cp;
    const seq = Number.isFinite(cp.seq) ? cp.seq : annotated[i].originalIdx + 1;
    const ticks = taskCompleteSorted.filter((t) => t >= start && t <= end);
    out.push(
      finalizeChapter(normalized, start, end, {
        mode: "checkpoint",
        label: safeLabel(cp.title, `Chapter ${seq}`),
        taskCompleteSortedIndexes: ticks,
        checkpointSeq: seq,
        checkpointTitle: safeLabel(cp.title, `Chapter ${seq}`),
      })
    );
    start = end + 1;
  }
  return out;
}

function buildTurnChapters<T extends PlaybackBaseEntry>(
  normalized: NormalizedEntry<T>[],
  turnSorted: number[],
  taskCompleteSorted: number[]
): Chapter[] {
  const total = normalized.length;
  const out: Chapter[] = [];
  // Pre-roll: events before the first turn_start become "Turn 0" when present.
  if (turnSorted[0] > 0) {
    const s = 0;
    const e = turnSorted[0] - 1;
    const ticks = taskCompleteSorted.filter((t) => t >= s && t <= e);
    out.push(
      finalizeChapter(normalized, s, e, {
        mode: "turn",
        label: "Turn 0",
        taskCompleteSortedIndexes: ticks,
      })
    );
  }
  for (let i = 0; i < turnSorted.length; i++) {
    const s = turnSorted[i];
    const e = i + 1 < turnSorted.length ? turnSorted[i + 1] - 1 : total - 1;
    const ticks = taskCompleteSorted.filter((t) => t >= s && t <= e);
    out.push(
      finalizeChapter(normalized, s, e, {
        mode: "turn",
        label: `Turn ${i + 1}`,
        taskCompleteSortedIndexes: ticks,
      })
    );
  }
  return out;
}

function buildSingleChapter<T extends PlaybackBaseEntry>(
  normalized: NormalizedEntry<T>[],
  taskCompleteSorted: number[]
): Chapter {
  return finalizeChapter(normalized, 0, normalized.length - 1, {
    mode: "single",
    label: "Session",
    taskCompleteSortedIndexes: taskCompleteSorted.slice(),
  });
}

interface FinalizeChapterOpts {
  mode: ChapterMode;
  label: string;
  taskCompleteSortedIndexes: number[];
  checkpointSeq?: number;
  checkpointTitle?: string;
}

function finalizeChapter<T extends PlaybackBaseEntry>(
  normalized: NormalizedEntry<T>[],
  startSortedIndex: number,
  endSortedIndex: number,
  opts: FinalizeChapterOpts
): Chapter {
  const { mode, label, taskCompleteSortedIndexes, checkpointSeq, checkpointTitle } = opts;
  const safeStart = Math.max(0, Math.min(startSortedIndex, normalized.length - 1));
  const safeEnd = Math.max(safeStart, Math.min(endSortedIndex, normalized.length - 1));

  let status: ChapterStatus = "incomplete";
  let firstMs: number | null = null;
  let lastMs: number | null = null;
  let sawError = false;
  let sawCancelled = false;
  let lastTerminal = false;

  for (let i = safeStart; i <= safeEnd; i++) {
    const ne = normalized[i];
    const entry = ne.entry;
    if (firstMs === null && ne.timestampMs !== null) firstMs = ne.timestampMs;
    if (ne.timestampMs !== null) lastMs = ne.timestampMs;
    if (isError(entry)) sawError = true;
    if (isCancelled(entry)) sawCancelled = true;
    if (i === safeEnd) lastTerminal = isTerminal(entry);
  }

  if (sawError) status = "error";
  else if (sawCancelled) status = "cancelled";
  else if (lastTerminal) status = "ok";
  else status = "incomplete";

  let durationMs: number | null = null;
  if (firstMs !== null && lastMs !== null) {
    durationMs = clampDurationMs(lastMs - firstMs);
  }

  const id =
    mode === "checkpoint" && typeof checkpointSeq === "number"
      ? `chapter-${mode}-${checkpointSeq}`
      : `chapter-${mode}-${safeStart}-${safeEnd}`;

  const chapter: Chapter = {
    id,
    mode,
    label,
    status,
    startSortedIndex: safeStart,
    endSortedIndex: safeEnd,
    startMs: firstMs,
    durationMs,
    eventCount: safeEnd - safeStart + 1,
    taskCompleteSortedIndexes: taskCompleteSortedIndexes.slice().sort((a, b) => a - b),
  };
  if (mode === "checkpoint") {
    if (typeof checkpointSeq === "number") chapter.checkpointSeq = checkpointSeq;
    if (typeof checkpointTitle === "string") chapter.checkpointTitle = checkpointTitle;
  }
  return chapter;
}

// ── deriveHotspots ────────────────────────────────────────────────────────────

/**
 * Errors / slowest / gaps. All stable-sorted; entries with invalid timestamps
 * are excluded from `gaps` (gap detection requires both endpoints valid).
 */
export function deriveHotspots<T extends PlaybackBaseEntry>(entries: T[]): Hotspots {
  const { normalized } = normalizeAndSortEntries(entries);

  // Errors: stable by sortedIndex asc.
  const errors: HotspotError[] = [];
  for (let i = 0; i < normalized.length; i++) {
    const e = normalized[i].entry;
    if (isError(e)) {
      errors.push({
        sortedIndex: i,
        entryIdx: e.idx,
        timestamp: e.timestamp,
        timestampMs: normalized[i].timestampMs,
      });
      if (errors.length >= MAX_HOTSPOT_ERRORS) break;
    }
  }

  // Slowest: finite, non-negative durations; desc by duration, idx asc tiebreak.
  const candidates: HotspotSlow[] = [];
  for (let i = 0; i < normalized.length; i++) {
    const e = normalized[i].entry;
    const d = clampDurationMs(e.duration_ms);
    if (d === null) continue;
    candidates.push({ sortedIndex: i, entryIdx: e.idx, durationMs: d });
  }
  candidates.sort((a, b) => {
    if (b.durationMs !== a.durationMs) return b.durationMs - a.durationMs;
    return a.entryIdx - b.entryIdx;
  });
  const slowest = candidates.slice(0, MAX_HOTSPOT_SLOWEST);

  // Gaps: pairs of consecutive entries with valid timestamps and diff > threshold.
  const gapCandidates: HotspotGap[] = [];
  let prevValidIdx = -1;
  for (let i = 0; i < normalized.length; i++) {
    const t = normalized[i].timestampMs;
    if (t === null) continue;
    if (prevValidIdx >= 0) {
      const prevT = normalized[prevValidIdx].timestampMs;
      // prevT is non-null by construction.
      if (typeof prevT === "number") {
        const gap = t - prevT;
        if (Number.isFinite(gap) && gap >= HOTSPOT_GAP_THRESHOLD_MS) {
          gapCandidates.push({
            startSortedIndex: prevValidIdx,
            endSortedIndex: i,
            startMs: prevT,
            endMs: t,
            gapMs: gap,
          });
        }
      }
    }
    prevValidIdx = i;
  }
  gapCandidates.sort((a, b) => {
    if (b.gapMs !== a.gapMs) return b.gapMs - a.gapMs;
    return a.startSortedIndex - b.startSortedIndex;
  });
  const gaps = gapCandidates.slice(0, MAX_HOTSPOT_GAPS);

  return { errors, slowest, gaps };
}

// ── deriveMissionRollup ───────────────────────────────────────────────────────

function bumpChip(map: Map<string, number>, key: string): void {
  map.set(key, (map.get(key) ?? 0) + 1);
}

function chipsFromMap(map: Map<string, number>): MissionChip[] {
  const out: MissionChip[] = [];
  for (const [name, count] of map.entries()) out.push({ name, count });
  out.sort((a, b) => {
    if (b.count !== a.count) return b.count - a.count;
    return a.name.localeCompare(b.name);
  });
  return out;
}

/**
 * Roll up full debug entries into mission-strip chips, sourced only from the
 * safe `deriveNodeRender(...)` projection. `compactions` is a count only
 * (the raw `compaction_kind` is intentionally never surfaced).
 */
export function deriveMissionRollup(entries: BrowseDebugEntry[]): MissionRollup {
  const tools = new Map<string, number>();
  const hooks = new Map<string, number>();
  const skills = new Map<string, number>();
  const subagents = new Map<string, number>();
  let compactions = 0;
  let errorCount = 0;

  for (const e of entries) {
    if (!e) continue;
    const render = deriveNodeRender(e);
    const category: FlowNodeCategory = render.category;
    const label = safeLabel(render.label, category);

    const isErrorEntry = category === "error" || render.status === "error";
    if (isErrorEntry) errorCount += 1;

    switch (category) {
      case "tool":
        bumpChip(tools, label);
        break;
      case "hook":
        bumpChip(hooks, label);
        break;
      case "skill":
        bumpChip(skills, label);
        break;
      case "subagent":
        bumpChip(subagents, label);
        break;
      case "compaction":
        compactions += 1;
        break;
      default:
        break;
    }
  }

  return {
    tools: chipsFromMap(tools),
    hooks: chipsFromMap(hooks),
    skills: chipsFromMap(skills),
    subagents: chipsFromMap(subagents),
    compactions,
    errorCount,
  };
}

// ── deriveResumeAnchors ───────────────────────────────────────────────────────

/**
 * Sort snapshots newest-first and attach the nearest task_complete boundary.
 * Association rules:
 *   1. Match by `event_span_id` ↔ `task_complete.span_id` when both present.
 *   2. Otherwise, nearest task_complete by timestamp (when both timestamps parse).
 *   3. Otherwise, leave null.
 *
 * Never echoes `userMessage` text; only the presence/byte-size flags pass through.
 */
export function deriveResumeAnchors<T extends PlaybackBaseEntry>(
  snapshots: BrowseRewindSnapshotSummary[],
  taskCompletes: T[]
): ResumeAnchor[] {
  // Build span_id → task_complete and a sorted timestamp list (with backing entry).
  const bySpan = new Map<string, T>();
  const tsList: Array<{ ms: number; entry: T }> = [];
  for (const tc of taskCompletes) {
    if (!tc) continue;
    if (typeof tc.span_id === "string" && tc.span_id.length > 0) {
      bySpan.set(tc.span_id, tc);
    }
    const ms = parseMs(tc.timestamp);
    if (ms !== null) tsList.push({ ms, entry: tc });
  }
  tsList.sort((a, b) => a.ms - b.ms);

  const nearestByTs = (ms: number): T | null => {
    if (tsList.length === 0) return null;
    // Linear scan is fine; resume anchor lists are small (≤500 cap server-side).
    let best: { d: number; entry: T } | null = null;
    for (const item of tsList) {
      const d = Math.abs(item.ms - ms);
      if (best === null || d < best.d) best = { d, entry: item.entry };
    }
    return best ? best.entry : null;
  };

  const anchors: ResumeAnchor[] = snapshots.map((snap) => {
    const timestampMs = parseMs(snap.timestamp ?? null);
    let assoc: T | null = null;
    let association: "exact" | "nearest" | "unavailable" = "unavailable";
    let associationDistanceMs: number | null = null;
    if (typeof snap.event_span_id === "string" && snap.event_span_id.length > 0) {
      const hit = bySpan.get(snap.event_span_id);
      if (hit) {
        assoc = hit;
        association = "exact";
      }
    }
    if (assoc === null && timestampMs !== null) {
      const near = nearestByTs(timestampMs);
      if (near !== null) {
        assoc = near;
        association = "nearest";
        const nearMs = parseMs(near.timestamp);
        associationDistanceMs = nearMs !== null ? Math.abs(timestampMs - nearMs) : null;
      }
    }
    return {
      snapshotId: snap.snapshot_id,
      timestamp: snap.timestamp,
      timestampMs,
      gitCommit: snap.git_commit,
      gitBranch: snap.git_branch,
      fileCount: Number.isFinite(snap.file_count) ? snap.file_count : 0,
      userMessagePresent: !!snap.user_message_present,
      userMessageByteSize: Number.isFinite(snap.user_message_byte_size)
        ? snap.user_message_byte_size
        : 0,
      eventSpanId: snap.event_span_id ?? null,
      taskCompleteEntryIdx: assoc ? assoc.idx : null,
      taskCompleteSpanId: assoc ? assoc.span_id : null,
      association,
      associationDistanceMs,
    };
  });

  // Sort desc by timestamp (newest first); stable secondary by snapshotId asc.
  anchors.sort((a, b) => {
    const ta = a.timestampMs;
    const tb = b.timestampMs;
    if (ta !== null && tb !== null && ta !== tb) return tb - ta;
    if (ta !== null && tb === null) return -1;
    if (ta === null && tb !== null) return 1;
    return a.snapshotId.localeCompare(b.snapshotId);
  });
  return anchors;
}

// ── Sub-agent Activity Model Helpers ──────────────────────────────────────────

/**
 * UI-friendly projection of a single `SubagentActivityEntry`.
 *
 * All nullable string fields default to `null` (never the string `"null"`).
 * `displayName` is always a non-empty string — it is the first non-empty of:
 *   agentDisplayName → agentName → "Sub-agent".
 */
export interface SubagentExecution {
  /** Stable identifier: `span_id` when available, else deterministic fallback. */
  id: string;
  /** Machine name of the agent (`agent_name` from the API). */
  agentName: string | null;
  /** Human-readable display name (`agent_display_name` from the API). */
  agentDisplayName: string | null;
  /**
   * Best available display label: `agentDisplayName` → `agentName` → "Sub-agent".
   * Always a non-empty string; never `"null"`.
   */
  displayName: string;
  /** Model identifier (null when unknown). */
  model: string | null;
  /** Execution outcome. */
  status: "running" | "completed" | "failed";
  /** Error category — non-null only when `status === "failed"`. */
  errorCategory: SubagentErrorCategory | null;
  /** ISO 8601 string when execution started (null when unknown). */
  startedAt: string | null;
  /** ISO 8601 string when execution ended (null when still running or unknown). */
  endedAt: string | null;
  /** `startedAt` parsed as ms since epoch (null when not parseable). */
  startedAtMs: number | null;
  /** `endedAt` parsed as ms since epoch (null when not parseable). */
  endedAtMs: number | null;
  /** Clamped finite non-negative duration in ms (null when not available). */
  durationMs: number | null;
  /** Total tool call count (null when unknown). */
  totalToolCalls: number | null;
  /** Total token count (null when unknown). */
  totalTokens: number | null;
  /**
   * Debug-log entry index of the `subagent.started` event.
   * Use for jump-to-event behavior in the debug log tab.
   */
  startIdx: number | null;
  /**
   * Debug-log entry index of the `subagent.completed` / `subagent.failed` event.
   * Use for jump-to-event behavior in the debug log tab.
   */
  endIdx: number | null;
  /** `true` when any string field was sanitised server-side. */
  redacted: boolean;
}

/**
 * Envelope-level metadata preserved from a `SubagentActivityResponse`
 * for cap/truncation display.  Never hides `truncated`.
 */
export interface SubagentActivitySummary {
  totalSeen: number;
  returned: number;
  cap: number;
  truncated: boolean;
  droppedPendingStarts: number;
}

/** Filter token for `filterSubagentExecutions`. */
export type SubagentFilter = "all" | "failed" | "running";

// ── Internal helpers for sub-agent model ─────────────────────────────────────

/**
 * Build a stable unique id for a sub-agent execution.
 * Preference order: span_id → start_idx → end_idx → positional fallback.
 */
function deriveSubagentId(entry: SubagentActivityEntry, index: number): string {
  if (entry.span_id !== null) return entry.span_id;
  if (entry.start_idx !== null) return `subagent-sidx-${entry.start_idx}`;
  if (entry.end_idx !== null) return `subagent-eidx-${entry.end_idx}`;
  return `subagent-pos-${index}`;
}

/**
 * Compute the best human-readable label for an agent entry.
 * Precedence: agent_display_name → agent_name → "Sub-agent".
 * Rejects strings that are empty after stripping control chars.
 */
function deriveSubagentDisplayName(entry: SubagentActivityEntry): string {
  const candidates = [entry.agent_display_name, entry.agent_name];
  for (const c of candidates) {
    if (typeof c !== "string") continue;
    const clean = c.replace(/[\u0000-\u001f\u007f]/g, "").trim();
    if (clean.length > 0) return clean.length > 120 ? clean.slice(0, 120) : clean;
  }
  return "Sub-agent";
}

// ── deriveSubagentExecutions ──────────────────────────────────────────────────

/**
 * Map a `SubagentActivityResponse` to an array of UI-friendly `SubagentExecution`
 * rows.  Empty/null/undefined input returns an empty array without throwing.
 *
 * Data source: the dedicated `/api/session/{id}/subagent-activity` route —
 * **not** paginated debug-log entries.
 */
export function deriveSubagentExecutions(
  response?: SubagentActivityResponse | null
): SubagentExecution[] {
  if (!response?.entries?.length) return [];

  return response.entries.map((entry, index): SubagentExecution => {
    const startedAtMs = parseMs(entry.started_at);
    const endedAtMs = parseMs(entry.ended_at);

    // Prefer server-provided duration_ms; fall back to wall-clock diff when both
    // timestamps are available and server value is absent.
    let durationMs: number | null = clampDurationMs(entry.duration_ms);
    if (durationMs === null && startedAtMs !== null && endedAtMs !== null) {
      durationMs = clampDurationMs(endedAtMs - startedAtMs);
    }

    const agentName = typeof entry.agent_name === "string" ? entry.agent_name || null : null;
    const agentDisplayName =
      typeof entry.agent_display_name === "string" ? entry.agent_display_name || null : null;

    return {
      id: deriveSubagentId(entry, index),
      agentName,
      agentDisplayName,
      displayName: deriveSubagentDisplayName(entry),
      model: typeof entry.model === "string" ? entry.model || null : null,
      status: entry.status,
      errorCategory: entry.error_category,
      startedAt: entry.started_at,
      endedAt: entry.ended_at,
      startedAtMs,
      endedAtMs,
      durationMs,
      totalToolCalls: entry.total_tool_calls,
      totalTokens: entry.total_tokens,
      startIdx: entry.start_idx,
      endIdx: entry.end_idx,
      redacted: entry.redacted,
    };
  });
}

// ── deriveSubagentActivitySummary ─────────────────────────────────────────────

/**
 * Extract envelope-level cap/truncation metadata from a
 * `SubagentActivityResponse`.  Returns zero-values on empty/null input.
 */
export function deriveSubagentActivitySummary(
  response?: SubagentActivityResponse | null
): SubagentActivitySummary {
  if (!response) {
    return { totalSeen: 0, returned: 0, cap: 0, truncated: false, droppedPendingStarts: 0 };
  }
  return {
    totalSeen: response.total_subagents_seen,
    returned: response.returned,
    cap: response.cap,
    truncated: response.truncated,
    droppedPendingStarts: response.dropped_pending_starts,
  };
}

// ── filterSubagentExecutions ──────────────────────────────────────────────────

/**
 * Filter a pre-derived `SubagentExecution[]` by outcome.
 *
 * - `"all"`     — returns the full array unchanged.
 * - `"failed"`  — only `status === "failed"`.
 * - `"running"` — only `status === "running"`.
 */
export function filterSubagentExecutions(
  executions: SubagentExecution[],
  filter: SubagentFilter = "all"
): SubagentExecution[] {
  if (filter === "all") return executions;
  return executions.filter((e) => e.status === filter);
}

// ── deriveSubagentChipsFromActivity ───────────────────────────────────────────

/**
 * Build `MissionChip[]` for the `MissionRollup.subagents` field from a
 * dedicated `SubagentActivityResponse` so labels use agent identity
 * (`displayName`) rather than raw event_type strings from debug-log entries.
 *
 * Safe to use in parallel with `deriveMissionRollup`; the caller decides
 * which source wins for the subagents array.
 */
export function deriveSubagentChipsFromActivity(
  response?: SubagentActivityResponse | null
): MissionChip[] {
  if (!response?.entries?.length) return [];
  const counts = new Map<string, number>();
  for (const entry of response.entries) {
    const label = deriveSubagentDisplayName(entry);
    bumpChip(counts, label);
  }
  return chipsFromMap(counts);
}

// ── Sub-agent Internals Model Helpers ─────────────────────────────────────────

export type SubagentInternalsBySpanId = Map<string, SubagentInternalsEntry>;

/**
 * Build a lookup from sub-agent span_id to internals row. Rows without span_id
 * are intentionally excluded because the UI cannot safely join them to an
 * activity execution.
 */
export function deriveSubagentInternalsBySpanId(
  response?: SubagentInternalsResponse | null
): SubagentInternalsBySpanId {
  const bySpan = new Map<string, SubagentInternalsEntry>();
  if (!response?.entries?.length) return bySpan;
  for (const entry of response.entries) {
    if (typeof entry.span_id === "string" && entry.span_id.length > 0) {
      bySpan.set(entry.span_id, entry);
    }
  }
  return bySpan;
}

export interface SubagentInternalsSummary {
  totalAgentsSeen: number;
  returned: number;
  cap: number;
  truncated: boolean;
  droppedPendingStarts: number;
  skillCorrelationSupported: boolean;
  uncorrelatedSkillInvocations: number;
  sessionSkillNames: string[];
}

export function deriveSubagentInternalsSummary(
  response?: SubagentInternalsResponse | null
): SubagentInternalsSummary {
  if (!response) {
    return {
      totalAgentsSeen: 0,
      returned: 0,
      cap: 0,
      truncated: false,
      droppedPendingStarts: 0,
      skillCorrelationSupported: false,
      uncorrelatedSkillInvocations: 0,
      sessionSkillNames: [],
    };
  }
  return {
    totalAgentsSeen: response.total_agents_seen,
    returned: response.returned,
    cap: response.cap,
    truncated: response.truncated,
    droppedPendingStarts: response.dropped_pending_starts,
    skillCorrelationSupported: response.skill_correlation_supported,
    uncorrelatedSkillInvocations: response.uncorrelated_skill_invocations,
    sessionSkillNames: response.session_skill_names,
  };
}
