/**
 * Pure aggregate model for the Flow Session Canvas.
 *
 * Derives a bounded, renderer-safe model from Mission Atlas + Subagent
 * Activity data. No raw entry content, paths, args, messages, or error
 * previews are included — only safe aggregate fields from Mission Atlas
 * and safe display labels from SubagentActivity.
 *
 * This module has zero React dependencies — fully unit-testable.
 */

import type {
  MissionAtlasLaneTotals,
  MissionAtlasNameCount,
  SessionMissionAtlasResponse,
  SubagentActivityResponse,
  SubagentInternalsResponse,
} from "@/lib/api/types";

// ── Lane display order ────────────────────────────────────────────────────────

/**
 * Ordered lane ids used in the Flow Session Canvas.
 * Matches the Mission Atlas lane taxonomy. Lanes with 0 total events
 * across the session are hidden by the renderer.
 */
export const FLOW_LANE_ORDER = [
  "turn",
  "model",
  "tool",
  "hook",
  "skill",
  "subagent",
  "error",
  "generic",
  "system",
] as const;

export type FlowLaneId = (typeof FLOW_LANE_ORDER)[number];

// ── Model types ───────────────────────────────────────────────────────────────

/** Safe per-bucket render model for one cell in the swimlane chart. */
export interface FlowBucket {
  bucket_idx: number;
  /** Raw entry index for click-through (null for gap buckets or buckets with no events). */
  start_idx: number | null;
  end_idx: number | null;
  event_count: number;
  /** True for synthetic gap buckets with no events (backend-generated). */
  is_gap: boolean;
  error_count: number;
  dominant_lane: string | null;
  /**
   * Per-lane normalised intensity in [0, 1].
   * 0.0 = no events for this lane in this bucket.
   * Non-zero counts are clamped to [0.15, 1] for visibility.
   */
  laneIntensities: Record<string, number>;
}

/** Safe render model for one subagent's timeline bar. */
export interface FlowSubagentBar {
  /** Stable key for React rendering. */
  key: string;
  /** Raw entry index for click-through (null when unavailable). */
  start_idx: number | null;
  end_idx: number | null;
  /** Safe display label — agent_display_name or agent_name, never raw ids. */
  label: string;
  status: string;
  durationMs: number | null;
}

/** Safe render model for one milestone marker on the timeline rail. */
export interface FlowMilestone {
  /** Raw entry index for click-through (null when not recorded). */
  idx: number | null;
  bucket_idx: number | null;
  /** Milestone kind enum (checkpoint, skill, rewind, task_complete, error, etc.). */
  kind: string;
  /** Safe short label for display — already bounded by backend _LABEL_MAX=80. */
  label: string;
}

/** Session-wide legend: top tools/skills/agents + per-lane totals. */
export interface FlowLegend {
  /** Top tool names by event count (up to 5 shown in canvas chips). */
  topTools: MissionAtlasNameCount[];
  /** Top skill names by event count (up to 5 shown in canvas chips). */
  topSkills: MissionAtlasNameCount[];
  /** Top agent names by event count (up to 5 shown in canvas chips). */
  topAgents: MissionAtlasNameCount[];
  /** Full-session per-lane event counts from Mission Atlas. */
  laneTotals: MissionAtlasLaneTotals;
}

/** Complete bounded model for the FlowSessionCanvas renderer. */
export interface FlowAggregateModel {
  totalEvents: number;
  durationMs: number | null;
  errorCount: number;
  firstEventAt: string | null;
  lastEventAt: string | null;
  buckets: FlowBucket[];
  subagentBars: FlowSubagentBar[];
  milestones: FlowMilestone[];
  legend: FlowLegend;
  /** Human-readable warnings about truncated data (tools, skills, agents, milestones). */
  truncationWarnings: string[];
  /** True when any backend cap was hit — controls truncation badge visibility. */
  hasTruncation: boolean;
}

// ── Pure helpers ──────────────────────────────────────────────────────────────

/**
 * Returns the debug-log page number for a given event index.
 *
 * This is the canonical Flow→page mapping used for click-through navigation.
 * page = Math.floor(idx / pageSize).
 */
export function pageForIdx(idx: number, pageSize: number): number {
  return Math.floor(idx / pageSize);
}

/** Returns a safe empty model when atlas is unavailable. */
function emptyModel(): FlowAggregateModel {
  return {
    totalEvents: 0,
    durationMs: null,
    errorCount: 0,
    firstEventAt: null,
    lastEventAt: null,
    buckets: [],
    subagentBars: [],
    milestones: [],
    legend: {
      topTools: [],
      topSkills: [],
      topAgents: [],
      laneTotals: {
        tool: 0,
        hook: 0,
        skill: 0,
        subagent: 0,
        model: 0,
        turn: 0,
        system: 0,
        error: 0,
        generic: 0,
      },
    },
    truncationWarnings: [],
    hasTruncation: false,
  };
}

/**
 * Builds the FlowAggregateModel from Mission Atlas + optional Subagent Activity.
 *
 * Security contract:
 * - No raw args, result, message, path, prompt, content, or preview fields
 *   are included. Only safe aggregate fields from Mission Atlas are used.
 * - error_sample is intentionally excluded — treat as opaque per policy.
 * - error_preview from SubagentActivityEntry is excluded.
 * - All name fields (tool/skill/agent) come from pre-validated Mission Atlas
 *   _SHORT_ENUM_RE / _safe_subagent_str backend filters.
 *
 * @param atlas  Full-session Mission Atlas response (null/undefined → empty model).
 * @param activity  Subagent Activity response (null/undefined → empty subagentBars).
 * @param _internals  SubagentInternals (reserved for V2 per-bucket detail; unused in V1).
 */
export function buildFlowAggregate(
  atlas: SessionMissionAtlasResponse | null | undefined,
  activity: SubagentActivityResponse | null | undefined,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _internals?: SubagentInternalsResponse | null
): FlowAggregateModel {
  if (!atlas) return emptyModel();

  // ── Compute global max lane event count for normalised intensity ──────────
  let globalMax = 0;
  for (const bucket of atlas.buckets) {
    if (bucket.is_gap) continue;
    for (const count of Object.values(bucket.lanes)) {
      if (count > globalMax) globalMax = count;
    }
  }

  // ── Build FlowBuckets ─────────────────────────────────────────────────────
  const buckets: FlowBucket[] = atlas.buckets.map((b) => {
    const laneIntensities: Record<string, number> = {};
    for (const lane of FLOW_LANE_ORDER) {
      const count = b.lanes[lane] ?? 0;
      // Use global max so intensities are comparable across lanes.
      if (count === 0 || globalMax === 0) {
        laneIntensities[lane] = 0;
      } else {
        // Clamp to [0.15, 1.0] so non-zero events are always visually distinct.
        laneIntensities[lane] = Math.max(0.15, count / globalMax);
      }
    }
    return {
      bucket_idx: b.bucket_idx,
      start_idx: b.start_idx ?? null,
      end_idx: b.end_idx ?? null,
      event_count: b.event_count,
      is_gap: b.is_gap,
      error_count: b.error_count,
      dominant_lane: b.dominant_lane ?? null,
      laneIntensities,
    };
  });

  // ── Build FlowSubagentBars ────────────────────────────────────────────────
  const subagentBars: FlowSubagentBar[] = (activity?.entries ?? []).map((entry, i) => ({
    key: entry.span_id ?? `subagent-${i}`,
    start_idx: entry.start_idx ?? null,
    end_idx: entry.end_idx ?? null,
    // Safe display label: never include error_preview or raw ids.
    label: entry.agent_display_name ?? entry.agent_name ?? "Sub-agent",
    status: entry.status,
    durationMs: entry.duration_ms ?? null,
  }));

  // ── Build FlowMilestones ──────────────────────────────────────────────────
  const milestones: FlowMilestone[] = atlas.milestones.map((m) => ({
    idx: m.idx ?? null,
    bucket_idx: m.bucket_idx ?? null,
    kind: m.kind,
    label: m.label,
  }));

  // ── Truncation warnings ───────────────────────────────────────────────────
  const truncationWarnings: string[] = [];
  const truncated = atlas.truncated ?? {};
  const caps = atlas.caps ?? { top_n: 10, milestones: 50 };
  if (truncated.tools) {
    truncationWarnings.push(`Showing top ${caps.top_n} tools`);
  }
  if (truncated.skills) {
    truncationWarnings.push(`Showing top ${caps.top_n} skills`);
  }
  if (truncated.agents) {
    truncationWarnings.push(`Showing top ${caps.top_n} agents`);
  }
  if (truncated.milestones) {
    truncationWarnings.push(`Showing up to ${caps.milestones} milestones`);
  }
  if (activity?.truncated) {
    truncationWarnings.push(`Showing up to ${activity.cap} sub-agents`);
  }

  const hasTruncation =
    truncated.tools ||
    truncated.skills ||
    truncated.agents ||
    truncated.milestones ||
    Boolean(activity?.truncated);

  return {
    totalEvents: atlas.total_events,
    durationMs: atlas.duration_ms ?? null,
    errorCount: atlas.error_count,
    firstEventAt: atlas.first_event_at ?? null,
    lastEventAt: atlas.last_event_at ?? null,
    buckets,
    subagentBars,
    milestones,
    legend: {
      // Limit to top 5 for the canvas chip rows; full list available in FlowLegend.
      topTools: (atlas.top_tools ?? []).slice(0, 5),
      topSkills: (atlas.top_skills ?? []).slice(0, 5),
      topAgents: (atlas.top_agent_names ?? []).slice(0, 5),
      laneTotals: atlas.lane_totals ?? {},
    },
    truncationWarnings,
    hasTruncation,
  };
}
